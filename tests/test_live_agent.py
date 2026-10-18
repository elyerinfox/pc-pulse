"""End-to-end tests against the real Rust agent running on localhost."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from typing import Any

import pytest
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr

from custom_components.pc_pulse.const import DOMAIN

pytestmark = pytest.mark.usefixtures("allow_sockets")


def _device(hass: HomeAssistant, entry) -> dr.DeviceEntry:
    (device,) = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    return device


async def _setup(hass: HomeAssistant, agent: dict[str, Any]):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: agent["host"], CONF_PORT: agent["port"], CONF_TOKEN: agent["token"]},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    await hass.async_block_till_done()
    return result["result"]


async def test_live_setup(hass: HomeAssistant, live_agent: dict[str, Any]) -> None:
    entry = await _setup(hass, live_agent)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.unique_id == "liveagent1"

    cpu = hass.states.get("sensor.live_agent_cpu_usage")
    assert 0.0 <= float(cpu.state) <= 100.0
    assert len(cpu.attributes["per_core_percent"]) >= 1
    assert 0.0 < float(hass.states.get("sensor.live_agent_memory_usage").state) < 100.0
    assert int(hass.states.get("sensor.live_agent_processes").state) > 10
    assert hass.states.get("binary_sensor.live_agent_online").state == "on"
    assert hass.states.get("sensor.live_agent_top_process_cpu").attributes["processes"]
    assert hass.states.get("sensor.live_agent_last_boot").state not in ("unknown", "unavailable")
    assert hass.states.get("button.live_agent_run_noop") is not None
    # Power is disabled in the test agent, so no power buttons or switch.
    assert hass.states.get("button.live_agent_shut_down") is None
    assert hass.states.get("switch.live_agent_power") is None
    # Notifications are disabled in the test agent.
    assert hass.states.get("notify.live_agent_desktop_notification").state == "unavailable"


async def test_live_services(hass: HomeAssistant, live_agent: dict[str, Any]) -> None:
    entry = await _setup(hass, live_agent)
    device = _device(hass, entry)

    result = await hass.services.async_call(
        DOMAIN, "get_processes", {"device_id": device.id, "sort": "memory", "limit": 3},
        blocking=True, return_response=True,
    )
    procs = result["computers"]["Live Agent"]
    assert len(procs) == 3
    assert procs[0]["memory_bytes"] >= procs[1]["memory_bytes"]

    # Spawn a throwaway process and kill it through Home Assistant.
    victim = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        await asyncio.sleep(0.5)
        result = await hass.services.async_call(
            DOMAIN, "kill_process", {"device_id": device.id, "pid": victim.pid},
            blocking=True, return_response=True,
        )
        assert result["Live Agent"]["killed"] == [victim.pid]
        assert victim.wait(10) is not None
    finally:
        if victim.poll() is None:
            victim.kill()

    await hass.services.async_call(
        DOMAIN, "run_command", {"device_id": device.id, "command": "noop"}, blocking=True
    )
