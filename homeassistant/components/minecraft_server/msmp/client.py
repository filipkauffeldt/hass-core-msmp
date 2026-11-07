"""Minimal MSMP WebSocket client.

This module must not import anything from Home Assistant.
"""

from typing import Any, Self

import aiohttp

from .exceptions import MsmpRequestError
from .models import MsmpServerStatus
from .ws_connection import DEFAULT_TLS, WsConnection


class MsmpClient:
    """Client for the Minecraft Server Management Protocol."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        secret: str,
        *,
        tls: bool = DEFAULT_TLS,
    ) -> None:
        """Initialize the client. The session is owned by the caller."""
        self._connection = WsConnection(session, host, port, secret, tls=tls)

    @property
    def connected(self) -> bool:
        """Return True if the WebSocket is open."""
        return self._connection.connected

    async def connect(self) -> None:
        """Open the WebSocket, authenticating with the bearer token."""
        await self._connection.connect()

    async def call(
        self, method: str, params: dict[str, Any] | list[Any] | None = None
    ) -> Any:
        """Call a method and return its result.

        Calls can run concurrently. Raises MsmpRequestError if the server
        answers with an error, and MsmpConnectionError if the connection fails
        or the call times out.
        """
        return await self._connection.call(method, params)

    async def get_server_status(self) -> MsmpServerStatus:
        """Return the state of the server.

        Raises MsmpRequestError if the server's answer is not a valid status.
        """
        result = await self.call("minecraft:server/status")

        try:
            return MsmpServerStatus.from_result(result)
        except (KeyError, TypeError) as err:
            raise MsmpRequestError(
                f"Unexpected server status response: {result!r}"
            ) from err

    async def close(self) -> None:
        """Close the WebSocket cleanly. Safe to call more than once."""
        await self._connection.close()

    async def __aenter__(self) -> Self:
        """Connect when entering the context."""
        await self.connect()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Close when leaving the context."""
        await self.close()
