use std::collections::HashMap;

use anyhow::Result;
use mdns_sd::{ServiceDaemon, ServiceInfo};

use crate::config::Config;

/// Must match the `zeroconf` entry in the integration's manifest.json.
pub const SERVICE_TYPE: &str = "_pc-pulse._tcp.local.";

/// Advertises the agent so Home Assistant can discover it. Keep the returned
/// daemon alive for as long as the advertisement should stay up.
pub fn advertise(cfg: &Config) -> Result<ServiceDaemon> {
    let daemon = ServiceDaemon::new()?;
    let host = hostname::get()?.to_string_lossy().into_owned();
    let host_label: String = host
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() || c == '-' { c } else { '-' })
        .collect();
    let properties: HashMap<String, String> = HashMap::from([
        ("id".into(), cfg.id.clone()),
        ("name".into(), cfg.name.clone()),
        ("version".into(), env!("CARGO_PKG_VERSION").into()),
        ("os".into(), std::env::consts::OS.into()),
    ]);
    // Prefer the default-route address so Home Assistant doesn't pick a
    // virtual adapter (Hyper-V, Docker, VPN); fall back to all interfaces.
    let primary = local_ip_address::local_ip().ok();
    let mut info = ServiceInfo::new(
        SERVICE_TYPE,
        &cfg.name,
        &format!("{host_label}.local."),
        primary.map(|ip| ip.to_string()).unwrap_or_default().as_str(),
        cfg.port,
        properties,
    )?;
    if primary.is_none() {
        info = info.enable_addr_auto();
    }
    daemon.register(info)?;
    tracing::info!("advertising {SERVICE_TYPE} as '{}'", cfg.name);
    Ok(daemon)
}
