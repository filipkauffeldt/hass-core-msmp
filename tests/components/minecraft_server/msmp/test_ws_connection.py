"""Tests for the WS connection wrapper."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

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


@pytest.fixture
async def ws() -> MagicMock:
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
async def session(ws: MagicMock) -> MagicMock:
    """Return a mock aiohttp session whose ws_connect returns the mock socket."""
    mock_session = MagicMock(spec=aiohttp.ClientSession)
    mock_session.ws_connect = AsyncMock(return_value=ws)

    yield mock_session

    await ws.close()


@pytest.fixture
async def ws_connection(session: MagicMock) -> WsConnection:
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


async def test_close_closes_socket_once(
    ws_connection: WsConnection, ws: MagicMock
) -> None:
    """Test close shuts the socket and is safe to call repeatedly."""
    await ws_connection.connect()

    await ws_connection.close()
    await ws_connection.close()

    ws.close.assert_awaited_once()
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
