"""Tests for setup, entities and services using a mocked agent."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_TOKEN, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pc_pulse.api import (
    AgentAuthError,
    AgentConnectionError,
    build_magic_packet,
)
from custom_components.pc_pulse.const import DOMAIN

from .conftest import make_status


def _device(hass: HomeAssistant, entry: MockConfigEntry) -> dr.DeviceEntry:
    (device,) = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    return device


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test PC",
        unique_id="abc123",
        data={CONF_HOST: "192.168.1.20", CONF_PORT: 8752, CONF_TOKEN: "t"},
    )


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_entities(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.LOADED

    assert hass.states.get("sensor.test_pc_cpu_usage").state == "12.5"
    assert hass.states.get("sensor.test_pc_memory_usage").state == "50.0"
    assert hass.states.get("sensor.test_pc_cpu_temperature").state == "55.0"
    assert hass.states.get("sensor.test_pc_disk_usage_c").state == "75.0"
    assert hass.states.get("sensor.test_pc_idle_time").state == "30"
    assert hass.states.get("sensor.test_pc_top_process_cpu").state == "game.exe"
    assert hass.states.get("sensor.test_pc_logged_in_users").state == "1"
    assert hass.states.get("sensor.test_pc_last_active").state == "2026-10-17T11:59:30+00:00"
    # Load average is not meaningful on Windows.
    assert hass.states.get("sensor.test_pc_load_1m") is None

    assert hass.states.get("binary_sensor.test_pc_online").state == STATE_ON
    assert hass.states.get("binary_sensor.test_pc_user_active").state == STATE_ON
    # Lock device class: on = unlocked.
    assert hass.states.get("binary_sensor.test_pc_screen_lock").state == STATE_ON
    assert hass.states.get("switch.test_pc_power").state == STATE_ON

    for button in ("shut_down", "restart", "sleep", "hibernate", "lock",
                   "turn_off_displays", "wake_on_lan", "run_open_steam"):
        assert hass.states.get(f"button.test_pc_{button}") is not None, button

    device = _device(hass, entry)
    assert device.model == "Windows 11 Pro"
    assert (dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:ff") in device.connections


async def test_power_buttons_and_switch(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    await _setup(hass)

    await hass.services.async_call(
        "button", "press", {"entity_id": "button.test_pc_hibernate"}, blocking=True
    )
    mock_client.power.assert_awaited_with("hibernate", 0)

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.test_pc_power"}, blocking=True
    )
    mock_client.power.assert_awaited_with("shutdown", 0)
    # Optimistically off right after shutdown is requested.
    assert hass.states.get("switch.test_pc_power").state == STATE_OFF

    with patch(
        "custom_components.pc_pulse.coordinator.send_magic_packet"
    ) as wol:
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": "switch.test_pc_power"}, blocking=True
        )
    wol.assert_called_once_with("aa:bb:cc:dd:ee:ff", "255.255.255.255")
    assert hass.states.get("switch.test_pc_power").state == STATE_ON

    await hass.services.async_call(
        "button", "press", {"entity_id": "button.test_pc_run_open_steam"}, blocking=True
    )
    mock_client.run_command.assert_awaited_with("open_steam")


async def test_notify(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    await _setup(hass)
    await hass.services.async_call(
        "notify",
        "send_message",
        {"entity_id": "notify.test_pc_desktop_notification", "title": "Hi", "message": "Laundry done"},
        blocking=True,
    )
    mock_client.notify.assert_awaited_with("Laundry done", "Hi")


async def test_services(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    entry = await _setup(hass)
    device = _device(hass, entry)

    await hass.services.async_call(
        DOMAIN, "power", {"device_id": device.id, "action": "sleep", "delay": 30}, blocking=True
    )
    mock_client.power.assert_awaited_with("sleep", 30)

    result = await hass.services.async_call(
        DOMAIN, "kill_process", {"device_id": device.id, "name": "game.exe"},
        blocking=True, return_response=True,
    )
    mock_client.kill.assert_awaited_with(None, "game.exe")
    assert result == {"Test PC": {"killed": [1], "failed": []}}

    result = await hass.services.async_call(
        DOMAIN, "get_processes", {"device_id": device.id, "sort": "memory", "limit": 5},
        blocking=True, return_response=True,
    )
    mock_client.processes.assert_awaited_with("memory", 5)
    assert result["computers"]["Test PC"][0]["name"] == "game.exe"


async def test_offline_after_first_setup_keeps_entities(
    hass: HomeAssistant, mock_client: AsyncMock
) -> None:
    """A computer that is off on HA restart still loads so it can be woken."""
    entry = await _setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)

    mock_client.status.side_effect = AgentConnectionError("down")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    assert hass.states.get("binary_sensor.test_pc_online").state == STATE_OFF
    assert hass.states.get("switch.test_pc_power").state == STATE_OFF
    assert hass.states.get("sensor.test_pc_cpu_usage").state == "unavailable"
    assert hass.states.get("button.test_pc_wake_on_lan").state != "unavailable"
    assert hass.states.get("button.test_pc_shut_down").state == "unavailable"


async def test_never_seen_offline_retries(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    mock_client.status.side_effect = AgentConnectionError("down")
    entry = _entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_auth_failure_starts_reauth(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    mock_client.status.side_effect = AgentAuthError("bad")
    entry = _entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_new_disk_added_dynamically(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    entry = await _setup(hass)
    status = make_status()
    status["disks"] = status["disks"] + [
        {**status["disks"][0], "mount_point": "D:\\", "usage_percent": 10.0}
    ]
    mock_client.status.return_value = status
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.test_pc_disk_usage_d").state == "10.0"


def test_magic_packet() -> None:
    packet = build_magic_packet("AA-BB-CC-DD-EE-FF")
    assert len(packet) == 102
    assert packet[:6] == b"\xff" * 6
    assert packet[6:12] == bytes.fromhex("aabbccddeeff")
