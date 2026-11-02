"""Tests for the WS connection wrapper."""

import asyncio
from collections.abc import AsyncGenerator
import json
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from yarl import URL

from homeassistant.components.minecraft_server.msmp.exceptions import (
    MsmpAuthError,
    MsmpConnectionError,
)
from homeassistant.components.minecraft_server.msmp.ws_connection import WsConnection

HOST = "localhost"
PORT = 25585
SECRET = "super-secret-token"


class FakeWs:
    """A WS fake to replace the aiohttp instance for unit testing purposes."""

    def __init__(self) -> None:
        """Initialisation."""
        self.connected = False
        self.closed = False

        self.incoming = asyncio.Queue()  # Messages sent from the server
        self.outgoing = asyncio.Queue()  # Messages sent to the server

        self.waited_for_n_outgoing_messages = asyncio.Event()

    def create_ws_response(
        self, id: int, output: dict[str, Any] | list | None = None
    ) -> aiohttp.WSMessage:
        """Create a WS response object."""
        return aiohttp.WSMessage(
            type=aiohttp.WSMsgType.TEXT,
            data=json.dumps({"id": id, "result": output}),
            extra=None,
        )

    async def ws_connect(self, *args, **kwargs):
        """Return connection."""
        self.connected = True
        return self

    async def send_json(self, request: dict) -> None:
        """Sending a message to the server."""
        await self.outgoing.put(request)
        self.waited_for_n_outgoing_messages.set()

    async def wait_for_n_outgoing_messages(self, n: int) -> None:
        """Wait for `n` messages to be sent -- use to ensure `n` messages have been sent before continuing with the test."""
        while self.outgoing.qsize() < n:
            self.waited_for_n_outgoing_messages.clear()
            await self.waited_for_n_outgoing_messages.wait()

    async def send_from_server(
        self, id: int, output: dict[str, Any] | list | None = None
    ) -> None:
        """Create a message to send from the "server" to the WS client."""
        await self.incoming.put(self.create_ws_response(id, output))

    async def receive(self) -> aiohttp.WSMessage:
        """Receiving a message from the server."""
        return await self.incoming.get()

    async def close(self):
        """Closes connection."""
        self.closed = True
        return True


@pytest.fixture
async def ws() -> AsyncGenerator[FakeWs]:
    """Return a fake WebSocket to test behaviour with incoming/outgoing messages."""
    mock_ws = FakeWs()

    yield mock_ws

    await mock_ws.close()


@pytest.fixture
async def simple_ws() -> AsyncGenerator[MagicMock]:
    """Return a mock WebSocket that flips `closed` when closed."""
    mock_ws = MagicMock(spec=aiohttp.ClientWebSocketResponse)
    mock_ws.closed = False

    async def _close(*args: object, **kwargs: object) -> bool:
        mock_ws.closed = True
        return True

    mock_ws.close = AsyncMock(side_effect=_close)

    yield mock_ws

    await mock_ws.close()


@pytest.fixture
async def session(ws: FakeWs) -> AsyncGenerator[MagicMock]:
    """Return a mock aiohttp session whose ws_connect returns the FakeWs instance."""
    mock_session = MagicMock(spec=aiohttp.ClientSession)
    mock_session.ws_connect = AsyncMock(return_value=ws)

    yield mock_session

    await ws.close()


@pytest.fixture
async def simple_session(simple_ws: MagicMock) -> AsyncGenerator[MagicMock]:
    """Return a mock aiohttp session whose ws_connect returns the mock socket."""
    mock_session = MagicMock()
    mock_session.ws_connect = AsyncMock(return_value=simple_ws)

    yield mock_session

    await simple_ws.close()


@pytest.fixture
async def ws_connection(session: MagicMock) -> AsyncGenerator[WsConnection]:
    """Return a WS connection using the mock session."""
    ws_connection = WsConnection(session, HOST, PORT, SECRET)

    yield ws_connection

    await ws_connection.close()


async def test_connect_sends_token_and_subprotocol(
    ws_connection: WsConnection, session: MagicMock
) -> None:
    """Test connect uses the bearer token and the minecraft-v1 subprotocol."""
    await ws_connection.connect()

    session.ws_connect.assert_awaited_once()
    args, kwargs = session.ws_connect.call_args
    assert args[0] == URL(f"ws://{HOST}:{PORT}")
    assert kwargs["headers"] == {"Authorization": f"Bearer {SECRET}"}
    assert kwargs["protocols"] == ("minecraft-v1",)
    assert ws_connection.connected


async def test_connect_uses_wss_when_tls_enabled(session: MagicMock) -> None:
    """Test the wss scheme is used when TLS is enabled."""
    ws_connection = WsConnection(session, HOST, PORT, SECRET, tls=True)

    await ws_connection.connect()
    await ws_connection.close()  # Required to close the listener

    assert session.ws_connect.call_args.args[0] == URL(f"wss://{HOST}:{PORT}")


async def test_connect_is_noop_when_already_connected(
    ws_connection: WsConnection, session: MagicMock
) -> None:
    """Test calling connect twice only opens one socket."""
    await ws_connection.connect()
    await ws_connection.connect()

    session.ws_connect.assert_awaited_once()


@pytest.mark.parametrize(
    "error",
    [
        aiohttp.ClientError("boom"),
        aiohttp.WSServerHandshakeError(MagicMock(), (), status=401),
        TimeoutError(),
        OSError("connection refused"),
    ],
    ids=["client_error", "handshake_rejected", "timeout", "os_error"],
)
async def test_connect_failure_raises_msmp_connection_error(
    ws_connection: WsConnection, session: MagicMock, error: Exception
) -> None:
    """Test connection failures are wrapped in MsmpConnectionError."""
    session.ws_connect.side_effect = error

    with pytest.raises(MsmpConnectionError) as exc_info:
        await ws_connection.connect()

    assert exc_info.value.__cause__ is error
    assert not ws_connection.connected


async def test_connect_failure_does_not_leak_secret(
    ws_connection: WsConnection, session: MagicMock
) -> None:
    """Test the secret never appears in the error message."""
    session.ws_connect.side_effect = aiohttp.ClientError("boom")

    with pytest.raises(MsmpConnectionError) as exc_info:
        await ws_connection.connect()

    assert SECRET not in str(exc_info.value)


async def test_connect_timeout(session: MagicMock) -> None:
    """Test a hanging handshake is cut off by the connect timeout."""

    async def _hang(*args: object, **kwargs: object) -> None:
        await asyncio.sleep(10)

    session.ws_connect.side_effect = _hang
    ws_connection = WsConnection(session, HOST, PORT, SECRET, connection_timeout=0.01)

    with pytest.raises(MsmpConnectionError):
        await ws_connection.connect()

    assert not ws_connection.connected


async def test_close_closes_socket_once(simple_session) -> None:
    """Test close shuts the socket and is safe to call repeatedly."""
    ws_connection = WsConnection(simple_session, HOST, PORT, SECRET)

    await ws_connection.connect()

    await ws_connection.close()
    await ws_connection.close()

    ws_connection._ws.close.assert_awaited_once()
    assert not ws_connection.connected
    assert ws_connection.closed


async def test_close_without_connect(
    ws_connection: WsConnection, session: MagicMock
) -> None:
    """Test close before connect is a harmless no-op."""
    await ws_connection.close()

    session.ws_connect.assert_not_awaited()
    assert not ws_connection.connected
    assert not ws_connection.closed


async def test_cannot_reconnect_after_close(
    ws_connection: WsConnection, session: MagicMock
) -> None:
    """Test the client cannot connect again after being closed."""
    await ws_connection.connect()
    await ws_connection.close()

    await ws_connection.connect()

    assert session.ws_connect.await_count == 1
    assert not ws_connection.connected
    assert ws_connection.closed


async def test_connected_reflects_socket_closed_state(
    ws_connection: WsConnection, ws: MagicMock
) -> None:
    """Test connected follows the socket's own closed flag."""
    await ws_connection.connect()
    ws.closed = True

    assert not ws_connection.connected


async def test_build_request_body_uses_incremental_ids(
    ws_connection: WsConnection,
) -> None:
    """Test `_build_request_body()` increments request ID automatically."""
    body1 = ws_connection._build_request_body("minecraft:method1")
    body2 = ws_connection._build_request_body("minecraft:method2")

    assert body1["id"] == 1
    assert body2["id"] == 2


async def test_build_request_body_without_params(
    ws_connection: WsConnection,
) -> None:
    """Test the body doesn't contain the `params` field of no parameters were passed."""
    body = ws_connection._build_request_body("minecraft:method1")

    assert "params" not in body


async def test_build_request_body_with_params(
    ws_connection: WsConnection,
) -> None:
    """Test the body contains the parameters if passed."""
    body = ws_connection._build_request_body("minecraft:method1", {"params": True})

    assert body["method"] == "minecraft:method1"
    assert body["params"] == {"params": True}


async def test_connect_401_raises_auth_error(
    ws_connection: WsConnection, session: MagicMock
) -> None:
    """Test a 401 handshake response is reported as an auth error."""
    session.ws_connect.side_effect = aiohttp.WSServerHandshakeError(
        MagicMock(), (), status=401
    )

    with pytest.raises(MsmpAuthError):
        await ws_connection.connect()

    assert not ws_connection.connected


async def test_connect_other_http_error_is_not_auth_error(
    ws_connection: WsConnection, session: MagicMock
) -> None:
    """Test non-401 handshake failures stay generic connection errors."""
    session.ws_connect.side_effect = aiohttp.WSServerHandshakeError(
        MagicMock(), (), status=500
    )

    with pytest.raises(MsmpConnectionError) as exc_info:
        await ws_connection.connect()

    assert not isinstance(exc_info.value, MsmpAuthError)


async def test_single_ws_request_receives_response(ws_connection: WsConnection) -> None:
    """Test a single WS request gets a response."""
    await ws_connection.connect()

    await ws_connection._ws.send_from_server(1, ["Player1"])

    response = await ws_connection.call("minecraft:allowlist")

    assert response == ["Player1"]


@pytest.mark.parametrize("id", [-2, -1, 0, 2, 3, 4])
async def test_listener_catches_unexpected_ids_2(
    id: int, ws_connection: WsConnection, caplog: pytest.LogCaptureFixture
) -> None:
    """Tests that unexpected IDs get caught.

    Since we're mocking the logger with `patch.object()`, we need to store a reference to the original `_LOGGER.error()` method so that we can invoke it when the mock gets called. I think this test looks very clumsy, but I didn't find any better way to ensure that `_LOGGER.error()` was called without relying on a timeout.
    """

    # Prepare logger for mocking
    _LOGGER: logging.Logger = logging.getLogger(
        "homeassistant.components.minecraft_server"
    )
    original_logger = _LOGGER.error
    logged = asyncio.Event()

    def mark_as_logged(*args, **kwargs):
        original_logger(*args, **kwargs)
        logged.set()

    # Connect and set up response
    await ws_connection.connect()
    await ws_connection._ws.send_from_server(id, [])

    # Set up a listener
    with (
        patch.object(_LOGGER, "error", side_effect=mark_as_logged),
        caplog.at_level(logging.ERROR, _LOGGER.name),
    ):
        call_task = asyncio.create_task(ws_connection.call("minecraft:allowlist"))
        await asyncio.wait_for(logged.wait(), timeout=1)

        call_task.cancel()

    assert f"Received response for request ID {id}" in caplog.text


async def test_sequential_ws_requests_receive_correct_responses(
    ws_connection: WsConnection,
) -> None:
    """Test that each of the multiple WS requests each gets the expected response when the responses are sent in order."""
    await ws_connection.connect()
    ws = ws_connection._ws

    await ws.send_from_server(1, [])
    response1 = await ws_connection.call("minecraft:allowlist")

    await ws.send_from_server(2, ["Mean Player1", "Herobrine"])
    response2 = await ws_connection.call("minecraft:ban")

    await ws.send_from_server(3, {"started": True})
    response3 = await ws_connection.call("minecraft:server/status")

    assert ws.incoming.qsize() == 0
    assert ws.outgoing.qsize() == 3

    assert response1 == []
    assert response2 == ["Mean Player1", "Herobrine"]
    assert response3 == {"started": True}


@pytest.mark.parametrize(
    "order", [[0, 1, 2], [0, 2, 1], [1, 0, 2], [1, 2, 0], [2, 0, 1], [2, 1, 0]]
)
async def test_concurrent_ws_requests_receive_correct_responses(
    order: list[int],
    ws_connection: WsConnection,
) -> None:
    """Test that each of the multiple WS requests gets the expected response, even when the responses are sent out of order."""
    await ws_connection.connect()
    ws = ws_connection._ws

    task1 = asyncio.create_task(ws_connection.call("minecraft:allowlist"))
    task2 = asyncio.create_task(ws_connection.call("minecraft:ban"))
    task3 = asyncio.create_task(ws_connection.call("minecraft:server/status"))

    await ws.wait_for_n_outgoing_messages(3)

    responses = [(1, []), (2, ["Mean Player1", "Herobrine"]), (3, {"started": True})]

    await ws.send_from_server(*responses[order[0]])
    await ws.send_from_server(*responses[order[1]])
    await ws.send_from_server(*responses[order[2]])

    response1, response2, response3 = await asyncio.gather(task1, task2, task3)

    assert response1 == []
    assert response2 == ["Mean Player1", "Herobrine"]
    assert response3 == {"started": True}
