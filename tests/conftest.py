"""Fixtures for the PC Pulse integration tests."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_socket

if sys.platform == "win32":
    # Home Assistant only supports Linux/macOS. When running the tests on Windows
    # the proactor event loop needs a TCP socketpair, which the socket guard blocks.
    pytest_socket.disable_socket = lambda *args, **kwargs: None

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def allow_sockets() -> None:
    """Let a test talk to the live agent over TCP on localhost."""
    pytest_socket.enable_socket()


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components in every test."""


def make_status(**overrides: Any) -> dict[str, Any]:
    """A status payload shaped like the agent's."""
    status: dict[str, Any] = {
        "id": "abc123",
        "name": "Test PC",
        "hostname": "testpc",
        "agent_version": "0.1.0",
        "timestamp": "2026-10-17T12:00:00Z",
        "os": {
            "name": "Windows",
            "version": "11 (26200)",
            "long_version": "Windows 11 Pro",
            "kernel": "26200",
            "arch": "x86_64",
            "family": "windows",
        },
        "uptime_secs": 100,
        "boot_time": "2026-10-17T11:58:20Z",
        "cpu": {
            "brand": "Test CPU",
            "logical_cores": 4,
            "physical_cores": 2,
            "usage_percent": 12.5,
            "frequency_mhz": 3000,
            "per_core_percent": [10.0, 15.0, 12.0, 13.0],
            "load_average": [0.0, 0.0, 0.0],
        },
        "memory": {
            "total_bytes": 17179869184,
            "used_bytes": 8589934592,
            "available_bytes": 8589934592,
            "usage_percent": 50.0,
            "swap_total_bytes": 0,
            "swap_used_bytes": 0,
            "swap_usage_percent": 0.0,
        },
        "disks": [
            {
                "name": "",
                "mount_point": "C:\\",
                "file_system": "NTFS",
                "kind": "SSD",
                "removable": False,
                "total_bytes": 1000,
                "available_bytes": 250,
                "used_bytes": 750,
                "usage_percent": 75.0,
            }
        ],
        "network": {
            "rx_bytes_per_sec": 1000.0,
            "tx_bytes_per_sec": 500.0,
            "rx_bytes_total": 10,
            "tx_bytes_total": 5,
        },
        "interfaces": [],
        "primary_ip": "192.168.1.20",
        "primary_mac": "aa:bb:cc:dd:ee:ff",
        "temperatures": [{"label": "CPU Package", "celsius": 55.0, "critical_celsius": None}],
        "activity": {
            "idle_seconds": 30,
            "last_active": "2026-10-17T11:59:30Z",
            "active": True,
            "locked": False,
            "users": ["alice"],
        },
        "process_count": 2,
        "top_cpu": [
            {"pid": 1, "name": "game.exe", "exe": None, "cpu_percent": 40.0,
             "memory_bytes": 1048576, "status": "Run", "run_time_secs": 5}
        ],
        "top_memory": [
            {"pid": 2, "name": "browser.exe", "exe": None, "cpu_percent": 1.0,
             "memory_bytes": 2097152, "status": "Run", "run_time_secs": 5}
        ],
        "disk_io": {"read_bytes_per_sec": 1048576.0, "write_bytes_per_sec": 0.0},
        "gpus": [
            {
                "index": 0, "name": "Test GPU", "vendor": "nvidia", "usage_percent": 40.0,
                "memory_used_bytes": 2147483648, "memory_total_bytes": 8589934592,
                "memory_usage_percent": 25.0, "temperature_celsius": 60.0, "power_watts": 120.5,
                "power_limit_watts": 250.0, "core_clock_mhz": 1800, "memory_clock_mhz": 7000,
                "fan_percent": 35, "encoder_percent": None, "decoder_percent": None,
            }
        ],
        "hardware": {
            "memory_modules": [
                {"locator": "DIMM 0", "size_bytes": 17179869184, "memory_type": "DDR5",
                 "speed_mts": 4800, "configured_speed_mts": 6000, "manufacturer": "Acme",
                 "part_number": "X1"},
            ],
            "memory_type": "DDR5", "memory_speed_mts": 6000,
            "system_manufacturer": None, "system_product": None,
            "board_manufacturer": "Acme", "board_product": "B650",
            "bios_vendor": "AMI", "bios_version": "1.0", "bios_date": "01/01/2026",
        },
        "battery": {"percent": 80.0, "charging": True, "plugged_in": True,
                    "time_remaining_secs": None},
        "update": {
            "current": "0.1.0", "latest": "0.2.0", "release_url": "https://example.com/r",
            "release_notes": "Notes", "available": True, "auto": False, "installing": False,
            "checked_at": "2026-10-17T12:00:00Z", "error": None,
        },
        "capabilities": {
            "power": ["shutdown", "restart", "sleep", "hibernate", "lock", "logoff",
                      "monitor_off", "monitor_on"],
            "kill": True,
            "notify": True,
            "update": True,
            "commands": ["open_steam"],
        },
    }
    status.update(overrides)
    return status


@pytest.fixture
def mock_client() -> Generator[AsyncMock]:
    """Patch the agent client everywhere it is constructed."""
    client = AsyncMock()
    client.status.return_value = make_status()
    client.power.return_value = {"scheduled": True}
    client.kill.return_value = {"killed": [1], "failed": []}
    client.processes.return_value = make_status()["top_cpu"]
    client.run_command.return_value = {"started": True}
    with (
        patch("custom_components.pc_pulse.AgentClient", return_value=client),
        patch("custom_components.pc_pulse.config_flow.AgentClient", return_value=client),
    ):
        yield client


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def live_agent() -> Generator[dict[str, Any]]:
    """Build and run the real Rust agent on localhost with power actions disabled."""
    # Session fixtures run after Home Assistant's plugin disabled sockets.
    pytest_socket.enable_socket()
    exe_name = "pc-pulse.exe" if os.name == "nt" else "pc-pulse"
    exe = REPO / "target" / "debug" / exe_name
    if not exe.exists():
        if shutil.which("cargo") is None:
            pytest.skip("agent binary not built and cargo unavailable")
        subprocess.run(["cargo", "build"], cwd=REPO, check=True)

    tmp = Path(tempfile.mkdtemp())
    port = _free_port()
    cfg = tmp / "config.toml"
    cfg.write_text(
        "\n".join(
            [
                'id = "liveagent1"',
                'name = "Live Agent"',
                'bind = "127.0.0.1"',
                f"port = {port}",
                'token = "livetoken"',
                "refresh_interval_secs = 1",
                "mdns = false",
                # Never let tests shut the machine down.
                "allow_power = false",
                "allow_kill = true",
                "allow_notify = false",
                'update_repo = ""',
                "[commands]",
                'noop = "echo hi"',
            ]
        )
    )
    proc = subprocess.Popen(
        [str(exe), "--config", str(cfg), "run"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.2)
    time.sleep(2)  # first sample
    yield {"host": "127.0.0.1", "port": port, "token": "livetoken"}
    proc.terminate()
    proc.wait(10)
    shutil.rmtree(tmp, ignore_errors=True)
