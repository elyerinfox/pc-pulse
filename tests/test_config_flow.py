"""Config flow tests."""

from __future__ import annotations

from ipaddress import ip_address
from unittest.mock import AsyncMock

from homeassistant.config_entries import SOURCE_USER, SOURCE_ZEROCONF
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pc_pulse.api import AgentAuthError, AgentConnectionError
from custom_components.pc_pulse.const import DOMAIN

USER_INPUT = {CONF_HOST: "192.168.1.20", CONF_PORT: 8752, CONF_TOKEN: "secret"}

DISCOVERY = ZeroconfServiceInfo(
    ip_address=ip_address("192.168.1.20"),
    ip_addresses=[ip_address("192.168.1.20")],
    hostname="testpc.local.",
    name="Test PC._pc-pulse._tcp.local.",
    port=8752,
    type="_pc-pulse._tcp.local.",
    properties={"id": "abc123", "name": "Test PC", "version": "0.1.0"},
)


async def test_user_flow(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM

    mock_client.status.side_effect = AgentAuthError("bad")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["errors"] == {"base": "invalid_auth"}

    mock_client.status.side_effect = AgentConnectionError("down")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["errors"] == {"base": "cannot_connect"}

    mock_client.status.side_effect = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test PC"
    assert result["data"] == USER_INPUT
    assert result["result"].unique_id == "abc123"


async def test_user_flow_duplicate(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id="abc123", data=USER_INPUT).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_zeroconf_flow(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=DISCOVERY
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "zeroconf_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: "secret"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == USER_INPUT


async def test_zeroconf_updates_host(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="abc123", data={**USER_INPUT, CONF_HOST: "10.0.0.5"}
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=DISCOVERY
    )
    assert result["type"] is FlowResultType.ABORT
    assert entry.data[CONF_HOST] == "192.168.1.20"


async def test_reauth(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="abc123", data=USER_INPUT)
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: "newtoken"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_TOKEN] == "newtoken"
    # Let the reload triggered by the reauth finish before teardown.
    await hass.async_block_till_done()


async def test_options(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="abc123", data=USER_INPUT)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"scan_interval": 30, "mac": "zz", "broadcast_address": "192.168.1.255"},
    )
    assert result["errors"] == {"mac": "invalid_mac"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"scan_interval": 30, "mac": "AA-BB-CC-00-11-22", "broadcast_address": "192.168.1.255"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["mac"] == "aa:bb:cc:00:11:22"
