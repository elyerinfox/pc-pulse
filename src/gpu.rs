//! GPU statistics: NVIDIA through NVML (Windows and Linux), AMD through sysfs (Linux).

use serde::Serialize;

#[derive(Debug, Clone, Default, Serialize)]
pub struct GpuInfo {
    pub index: usize,
    pub name: String,
    pub vendor: &'static str,
    pub usage_percent: Option<f32>,
    pub memory_used_bytes: Option<u64>,
    pub memory_total_bytes: Option<u64>,
    pub memory_usage_percent: Option<f32>,
    pub temperature_celsius: Option<f32>,
    pub power_watts: Option<f32>,
    pub power_limit_watts: Option<f32>,
    pub core_clock_mhz: Option<u32>,
    pub memory_clock_mhz: Option<u32>,
    pub fan_percent: Option<u32>,
    pub encoder_percent: Option<u32>,
    pub decoder_percent: Option<u32>,
}

impl GpuInfo {
    fn finish(mut self) -> Self {
        if let (Some(used), Some(total)) = (self.memory_used_bytes, self.memory_total_bytes)
            && total > 0
        {
            self.memory_usage_percent = Some((used as f64 / total as f64 * 1000.0).round() as f32 / 10.0);
        }
        self
    }
}

pub struct Gpus {
    #[cfg(any(windows, target_os = "linux"))]
    nvml: Option<nvml_wrapper::Nvml>,
}

impl Gpus {
    pub fn new() -> Self {
        Self {
            #[cfg(any(windows, target_os = "linux"))]
            nvml: match nvml_wrapper::Nvml::init() {
                Ok(n) => Some(n),
                Err(e) => {
                    tracing::debug!("NVML unavailable: {e}");
                    None
                }
            },
        }
    }

    pub fn collect(&self) -> Vec<GpuInfo> {
        #[allow(unused_mut)]
        let mut gpus = Vec::new();
        #[cfg(any(windows, target_os = "linux"))]
        if let Some(nvml) = &self.nvml {
            gpus.extend(nvidia(nvml));
        }
        #[cfg(target_os = "linux")]
        gpus.extend(amd_sysfs());
        for (i, g) in gpus.iter_mut().enumerate() {
            g.index = i;
        }
        gpus
    }
}

#[cfg(any(windows, target_os = "linux"))]
fn nvidia(nvml: &nvml_wrapper::Nvml) -> Vec<GpuInfo> {
    use nvml_wrapper::enum_wrappers::device::{Clock, TemperatureSensor};

    let count = nvml.device_count().unwrap_or(0);
    (0..count)
        .filter_map(|i| nvml.device_by_index(i).ok())
        .map(|d| {
            let mem = d.memory_info().ok();
            let util = d.utilization_rates().ok();
            GpuInfo {
                name: d.name().unwrap_or_else(|_| "NVIDIA GPU".into()),
                vendor: "nvidia",
                usage_percent: util.as_ref().map(|u| u.gpu as f32),
                memory_used_bytes: mem.as_ref().map(|m| m.used),
                memory_total_bytes: mem.as_ref().map(|m| m.total),
                temperature_celsius: d.temperature(TemperatureSensor::Gpu).ok().map(|t| t as f32),
                power_watts: d.power_usage().ok().map(|mw| (mw as f32 / 100.0).round() / 10.0),
                power_limit_watts: d.enforced_power_limit().ok().map(|mw| (mw / 1000) as f32),
                core_clock_mhz: d.clock_info(Clock::Graphics).ok(),
                memory_clock_mhz: d.clock_info(Clock::Memory).ok(),
                fan_percent: d.fan_speed(0).ok(),
                encoder_percent: d.encoder_utilization().ok().map(|u| u.utilization),
                decoder_percent: d.decoder_utilization().ok().map(|u| u.utilization),
                ..Default::default()
            }
            .finish()
        })
        .collect()
}

#[cfg(target_os = "linux")]
fn amd_sysfs() -> Vec<GpuInfo> {
    use std::fs;
    use std::path::Path;

    fn read<T: std::str::FromStr>(p: &Path) -> Option<T> {
        fs::read_to_string(p).ok()?.trim().parse().ok()
    }

    let Ok(entries) = fs::read_dir("/sys/class/drm") else {
        return Vec::new();
    };
    let mut cards: Vec<_> = entries
        .flatten()
        .map(|e| e.path())
        .filter(|p| {
            p.file_name()
                .and_then(|n| n.to_str())
                .is_some_and(|n| n.starts_with("card") && n[4..].chars().all(|c| c.is_ascii_digit()))
        })
        .collect();
    cards.sort();
    cards
        .into_iter()
        .filter_map(|card| {
            let dev = card.join("device");
            let vendor = fs::read_to_string(dev.join("vendor")).ok()?;
            if vendor.trim() != "0x1002" {
                return None;
            }
            let hwmon = fs::read_dir(dev.join("hwmon"))
                .ok()
                .and_then(|mut d| d.next())
                .and_then(|e| e.ok())
                .map(|e| e.path());
            let hw = |f: &str| hwmon.as_ref().and_then(|h| read::<u64>(&h.join(f)));
            Some(
                GpuInfo {
                    name: fs::read_to_string(dev.join("product_name"))
                        .map(|s| s.trim().to_owned())
                        .ok()
                        .filter(|s| !s.is_empty())
                        .unwrap_or_else(|| "AMD GPU".into()),
                    vendor: "amd",
                    usage_percent: read::<u32>(&dev.join("gpu_busy_percent")).map(|v| v as f32),
                    memory_used_bytes: read(&dev.join("mem_info_vram_used")),
                    memory_total_bytes: read(&dev.join("mem_info_vram_total")),
                    temperature_celsius: hw("temp1_input").map(|m| m as f32 / 1000.0),
                    power_watts: hw("power1_average")
                        .or_else(|| hw("power1_input"))
                        .map(|uw| (uw as f32 / 100_000.0).round() / 10.0),
                    power_limit_watts: hw("power1_cap").map(|uw| (uw / 1_000_000) as f32),
                    core_clock_mhz: hw("freq1_input").map(|hz| (hz / 1_000_000) as u32),
                    memory_clock_mhz: hw("freq2_input").map(|hz| (hz / 1_000_000) as u32),
                    fan_percent: hw("pwm1").map(|pwm| (pwm * 100 / 255) as u32),
                    ..Default::default()
                }
                .finish(),
            )
        })
        .collect()
}
