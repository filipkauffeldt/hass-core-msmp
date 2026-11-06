"""WS connection wrapper to be used by the MsmpClient."""

import asyncio
from typing import Any

import aiohttp
from yarl import URL

from .. import _LOGGER
from .exceptions import MsmpAuthError, MsmpConnectionError, MsmpRequestError

SUBPROTOCOL = "minecraft-v1"
DEFAULT_CONNECTION_TIMEOUT = 10
DEFAULT_REQUEST_TIMEOUT = 10


class WsConnection:
    """A wrapper for aiohttp."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        secret: str,
        *,
        tls: bool = False,
        connection_timeout: float = DEFAULT_CONNECTION_TIMEOUT,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> None:
        """Initialise the WS connection wrapper instance.

        The wrapper should be used as a singleton, i.e., only one instance of this class should live.
        """
        self._session = session
        self._url = URL.build(scheme="wss" if tls else "ws", host=host, port=port)
        self.__secret = secret

        self._connection_timeout = connection_timeout
        self._request_timeout = request_timeout

        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._ws_listener: asyncio.Task | None = None
        self._pending_requests: dict[int, asyncio.Future] = {}

        self.__request_id = 0

    @property
    def pending_requests_count(self) -> int:
        """Returns how many requests are pending."""
        return len(self._pending_requests)

    @property
    def connected(self) -> bool:
        """Return True if the WebSocket is open."""
        return self._ws is not None and not self._ws.closed

    @property
    def closed(self) -> bool:
        """Return True iff the WS connection is closed."""
        return self._ws is not None and self._ws.closed

    async def connect(self) -> None:
        """Open the WebSocket, authenticating with the bearer token."""
        if self.connected or self.closed:
            return
        try:
            async with asyncio.timeout(self._connection_timeout):
                self._ws = await self._session.ws_connect(
                    self._url,
                    headers={"Authorization": f"Bearer {self.__secret}"},
                    protocols=(SUBPROTOCOL,),
                )
                self._ws_listener = asyncio.create_task(
                    self.listen(), name="minecraft_server:wsconnection_listener"
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

    def __get_next_request_id(self) -> int:
        """Gets the ID of the next request to ensure each request has a unique ID."""
        self.__request_id += 1

        return self.__request_id

    def __cancel_pending_requests(self) -> None:
        """Cancels all pending requests."""
        for future in self._pending_requests.values():
            future.cancel()

        self._pending_requests.clear()

    def build_request_body(
        self, method: str, params: dict[str, Any] | list[Any] | None = None
    ) -> dict[str, Any]:
        """Build the request body."""
        body: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self.__get_next_request_id(),
            "method": method,
        }

        if params is not None:
            body["params"] = params

        return body

    async def call(
        self, method: str, params: dict[str, Any] | list[Any] | None = None
    ) -> Any:
        """Send a message to the WS server and return its result."""
        ws = self._ws
        if ws is None or ws.closed:
            raise MsmpConnectionError("Not connected")

        request = self.build_request_body(method, params)

        try:
            future = asyncio.get_running_loop().create_future()
            self._pending_requests[request["id"]] = future

            async with asyncio.timeout(self._request_timeout):
                await ws.send_json(request)
                response = await asyncio.wait_for(future, self._request_timeout)

        except (
            aiohttp.ClientError,
            asyncio.CancelledError,
            TimeoutError,
            OSError,
        ) as err:
            raise MsmpConnectionError(
                f"Request (ID: {request['id']}) to {method} failed: {err}"
            ) from err
        finally:
            # Something might break in the listener that will clear the pending requests dictionary, so before trying to delete it from the dictionary, we need to check it's still there. Otherwise, it'll throw an exception
            if request["id"] in self._pending_requests:
                del self._pending_requests[request["id"]]

        if (error := response.get("error")) is not None:
            raise MsmpRequestError(
                f"Request (ID: {request['id']}) to {method} failed: {error}"
            )
        return response.get("result")

    async def listen(self) -> None:
        """Listens for messages from the WS server and matches them to their corresponding request."""
        while self._ws is not None and not self._ws.closed:
            try:
                msg = await self._ws.receive()
            except (aiohttp.ClientError, asyncio.CancelledError, TimeoutError) as err:
                _LOGGER.error(
                    "Something went wrong whilst getting a WS message: %s", err
                )
                self.__cancel_pending_requests()
                break

            if msg.type is not aiohttp.WSMsgType.TEXT:
                _LOGGER.error("Connection lost (%s)", msg.type.name)
                self.__cancel_pending_requests()
                continue

            try:
                data = msg.json()
            except ValueError as err:
                _LOGGER.error("Received invalid JSON (%s)", err)
                continue

            if not isinstance(data, dict):
                _LOGGER.error("Unexpected response format: %s", type(data))
                continue

            # Responses without ID are notifications, and for now ignored
            msg_id = data.get("id")
            if (
                msg_id in self._pending_requests
                and not self._pending_requests[msg_id].done()
            ):
                self._pending_requests[msg_id].set_result(data)

    async def close(self) -> None:
        """Close the WebSocket connection cleanly."""
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()

        if self._ws_listener is not None and not self._ws_listener.done():
            self._ws_listener.cancel()

        self.__cancel_pending_requests()
