# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.1] - 2026-10-18

### Fixed

- Decimal values such as GPU memory usage and power showing float noise
  (`6.30000019073486` instead of `6.3`) in the agent's status.

## [0.3.0] - 2026-10-18

### Added

- GPU sensors per GPU: usage, memory usage and used, temperature, power draw (with the
  power limit as an attribute), core and memory clock, fan speed, and encoder/decoder
  usage. NVIDIA GPUs are read through the driver's NVML library on Windows and Linux;
  AMD GPUs through sysfs on Linux.
- Memory speed sensor (configured MT/s) with memory type and per-module details,
  read from the SMBIOS tables.
- Motherboard sensor with BIOS vendor, version and date.
- Disk read and write speed sensors, plus per-disk rates in the agent's status.
- Battery level sensor and battery charging binary sensor on laptops.
- OS updates sensor with the pending update titles (Windows Update, apt, dnf, pacman
  or macOS softwareupdate), and a restart required binary sensor. Checked every
  6 hours (`os_update_check_hours`).
- Agent self-updates: the agent checks GitHub's releases API once a day, downloads the
  binary for its platform, verifies its SHA-256 checksum, replaces itself and restarts.
  Configured with `auto_update`, `update_repo` and `update_check_hours`.
- Agent update entity in Home Assistant showing the installed and latest version, with
  release notes and an install button.
- `pc-pulse update` command (`--check` only reports) and the `/api/v1/update` endpoint.
- Releases include a raw binary and a `.sha256` checksum per platform for the updater.
- This changelog.

### Changed

- After a self-update the agent waits up to 30 seconds for its port to be released
  instead of failing to start.

## [0.2.0] - 2026-10-18

### Added

- Desktop notifications: a notify entity per computer
  (`notify.<name>_desktop_notification`) that shows a native notification (Windows
  toast, freedesktop notification on Linux, Notification Center on macOS).
- `/api/v1/notify` endpoint and `allow_notify` option in the agent config.

### Fixed

- Flaky reauthentication test that left a pending reload at teardown.

## [0.1.1] - 2026-10-17

### Added

- Brand icon for Home Assistant and HACS.
- MIT license.

### Fixed

- End-to-end tests against the real agent failing on Linux CI because of the socket
  guard.

## [0.1.0] - 2026-10-17

### Added

- Cross-platform agent (Windows, Linux, macOS) with a token-protected HTTP API,
  zeroconf discovery and login autostart (`pc-pulse install-autostart`).
- System sensors: CPU usage, frequency and temperature, load average, memory, swap,
  disk usage per drive, network speed and totals, process count, top processes by CPU
  and memory, idle time, last active, logged-in users, last boot, OS and IP address.
- Binary sensors for online, user active and screen lock.
- Buttons for shut down, restart, sleep, hibernate, lock, log off, display off/on,
  Wake-on-LAN and custom commands from the agent config.
- Power switch (on sends Wake-on-LAN, off shuts down).
- Services `pc_pulse.power`, `wake`, `kill_process`, `run_command` and
  `get_processes`.
- Home Assistant integration installable through HACS, with zeroconf discovery,
  reauthentication, reconfiguration, options and diagnostics.

[Unreleased]: https://github.com/elyerinfox/pc-pulse/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/elyerinfox/pc-pulse/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/elyerinfox/pc-pulse/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/elyerinfox/pc-pulse/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/elyerinfox/pc-pulse/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/elyerinfox/pc-pulse/releases/tag/v0.1.0
