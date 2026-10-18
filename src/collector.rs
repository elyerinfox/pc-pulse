use std::collections::HashMap;
use std::sync::Arc;
use std::time::{Duration, Instant};

use chrono::{DateTime, Utc};
use serde::Serialize;
use sysinfo::{
    Components, CpuRefreshKind, Disks, MemoryRefreshKind, Networks, ProcessRefreshKind,
    ProcessesToUpdate, RefreshKind, System, UpdateKind,
};
use tokio::sync::RwLock;

use crate::config::Config;
use crate::gpu::{GpuInfo, Gpus};
use crate::hardware::{self, HardwareInfo};
use crate::platform::{self, Battery};

#[derive(Debug, Clone, Serialize)]
pub struct OsInfo {
    pub name: Option<String>,
    pub version: Option<String>,
    pub long_version: Option<String>,
    pub kernel: Option<String>,
    pub arch: String,
    pub family: &'static str,
}

#[derive(Debug, Clone, Serialize)]
pub struct CpuInfo {
    pub brand: String,
    pub logical_cores: usize,
    pub physical_cores: Option<usize>,
    pub usage_percent: f32,
    pub frequency_mhz: u64,
    pub per_core_percent: Vec<f32>,
    /// 1, 5 and 15 minute load averages (zero on Windows).
    pub load_average: [f64; 3],
}

#[derive(Debug, Clone, Serialize)]
pub struct MemoryInfo {
    pub total_bytes: u64,
    pub used_bytes: u64,
    pub available_bytes: u64,
    pub usage_percent: f32,
    pub swap_total_bytes: u64,
    pub swap_used_bytes: u64,
    pub swap_usage_percent: f32,
}

#[derive(Debug, Clone, Serialize)]
pub struct DiskInfo {
    pub name: String,
    pub mount_point: String,
    pub file_system: String,
    pub kind: String,
    pub removable: bool,
    pub total_bytes: u64,
    pub available_bytes: u64,
    pub used_bytes: u64,
    pub usage_percent: f32,
    pub read_bytes_per_sec: f64,
    pub write_bytes_per_sec: f64,
}

#[derive(Debug, Clone, Serialize)]
pub struct DiskIo {
    pub read_bytes_per_sec: f64,
    pub write_bytes_per_sec: f64,
}

#[derive(Debug, Clone, Serialize)]
pub struct NetworkInfo {
    pub name: String,
    pub mac_address: String,
    pub ip_addresses: Vec<String>,
    pub rx_bytes_total: u64,
    pub tx_bytes_total: u64,
    pub rx_bytes_per_sec: f64,
    pub tx_bytes_per_sec: f64,
}

#[derive(Debug, Clone, Serialize)]
pub struct NetworkTotals {
    pub rx_bytes_per_sec: f64,
    pub tx_bytes_per_sec: f64,
    pub rx_bytes_total: u64,
    pub tx_bytes_total: u64,
}

#[derive(Debug, Clone, Serialize)]
pub struct TemperatureInfo {
    pub label: String,
    pub celsius: f32,
    pub critical_celsius: Option<f32>,
}

#[derive(Debug, Clone, Serialize)]
pub struct ProcessInfo {
    pub pid: u32,
    pub name: String,
    pub exe: Option<String>,
    /// CPU usage normalised to the whole machine (0-100).
    pub cpu_percent: f32,
    pub memory_bytes: u64,
    pub status: String,
    pub run_time_secs: u64,
}

#[derive(Debug, Clone, Serialize)]
pub struct ActivityInfo {
    pub idle_seconds: Option<u64>,
    pub last_active: Option<DateTime<Utc>>,
    pub active: Option<bool>,
    pub locked: Option<bool>,
    pub users: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct Snapshot {
    pub id: String,
    pub name: String,
    pub hostname: String,
    pub agent_version: &'static str,
    pub timestamp: DateTime<Utc>,
    pub os: OsInfo,
    pub uptime_secs: u64,
    pub boot_time: DateTime<Utc>,
    pub cpu: CpuInfo,
    pub memory: MemoryInfo,
    pub disks: Vec<DiskInfo>,
    pub disk_io: DiskIo,
    pub network: NetworkTotals,
    pub interfaces: Vec<NetworkInfo>,
    /// Address and MAC of the interface used for the default route (for Wake-on-LAN).
    pub primary_ip: Option<String>,
    pub primary_mac: Option<String>,
    pub temperatures: Vec<TemperatureInfo>,
    pub gpus: Vec<GpuInfo>,
    pub hardware: HardwareInfo,
    pub battery: Option<Battery>,
    pub activity: ActivityInfo,
    pub process_count: usize,
    pub top_cpu: Vec<ProcessInfo>,
    pub top_memory: Vec<ProcessInfo>,
    pub capabilities: Capabilities,
}

#[derive(Debug, Clone, Serialize)]
pub struct Capabilities {
    pub power: Vec<&'static str>,
    pub kill: bool,
    pub notify: bool,
    pub update: bool,
    pub os_updates: bool,
    pub commands: Vec<String>,
}

pub struct Collector {
    sys: System,
    disks: Disks,
    networks: Networks,
    components: Components,
    gpus: Gpus,
    hardware: HardwareInfo,
    battery: Option<Battery>,
    /// Cumulative (read, written) bytes per mount point at the previous sample.
    disk_totals: HashMap<String, (u64, u64)>,
    last_refresh: Instant,
    /// Last time the user was seen active, kept when idle time can't be determined.
    last_active: Option<DateTime<Utc>>,
    tick: u64,
}

pub type Shared = Arc<RwLock<Option<Snapshot>>>;

fn percent(part: u64, whole: u64) -> f32 {
    if whole == 0 {
        0.0
    } else {
        (part as f64 / whole as f64 * 100.0) as f32
    }
}

fn round1(v: f32) -> f32 {
    (v * 10.0).round() / 10.0
}

impl Collector {
    pub fn new() -> Self {
        let sys = System::new_with_specifics(
            RefreshKind::nothing()
                .with_cpu(CpuRefreshKind::everything())
                .with_memory(MemoryRefreshKind::everything()),
        );
        Self {
            sys,
            disks: Disks::new_with_refreshed_list(),
            networks: Networks::new_with_refreshed_list(),
            components: Components::new_with_refreshed_list(),
            gpus: Gpus::new(),
            hardware: hardware::collect(),
            battery: platform::battery(),
            disk_totals: HashMap::new(),
            last_refresh: Instant::now(),
            last_active: None,
            tick: 0,
        }
    }

    pub fn process_kind() -> ProcessRefreshKind {
        ProcessRefreshKind::nothing()
            .with_cpu()
            .with_memory()
            .with_exe(UpdateKind::OnlyIfNotSet)
    }

    pub fn refresh_processes(&mut self, which: ProcessesToUpdate) {
        self.sys
            .refresh_processes_specifics(which, true, Self::process_kind());
    }

    pub fn system(&self) -> &System {
        &self.sys
    }

    pub fn collect(&mut self, cfg: &Config) -> Snapshot {
        self.tick += 1;
        self.sys.refresh_cpu_all();
        self.sys.refresh_memory();
        self.sys
            .refresh_processes_specifics(ProcessesToUpdate::All, true, Self::process_kind());

        // Disk and component lists change rarely; rescan them occasionally.
        let rescan = self.tick.is_multiple_of(30);
        self.disks.refresh(rescan);
        if rescan {
            self.disks = Disks::new_with_refreshed_list();
            self.components = Components::new_with_refreshed_list();
        } else {
            self.components.refresh(false);
        }

        let elapsed = self.last_refresh.elapsed().as_secs_f64().max(0.001);
        self.networks.refresh(true);
        self.last_refresh = Instant::now();
        if self.tick.is_multiple_of(15) {
            self.battery = platform::battery();
        }

        let cpus = self.sys.cpus();
        let cpu = CpuInfo {
            brand: cpus
                .first()
                .map(|c| c.brand().trim().to_owned())
                .unwrap_or_default(),
            logical_cores: cpus.len(),
            physical_cores: System::physical_core_count(),
            usage_percent: round1(self.sys.global_cpu_usage()),
            frequency_mhz: cpus.first().map(|c| c.frequency()).unwrap_or(0),
            per_core_percent: cpus.iter().map(|c| round1(c.cpu_usage())).collect(),
            load_average: {
                let l = System::load_average();
                [l.one, l.five, l.fifteen]
            },
        };

        let memory = MemoryInfo {
            total_bytes: self.sys.total_memory(),
            used_bytes: self.sys.used_memory(),
            available_bytes: self.sys.available_memory(),
            usage_percent: round1(percent(self.sys.used_memory(), self.sys.total_memory())),
            swap_total_bytes: self.sys.total_swap(),
            swap_used_bytes: self.sys.used_swap(),
            swap_usage_percent: round1(percent(self.sys.used_swap(), self.sys.total_swap())),
        };

        let mut disks: Vec<DiskInfo> = self
            .disks
            .iter()
            .filter(|d| d.total_space() > 0)
            .map(|d| {
                let used = d.total_space().saturating_sub(d.available_space());
                let mount = d.mount_point().to_string_lossy().into_owned();
                let io = d.usage();
                let (read_rate, write_rate) = match self
                    .disk_totals
                    .insert(mount.clone(), (io.total_read_bytes, io.total_written_bytes))
                {
                    Some((r, w)) => (
                        (io.total_read_bytes.saturating_sub(r) as f64 / elapsed).round(),
                        (io.total_written_bytes.saturating_sub(w) as f64 / elapsed).round(),
                    ),
                    None => (0.0, 0.0),
                };
                DiskInfo {
                    name: d.name().to_string_lossy().into_owned(),
                    mount_point: mount,
                    file_system: d.file_system().to_string_lossy().into_owned(),
                    kind: format!("{:?}", d.kind()),
                    removable: d.is_removable(),
                    total_bytes: d.total_space(),
                    available_bytes: d.available_space(),
                    used_bytes: used,
                    usage_percent: round1(percent(used, d.total_space())),
                    read_bytes_per_sec: read_rate,
                    write_bytes_per_sec: write_rate,
                }
            })
            .collect();
        disks.sort_by(|a, b| a.mount_point.cmp(&b.mount_point));
        disks.dedup_by(|a, b| a.mount_point == b.mount_point);
        let disk_io = DiskIo {
            read_bytes_per_sec: disks.iter().map(|d| d.read_bytes_per_sec).sum(),
            write_bytes_per_sec: disks.iter().map(|d| d.write_bytes_per_sec).sum(),
        };

        let mut interfaces: Vec<NetworkInfo> = self
            .networks
            .iter()
            .map(|(name, data)| NetworkInfo {
                name: name.clone(),
                mac_address: data.mac_address().to_string(),
                ip_addresses: data
                    .ip_networks()
                    .iter()
                    .map(|n| n.addr.to_string())
                    .collect(),
                rx_bytes_total: data.total_received(),
                tx_bytes_total: data.total_transmitted(),
                rx_bytes_per_sec: (data.received() as f64 / elapsed).round(),
                tx_bytes_per_sec: (data.transmitted() as f64 / elapsed).round(),
            })
            .collect();
        interfaces.sort_by(|a, b| a.name.cmp(&b.name));

        let physical = |i: &&NetworkInfo| {
            i.mac_address != "00:00:00:00:00:00" && !i.name.to_lowercase().contains("loopback")
        };
        let network = NetworkTotals {
            rx_bytes_per_sec: interfaces
                .iter()
                .filter(physical)
                .map(|i| i.rx_bytes_per_sec)
                .sum(),
            tx_bytes_per_sec: interfaces
                .iter()
                .filter(physical)
                .map(|i| i.tx_bytes_per_sec)
                .sum(),
            rx_bytes_total: interfaces
                .iter()
                .filter(physical)
                .map(|i| i.rx_bytes_total)
                .sum(),
            tx_bytes_total: interfaces
                .iter()
                .filter(physical)
                .map(|i| i.tx_bytes_total)
                .sum(),
        };

        let primary_ip = local_ip_address::local_ip().ok().map(|ip| ip.to_string());
        let primary_mac = primary_ip.as_ref().and_then(|ip| {
            interfaces
                .iter()
                .find(|i| i.ip_addresses.contains(ip))
                .map(|i| i.mac_address.clone())
        });

        let temperatures = self
            .components
            .iter()
            .filter_map(|c| {
                let t = c.temperature()?;
                (t.is_finite() && t > 0.0).then(|| TemperatureInfo {
                    label: c.label().to_owned(),
                    celsius: round1(t),
                    critical_celsius: c.critical(),
                })
            })
            .collect();

        let processes = self.process_list();
        let process_count = processes.len();
        let top_n = cfg.top_processes;
        let mut top_cpu = processes.clone();
        top_cpu.sort_by(|a, b| b.cpu_percent.total_cmp(&a.cpu_percent));
        top_cpu.truncate(top_n);
        let mut top_memory = processes;
        top_memory.sort_by_key(|p| std::cmp::Reverse(p.memory_bytes));
        top_memory.truncate(top_n);

        let now = Utc::now();
        let act = platform::activity();
        let active = act
            .idle_seconds
            .map(|idle| idle < cfg.active_threshold_secs);
        if let Some(idle) = act.idle_seconds {
            self.last_active = Some(now - chrono::Duration::seconds(idle as i64));
        }
        let activity = ActivityInfo {
            idle_seconds: act.idle_seconds,
            last_active: self.last_active,
            active,
            locked: act.locked,
            users: platform::logged_in_users(),
        };

        let uptime = System::uptime();
        Snapshot {
            id: cfg.id.clone(),
            name: cfg.name.clone(),
            hostname: System::host_name().unwrap_or_default(),
            agent_version: env!("CARGO_PKG_VERSION"),
            timestamp: now,
            os: os_info(),
            uptime_secs: uptime,
            boot_time: DateTime::from_timestamp(System::boot_time() as i64, 0).unwrap_or(now),
            cpu,
            memory,
            disks,
            disk_io,
            network,
            interfaces,
            primary_ip,
            primary_mac,
            temperatures,
            gpus: self.gpus.collect(),
            hardware: self.hardware.clone(),
            battery: self.battery.clone(),
            activity,
            process_count,
            top_cpu,
            top_memory,
            capabilities: capabilities(cfg),
        }
    }

    pub fn process_list(&self) -> Vec<ProcessInfo> {
        let cores = self.sys.cpus().len().max(1) as f32;
        self.sys
            .processes()
            .values()
            .filter(|p| p.thread_kind().is_none())
            .map(|p| ProcessInfo {
                pid: p.pid().as_u32(),
                name: p.name().to_string_lossy().into_owned(),
                exe: p.exe().map(|e| e.to_string_lossy().into_owned()),
                cpu_percent: round1(p.cpu_usage() / cores),
                memory_bytes: p.memory(),
                status: p.status().to_string(),
                run_time_secs: p.run_time(),
            })
            .collect()
    }
}

pub fn capabilities(cfg: &Config) -> Capabilities {
    Capabilities {
        power: if cfg.allow_power {
            platform::supported_actions()
                .into_iter()
                .map(|a| a.as_str())
                .collect()
        } else {
            Vec::new()
        },
        kill: cfg.allow_kill,
        notify: cfg.allow_notify,
        update: !cfg.update_repo.is_empty(),
        os_updates: crate::osupdates::enabled(cfg),
        commands: cfg.commands.keys().cloned().collect(),
    }
}

fn os_info() -> OsInfo {
    OsInfo {
        name: System::name(),
        version: System::os_version(),
        long_version: System::long_os_version(),
        kernel: System::kernel_version(),
        arch: System::cpu_arch(),
        family: std::env::consts::OS,
    }
}

/// Samples the system periodically and publishes snapshots to `shared`.
pub fn spawn(
    cfg: Arc<Config>,
    collector: Arc<std::sync::Mutex<Collector>>,
    shared: Shared,
) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        let interval = Duration::from_secs(cfg.refresh_interval_secs.max(1));
        // CPU usage needs two samples; prime the collector first.
        {
            let c = collector.clone();
            let _ = tokio::task::spawn_blocking(move || {
                let mut c = c.lock().unwrap();
                c.sys.refresh_cpu_all();
                c.sys.refresh_processes_specifics(
                    ProcessesToUpdate::All,
                    true,
                    Collector::process_kind(),
                );
            })
            .await;
        }
        tokio::time::sleep(sysinfo::MINIMUM_CPU_UPDATE_INTERVAL.max(Duration::from_millis(500)))
            .await;
        let mut ticker = tokio::time::interval(interval);
        ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
        loop {
            ticker.tick().await;
            let c = collector.clone();
            let cfg = cfg.clone();
            match tokio::task::spawn_blocking(move || c.lock().unwrap().collect(&cfg)).await {
                Ok(snapshot) => *shared.write().await = Some(snapshot),
                Err(e) => tracing::error!("collector task failed: {e}"),
            }
        }
    })
}
