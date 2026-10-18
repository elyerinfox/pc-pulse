"""Client for the pc-pulse agent HTTP API."""

from __future__ import annotations

import socket
from typing import Any

import aiohttp


class AgentError(Exception):
    """Base error talking to the agent."""


class AgentConnectionError(AgentError):
    """The agent could not be reached."""


class AgentAuthError(AgentError):
    """The token was rejected."""


class AgentRequestError(AgentError):
    """The agent rejected the request."""


class AgentClient:
    """Small async client for one agent."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        token: str,
        timeout: float = 10,
    ) -> None:
        self._session = session
        self._base = f"http://{_format_host(host)}:{port}/api/v1"
        self._token = token
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        auth: bool = True,
    ) -> Any:
        headers = {"Authorization": f"Bearer {self._token}"} if auth else {}
        try:
            async with self._session.request(
                method,
                f"{self._base}{path}",
                json=json,
                params=params,
                headers=headers,
                timeout=self._timeout,
            ) as resp:
                if resp.status == 401:
                    raise AgentAuthError("Invalid token")
                try:
                    data = await resp.json(content_type=None)
                except ValueError:
                    data = None
                if resp.status >= 400:
                    message = (
                        data.get("error") if isinstance(data, dict) else None
                    ) or f"HTTP {resp.status}"
                    raise AgentRequestError(message)
                return data
        except (TimeoutError, aiohttp.ClientError, OSError) as err:
            raise AgentConnectionError(str(err) or type(err).__name__) from err

    async def health(self) -> dict[str, Any]:
        """Unauthenticated liveness check."""
        return await self._request("GET", "/health", auth=False)

    async def status(self) -> dict[str, Any]:
        """Full status snapshot."""
        return await self._request("GET", "/status")

    async def processes(
        self, sort: str = "cpu", limit: int | None = None
    ) -> list[dict[str, Any]]:
        """List running processes."""
        params: dict[str, Any] = {"sort": sort}
        if limit:
            params["limit"] = limit
        return await self._request("GET", "/processes", params=params)

    async def power(self, action: str, delay: int = 0) -> dict[str, Any]:
        """Run a power action (shutdown, sleep, ...)."""
        return await self._request(
            "POST", f"/power/{action}", json={"delay_secs": int(delay)}
        )

    async def kill(
        self, pid: int | None = None, name: str | None = None
    ) -> dict[str, Any]:
        """Kill processes by pid or name."""
        body: dict[str, Any] = {}
        if pid is not None:
            body["pid"] = int(pid)
        if name:
            body["name"] = name
        return await self._request("POST", "/processes/kill", json=body)

    async def run_command(self, name: str) -> dict[str, Any]:
        """Run a command defined in the agent's config."""
        return await self._request("POST", f"/commands/{name}")


def _format_host(host: str) -> str:
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


def build_magic_packet(mac: str) -> bytes:
    """Build a Wake-on-LAN magic packet for a MAC address."""
    digits = "".join(c for c in mac if c.isalnum())
    if len(digits) != 12:
        raise ValueError(f"Invalid MAC address: {mac}")
    return b"\xff" * 6 + bytes.fromhex(digits) * 16


def send_magic_packet(mac: str, broadcast: str, port: int = 9) -> None:
    """Send a Wake-on-LAN packet (blocking)."""
    packet = build_magic_packet(mac)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, (broadcast, port))
        if port != 7:
            sock.sendto(packet, (broadcast, 7))
