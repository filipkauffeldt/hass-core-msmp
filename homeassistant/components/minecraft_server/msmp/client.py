"""Minimal MSMP WebSocket client.

This module must not import anything from Home Assistant.
"""

import asyncio
from typing import Self

import aiohttp
from yarl import URL

from .exceptions import MsmpConnectionError

SUBPROTOCOL = "minecraft-v1"
DEFAULT_CONNECT_TIMEOUT = 10


class MsmpClient:
    """WebSocket client for the Minecraft Server Management Protocol."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        secret: str,
        *,
        tls: bool = False,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    ) -> None:
        """Initialize the client. The session is owned by the caller."""
        self._session = session
        self._url = URL.build(scheme="wss" if tls else "ws", host=host, port=port)
        self._secret = secret
        self._connect_timeout = connect_timeout
        self._ws: aiohttp.ClientWebSocketResponse | None = None

    @property
    def connected(self) -> bool:
        """Return True if the WebSocket is open."""
        return self._ws is not None and not self._ws.closed

    async def connect(self) -> None:
        """Open the WebSocket, authenticating with the bearer token."""
        if self.connected:
            return
        try:
            async with asyncio.timeout(self._connect_timeout):
                self._ws = await self._session.ws_connect(
                    self._url,
                    headers={"Authorization": f"Bearer {self._secret}"},
                    protocols=(SUBPROTOCOL,),
                )
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            self._ws = None
            raise MsmpConnectionError(
                f"Unable to connect to {self._url}: {err}"
            ) from err

    async def close(self) -> None:
        """Close the WebSocket cleanly. Safe to call more than once."""
        ws, self._ws = self._ws, None
        if ws is not None and not ws.closed:
            await ws.close()

    async def __aenter__(self) -> Self:
        """Connect when entering the context."""
        await self.connect()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Close when leaving the context."""
        await self.close()
