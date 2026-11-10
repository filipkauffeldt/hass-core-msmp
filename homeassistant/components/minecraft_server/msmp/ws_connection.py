"""WS connection wrapper to be used by the MsmpClient."""

import asyncio
import logging
from typing import Any

import aiohttp
from yarl import URL

from .exceptions import MsmpAuthError, MsmpConnectionError, MsmpRequestError

_LOGGER = logging.getLogger(__name__)

SUBPROTOCOL = "minecraft-v1"
DEFAULT_TLS = False
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
        tls: bool = DEFAULT_TLS,
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
        self._connect_lock = asyncio.Lock()

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
        """Return True if the WebSocket has been closed."""
        return self._ws is not None and self._ws.closed

    async def connect(self) -> None:
        """Open the WebSocket, authenticating with the bearer token."""
        async with self._connect_lock:
            if self.connected:
                return

            self._ws = await self._open_websocket()
            self._ws_listener = asyncio.create_task(
                self.listen(self._ws), name="minecraft_server:wsconnection_listener"
            )

    async def _open_websocket(self) -> aiohttp.ClientWebSocketResponse:
        """Open the socket and translate connection failures."""
        try:
            async with asyncio.timeout(self._connection_timeout):
                return await self._session.ws_connect(
                    self._url,
                    headers={"Authorization": f"Bearer {self.__secret}"},
                    protocols=(SUBPROTOCOL,),
                )
        except aiohttp.WSServerHandshakeError as err:
            if err.status == 401:
                raise MsmpAuthError(
                    f"Authentication failed for {self._url}: Unauthorized"
                ) from err
            raise MsmpConnectionError(
                f"Unable to connect to {self._url}: HTTP {err.status}"
            ) from err
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise MsmpConnectionError(
                f"Unable to connect to {self._url}: {err}"
            ) from err

    def __get_next_request_id(self) -> int:
        """Gets the ID of the next request to ensure each request has a unique ID."""
        self.__request_id += 1

        return self.__request_id

    def _fail_pending_requests(self, reason: str) -> None:
        """Fail every waiting call so callers don't sit out their timeout."""
        for request_id, future in self._pending_requests.items():
            if not future.done():
                future.set_exception(
                    MsmpConnectionError(f"Request (ID: {request_id}) failed: {reason}")
                )

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
        request_id = request["id"]

        future = asyncio.get_running_loop().create_future()
        self._pending_requests[request_id] = future

        try:
            async with asyncio.timeout(self._request_timeout):
                await ws.send_json(request)
                response = await future
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            reason = str(err) or type(err).__name__
            raise MsmpConnectionError(
                f"Request (ID: {request_id}) to {method} failed: {reason}"
            ) from err
        finally:
            self._pending_requests.pop(request_id, None)

        if (rpc_error := response.get("error")) is not None:
            raise MsmpRequestError(
                f"Request (ID: {request_id}) to {method} failed: {rpc_error}"
            )

        return response.get("result")

    async def listen(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Match incoming replies to pending requests until the socket closes."""
        try:
            while not ws.closed:
                message = await ws.receive()

                match message.type:
                    case aiohttp.WSMsgType.TEXT:
                        self._handle_text_message(message)
                    case (
                        aiohttp.WSMsgType.CLOSE
                        | aiohttp.WSMsgType.CLOSING
                        | aiohttp.WSMsgType.CLOSED
                    ):
                        break
                    case aiohttp.WSMsgType.ERROR:
                        _LOGGER.error("WebSocket error: %s", message.data)
                        break
                    case _:
                        _LOGGER.warning(
                            "Ignoring WS message of type %s", message.type.name
                        )
        except (aiohttp.ClientError, OSError) as err:
            _LOGGER.error("WS listener stopped: %s", err)
        finally:
            self._fail_pending_requests("connection closed")

    def _handle_text_message(self, message: aiohttp.WSMessage) -> None:
        """Hand a reply to the call waiting for it."""
        try:
            data = message.json()
        except ValueError as err:
            _LOGGER.error("Received invalid JSON (%s)", err)
            return

        match data:
            case {"id": int() as request_id}:
                future = self._pending_requests.get(request_id)
                if future is not None and not future.done():
                    future.set_result(data)
            case dict():
                # Notifications have no ID and are not handled yet
                _LOGGER.debug("Ignoring message without a usable request ID")
            case _:
                _LOGGER.error("Unexpected response format: %s", type(data).__name__)

    async def close(self) -> None:
        """Close the WebSocket connection cleanly."""
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()

        if self._ws_listener is not None:
            # The listener's `finally` fails any pending requests
            self._ws_listener.cancel()
            self._ws_listener = None
