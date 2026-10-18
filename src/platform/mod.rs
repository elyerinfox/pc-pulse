//! Platform specific functionality: user activity, sessions and power control.

#[cfg(target_os = "linux")]
mod linux;
#[cfg(target_os = "macos")]
mod macos;
#[cfg(windows)]
mod windows;

#[cfg(target_os = "linux")]
use linux as imp;
#[cfg(target_os = "macos")]
use macos as imp;
#[cfg(windows)]
use windows as imp;

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PowerAction {
    Shutdown,
    Restart,
    Sleep,
    Hibernate,
    Lock,
    Logoff,
    MonitorOff,
    MonitorOn,
}

impl PowerAction {
    pub const ALL: [PowerAction; 8] = [
        PowerAction::Shutdown,
        PowerAction::Restart,
        PowerAction::Sleep,
        PowerAction::Hibernate,
        PowerAction::Lock,
        PowerAction::Logoff,
        PowerAction::MonitorOff,
        PowerAction::MonitorOn,
    ];

    pub fn parse(s: &str) -> Option<Self> {
        serde_json::from_value(serde_json::Value::String(s.to_owned())).ok()
    }

    pub fn as_str(self) -> &'static str {
        match self {
            PowerAction::Shutdown => "shutdown",
            PowerAction::Restart => "restart",
            PowerAction::Sleep => "sleep",
            PowerAction::Hibernate => "hibernate",
            PowerAction::Lock => "lock",
            PowerAction::Logoff => "logoff",
            PowerAction::MonitorOff => "monitor_off",
            PowerAction::MonitorOn => "monitor_on",
        }
    }
}

/// Information about the interactive user's activity.
#[derive(Debug, Clone, Default, Serialize)]
pub struct Activity {
    /// Seconds since the last keyboard/mouse input, if it can be determined.
    pub idle_seconds: Option<u64>,
    /// Whether the session is locked, if it can be determined.
    pub locked: Option<bool>,
}

pub fn activity() -> Activity {
    imp::activity()
}

/// Names of users with an active login session.
pub fn logged_in_users() -> Vec<String> {
    let mut users = imp::logged_in_users();
    users.sort();
    users.dedup();
    users
}

pub fn supported_actions() -> Vec<PowerAction> {
    imp::supported_actions()
}

#[derive(Debug, Clone, Serialize)]
pub struct Battery {
    pub percent: f32,
    pub charging: bool,
    pub plugged_in: Option<bool>,
    /// Estimated seconds until empty while discharging.
    pub time_remaining_secs: Option<u64>,
}

/// Battery state, or `None` on machines without one.
pub fn battery() -> Option<Battery> {
    imp::battery()
}

pub fn execute(action: PowerAction) -> anyhow::Result<()> {
    tracing::warn!("executing power action {}", action.as_str());
    imp::execute(action)
}

/// Runs a command line through the platform shell without waiting for it.
pub fn spawn_shell(command: &str) -> anyhow::Result<()> {
    #[cfg(windows)]
    let mut cmd = {
        let mut c = std::process::Command::new("cmd");
        c.arg("/C").arg(command);
        c
    };
    #[cfg(not(windows))]
    let mut cmd = {
        let mut c = std::process::Command::new("sh");
        c.arg("-c").arg(command);
        c
    };
    cmd.stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()?;
    Ok(())
}

/// Runs a program and returns stdout if it exited successfully.
#[allow(dead_code)]
pub(crate) fn output_of(program: &str, args: &[&str]) -> Option<String> {
    let out = std::process::Command::new(program)
        .args(args)
        .output()
        .ok()?;
    out.status
        .success()
        .then(|| String::from_utf8_lossy(&out.stdout).into_owned())
}

#[allow(dead_code)]
fn run_checked(program: &str, args: &[&str]) -> anyhow::Result<()> {
    let status = std::process::Command::new(program).args(args).status()?;
    anyhow::ensure!(status.success(), "{program} exited with {status}");
    Ok(())
}
