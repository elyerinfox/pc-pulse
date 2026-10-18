"""Constants for the PC Pulse integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "pc_pulse"

DEFAULT_PORT: Final = 8752
DEFAULT_SCAN_INTERVAL: Final = 15
MIN_SCAN_INTERVAL: Final = 5

CONF_MAC: Final = "mac"
CONF_BROADCAST: Final = "broadcast_address"
CONF_AGENT_ID: Final = "agent_id"

DEFAULT_BROADCAST: Final = "255.255.255.255"

ZEROCONF_TYPE: Final = "_pc-pulse._tcp.local."

POWER_ACTIONS: Final = (
    "shutdown",
    "restart",
    "sleep",
    "hibernate",
    "lock",
    "logoff",
    "monitor_off",
    "monitor_on",
)

# How long to keep showing a machine as on after a shutdown/sleep was requested
# before polling decides otherwise.
POWER_OFF_GRACE: Final = timedelta(seconds=90)

SERVICE_POWER: Final = "power"
SERVICE_KILL_PROCESS: Final = "kill_process"
SERVICE_RUN_COMMAND: Final = "run_command"
SERVICE_WAKE: Final = "wake"
SERVICE_GET_PROCESSES: Final = "get_processes"

ATTR_ACTION: Final = "action"
ATTR_DELAY: Final = "delay"
ATTR_PID: Final = "pid"
ATTR_NAME: Final = "name"
ATTR_COMMAND: Final = "command"
ATTR_SORT: Final = "sort"
ATTR_LIMIT: Final = "limit"
