use std::sync::{Arc, Mutex};
use std::time::Duration;

use axum::extract::{Path, Query, Request, State};
use axum::http::{StatusCode, header};
use axum::middleware::{self, Next};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::{Deserialize, Serialize};
use serde_json::json;
use sysinfo::{Pid, ProcessesToUpdate};

use crate::collector::{Collector, Shared, Snapshot};
use crate::config::Config;
use crate::notify;
use crate::osupdates::{OsUpdates, SharedOsUpdates};
use crate::update::{self, SharedUpdate, UpdateStatus};
use crate::platform::{self, PowerAction};

#[derive(Clone)]
pub struct AppState {
    pub cfg: Arc<Config>,
    pub collector: Arc<Mutex<Collector>>,
    pub shared: Shared,
    pub update: SharedUpdate,
    pub os_updates: SharedOsUpdates,
}

pub struct ApiError(StatusCode, String);

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (self.0, Json(json!({ "error": self.1 }))).into_response()
    }
}

type ApiResult<T> = Result<T, ApiError>;

fn err(status: StatusCode, msg: impl Into<String>) -> ApiError {
    ApiError(status, msg.into())
}

pub fn router(state: AppState) -> Router {
    let protected = Router::new()
        .route("/api/v1/status", get(status))
        .route("/api/v1/processes", get(processes))
        .route("/api/v1/processes/kill", post(kill))
        .route("/api/v1/power/{action}", post(power))
        .route("/api/v1/commands/{name}", post(run_command))
        .route("/api/v1/notify", post(notify))
        .route("/api/v1/update", get(update_status).post(update_install))
        .route_layer(middleware::from_fn_with_state(state.clone(), auth));

    Router::new()
        .route("/api/v1/health", get(health))
        .merge(protected)
        .with_state(state)
}

/// Constant-time comparison so the token can't be guessed byte by byte.
fn token_matches(given: &str, expected: &str) -> bool {
    given.len() == expected.len()
        && given
            .bytes()
            .zip(expected.bytes())
            .fold(0u8, |acc, (a, b)| acc | (a ^ b))
            == 0
}

async fn auth(State(state): State<AppState>, req: Request, next: Next) -> Response {
    let given = req
        .headers()
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer "))
        .unwrap_or("");
    if token_matches(given, &state.cfg.token) {
        next.run(req).await
    } else {
        err(StatusCode::UNAUTHORIZED, "invalid or missing token").into_response()
    }
}

async fn health(State(state): State<AppState>) -> impl IntoResponse {
    Json(json!({
        "ok": true,
        "id": state.cfg.id,
        "name": state.cfg.name,
        "version": env!("CARGO_PKG_VERSION"),
    }))
}

/// The snapshot plus state kept outside the collector. Serialized directly (not via
/// `serde_json::Value`) so f32 values keep their short form.
#[derive(Serialize)]
struct StatusResponse<'a> {
    #[serde(flatten)]
    snapshot: &'a Snapshot,
    update: UpdateStatus,
    os_updates: Option<OsUpdates>,
}

async fn status(State(state): State<AppState>) -> ApiResult<Response> {
    let guard = state.shared.read().await;
    let Some(snapshot) = guard.as_ref() else {
        return Err(err(StatusCode::SERVICE_UNAVAILABLE, "still collecting initial sample"));
    };
    let body = StatusResponse {
        snapshot,
        update: state.update.lock().unwrap().clone(),
        os_updates: state.os_updates.lock().unwrap().clone(),
    };
    Ok(Json(body).into_response())
}

#[derive(Deserialize)]
struct ProcessQuery {
    #[serde(default)]
    sort: Option<String>,
    #[serde(default)]
    limit: Option<usize>,
}

async fn processes(
    State(state): State<AppState>,
    Query(q): Query<ProcessQuery>,
) -> ApiResult<Response> {
    let mut list = state.collector.lock().unwrap().process_list();
    match q.sort.as_deref().unwrap_or("cpu") {
        "cpu" => list.sort_by(|a, b| b.cpu_percent.total_cmp(&a.cpu_percent)),
        "memory" => list.sort_by_key(|p| std::cmp::Reverse(p.memory_bytes)),
        "name" => list.sort_by_key(|p| p.name.to_lowercase()),
        "pid" => list.sort_by_key(|p| p.pid),
        other => return Err(err(StatusCode::BAD_REQUEST, format!("unknown sort '{other}'"))),
    }
    if let Some(limit) = q.limit {
        list.truncate(limit);
    }
    Ok(Json(list).into_response())
}

#[derive(Deserialize)]
struct KillRequest {
    pid: Option<u32>,
    name: Option<String>,
}

async fn kill(
    State(state): State<AppState>,
    Json(req): Json<KillRequest>,
) -> ApiResult<Response> {
    if !state.cfg.allow_kill {
        return Err(err(StatusCode::FORBIDDEN, "killing processes is disabled"));
    }
    if req.pid.is_none() && req.name.is_none() {
        return Err(err(StatusCode::BAD_REQUEST, "pid or name is required"));
    }
    let mut collector = state.collector.lock().unwrap();
    // The cached list may predate the target process.
    match req.pid {
        Some(pid) => collector.refresh_processes(ProcessesToUpdate::Some(&[Pid::from_u32(pid)])),
        None => collector.refresh_processes(ProcessesToUpdate::All),
    }
    let own_pid = std::process::id();
    let name = req.name.as_deref().map(str::to_lowercase);
    let mut killed = Vec::new();
    let mut failed = Vec::new();
    {
        let sys = collector.system();
        for (pid, proc_) in sys.processes() {
            if pid.as_u32() == own_pid || proc_.thread_kind().is_some() {
                continue;
            }
            let pname = proc_.name().to_string_lossy().to_lowercase();
            let matches = req.pid == Some(pid.as_u32())
                || name.as_deref().is_some_and(|n| {
                    pname == n || pname.strip_suffix(".exe") == Some(n)
                });
            if matches {
                if proc_.kill() {
                    killed.push(pid.as_u32());
                } else {
                    failed.push(pid.as_u32());
                }
            }
        }
    }
    if !killed.is_empty() {
        let pids: Vec<Pid> = killed.iter().map(|p| Pid::from_u32(*p)).collect();
        collector.refresh_processes(ProcessesToUpdate::Some(&pids));
    }
    if killed.is_empty() && failed.is_empty() {
        return Err(err(StatusCode::NOT_FOUND, "no matching process"));
    }
    tracing::warn!("killed processes {killed:?} (failed: {failed:?})");
    Ok(Json(json!({ "killed": killed, "failed": failed })).into_response())
}

#[derive(Deserialize, Default)]
struct PowerRequest {
    /// Seconds to wait before performing the action.
    #[serde(default)]
    delay_secs: u64,
}

async fn power(
    State(state): State<AppState>,
    Path(action): Path<String>,
    body: Option<Json<PowerRequest>>,
) -> ApiResult<Response> {
    if !state.cfg.allow_power {
        return Err(err(StatusCode::FORBIDDEN, "power actions are disabled"));
    }
    let action = PowerAction::parse(&action)
        .ok_or_else(|| err(StatusCode::NOT_FOUND, format!("unknown action '{action}'")))?;
    if !platform::supported_actions().contains(&action) {
        return Err(err(
            StatusCode::NOT_IMPLEMENTED,
            format!("'{}' is not supported on this platform", action.as_str()),
        ));
    }
    let delay = body.map(|b| b.0.delay_secs).unwrap_or(0);

    // Instant actions run inline so errors are reported. Actions that take the
    // machine down are deferred so the HTTP response gets back to Home Assistant first.
    let deferred = matches!(
        action,
        PowerAction::Shutdown
            | PowerAction::Restart
            | PowerAction::Sleep
            | PowerAction::Hibernate
            | PowerAction::Logoff
    );
    if deferred || delay > 0 {
        tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(750) + Duration::from_secs(delay)).await;
            if let Err(e) = tokio::task::spawn_blocking(move || platform::execute(action))
                .await
                .unwrap_or_else(|e| Err(e.into()))
            {
                tracing::error!("power action {} failed: {e:#}", action.as_str());
            }
        });
        Ok((
            StatusCode::ACCEPTED,
            Json(json!({ "action": action.as_str(), "scheduled": true, "delay_secs": delay })),
        )
            .into_response())
    } else {
        tokio::task::spawn_blocking(move || platform::execute(action))
            .await
            .map_err(|e| err(StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
            .map_err(|e| err(StatusCode::INTERNAL_SERVER_ERROR, format!("{e:#}")))?;
        Ok(Json(json!({ "action": action.as_str(), "scheduled": false })).into_response())
    }
}

async fn run_command(
    State(state): State<AppState>,
    Path(name): Path<String>,
) -> ApiResult<Response> {
    let command = state
        .cfg
        .commands
        .get(&name)
        .ok_or_else(|| err(StatusCode::NOT_FOUND, format!("unknown command '{name}'")))?;
    tracing::warn!("running command '{name}': {command}");
    platform::spawn_shell(command)
        .map_err(|e| err(StatusCode::INTERNAL_SERVER_ERROR, format!("{e:#}")))?;
    Ok(Json(json!({ "command": name, "started": true })).into_response())
}

#[derive(Deserialize)]
struct NotifyRequest {
    message: String,
    #[serde(default)]
    title: Option<String>,
}

async fn notify(
    State(state): State<AppState>,
    Json(req): Json<NotifyRequest>,
) -> ApiResult<Response> {
    if !state.cfg.allow_notify {
        return Err(err(StatusCode::FORBIDDEN, "notifications are disabled"));
    }
    if req.message.trim().is_empty() {
        return Err(err(StatusCode::BAD_REQUEST, "message is required"));
    }
    let title = req.title.filter(|t| !t.trim().is_empty());
    tokio::task::spawn_blocking(move || notify::show(title.as_deref(), &req.message))
        .await
        .map_err(|e| err(StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .map_err(|e| err(StatusCode::INTERNAL_SERVER_ERROR, format!("{e:#}")))?;
    Ok(Json(json!({ "shown": true })).into_response())
}

async fn update_status(State(state): State<AppState>) -> ApiResult<Response> {
    if state.cfg.update_repo.is_empty() {
        return Err(err(StatusCode::FORBIDDEN, "updates are disabled"));
    }
    let (cfg, status) = (state.cfg.clone(), state.update.clone());
    // A failed check is recorded in the status, which is returned either way.
    let _ = tokio::task::spawn_blocking(move || update::check(&cfg, &status)).await;
    Ok(Json(json!(*state.update.lock().unwrap())).into_response())
}

async fn update_install(State(state): State<AppState>) -> ApiResult<Response> {
    if state.cfg.update_repo.is_empty() {
        return Err(err(StatusCode::FORBIDDEN, "updates are disabled"));
    }
    if state.update.lock().unwrap().installing {
        return Err(err(StatusCode::CONFLICT, "an update is already being installed"));
    }
    let (cfg, status) = (state.cfg.clone(), state.update.clone());
    let release = tokio::task::spawn_blocking(move || update::check(&cfg, &status))
        .await
        .map_err(|e| err(StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .map_err(|e| err(StatusCode::BAD_GATEWAY, format!("{e:#}")))?;
    let Some(release) = release else {
        return Err(err(StatusCode::CONFLICT, "already up to date"));
    };
    let version = release.version.clone();
    let status = state.update.clone();
    status.lock().unwrap().installing = true;
    // Install after responding, then restart into the new version.
    tokio::spawn(async move {
        tokio::time::sleep(Duration::from_millis(500)).await;
        let st = status.clone();
        let result = tokio::task::spawn_blocking(move || update::install(&release)).await;
        let mut s = st.lock().unwrap();
        s.installing = false;
        match result {
            Ok(Ok(())) => {
                drop(s);
                update::restart();
            }
            Ok(Err(e)) => {
                tracing::error!("update failed: {e:#}");
                s.error = Some(format!("{e:#}"));
            }
            Err(e) => s.error = Some(e.to_string()),
        }
    });
    Ok((
        StatusCode::ACCEPTED,
        Json(json!({ "installing": true, "version": version })),
    )
        .into_response())
}
