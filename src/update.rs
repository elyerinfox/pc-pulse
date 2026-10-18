//! Self-updates from GitHub releases.
//!
//! Each release carries a raw binary per target (`pc-pulse-<target>[.exe]`) plus a
//! `.sha256` file. The agent checks the latest release once a day through the
//! unauthenticated GitHub API, installs newer versions and restarts itself.

use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use anyhow::{Context, Result, bail};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::config::Config;

pub const TARGET: &str = env!("PC_PULSE_TARGET");
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

#[derive(Debug, Clone, Default, Serialize)]
pub struct UpdateStatus {
    pub current: &'static str,
    pub latest: Option<String>,
    pub release_url: Option<String>,
    pub release_notes: Option<String>,
    pub available: bool,
    pub auto: bool,
    pub installing: bool,
    pub checked_at: Option<DateTime<Utc>>,
    pub error: Option<String>,
}

pub type SharedUpdate = Arc<Mutex<UpdateStatus>>;

pub fn new_shared(cfg: &Config) -> SharedUpdate {
    Arc::new(Mutex::new(UpdateStatus {
        current: VERSION,
        auto: cfg.auto_update,
        ..Default::default()
    }))
}

#[derive(Debug, Clone)]
pub struct Release {
    pub version: String,
    binary_url: String,
    checksum_url: Option<String>,
}

#[derive(Deserialize)]
struct GhRelease {
    tag_name: String,
    html_url: String,
    body: Option<String>,
    assets: Vec<GhAsset>,
}

#[derive(Deserialize)]
struct GhAsset {
    name: String,
    browser_download_url: String,
}

fn asset_name() -> String {
    format!("pc-pulse-{TARGET}{}", std::env::consts::EXE_SUFFIX)
}

fn parse_version(v: &str) -> Option<(u64, u64, u64)> {
    let mut it = v.trim_start_matches('v').split(['.', '-', '+']);
    Some((
        it.next()?.parse().ok()?,
        it.next()?.parse().ok()?,
        it.next().unwrap_or("0").parse().ok()?,
    ))
}

fn is_newer(latest: &str, current: &str) -> bool {
    matches!((parse_version(latest), parse_version(current)), (Some(l), Some(c)) if l > c)
}

fn agent() -> ureq::Agent {
    ureq::Agent::config_builder()
        .timeout_global(Some(Duration::from_secs(120)))
        .user_agent(format!("pc-pulse/{VERSION}"))
        .build()
        .into()
}

/// Asks GitHub for the latest release and records the result in `status`.
/// Returns the release if it is newer than this build and has a binary for this target.
pub fn check(cfg: &Config, status: &SharedUpdate) -> Result<Option<Release>> {
    let result = fetch_latest(&cfg.update_repo);
    let mut st = status.lock().unwrap();
    st.checked_at = Some(Utc::now());
    match result {
        Ok((gh, release)) => {
            let version = gh.tag_name.trim_start_matches('v').to_owned();
            st.available = release.is_some() && is_newer(&version, VERSION);
            st.latest = Some(version);
            st.release_url = Some(gh.html_url);
            st.release_notes = gh.body;
            st.error = None;
            Ok(release.filter(|_| st.available))
        }
        Err(e) => {
            st.error = Some(format!("{e:#}"));
            Err(e)
        }
    }
}

fn fetch_latest(repo: &str) -> Result<(GhRelease, Option<Release>)> {
    let url = format!("https://api.github.com/repos/{repo}/releases/latest");
    let gh: GhRelease = agent()
        .get(&url)
        .header("Accept", "application/vnd.github+json")
        .call()
        .with_context(|| format!("querying {url}"))?
        .body_mut()
        .read_json()
        .context("parsing release")?;
    let name = asset_name();
    let find = |n: &str| {
        gh.assets
            .iter()
            .find(|a| a.name == n)
            .map(|a| a.browser_download_url.clone())
    };
    let release = find(&name).map(|binary_url| Release {
        version: gh.tag_name.trim_start_matches('v').to_owned(),
        binary_url,
        checksum_url: find(&format!("{name}.sha256")),
    });
    Ok((gh, release))
}

fn download(url: &str) -> Result<Vec<u8>> {
    agent()
        .get(url)
        .call()
        .with_context(|| format!("downloading {url}"))?
        .body_mut()
        .with_config()
        .limit(256 * 1024 * 1024)
        .read_to_vec()
        .with_context(|| format!("reading {url}"))
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// Replaces the running executable with the release's binary.
pub fn install(release: &Release) -> Result<()> {
    tracing::info!("downloading pc-pulse {}", release.version);
    let binary = download(&release.binary_url)?;
    match &release.checksum_url {
        Some(url) => {
            let expected = String::from_utf8(download(url)?)?;
            let expected = expected.split_whitespace().next().unwrap_or("").to_lowercase();
            let actual = hex(&Sha256::digest(&binary));
            if expected != actual {
                bail!("checksum mismatch: expected {expected}, got {actual}");
            }
        }
        None => bail!("release has no checksum file"),
    }

    let exe = std::env::current_exe()?;
    let new = exe.with_extension("new");
    std::fs::write(&new, &binary).with_context(|| format!("writing {}", new.display()))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&new, std::fs::Permissions::from_mode(0o755))?;
    }
    #[cfg(windows)]
    {
        // A running exe can't be overwritten, but it can be renamed out of the way.
        let old = old_path(&exe);
        let _ = std::fs::remove_file(&old);
        std::fs::rename(&exe, &old).context("moving the current executable aside")?;
        if let Err(e) = std::fs::rename(&new, &exe) {
            let _ = std::fs::rename(&old, &exe);
            return Err(e).context("installing the new executable");
        }
    }
    #[cfg(not(windows))]
    std::fs::rename(&new, &exe).context("installing the new executable")?;
    tracing::info!("installed pc-pulse {} at {}", release.version, exe.display());
    Ok(())
}

#[cfg_attr(not(windows), allow(dead_code))]
fn old_path(exe: &Path) -> PathBuf {
    exe.with_extension("old.exe")
}

/// Removes the executable left behind by a previous update.
pub fn cleanup() {
    if let Ok(exe) = std::env::current_exe() {
        let _ = std::fs::remove_file(old_path(&exe));
        let _ = std::fs::remove_file(exe.with_extension("new"));
    }
}

/// Starts the freshly installed binary with the same arguments and ends this process.
pub fn restart() -> ! {
    let exe = std::env::current_exe().expect("current exe");
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    tracing::info!("restarting into the new version");
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        // Replaces this process, so systemd/launchd keep tracking the same PID.
        let err = std::process::Command::new(&exe).args(&args).exec();
        tracing::error!("restart failed: {err}");
        // The service manager restarts us (with the new binary) on failure.
        std::process::exit(1);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        const CREATE_BREAKAWAY_FROM_JOB: u32 = 0x0100_0000;
        let spawn = |flags| std::process::Command::new(&exe).args(&args).creation_flags(flags).spawn();
        // Break away from the scheduled task's job so the new process outlives this one.
        if let Err(e) = spawn(CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB)
            .or_else(|_| spawn(CREATE_NO_WINDOW))
        {
            tracing::error!("restart failed: {e}");
            std::process::exit(1);
        }
        std::process::exit(0);
    }
}

/// Checks, installs and restarts. Used by the daily task and the API.
pub fn update_now(cfg: &Config, status: &SharedUpdate) -> Result<bool> {
    let Some(release) = check(cfg, status)? else {
        return Ok(false);
    };
    status.lock().unwrap().installing = true;
    let result = install(&release);
    let mut st = status.lock().unwrap();
    st.installing = false;
    if let Err(e) = &result {
        st.error = Some(format!("{e:#}"));
    }
    result.map(|_| true)
}

/// Checks for updates shortly after start and then every `update_check_hours`.
pub fn spawn_checker(cfg: Arc<Config>, status: SharedUpdate) {
    if cfg.update_repo.is_empty() {
        return;
    }
    tokio::spawn(async move {
        tokio::time::sleep(Duration::from_secs(60)).await;
        let every = Duration::from_secs(cfg.update_check_hours.max(1) * 3600);
        loop {
            let (c, s) = (cfg.clone(), status.clone());
            let outcome = tokio::task::spawn_blocking(move || {
                if c.auto_update {
                    update_now(&c, &s)
                } else {
                    check(&c, &s).map(|_| false)
                }
            })
            .await;
            match outcome {
                Ok(Ok(true)) => restart(),
                Ok(Ok(false)) => {}
                Ok(Err(e)) => tracing::warn!("update check failed: {e:#}"),
                Err(e) => tracing::error!("update task failed: {e}"),
            }
            tokio::time::sleep(every).await;
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn versions() {
        assert!(is_newer("0.3.0", "0.2.9"));
        assert!(is_newer("v1.0.0", "0.9.9"));
        assert!(!is_newer("0.2.0", "0.2.0"));
        assert!(!is_newer("0.1.9", "0.2.0"));
        assert!(!is_newer("garbage", "0.2.0"));
    }
}
