//! Pending operating system updates: Windows Update, apt/dnf/pacman, macOS softwareupdate.
//!
//! These checks are slow (seconds to minutes), so they run in the background every
//! `os_update_check_hours` and the latest result is served with the status.

use std::process::{Command, Output};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use chrono::{DateTime, Utc};
use serde::Serialize;

use crate::config::Config;

#[derive(Debug, Clone, Serialize)]
pub struct OsUpdates {
    pub count: usize,
    pub titles: Vec<String>,
    pub reboot_required: Option<bool>,
    pub source: &'static str,
    pub checked_at: DateTime<Utc>,
}

pub type SharedOsUpdates = Arc<Mutex<Option<OsUpdates>>>;

fn command(program: &str) -> Command {
    #[allow(unused_mut)]
    let mut cmd = Command::new(program);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        // The agent runs without a console; don't flash a window.
        cmd.creation_flags(0x0800_0000);
    }
    cmd
}

fn run(program: &str, args: &[&str]) -> Option<Output> {
    command(program).args(args).output().ok()
}

#[cfg(windows)]
fn check() -> Option<OsUpdates> {
    const SCRIPT: &str = r#"
$ErrorActionPreference = 'Stop'
$searcher = (New-Object -ComObject Microsoft.Update.Session).CreateUpdateSearcher()
$result = $searcher.Search("IsInstalled=0 and IsHidden=0")
$reboot = (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired') -or
          (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending')
[pscustomobject]@{ titles = @($result.Updates | ForEach-Object { $_.Title }); reboot = $reboot } | ConvertTo-Json -Compress
"#;
    let out = run(
        "powershell",
        &["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", SCRIPT],
    )?;
    if !out.status.success() {
        tracing::warn!(
            "Windows Update query failed: {}",
            String::from_utf8_lossy(&out.stderr).trim()
        );
        return None;
    }
    let v: serde_json::Value = serde_json::from_slice(&out.stdout).ok()?;
    let titles: Vec<String> = match &v["titles"] {
        serde_json::Value::Array(a) => a.iter().filter_map(|t| t.as_str().map(str::to_owned)).collect(),
        serde_json::Value::String(s) => vec![s.clone()],
        _ => Vec::new(),
    };
    Some(OsUpdates {
        count: titles.len(),
        titles,
        reboot_required: v["reboot"].as_bool(),
        source: "windows_update",
        checked_at: Utc::now(),
    })
}

#[cfg(target_os = "linux")]
fn check() -> Option<OsUpdates> {
    let stdout = |o: &Output| String::from_utf8_lossy(&o.stdout).into_owned();
    // Uses the package lists the system already refreshes; no root needed.
    let (source, titles): (&'static str, Vec<String>) = if let Some(o) =
        run("apt", &["list", "--upgradable"]).filter(|o| o.status.success())
    {
        let titles = stdout(&o)
            .lines()
            .filter(|l| l.contains("[upgradable"))
            .filter_map(|l| l.split('/').next().map(str::to_owned))
            .collect();
        ("apt", titles)
    } else if let Some(o) = run("dnf", &["check-update", "-q"])
        // Exit code 100 means updates are available.
        .filter(|o| matches!(o.status.code(), Some(0) | Some(100)))
    {
        let titles = stdout(&o)
            .lines()
            .take_while(|l| !l.starts_with("Obsoleting"))
            .filter(|l| l.split_whitespace().count() == 3)
            .filter_map(|l| l.split_whitespace().next().map(str::to_owned))
            .collect();
        ("dnf", titles)
    } else if let Some(o) = run("checkupdates", &[])
        // Exit code 2 means no updates.
        .filter(|o| matches!(o.status.code(), Some(0) | Some(2)))
    {
        let titles = stdout(&o)
            .lines()
            .filter_map(|l| l.split_whitespace().next().map(str::to_owned))
            .collect();
        ("pacman", titles)
    } else {
        return None;
    };
    Some(OsUpdates {
        count: titles.len(),
        titles,
        reboot_required: Some(std::path::Path::new("/var/run/reboot-required").exists()),
        source,
        checked_at: Utc::now(),
    })
}

#[cfg(target_os = "macos")]
fn check() -> Option<OsUpdates> {
    let out = run("softwareupdate", &["-l"])?;
    if !out.status.success() {
        return None;
    }
    // softwareupdate prints part of its listing on stderr.
    let text = format!(
        "{}\n{}",
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr)
    );
    let titles: Vec<String> = text
        .lines()
        .filter_map(|l| l.trim().strip_prefix("* Label: "))
        .map(str::to_owned)
        .collect();
    Some(OsUpdates {
        count: titles.len(),
        titles,
        reboot_required: Some(text.contains("Action: restart")),
        source: "softwareupdate",
        checked_at: Utc::now(),
    })
}

pub fn enabled(cfg: &Config) -> bool {
    cfg.os_update_check_hours > 0
}

/// Checks shortly after start and then every `os_update_check_hours`.
pub fn spawn_checker(cfg: Arc<Config>, shared: SharedOsUpdates) {
    if !enabled(&cfg) {
        return;
    }
    tokio::spawn(async move {
        tokio::time::sleep(Duration::from_secs(30)).await;
        let every = Duration::from_secs(cfg.os_update_check_hours * 3600);
        loop {
            match tokio::task::spawn_blocking(check).await {
                Ok(Some(result)) => {
                    tracing::info!("{} pending OS updates", result.count);
                    *shared.lock().unwrap() = Some(result);
                }
                Ok(None) => tracing::debug!("OS update check unavailable"),
                Err(e) => tracing::error!("OS update task failed: {e}"),
            }
            tokio::time::sleep(every).await;
        }
    });
}
