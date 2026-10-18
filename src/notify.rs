//! Native desktop notifications (Windows toasts, freedesktop notifications, macOS Notification Center).

use anyhow::{Context, Result};
use notify_rust::Notification;

pub fn show(title: Option<&str>, message: &str) -> Result<()> {
    let mut n = Notification::new();
    n.summary(title.unwrap_or("Home Assistant")).body(message);
    #[cfg(all(unix, not(target_os = "macos")))]
    n.appname("Home Assistant");
    n.show().context("showing notification")?;
    Ok(())
}
