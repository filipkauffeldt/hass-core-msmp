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
    MsmpRequestError,
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

    def create_ws_error_response(
        self, id: int, code: int, message: str
    ) -> aiohttp.WSMessage:
        """Create a WS error response object."""
        return aiohttp.WSMessage(
            type=aiohttp.WSMsgType.TEXT,
            data=json.dumps({"id": id, "error": {"code": code, "message": message}}),
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
        await self.send_raw_from_server(self.create_ws_response(id, output))

    async def send_error_from_server(self, id: int, code: int, message: str) -> None:
        """Create an error message to send from the "server" to the WS client."""
        await self.send_raw_from_server(
            self.create_ws_error_response(id, code, message)
        )

    async def send_raw_from_server(self, response: aiohttp.WSMessage) -> None:
        """Add a message to send from the "server" to the WS client."""
        await self.incoming.put(response)

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


async def test_connect_after_close_opens_a_new_socket(
    ws_connection: WsConnection, session: MagicMock
) -> None:
    """Test a closed connection can connect again and serve calls."""
    await ws_connection.connect()
    await ws_connection.close()
    new_ws = FakeWs()
    session.ws_connect.return_value = new_ws

    await ws_connection.connect()

    call_task = asyncio.create_task(ws_connection.call("minecraft:allowlist"))
    request = await new_ws.outgoing.get()
    await new_ws.send_from_server(request["id"], ["Player1"])
    assert await asyncio.wait_for(call_task, timeout=1) == ["Player1"]


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
    body1 = ws_connection.build_request_body("minecraft:method1")
    body2 = ws_connection.build_request_body("minecraft:method2")

    assert body1["id"] == 1
    assert body2["id"] == 2


async def test_build_request_body_without_params(
    ws_connection: WsConnection,
) -> None:
    """Test the body doesn't contain the `params` field of no parameters were passed."""
    body = ws_connection.build_request_body("minecraft:method1")

    assert "params" not in body


async def test_build_request_body_with_params(
    ws_connection: WsConnection,
) -> None:
    """Test the body contains the parameters if passed."""
    body = ws_connection.build_request_body("minecraft:method1", {"params": True})

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


async def test_cant_call_without_connection(ws_connection: WsConnection) -> None:
    """Test that `call()` doesn't run when no connection is established."""
    with pytest.raises(MsmpConnectionError) as exc_info:
        await ws_connection.call("minecraft:allowlist")

    assert str(exc_info.value) == "Not connected"


async def test_single_ws_request_receives_response(ws_connection: WsConnection) -> None:
    """Test a single WS request gets a response."""
    await ws_connection.connect()
    ws = ws_connection._ws

    task1 = asyncio.create_task(ws_connection.call("minecraft:allowlist"))

    await ws.wait_for_n_outgoing_messages(1)
    await ws.send_from_server(1, ["Player1"])

    response = await asyncio.wait_for(task1, timeout=1)

    assert response == ["Player1"]


async def test_sequential_ws_requests_receive_correct_responses(
    ws_connection: WsConnection,
) -> None:
    """Test that each of the multiple WS requests each gets the expected response when the responses are sent in order."""
    await ws_connection.connect()
    ws = ws_connection._ws

    requests = "minecraft:allowlist", "minecraft:ban", "minecraft:server/status"
    responses = [], ["Mean Player1", "Herobrine"], {"Started": True}

    for i, request in enumerate(requests):
        task = asyncio.create_task(ws_connection.call(request))

        await ws.wait_for_n_outgoing_messages(1)
        await ws.send_from_server(i + 1, responses[i])

        response = await asyncio.wait_for(task, timeout=1)

        assert response == responses[i]


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
    assert ws_connection.pending_requests_count == 3

    responses = [(1, []), (2, ["Mean Player1", "Herobrine"]), (3, {"started": True})]

    await ws.send_from_server(*responses[order[0]])
    await ws.send_from_server(*responses[order[1]])
    await ws.send_from_server(*responses[order[2]])

    response1, response2, response3 = await asyncio.gather(task1, task2, task3)

    assert response1 == []
    assert response2 == ["Mean Player1", "Herobrine"]
    assert response3 == {"started": True}


async def test_concurrent_ws_requests_receive_correct_responses_with_errors(
    ws_connection: WsConnection,
) -> None:
    """Test that each of the multiple WS requests gets the expected response, even when the responses are sent out of order."""
    await ws_connection.connect()
    ws = ws_connection._ws

    task1 = asyncio.create_task(ws_connection.call("minecraft:allowlist"))
    task2 = asyncio.create_task(ws_connection.call("minecraft:ban"))
    task3 = asyncio.create_task(ws_connection.call("minecraft:server/status"))

    await ws.wait_for_n_outgoing_messages(3)
    assert ws_connection.pending_requests_count == 3

    await ws.send_from_server(3, {"started": True})
    await ws.send_from_server(2, ["Mean Player1", "Herobrine"])
    await ws.send_error_from_server(1, -32602, "Invalid parameters")

    response2, response3 = await asyncio.gather(task2, task3)
    with pytest.raises(MsmpRequestError) as exc_info:
        (_response1,) = await asyncio.gather(task1)

    assert response2 == ["Mean Player1", "Herobrine"]
    assert response3 == {"started": True}

    assert "(ID: 1) to minecraft:allowlist failed" in str(exc_info.value)
    assert "-32602" in str(exc_info.value)
    assert "Invalid parameters" in str(exc_info.value)


async def test_ws_request_raises_error_on_error_response(
    ws_connection: WsConnection,
) -> None:
    """Test that when the response is an error, an error is raised."""
    await ws_connection.connect()
    ws = ws_connection._ws

    task1 = asyncio.create_task(ws_connection.call("minecraft:allowlist"))

    await ws.wait_for_n_outgoing_messages(1)
    await ws.send_error_from_server(1, -32602, "Invalid parameters")

    with pytest.raises(MsmpRequestError) as exc_info:
        await asyncio.wait_for(task1, timeout=1)

    assert "(ID: 1) to minecraft:allowlist failed" in str(exc_info.value)
    assert "-32602" in str(exc_info.value)
    assert "Invalid parameters" in str(exc_info.value)


async def test_ws_request_timeout(ws_connection: WsConnection) -> None:
    """Test that when the response is an error, an error is raised."""
    await ws_connection.connect()
    ws_connection._request_timeout = 0.1

    with pytest.raises(MsmpConnectionError) as exc_info:
        await ws_connection.call("minecraft:allowlist")

    assert "(ID: 1) to minecraft:allowlist failed" in str(exc_info.value)


async def test_closing_message_fails_pending_requests(
    ws_connection: WsConnection,
) -> None:
    """Test that a closing message fails the calls that are waiting for a reply."""
    await ws_connection.connect()
    ws = ws_connection._ws

    task1 = asyncio.create_task(ws_connection.call("minecraft:allowlist"))

    await ws.wait_for_n_outgoing_messages(1)
    await ws.send_raw_from_server(
        aiohttp.WSMessage(
            type=aiohttp.WSMsgType.CLOSING,
            data=None,
            extra=None,
        )
    )
    with pytest.raises(MsmpConnectionError) as exc_info:
        await asyncio.wait_for(task1, timeout=3)

    assert "Request (ID: 1) failed: connection closed" in str(exc_info.value)
    assert ws_connection.pending_requests_count == 0


async def test_invalid_json_gets_logged(
    ws_connection: WsConnection, caplog: pytest.LogCaptureFixture
) -> None:
    """Test that when the received message isn't in JSON format, this is logged."""
    _LOGGER: logging.Logger = logging.getLogger(
        "homeassistant.components.minecraft_server.msmp.ws_connection"
    )
    original_logger = _LOGGER.error
    logged = asyncio.Event()

    def mark_as_logged(*args, **kwargs):
        original_logger(*args, **kwargs)
        logged.set()

    await ws_connection.connect()
    ws = ws_connection._ws

    with (
        patch.object(_LOGGER, "error", side_effect=mark_as_logged),
        caplog.at_level(logging.ERROR, _LOGGER.name),
    ):
        task1 = asyncio.create_task(ws_connection.call("minecraft:allowlist"))

        await ws.wait_for_n_outgoing_messages(1)
        await ws.send_raw_from_server(
            aiohttp.WSMessage(
                type=aiohttp.WSMsgType.TEXT,
                data="This is not valid JSON syntax",
                extra=None,
            )
        )

        await asyncio.wait_for(logged.wait(), timeout=1)
        task1.cancel()

    assert "Received invalid JSON" in caplog.text


async def test_non_dict_response_gets_logged(
    ws_connection: WsConnection, caplog: pytest.LogCaptureFixture
) -> None:
    """Test that when the received message isn't a dictionary, this is logged."""
    _LOGGER: logging.Logger = logging.getLogger(
        "homeassistant.components.minecraft_server.msmp.ws_connection"
    )
    original_logger = _LOGGER.error
    logged = asyncio.Event()

    def mark_as_logged(*args, **kwargs):
        original_logger(*args, **kwargs)
        logged.set()

    await ws_connection.connect()
    ws = ws_connection._ws

    with (
        patch.object(_LOGGER, "error", side_effect=mark_as_logged),
        caplog.at_level(logging.ERROR, _LOGGER.name),
    ):
        task1 = asyncio.create_task(ws_connection.call("minecraft:allowlist"))

        await ws.wait_for_n_outgoing_messages(1)
        await ws.send_raw_from_server(
            aiohttp.WSMessage(
                type=aiohttp.WSMsgType.TEXT,
                data='["VALID JSON", "BUT NOT A DICT"]',
                extra=None,
            )
        )

        await asyncio.wait_for(logged.wait(), timeout=1)
        task1.cancel()

    assert "Unexpected response format" in caplog.text


async def test_duplicate_replies_have_no_impact(ws_connection: WsConnection) -> None:
    """Test that when a response gets sent twice, the future doesn't get resolved twice.

    According to the asyncio documentation, calling `set_result()` multiple times raises an `InvalidStateError`. Thus, if we can reach the end of the test method without an exception being thrown at us, we've successfully ignored the duplicate reply.
    """
    await ws_connection.connect()
    ws = ws_connection._ws

    task1 = asyncio.create_task(ws_connection.call("minecraft:allowlist"))

    await ws.wait_for_n_outgoing_messages(1)
    await ws.send_from_server(1, ["Player1"])
    await ws.send_from_server(1, ["Player1"])

    response = await asyncio.wait_for(task1, timeout=1)

    assert response == ["Player1"]


async def test_close_fails_pending_requests(
    ws: FakeWs, ws_connection: WsConnection
) -> None:
    """Test that closing the connection fails a call that is waiting for a reply."""
    await ws_connection.connect()
    call_task = asyncio.create_task(ws_connection.call("minecraft:allowlist"))
    await ws.outgoing.get()

    await ws_connection.close()

    with pytest.raises(MsmpConnectionError, match=r"Request \(ID: 1\) failed"):
        await asyncio.wait_for(call_task, timeout=1)
    assert ws_connection.pending_requests_count == 0


async def test_cancelled_call_propagates_cancellation(
    ws: FakeWs, ws_connection: WsConnection
) -> None:
    """Test that cancelling the calling task is not reported as a connection error."""
    await ws_connection.connect()
    call_task = asyncio.create_task(ws_connection.call("minecraft:allowlist"))
    await ws.outgoing.get()

    call_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await call_task
    assert ws_connection.pending_requests_count == 0


@pytest.mark.parametrize(
    "unmatched_message",
    [
        pytest.param('{"id": []}', id="unhashable-id"),
        pytest.param('{"id": null}', id="null-id"),
        pytest.param('{"id": 99, "result": []}', id="unknown-id"),
        pytest.param(
            '{"jsonrpc": "2.0", "method": "minecraft:notification/players/joined",'
            ' "params": [{"id": "853c80ef-3c37-49fd-aa49-938b674adae6",'
            ' "name": "jeb_"}]}',
            id="notification",
        ),
        pytest.param("not json", id="invalid-json"),
        pytest.param('["not", "a", "dict"]', id="non-dict"),
    ],
)
async def test_unmatched_message_does_not_stop_listener(
    unmatched_message: str, ws: FakeWs, ws_connection: WsConnection
) -> None:
    """Test that a message matching no request does not stop later replies."""
    await ws_connection.connect()
    call_task = asyncio.create_task(ws_connection.call("minecraft:allowlist"))
    request = await ws.outgoing.get()

    await ws.send_raw_from_server(
        aiohttp.WSMessage(
            type=aiohttp.WSMsgType.TEXT, data=unmatched_message, extra=None
        )
    )
    await ws.send_from_server(request["id"], ["Player1"])

    assert await asyncio.wait_for(call_task, timeout=1) == ["Player1"]


@pytest.fixture
async def fast_timeout_connection(
    session: MagicMock,
) -> AsyncGenerator[WsConnection]:
    """Return a connected WsConnection whose requests time out quickly."""
    connection = WsConnection(session, HOST, PORT, SECRET, request_timeout=0.05)
    await connection.connect()

    yield connection

    await connection.close()


async def test_late_reply_after_timeout_is_ignored(
    ws: FakeWs, fast_timeout_connection: WsConnection
) -> None:
    """Test that a reply arriving after its call timed out does not break later calls."""
    with pytest.raises(MsmpConnectionError, match=r"Request \(ID: 1\)"):
        await fast_timeout_connection.call("minecraft:allowlist")
    timed_out_request = await ws.outgoing.get()
    assert timed_out_request["id"] == 1

    await ws.send_from_server(1, ["Player1"])
    call_task = asyncio.create_task(fast_timeout_connection.call("minecraft:ban"))
    request = await ws.outgoing.get()
    await ws.send_from_server(request["id"], ["Herobrine"])

    assert await asyncio.wait_for(call_task, timeout=1) == ["Herobrine"]
    assert fast_timeout_connection.pending_requests_count == 0


async def test_close_logs_no_error(
    ws_connection: WsConnection, caplog: pytest.LogCaptureFixture
) -> None:
    """Test that a normal close does not log at error level."""
    await ws_connection.connect()

    with caplog.at_level(logging.ERROR):
        await ws_connection.close()

    assert caplog.records == []


async def test_concurrent_connects_open_one_socket(
    ws: FakeWs, session: MagicMock, ws_connection: WsConnection
) -> None:
    """Test that two concurrent connect calls share one socket."""

    async def connect_after_yielding(*args: object, **kwargs: object) -> FakeWs:
        await asyncio.sleep(0)
        return ws

    session.ws_connect.side_effect = connect_after_yielding

    await asyncio.gather(ws_connection.connect(), ws_connection.connect())

    assert session.ws_connect.call_count == 1
