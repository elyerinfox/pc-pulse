use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};

pub const DEFAULT_PORT: u16 = 8752;

/// Agent configuration, persisted as TOML.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct Config {
    /// Stable identifier for this machine, used as the Home Assistant unique id.
    pub id: String,
    /// Display name. Defaults to the hostname.
    pub name: String,
    /// Address to listen on.
    pub bind: String,
    pub port: u16,
    /// Bearer token Home Assistant must send.
    pub token: String,
    /// How often system stats are sampled, in seconds.
    pub refresh_interval_secs: u64,
    /// Number of top processes (by CPU and by memory) included in the status.
    pub top_processes: usize,
    /// Idle seconds after which the user is considered inactive.
    pub active_threshold_secs: u64,
    /// Advertise the agent over mDNS / zeroconf.
    pub mdns: bool,
    /// Allow power actions (shutdown, restart, sleep, ...).
    pub allow_power: bool,
    /// Allow killing processes.
    pub allow_kill: bool,
    /// Allow Home Assistant to show desktop notifications.
    pub allow_notify: bool,
    /// Named shell commands Home Assistant may trigger. Only commands listed here can be run.
    pub commands: BTreeMap<String, String>,
}

impl Default for Config {
    fn default() -> Self {
        let host = hostname::get()
            .map(|h| h.to_string_lossy().into_owned())
            .unwrap_or_else(|_| "computer".into());
        Self {
            id: random_hex(8),
            name: host,
            bind: "0.0.0.0".into(),
            port: DEFAULT_PORT,
            token: random_hex(24),
            refresh_interval_secs: 2,
            top_processes: 10,
            active_threshold_secs: 300,
            mdns: true,
            allow_power: true,
            allow_kill: true,
            allow_notify: true,
            commands: BTreeMap::new(),
        }
    }
}

impl Config {
    pub fn default_path() -> PathBuf {
        dirs::config_dir()
            .unwrap_or_else(|| PathBuf::from("."))
            .join("pc-pulse")
            .join("config.toml")
    }

    /// Loads the config, creating it with fresh credentials if it does not exist yet.
    /// Missing fields are filled in and written back so ids/tokens stay stable.
    pub fn load_or_create(path: &Path) -> Result<Self> {
        if path.exists() {
            let raw = std::fs::read_to_string(path)
                .with_context(|| format!("reading {}", path.display()))?;
            let value: toml::Table =
                toml::from_str(&raw).with_context(|| format!("parsing {}", path.display()))?;
            let cfg: Config = value.clone().try_into()?;
            let complete = ["id", "token"].iter().all(|k| value.contains_key(*k));
            if !complete {
                cfg.save(path)?;
            }
            Ok(cfg)
        } else {
            let cfg = Config::default();
            cfg.save(path)?;
            tracing::info!("created new config at {}", path.display());
            Ok(cfg)
        }
    }

    pub fn save(&self, path: &Path) -> Result<()> {
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        std::fs::write(path, toml::to_string_pretty(self)?)
            .with_context(|| format!("writing {}", path.display()))
    }
}

pub fn random_hex(bytes: usize) -> String {
    (0..bytes)
        .map(|_| format!("{:02x}", rand::random::<u8>()))
        .collect()
}
