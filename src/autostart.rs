//! Registers the agent to start when the user logs in. The agent runs inside
//! the user's session so it can read input idle time and lock the screen.

use std::path::Path;

use anyhow::{Context, Result};

const TASK_NAME: &str = "pc-pulse-agent";

#[cfg(windows)]
pub fn install(exe: &Path, config: &Path) -> Result<()> {
    let action = format!(
        "\"{}\" --config \"{}\" run --background",
        exe.display(),
        config.display()
    );
    let create = |level: &str| {
        std::process::Command::new("schtasks")
            .args([
                "/Create", "/F", "/SC", "ONLOGON", "/RL", level, "/TN", TASK_NAME, "/TR",
            ])
            .arg(&action)
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status()
            .context("running schtasks")
    };
    // Elevated tasks can read hardware temperatures; fall back when not run as admin.
    let elevated = create("HIGHEST")?.success();
    if !elevated {
        let status = create("LIMITED")?;
        anyhow::ensure!(status.success(), "schtasks failed ({status})");
        println!("Not elevated: installed without admin rights (no temperature sensors).");
    }
    let rule = format!("name={TASK_NAME}");
    let program = format!("program={}", exe.display());
    let _ = std::process::Command::new("netsh")
        .args(["advfirewall", "firewall", "delete", "rule", &rule])
        .stdout(std::process::Stdio::null())
        .status();
    let firewall = std::process::Command::new("netsh")
        .args([
            "advfirewall",
            "firewall",
            "add",
            "rule",
            &rule,
            "dir=in",
            "action=allow",
            &program,
            "enable=yes",
            "profile=private,domain",
        ])
        .stdout(std::process::Stdio::null())
        .status()
        .is_ok_and(|s| s.success());
    if !firewall {
        println!(
            "Could not add a firewall rule; allow the agent through Windows Firewall manually."
        );
    }
    // Start it right away instead of waiting for the next logon.
    let _ = std::process::Command::new("schtasks")
        .args(["/Run", "/TN", TASK_NAME])
        .status();
    println!("Installed scheduled task '{TASK_NAME}' (runs at logon).");
    Ok(())
}

#[cfg(windows)]
pub fn uninstall() -> Result<()> {
    let _ = std::process::Command::new("schtasks")
        .args(["/End", "/TN", TASK_NAME])
        .status();
    let status = std::process::Command::new("schtasks")
        .args(["/Delete", "/F", "/TN", TASK_NAME])
        .status()?;
    anyhow::ensure!(status.success(), "schtasks failed ({status})");
    let _ = std::process::Command::new("netsh")
        .args([
            "advfirewall",
            "firewall",
            "delete",
            "rule",
            &format!("name={TASK_NAME}"),
        ])
        .stdout(std::process::Stdio::null())
        .status();
    println!("Removed scheduled task '{TASK_NAME}'.");
    Ok(())
}

#[cfg(target_os = "linux")]
fn unit_path() -> Result<std::path::PathBuf> {
    Ok(dirs::config_dir()
        .context("no config dir")?
        .join("systemd/user")
        .join(format!("{TASK_NAME}.service")))
}

#[cfg(target_os = "linux")]
pub fn install(exe: &Path, config: &Path) -> Result<()> {
    let unit = format!(
        "[Unit]\nDescription=Home Assistant computer agent\nAfter=network-online.target\n\n\
         [Service]\nExecStart=\"{}\" --config \"{}\" run\nRestart=on-failure\nRestartSec=5\n\n\
         [Install]\nWantedBy=default.target\n",
        exe.display(),
        config.display()
    );
    let path = unit_path()?;
    std::fs::create_dir_all(path.parent().unwrap())?;
    std::fs::write(&path, unit)?;
    for args in [
        &["--user", "daemon-reload"][..],
        &["--user", "enable", "--now", &format!("{TASK_NAME}.service")],
    ] {
        let status = std::process::Command::new("systemctl")
            .args(args)
            .status()?;
        anyhow::ensure!(status.success(), "systemctl {args:?} failed");
    }
    println!(
        "Installed {}. Run `loginctl enable-linger $USER` to keep it running without a login.",
        path.display()
    );
    Ok(())
}

#[cfg(target_os = "linux")]
pub fn uninstall() -> Result<()> {
    let _ = std::process::Command::new("systemctl")
        .args([
            "--user",
            "disable",
            "--now",
            &format!("{TASK_NAME}.service"),
        ])
        .status();
    let path = unit_path()?;
    if path.exists() {
        std::fs::remove_file(&path)?;
    }
    println!("Removed {}.", path.display());
    Ok(())
}

#[cfg(target_os = "macos")]
fn plist_path() -> Result<std::path::PathBuf> {
    Ok(dirs::home_dir()
        .context("no home dir")?
        .join(format!("Library/LaunchAgents/{TASK_NAME}.plist")))
}

#[cfg(target_os = "macos")]
pub fn install(exe: &Path, config: &Path) -> Result<()> {
    let plist = format!(
        r#"<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{TASK_NAME}</string>
  <key>ProgramArguments</key><array>
    <string>{}</string><string>--config</string><string>{}</string><string>run</string><string>--background</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict></plist>
"#,
        exe.display(),
        config.display()
    );
    let path = plist_path()?;
    std::fs::create_dir_all(path.parent().unwrap())?;
    std::fs::write(&path, plist)?;
    let status = std::process::Command::new("launchctl")
        .arg("load")
        .arg("-w")
        .arg(&path)
        .status()?;
    anyhow::ensure!(status.success(), "launchctl load failed");
    println!("Installed {}.", path.display());
    Ok(())
}

#[cfg(target_os = "macos")]
pub fn uninstall() -> Result<()> {
    let path = plist_path()?;
    let _ = std::process::Command::new("launchctl")
        .arg("unload")
        .arg(&path)
        .status();
    if path.exists() {
        std::fs::remove_file(&path)?;
    }
    println!("Removed {}.", path.display());
    Ok(())
}
