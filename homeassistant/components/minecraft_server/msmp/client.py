"""Minimal MSMP WebSocket client.

This module must not import anything from Home Assistant.
"""

import asyncio
from typing import Any, Self

import aiohttp
from yarl import URL

from .exceptions import MsmpAuthError, MsmpConnectionError, MsmpRequestError

SUBPROTOCOL = "minecraft-v1"
DEFAULT_CONNECT_TIMEOUT = 10
DEFAULT_REQUEST_TIMEOUT = 10


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
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> None:
        """Initialize the client. The session is owned by the caller."""
        self._session = session
        self._url = URL.build(scheme="wss" if tls else "ws", host=host, port=port)
        self._secret = secret
        self._connect_timeout = connect_timeout
        self._request_timeout = request_timeout
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._request_id = 0
        # Only one call may read from the socket at a time.
        self._lock = asyncio.Lock()

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
        except aiohttp.WSServerHandshakeError as err:
            self._ws = None
            if err.status == 401:
                raise MsmpAuthError(
                    f"Authentication failed for {self._url}: Unauthorized"
                ) from err
            raise MsmpConnectionError(
                f"Unable to connect to {self._url}: HTTP {err.status}"
            ) from err
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            self._ws = None
            raise MsmpConnectionError(
                f"Unable to connect to {self._url}: {err}"
            ) from err

    async def call(
        self, method: str, params: dict[str, Any] | list[Any] | None = None
    ) -> Any:
        """Call a method and return its result.

        Raises MsmpRequestError if the server answers with an error, and
        MsmpConnectionError if the connection fails or the call times out.
        """
        async with self._lock:
            ws = self._ws
            if ws is None or ws.closed:
                raise MsmpConnectionError("Not connected")

            self._request_id += 1
            request_id = self._request_id
            request: dict[str, Any] = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
            }
            if params is not None:
                request["params"] = params

            try:
                async with asyncio.timeout(self._request_timeout):
                    await ws.send_json(request)
                    response = await self._receive_response(ws, request_id)
            except (aiohttp.ClientError, TimeoutError, OSError) as err:
                raise MsmpConnectionError(f"Call to {method} failed: {err}") from err

        if (error := response.get("error")) is not None:
            raise MsmpRequestError(f"Call to {method} failed: {error}")
        return response.get("result")

    @staticmethod
    async def _receive_response(
        ws: aiohttp.ClientWebSocketResponse, request_id: int
    ) -> dict[str, Any]:
        """Read messages until the response with the given ID arrives."""
        while True:
            msg = await ws.receive()
            if msg.type is not aiohttp.WSMsgType.TEXT:
                raise MsmpConnectionError(f"Connection lost ({msg.type.name})")
            try:
                data = msg.json()
            except ValueError as err:
                raise MsmpConnectionError("Received invalid JSON") from err
            # Messages without a matching ID are notifications, ignored for now.
            if isinstance(data, dict) and data.get("id") == request_id:
                return data

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
