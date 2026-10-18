# PC Pulse for Home Assistant

Monitor and control your PCs from Home Assistant. This repository has two parts:

- **The agent** (`src/`, Rust): a small service that runs on each computer (Windows, Linux or macOS) and serves a token-protected HTTP API.
- **The integration** (`custom_components/pc_pulse`): a Home Assistant integration, installable with HACS, that discovers agents and turns them into devices.

## What you get

| Type | Entities |
| --- | --- |
| Sensors | CPU usage (per-core attributes), CPU frequency, CPU temperature\*, load average (Linux/macOS), memory usage/used/available, swap, disk usage per drive, download/upload speed, total traffic, process count, top process by CPU and by memory (top N list in attributes), idle time, last active, logged-in users, last boot, OS, IP address, agent version |
| Binary sensors | Online, user active (keyboard/mouse within the threshold), screen lock |
| Buttons | Shut down, restart, sleep, hibernate, lock, log off, turn displays off/on, Wake-on-LAN, one button per custom command |
| Switch | Power: on sends Wake-on-LAN, off shuts down |
| Notify | Desktop notification: shows a native notification on the computer (Windows toast, Linux notification daemon, macOS Notification Center) |
| Services | `pc_pulse.power`, `.wake`, `.kill_process`, `.run_command`, `.get_processes` (returns data) |

\* Temperatures depend on the platform. On Windows they usually need the agent to run elevated.

When a computer is off, its sensors go unavailable. *Online*, *Power* and *Wake-on-LAN* stay usable, so you can turn it back on.

## 1. Install the agent

Download the archive for your platform from [Releases](https://github.com/elyerinfox/pc-pulse/releases), or build it yourself with `cargo build --release`.

```sh
pc-pulse info               # creates the config and prints host, port and token
pc-pulse install-autostart  # start at login (Windows: run from an elevated terminal)
```

- **Windows:** creates a logon scheduled task (`pc-pulse-agent`) that runs in the background and adds a firewall rule for private networks. The agent must run in the user's session, not as a session-0 service, to read idle time and lock the screen.
- **Linux:** creates a systemd user service. Power actions go through `systemctl` and `loginctl`. Idle time uses `xprintidle` when available, otherwise logind's idle hint.
- **macOS:** creates a LaunchAgent. Shutdown, restart and log off use AppleScript (approve the automation prompt).

Other commands: `pc-pulse run` (foreground), `sample` (print one JSON sample), `regenerate-token`, `uninstall-autostart`.

The config file lives at `%APPDATA%\pc-pulse\config.toml` (Windows), `~/.config/pc-pulse/config.toml` (Linux) or `~/Library/Application Support/pc-pulse/config.toml` (macOS). See [`agent.example.toml`](agent.example.toml). Restart the agent after editing it.

## 2. Install the integration

**HACS:** HACS → ⋮ → *Custom repositories* → add `https://github.com/elyerinfox/pc-pulse` as an *Integration*, install **PC Pulse**, and restart Home Assistant.

**Manual:** copy `custom_components/pc_pulse` into your `config/custom_components/` folder and restart.

Agents on your network are discovered automatically through zeroconf. Open the discovered device and paste the token from `pc-pulse info`. Or go to *Settings → Devices & services → Add integration → PC Pulse* and enter the host, port (default `8752`) and token.

Options (per computer): polling interval, a Wake-on-LAN MAC override, and the broadcast address. The MAC is learned from the agent automatically.

## Examples

```yaml
# Put the PC to sleep when nobody has used it for 30 minutes after midnight
automation:
  - alias: Sleep idle PC
    triggers:
      - trigger: numeric_state
        entity_id: sensor.gaming_pc_idle_time
        above: 1800
    conditions:
      - condition: time
        after: "00:00:00"
        before: "06:00:00"
    actions:
      - action: button.press
        target:
          entity_id: button.gaming_pc_sleep

# Pop up a notification on the PC
      - action: notify.send_message
        target:
          entity_id: notify.gaming_pc_desktop_notification
        data:
          title: Laundry
          message: The washer is done.

# Kill a game remotely
script:
  stop_game:
    sequence:
      - action: pc_pulse.kill_process
        data:
          device_id: <your device id>
          name: game.exe

# List the top 10 memory users
      - action: pc_pulse.get_processes
        data:
          device_id: <your device id>
          sort: memory
          limit: 10
        response_variable: procs
```

## Agent API

All endpoints except `/api/v1/health` need `Authorization: Bearer <token>`.

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/v1/health` | Liveness, id, name and version |
| GET | `/api/v1/status` | Full snapshot: OS, CPU, memory, disks, network, temperatures, activity, top processes, capabilities |
| GET | `/api/v1/processes?sort=cpu\|memory\|name\|pid&limit=N` | All processes |
| POST | `/api/v1/processes/kill` | `{"pid": 123}` or `{"name": "notepad.exe"}` |
| POST | `/api/v1/power/{action}` | `shutdown`, `restart`, `sleep`, `hibernate`, `lock`, `logoff`, `monitor_off`, `monitor_on`; optional `{"delay_secs": N}` |
| POST | `/api/v1/commands/{name}` | Run a command from the `[commands]` table |
| POST | `/api/v1/notify` | `{"message": "...", "title": "..."}` shows a desktop notification |

## Security

The API is plain HTTP on your LAN, protected by a random 48-character token. Anyone with the token can shut the computer down or kill processes. Keep the agent off untrusted networks, or turn off `allow_power`, `allow_kill` and `allow_notify` in the config. Arbitrary commands can't be run, only the ones you list in `[commands]`.

## Development

```sh
cargo clippy && cargo build
pip install -r requirements_test.txt
pytest   # unit tests plus end-to-end tests against the real agent (power actions disabled)
```
