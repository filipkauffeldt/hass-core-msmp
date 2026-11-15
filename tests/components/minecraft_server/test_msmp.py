"""Tests for the MSMP part of the Minecraft Server integration.

The MSMP server is mocked: MsmpClient is patched where the coordinator creates
it, next to the usual JavaServer patches. The tests assert on Home Assistant
entity and config entry states, not on calls to the mock.
"""

from collections.abc import Generator
from datetime import timedelta
from unittest.mock import MagicMock, patch

from freezegun.api import FrozenDateTimeFactory
from mcstatus import JavaServer
import pytest

from homeassistant.components.minecraft_server.api import MinecraftServerType
from homeassistant.components.minecraft_server.const import (
    CONF_MANAGEMENT_HOST,
    CONF_MANAGEMENT_PORT,
    CONF_MANAGEMENT_SECRET,
    CONF_MANAGEMENT_TLS,
    DEFAULT_MANAGEMENT_PORT,
    DOMAIN,
)
from homeassistant.components.minecraft_server.msmp.exceptions import (
    MsmpAuthError,
    MsmpConnectionError,
    MsmpRequestError,
)
from homeassistant.components.minecraft_server.msmp.models import (
    MsmpGameMode,
    MsmpServerStatus,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ADDRESS, CONF_TYPE, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant

from .const import (
    TEST_ADDRESS,
    TEST_CONFIG_ENTRY_ID,
    TEST_HOST,
    TEST_JAVA_STATUS_RESPONSE,
    TEST_PORT,
)

from tests.common import MockConfigEntry, async_fire_time_changed

GAME_MODE_ENTITY = "sensor.mc_dummyserver_com_25566_msmp_game_mode"
LATENCY_ENTITY = "sensor.mc_dummyserver_com_25566_latency"


@pytest.fixture
def msmp_mock_config_entry() -> MockConfigEntry:
    """Create a Java Edition config entry with MSMP management settings."""
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=None,
        entry_id=TEST_CONFIG_ENTRY_ID,
        title=TEST_ADDRESS,
        data={
            CONF_ADDRESS: TEST_ADDRESS,
            CONF_TYPE: MinecraftServerType.JAVA_EDITION,
            CONF_MANAGEMENT_HOST: TEST_HOST,
            CONF_MANAGEMENT_PORT: DEFAULT_MANAGEMENT_PORT,
            CONF_MANAGEMENT_SECRET: "test-secret",
            CONF_MANAGEMENT_TLS: False,
        },
        version=3,
    )


@pytest.fixture
def mock_msmp_client() -> Generator[MagicMock]:
    """Patch MsmpClient where the coordinator creates it."""
    with patch(
        "homeassistant.components.minecraft_server.coordinator.MsmpClient",
        autospec=True,
    ) as client_cls:
        client = client_cls.return_value
        client.get_server_status.return_value = MsmpServerStatus(
            started=True,
            version_name="Dummy Version",
            protocol_version=123,
            players=[],
        )
        client.get_game_mode.return_value = MsmpGameMode(game_mode="survival")
        yield client_cls


@pytest.fixture
def mock_java_server() -> Generator[None]:
    """Patch the mcstatus Java server, like the existing tests do."""
    with (
        patch(
            "homeassistant.components.minecraft_server.api.JavaServer.async_lookup",
            return_value=JavaServer(host=TEST_HOST, port=TEST_PORT),
        ),
        patch(
            "homeassistant.components.minecraft_server.api.JavaServer.async_status",
            return_value=TEST_JAVA_STATUS_RESPONSE,
        ),
    ):
        yield


@pytest.mark.usefixtures("mock_java_server", "mock_msmp_client")
async def test_msmp_game_mode_sensor(
    hass: HomeAssistant, msmp_mock_config_entry: MockConfigEntry
) -> None:
    """With MSMP configured, the game mode sensor shows the value from MSMP."""
    msmp_mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(msmp_mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert msmp_mock_config_entry.state is ConfigEntryState.LOADED
    state = hass.states.get(GAME_MODE_ENTITY)
    assert state is not None
    assert state.state == "survival"


@pytest.mark.usefixtures("mock_java_server")
async def test_no_msmp_configured(
    hass: HomeAssistant,
    java_mock_config_entry: MockConfigEntry,
    mock_msmp_client: MagicMock,
) -> None:
    """Without MSMP settings, no client is created and mcstatus sensors still work.

    The game mode sensor is still created for Java Edition, with state unknown.
    This documents the current behaviour.
    """
    java_mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(java_mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert java_mock_config_entry.state is ConfigEntryState.LOADED
    mock_msmp_client.assert_not_called()
    assert hass.states.get(LATENCY_ENTITY).state == "5"
    assert hass.states.get(GAME_MODE_ENTITY).state == STATE_UNAVAILABLE


@pytest.mark.usefixtures("mock_java_server")
@pytest.mark.parametrize(
    ("error", "expected_state"),
    [
        (MsmpAuthError, ConfigEntryState.SETUP_ERROR),
        (MsmpConnectionError, ConfigEntryState.SETUP_RETRY),
    ],
)
async def test_msmp_setup_errors(
    hass: HomeAssistant,
    msmp_mock_config_entry: MockConfigEntry,
    mock_msmp_client: MagicMock,
    error: type[Exception],
    expected_state: ConfigEntryState,
) -> None:
    """A wrong secret fails setup; an unreachable server retries setup."""
    mock_msmp_client.return_value.connect.side_effect = error
    msmp_mock_config_entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(msmp_mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert msmp_mock_config_entry.state is expected_state


@pytest.mark.usefixtures("mock_java_server")
async def test_msmp_update_failure(
    hass: HomeAssistant,
    msmp_mock_config_entry: MockConfigEntry,
    mock_msmp_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """An MSMP error during an update only makes the MSMP sensor unavailable.

    The mcstatus sensors keep their values, since their data did not fail.
    """
    msmp_mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(msmp_mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(GAME_MODE_ENTITY).state == "survival"

    mock_msmp_client.return_value.get_game_mode.side_effect = MsmpRequestError
    freezer.tick(timedelta(minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass.states.get(GAME_MODE_ENTITY).state == STATE_UNAVAILABLE
    assert hass.states.get(LATENCY_ENTITY).state == "5"
