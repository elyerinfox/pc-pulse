use super::{Activity, PowerAction, output_of, run_checked};

struct Session {
    id: String,
    user: String,
}

fn sessions() -> Vec<Session> {
    let Some(out) = output_of("loginctl", &["list-sessions", "--no-legend", "--no-pager"]) else {
        return Vec::new();
    };
    out.lines()
        .filter_map(|line| {
            let mut cols = line.split_whitespace();
            let id = cols.next()?.to_owned();
            let _uid = cols.next()?;
            let user = cols.next()?.to_owned();
            Some(Session { id, user })
        })
        .collect()
}

fn session_props(id: &str) -> std::collections::HashMap<String, String> {
    output_of(
        "loginctl",
        &[
            "show-session",
            id,
            "-p",
            "Type",
            "-p",
            "Class",
            "-p",
            "Active",
            "-p",
            "IdleHint",
            "-p",
            "IdleSinceHint",
            "-p",
            "LockedHint",
        ],
    )
    .unwrap_or_default()
    .lines()
    .filter_map(|l| l.split_once('='))
    .map(|(k, v)| (k.to_owned(), v.to_owned()))
    .collect()
}

pub fn activity() -> Activity {
    let mut activity = Activity::default();

    // xprintidle gives precise idle time on X11 when DISPLAY is reachable.
    if let Some(ms) = output_of("xprintidle", &[]).and_then(|s| s.trim().parse::<u64>().ok()) {
        activity.idle_seconds = Some(ms / 1000);
    }

    for s in sessions() {
        let props = session_props(&s.id);
        let graphical = matches!(
            props.get("Type").map(String::as_str),
            Some("x11" | "wayland" | "mir")
        );
        if !graphical || props.get("Active").map(String::as_str) != Some("yes") {
            continue;
        }
        if let Some(locked) = props.get("LockedHint") {
            activity.locked = Some(locked == "yes");
        }
        if activity.idle_seconds.is_none() {
            let idle = props.get("IdleHint").map(String::as_str) == Some("yes");
            let since_us = props
                .get("IdleSinceHint")
                .and_then(|v| v.parse::<u64>().ok())
                .unwrap_or(0);
            activity.idle_seconds = Some(if idle && since_us > 0 {
                let now_us = std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .map(|d| d.as_micros() as u64)
                    .unwrap_or(0);
                now_us.saturating_sub(since_us) / 1_000_000
            } else {
                0
            });
        }
        break;
    }
    activity
}

pub fn logged_in_users() -> Vec<String> {
    let users: Vec<String> = sessions().into_iter().map(|s| s.user).collect();
    if !users.is_empty() {
        return users;
    }
    output_of("who", &[])
        .unwrap_or_default()
        .lines()
        .filter_map(|l| l.split_whitespace().next().map(str::to_owned))
        .collect()
}

pub fn supported_actions() -> Vec<PowerAction> {
    PowerAction::ALL.to_vec()
}

pub fn execute(action: PowerAction) -> anyhow::Result<()> {
    match action {
        PowerAction::Shutdown => run_checked("systemctl", &["poweroff"]),
        PowerAction::Restart => run_checked("systemctl", &["reboot"]),
        PowerAction::Sleep => run_checked("systemctl", &["suspend"]),
        PowerAction::Hibernate => run_checked("systemctl", &["hibernate"]),
        PowerAction::Lock => run_checked("loginctl", &["lock-sessions"]),
        PowerAction::Logoff => {
            for s in sessions() {
                let props = session_props(&s.id);
                if props.get("Class").map(String::as_str) == Some("user") {
                    let _ = run_checked("loginctl", &["terminate-session", &s.id]);
                }
            }
            Ok(())
        }
        PowerAction::MonitorOff => run_checked("xset", &["dpms", "force", "off"]),
        PowerAction::MonitorOn => run_checked("xset", &["dpms", "force", "on"]),
    }
}
