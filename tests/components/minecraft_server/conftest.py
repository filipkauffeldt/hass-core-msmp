"""Fixtures for Minecraft Server integration tests."""

from collections.abc import Generator
from unittest.mock import MagicMock, patch

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
from homeassistant.components.minecraft_server.msmp.models import (
    MsmpClientResponse,
    MsmpGameMode,
    MsmpServerSettings,
    MsmpServerStatus,
)
from homeassistant.const import CONF_ADDRESS, CONF_TYPE

from .const import (
    TEST_ADDRESS,
    TEST_CONFIG_ENTRY_ID,
    TEST_HOST,
    TEST_JAVA_STATUS_RESPONSE,
    TEST_PORT,
)

from tests.common import MockConfigEntry


@pytest.fixture
def java_mock_config_entry() -> MockConfigEntry:
    """Create Java Edition mock config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=None,
        entry_id=TEST_CONFIG_ENTRY_ID,
        title=TEST_ADDRESS,
        data={
            CONF_ADDRESS: TEST_ADDRESS,
            CONF_TYPE: MinecraftServerType.JAVA_EDITION,
        },
        version=3,
    )


@pytest.fixture
def bedrock_mock_config_entry() -> MockConfigEntry:
    """Create Bedrock Edition mock config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=None,
        entry_id=TEST_CONFIG_ENTRY_ID,
        title=TEST_ADDRESS,
        data={
            CONF_ADDRESS: TEST_ADDRESS,
            CONF_TYPE: MinecraftServerType.BEDROCK_EDITION,
        },
        version=3,
    )


@pytest.fixture
def legacy_java_mock_config_entry() -> MockConfigEntry:
    """Create legacy Java Edition mock config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=None,
        entry_id=TEST_CONFIG_ENTRY_ID,
        title=TEST_ADDRESS,
        data={
            CONF_ADDRESS: TEST_ADDRESS,
            CONF_TYPE: MinecraftServerType.LEGACY_JAVA_EDITION,
        },
        version=3,
    )


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
        client.get_all.return_value = MsmpClientResponse(
            server_status=MsmpServerStatus(
                started=True,
                version_name="Dummy Version",
                protocol_version=123,
                players=[],
            ),
            server_settings=MsmpServerSettings(
                game_mode=MsmpGameMode(game_mode="survival")
            ),
        )
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
