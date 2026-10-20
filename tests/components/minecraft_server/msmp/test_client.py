"""Tests for the MSMP client."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from yarl import URL

from homeassistant.components.minecraft_server.msmp.client import MsmpClient
from homeassistant.components.minecraft_server.msmp.exceptions import (
    MsmpAuthError,
    MsmpConnectionError,
    MsmpResponseError,
)

HOST = "localhost"
PORT = 25585
SECRET = "super-secret-token"


def mock_ws_response(
    id: int, output: dict[str, any] | list[any] | None = None
) -> AsyncMock:
    """Create a mock message for the mocked websocket server."""
    return AsyncMock(
        return_value=aiohttp.WSMessage(
            type=aiohttp.WSMsgType.TEXT,
            data=json.dumps(
                {
                    "id": id,
                    "result": output,
                }
            ),
            extra=None,
        )
    )


@pytest.fixture
def ws() -> MagicMock:
    """Return a mock WebSocket that flips `closed` when closed."""
    mock_ws = MagicMock(spec=aiohttp.ClientWebSocketResponse)
    mock_ws.closed = False

    received_messages = {}

    async def _close(*args: object, **kwargs: object) -> bool:
        mock_ws.closed = True
        return True

    def _send_json(*args, **kwargs):
        request: object = args[0]

        received_messages[request["id"]] = request

    # def _receive():
    #     return mock_server_message(1, [])
    #     # return aiohttp.WSMessage(
    #     #     type=aiohttp.WSMsgType.TEXT,
    #     #     data=json.dumps(
    #     #         {
    #     #             "id": 1,
    #     #             "result": [],
    #     #         }
    #     #     ),
    #     #     extra=None,
    #     # )

    mock_ws.send_json = AsyncMock(side_effect=_send_json)
    mock_ws.close = AsyncMock(side_effect=_close)
    return mock_ws


@pytest.fixture
def session(ws: MagicMock) -> MagicMock:
    """Return a mock aiohttp session whose ws_connect returns the mock socket."""
    mock_session = MagicMock(spec=aiohttp.ClientSession)
    mock_session.ws_connect = AsyncMock(return_value=ws)
    return mock_session


@pytest.fixture
def client(session: MagicMock) -> MsmpClient:
    """Return a client using the mock session."""
    return MsmpClient(session, HOST, PORT, SECRET)


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
    client = MsmpClient(session, HOST, PORT, SECRET, tls=True)

    await client.connect()

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


async def test_connect_timeout(session: MagicMock) -> None:
    """Test a hanging handshake is cut off by the connect timeout."""

    async def _hang(*args: object, **kwargs: object) -> None:
        await asyncio.sleep(10)

    session.ws_connect.side_effect = _hang
    client = MsmpClient(session, HOST, PORT, SECRET, connect_timeout=0.01)

    with pytest.raises(MsmpConnectionError):
        await client.connect()

    assert not client.connected


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
    new_ws = MagicMock(spec=aiohttp.ClientWebSocketResponse)
    new_ws.closed = False
    session.ws_connect.return_value = new_ws

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


async def test_ws_request_response(
    client: MsmpClient,
    ws: MagicMock,
) -> None:
    """Test a single WS request gets a response."""
    await client.connect()

    ws.receive = mock_ws_response(1, [])

    response = await client.call("minecraft:allowlist")

    assert response == []


@pytest.mark.parametrize("id", [-2, -1, 0, 2, 3, 4])
async def test_ws_request_response_catches_unexpected_ids(
    id: int, client: MsmpClient, ws: MagicMock
) -> None:
    """Tests that unexpected IDs get caught."""
    await client.connect()

    ws.receive = mock_ws_response(id)

    with pytest.raises(MsmpResponseError) as error:
        await client.call("minecraft:allowlist")

    assert f"Unexpectedly received response for request {id} -- waiting for 1" in str(
        error.value
    )


async def test_ws_multiple_sequential_request_response(
    client: MsmpClient,
    ws: MagicMock,
) -> None:
    """Test that each of the multiple WS requests each gets the expected response when the responses are sent in order."""
    await client.connect()

    ws.receive = mock_ws_response(1, [])
    response1 = await client.call("minecraft:allowlist")

    ws.receive = mock_ws_response(2, ["Player1", "Herobrine"])
    response2 = await client.call("minecraft:ban")

    ws.receive = mock_ws_response(3, {"players": [], "started": True})
    response3 = await client.call("minecraft:server/status")

    assert response1 == []
    assert response2 == ["Player1", "Herobrine"]
    assert response3 == {"players": [], "started": True}


# # TODO: Figure out how to "send" the replies asynchronously.
# async def test_ws_multiple_asnychronous_request_response(
#     client: MsmpClient,
#     ws: MagicMock,
# ) -> None:
#     """Test that each of the multiple WS requests gets the expected response, even when the responses are sent out of order."""
#     await client.connect()

#     ws.receive = mock_ws_response(1, [])
#     ws.receive = mock_ws_response(3, {"players": [], "started": True})
#     ws.receive = mock_ws_response(2, ["Player1", "Herobrine"])

#     response1 = await client.call("minecraft:allowlist")
#     response2 = await client.call("minecraft:ban")
#     response3 = await client.call("minecraft:server/status")

#     assert response1 == []
#     assert response2 == ["Player1", "Herobrine"]
#     assert response3 == {"players": [], "started": True}
