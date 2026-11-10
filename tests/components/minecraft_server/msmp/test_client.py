"""Tests for the MSMP client."""

import asyncio
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from yarl import URL

from homeassistant.components.minecraft_server.msmp.client import MsmpClient
from homeassistant.components.minecraft_server.msmp.exceptions import (
    MsmpAuthError,
    MsmpConnectionError,
    MsmpRequestError,
)
from homeassistant.components.minecraft_server.msmp.models import (
    MsmpPlayer,
    MsmpServerStatus,
)

from .test_ws_connection import FakeWs

HOST = "localhost"
PORT = 25585
SECRET = "super-secret-token"


def create_mock_ws() -> MagicMock:
    """Return a mock WebSocket that flips `closed` when closed and never receives."""
    mock_ws = MagicMock(spec=aiohttp.ClientWebSocketResponse)
    mock_ws.closed = False

    async def _close(*args: object, **kwargs: object) -> bool:
        mock_ws.closed = True
        return True

    async def _receive_forever(*args: object, **kwargs: object) -> None:
        await asyncio.Event().wait()

    mock_ws.close = AsyncMock(side_effect=_close)
    mock_ws.receive = AsyncMock(side_effect=_receive_forever)
    return mock_ws


@pytest.fixture
def ws() -> MagicMock:
    """Return a mock WebSocket."""
    return create_mock_ws()


@pytest.fixture
def session(ws: MagicMock) -> MagicMock:
    """Return a mock aiohttp session whose ws_connect returns the mock socket."""
    mock_session = MagicMock(spec=aiohttp.ClientSession)
    mock_session.ws_connect = AsyncMock(return_value=ws)
    return mock_session


@pytest.fixture
async def client(session: MagicMock) -> AsyncGenerator[MsmpClient]:
    """Return a client using the mock session."""
    msmp_client = MsmpClient(session, HOST, PORT, SECRET)

    yield msmp_client

    await msmp_client.close()


@pytest.fixture
async def fake_ws_client() -> AsyncGenerator[tuple[MsmpClient, FakeWs]]:
    """Return a connected client together with the fake socket it talks to."""
    fake_ws = FakeWs()
    mock_session = MagicMock(spec=aiohttp.ClientSession)
    mock_session.ws_connect = AsyncMock(return_value=fake_ws)

    async with MsmpClient(mock_session, HOST, PORT, SECRET) as msmp_client:
        yield msmp_client, fake_ws


async def test_connect_sends_token_and_subprotocol(
    client: MsmpClient, session: MagicMock
) -> None:
    """Test connect uses the bearer token and the minecraft-v1 subprotocol."""
    await client.connect()

    session.ws_connect.assert_awaited_once()
    args, kwargs = session.ws_connect.call_args
    assert args[0] == URL(f"ws://{HOST}:{PORT}")
    assert kwargs["headers"] == {"Authorization": f"Bearer {SECRET}"}
    assert kwargs["protocols"] == ("minecraft-v1",)
    assert client.connected


async def test_connect_uses_wss_when_tls_enabled(session: MagicMock) -> None:
    """Test the wss scheme is used when TLS is enabled."""
    async with MsmpClient(session, HOST, PORT, SECRET, tls=True):
        assert session.ws_connect.call_args.args[0] == URL(f"wss://{HOST}:{PORT}")


async def test_connect_is_noop_when_already_connected(
    client: MsmpClient, session: MagicMock
) -> None:
    """Test calling connect twice only opens one socket."""
    await client.connect()
    await client.connect()

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
    client: MsmpClient, session: MagicMock, error: Exception
) -> None:
    """Test connection failures are wrapped in MsmpConnectionError."""
    session.ws_connect.side_effect = error

    with pytest.raises(MsmpConnectionError) as exc_info:
        await client.connect()

    assert exc_info.value.__cause__ is error
    assert not client.connected


async def test_connect_failure_does_not_leak_secret(
    client: MsmpClient, session: MagicMock
) -> None:
    """Test the secret never appears in the error message."""
    session.ws_connect.side_effect = aiohttp.ClientError("boom")

    with pytest.raises(MsmpConnectionError) as exc_info:
        await client.connect()

    assert SECRET not in str(exc_info.value)


async def test_close_closes_socket_once(client: MsmpClient, ws: MagicMock) -> None:
    """Test close shuts the socket and is safe to call repeatedly."""
    await client.connect()

    await client.close()
    await client.close()

    ws.close.assert_awaited_once()
    assert not client.connected


async def test_close_without_connect(client: MsmpClient, session: MagicMock) -> None:
    """Test close before connect is a harmless no-op."""
    await client.close()

    session.ws_connect.assert_not_awaited()
    assert not client.connected


async def test_reconnect_after_close(client: MsmpClient, session: MagicMock) -> None:
    """Test the client can connect again after being closed."""
    await client.connect()
    await client.close()

    # A real ws_connect returns a fresh socket on every call
    session.ws_connect.return_value = create_mock_ws()

    await client.connect()

    assert session.ws_connect.await_count == 2
    assert client.connected


async def test_connected_reflects_socket_closed_state(
    client: MsmpClient, ws: MagicMock
) -> None:
    """Test connected follows the socket's own closed flag."""
    await client.connect()
    ws.closed = True

    assert not client.connected


async def test_context_manager(client: MsmpClient, ws: MagicMock) -> None:
    """Test the async context manager connects and closes."""
    async with client as entered:
        assert entered is client
        assert client.connected

    ws.close.assert_awaited_once()
    assert not client.connected


async def test_connect_401_raises_auth_error(
    client: MsmpClient, session: MagicMock
) -> None:
    """Test a 401 handshake response is reported as an auth error."""
    session.ws_connect.side_effect = aiohttp.WSServerHandshakeError(
        MagicMock(), (), status=401
    )

    with pytest.raises(MsmpAuthError):
        await client.connect()

    assert not client.connected


async def test_connect_other_http_error_is_not_auth_error(
    client: MsmpClient, session: MagicMock
) -> None:
    """Test non-401 handshake failures stay generic connection errors."""
    session.ws_connect.side_effect = aiohttp.WSServerHandshakeError(
        MagicMock(), (), status=500
    )

    with pytest.raises(MsmpConnectionError) as exc_info:
        await client.connect()

    assert not isinstance(exc_info.value, MsmpAuthError)


async def test_concurrent_calls_get_their_own_replies_when_answered_out_of_order(
    fake_ws_client: tuple[MsmpClient, FakeWs],
) -> None:
    """Test each concurrent call receives the reply carrying its own ID."""
    client, fake_ws = fake_ws_client
    allowlist_call = asyncio.create_task(client.call("minecraft:allowlist"))
    bans_call = asyncio.create_task(client.call("minecraft:bans"))
    sent_requests = [await fake_ws.outgoing.get(), await fake_ws.outgoing.get()]
    request_ids = {request["method"]: request["id"] for request in sent_requests}

    await fake_ws.send_from_server(request_ids["minecraft:bans"], ["Herobrine"])
    await fake_ws.send_from_server(request_ids["minecraft:allowlist"], ["Player1"])

    allowlist, bans = await asyncio.wait_for(
        asyncio.gather(allowlist_call, bans_call), timeout=1
    )
    assert allowlist == ["Player1"]
    assert bans == ["Herobrine"]


async def test_call_raises_request_error_with_call_id_when_server_answers_with_error(
    fake_ws_client: tuple[MsmpClient, FakeWs],
) -> None:
    """Test a server error is raised for its own call and names the call ID."""
    client, fake_ws = fake_ws_client
    call = asyncio.create_task(client.call("minecraft:allowlist"))
    request = await fake_ws.outgoing.get()

    await fake_ws.send_error_from_server(request["id"], -32602, "Invalid parameters")

    with pytest.raises(
        MsmpRequestError,
        match=rf"Request \(ID: {request['id']}\) to minecraft:allowlist failed.*Invalid parameters",
    ):
        await asyncio.wait_for(call, timeout=1)


async def test_get_server_status_returns_typed_status(
    fake_ws_client: tuple[MsmpClient, FakeWs],
) -> None:
    """Test the status result is returned as a typed status."""
    client, fake_ws = fake_ws_client
    status_call = asyncio.create_task(client.get_server_status())
    request = await fake_ws.outgoing.get()

    await fake_ws.send_from_server(
        request["id"],
        {
            "started": True,
            "version": {"name": "1.21.9", "protocol": 773},
            "players": [{"id": "853c80ef-3c37-49fd-aa49-938b674adae6", "name": "jeb_"}],
        },
    )

    assert request["method"] == "minecraft:server/status"
    assert await asyncio.wait_for(status_call, timeout=1) == MsmpServerStatus(
        started=True,
        version_name="1.21.9",
        protocol_version=773,
        players=[MsmpPlayer(id="853c80ef-3c37-49fd-aa49-938b674adae6", name="jeb_")],
    )


@pytest.mark.parametrize(
    "malformed_result",
    [
        pytest.param({}, id="empty-object"),
        pytest.param({"started": True, "players": []}, id="missing-version"),
        pytest.param(
            {"started": True, "version": {"name": "1.21.9"}, "players": []},
            id="missing-protocol",
        ),
        pytest.param(
            {
                "started": True,
                "version": {"name": "1.21.9", "protocol": 773},
                "players": [{"name": "jeb_"}],
            },
            id="player-without-id",
        ),
        pytest.param(["not", "an", "object"], id="not-an-object"),
        pytest.param(None, id="no-result"),
    ],
)
async def test_get_server_status_raises_request_error_when_result_is_malformed(
    malformed_result: dict[str, object] | list[str] | None,
    fake_ws_client: tuple[MsmpClient, FakeWs],
) -> None:
    """Test a status result that does not fit the status shape is a request error."""
    client, fake_ws = fake_ws_client
    status_call = asyncio.create_task(client.get_server_status())
    request = await fake_ws.outgoing.get()

    await fake_ws.send_from_server(request["id"], malformed_result)

    with pytest.raises(MsmpRequestError, match="Unexpected server status response"):
        await asyncio.wait_for(status_call, timeout=1)
