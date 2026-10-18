//! Static hardware details (memory modules, motherboard, BIOS), read once at startup.
//!
//! Windows and Linux (when the raw table is readable) parse the SMBIOS tables;
//! Linux falls back to /sys/class/dmi/id and macOS uses system_profiler.

use serde::Serialize;

#[derive(Debug, Clone, Default, Serialize)]
pub struct MemoryModule {
    pub locator: Option<String>,
    pub size_bytes: u64,
    pub memory_type: Option<String>,
    /// Rated speed in MT/s.
    pub speed_mts: Option<u32>,
    /// Speed the module actually runs at, in MT/s.
    pub configured_speed_mts: Option<u32>,
    pub manufacturer: Option<String>,
    pub part_number: Option<String>,
}

#[derive(Debug, Clone, Default, Serialize)]
pub struct HardwareInfo {
    pub memory_modules: Vec<MemoryModule>,
    pub memory_type: Option<String>,
    /// Configured memory speed in MT/s (slowest module).
    pub memory_speed_mts: Option<u32>,
    pub system_manufacturer: Option<String>,
    pub system_product: Option<String>,
    pub board_manufacturer: Option<String>,
    pub board_product: Option<String>,
    pub bios_vendor: Option<String>,
    pub bios_version: Option<String>,
    pub bios_date: Option<String>,
}

impl HardwareInfo {
    fn finish(mut self) -> Self {
        self.memory_type = self.memory_modules.iter().find_map(|m| m.memory_type.clone());
        self.memory_speed_mts = self
            .memory_modules
            .iter()
            .filter_map(|m| m.configured_speed_mts.or(m.speed_mts))
            .min();
        self
    }
}

pub fn collect() -> HardwareInfo {
    imp().finish()
}

#[cfg(windows)]
fn imp() -> HardwareInfo {
    use windows_sys::Win32::System::SystemInformation::GetSystemFirmwareTable;
    const RSMB: u32 = u32::from_be_bytes(*b"RSMB");
    unsafe {
        let size = GetSystemFirmwareTable(RSMB, 0, std::ptr::null_mut(), 0);
        if size == 0 {
            return HardwareInfo::default();
        }
        let mut buf = vec![0u8; size as usize];
        if GetSystemFirmwareTable(RSMB, 0, buf.as_mut_ptr().cast(), size) == 0 {
            return HardwareInfo::default();
        }
        // RawSMBIOSData: 4 version bytes, u32 length, then the table.
        let len = u32::from_le_bytes(buf[4..8].try_into().unwrap()) as usize;
        parse_smbios(&buf[8..(8 + len).min(buf.len())])
    }
}

#[cfg(target_os = "linux")]
fn imp() -> HardwareInfo {
    if let Ok(table) = std::fs::read("/sys/firmware/dmi/tables/DMI") {
        return parse_smbios(&table);
    }
    // The raw table needs root; these are world readable.
    let read = |f: &str| {
        std::fs::read_to_string(format!("/sys/class/dmi/id/{f}"))
            .ok()
            .map(|s| s.trim().to_owned())
            .filter(|s| !s.is_empty())
    };
    HardwareInfo {
        system_manufacturer: read("sys_vendor"),
        system_product: read("product_name"),
        board_manufacturer: read("board_vendor"),
        board_product: read("board_name"),
        bios_vendor: read("bios_vendor"),
        bios_version: read("bios_version"),
        bios_date: read("bios_date"),
        ..Default::default()
    }
}

#[cfg(target_os = "macos")]
fn imp() -> HardwareInfo {
    let Some(out) = crate::platform::output_of(
        "system_profiler",
        &["-json", "SPHardwareDataType", "SPMemoryDataType"],
    ) else {
        return HardwareInfo::default();
    };
    let Ok(v) = serde_json::from_str::<serde_json::Value>(&out) else {
        return HardwareInfo::default();
    };
    let s = |v: &serde_json::Value, k: &str| v.get(k).and_then(|x| x.as_str()).map(str::to_owned);
    let hw = &v["SPHardwareDataType"][0];
    let mem = &v["SPMemoryDataType"][0];
    let parse_size = |s: &str| -> u64 {
        let mut it = s.split_whitespace();
        let n: u64 = it.next().and_then(|n| n.parse().ok()).unwrap_or(0);
        match it.next() {
            Some("GB") => n << 30,
            Some("MB") => n << 20,
            _ => 0,
        }
    };
    let speed = |s: Option<String>| s.and_then(|s| s.split_whitespace().next()?.parse().ok());
    let mut modules: Vec<MemoryModule> = mem
        .get("_items")
        .and_then(|i| i.as_array())
        .map(|items| {
            items
                .iter()
                .map(|m| MemoryModule {
                    locator: s(m, "_name"),
                    size_bytes: s(m, "dimm_size").map(|x| parse_size(&x)).unwrap_or(0),
                    memory_type: s(m, "dimm_type"),
                    speed_mts: speed(s(m, "dimm_speed")),
                    configured_speed_mts: None,
                    manufacturer: s(m, "dimm_manufacturer"),
                    part_number: s(m, "dimm_part_number"),
                })
                .collect()
        })
        .unwrap_or_default();
    if modules.is_empty() && mem.get("SPMemoryDataType").is_some() {
        // Apple Silicon reports unified memory as a single entry.
        modules.push(MemoryModule {
            locator: Some("Unified".into()),
            size_bytes: s(mem, "SPMemoryDataType").map(|x| parse_size(&x)).unwrap_or(0),
            memory_type: s(mem, "dimm_type"),
            manufacturer: s(mem, "dimm_manufacturer"),
            ..Default::default()
        });
    }
    HardwareInfo {
        memory_modules: modules,
        system_manufacturer: Some("Apple".into()),
        system_product: s(hw, "machine_model").or_else(|| s(hw, "machine_name")),
        bios_version: s(hw, "boot_rom_version"),
        ..Default::default()
    }
}

/// Parses an SMBIOS structure table.
#[cfg_attr(target_os = "macos", allow(dead_code))]
fn parse_smbios(table: &[u8]) -> HardwareInfo {
    let mut info = HardwareInfo::default();
    let mut pos = 0;
    while pos + 4 <= table.len() {
        let kind = table[pos];
        let len = table[pos + 1] as usize;
        if len < 4 || pos + len > table.len() {
            break;
        }
        let formatted = &table[pos..pos + len];
        // The string set follows the formatted area and ends with a double NUL.
        let mut end = pos + len;
        while end + 1 < table.len() && !(table[end] == 0 && table[end + 1] == 0) {
            end += 1;
        }
        let strings: Vec<String> = table[pos + len..end]
            .split(|b| *b == 0)
            .filter(|s| !s.is_empty())
            .map(|s| String::from_utf8_lossy(s).trim().to_owned())
            .collect();
        let byte = |o: usize| formatted.get(o).copied();
        let word = |o: usize| {
            formatted
                .get(o..o + 2)
                .map(|b| u16::from_le_bytes([b[0], b[1]]))
        };
        let dword = |o: usize| {
            formatted
                .get(o..o + 4)
                .map(|b| u32::from_le_bytes([b[0], b[1], b[2], b[3]]))
        };
        let string = |o: usize| {
            let idx = byte(o)? as usize;
            strings
                .get(idx.checked_sub(1)?)
                .filter(|s| !s.is_empty() && !is_placeholder(s))
                .cloned()
        };
        match kind {
            0 => {
                info.bios_vendor = string(0x04);
                info.bios_version = string(0x05);
                info.bios_date = string(0x08);
            }
            1 => {
                info.system_manufacturer = string(0x04);
                info.system_product = string(0x05);
            }
            2 => {
                info.board_manufacturer = string(0x04);
                info.board_product = string(0x05);
            }
            17 => {
                let size = match word(0x0C) {
                    None | Some(0) | Some(0xFFFF) => 0,
                    Some(0x7FFF) => (dword(0x1C).unwrap_or(0) as u64 & 0x7FFF_FFFF) << 20,
                    Some(s) if s & 0x8000 != 0 => ((s & 0x7FFF) as u64) << 10,
                    Some(s) => (s as u64) << 20,
                };
                if size > 0 {
                    let speed = |o: usize, ext: usize| match word(o) {
                        None | Some(0) => None,
                        Some(0xFFFF) => dword(ext).filter(|v| *v > 0),
                        Some(v) => Some(v as u32),
                    };
                    info.memory_modules.push(MemoryModule {
                        locator: string(0x10),
                        size_bytes: size,
                        memory_type: byte(0x12).and_then(memory_type_name).map(str::to_owned),
                        speed_mts: speed(0x15, 0x54),
                        configured_speed_mts: speed(0x20, 0x58),
                        manufacturer: string(0x17),
                        part_number: string(0x1A),
                    });
                }
            }
            127 => break,
            _ => {}
        }
        pos = end + 2;
    }
    info
}

#[cfg_attr(target_os = "macos", allow(dead_code))]
fn is_placeholder(s: &str) -> bool {
    let l = s.to_ascii_lowercase();
    matches!(
        l.as_str(),
        "to be filled by o.e.m."
            | "default string"
            | "system product name"
            | "system manufacturer" | "not specified" | "unknown" | "none" | "n/a"
    )
}

#[cfg_attr(target_os = "macos", allow(dead_code))]
fn memory_type_name(code: u8) -> Option<&'static str> {
    Some(match code {
        0x12 => "DDR",
        0x13 => "DDR2",
        0x18 => "DDR3",
        0x1A => "DDR4",
        0x1B => "LPDDR",
        0x1C => "LPDDR2",
        0x1D => "LPDDR3",
        0x1E => "LPDDR4",
        0x20 => "HBM",
        0x21 => "HBM2",
        0x22 => "DDR5",
        0x23 => "LPDDR5",
        0x24 => "HBM3",
        _ => return None,
    })
}
