"""Tests for the Minecraft Server config flow."""

from unittest.mock import MagicMock, patch

from mcstatus import BedrockServer, JavaServer, LegacyServer
import pytest

from homeassistant.components.minecraft_server.api import MinecraftServerType
from homeassistant.components.minecraft_server.const import (
    CONF_MANAGEMENT_HOST,
    CONF_MANAGEMENT_PORT,
    CONF_MANAGEMENT_SECRET,
    CONF_MANAGEMENT_TLS,
    DOMAIN,
)
from homeassistant.components.minecraft_server.msmp.exceptions import (
    MsmpAuthError,
    MsmpConnectionError,
    MsmpRequestError,
)
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_ADDRESS, CONF_TYPE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult, FlowResultType

from .const import (
    TEST_ADDRESS,
    TEST_BEDROCK_STATUS_RESPONSE,
    TEST_HOST,
    TEST_JAVA_STATUS_RESPONSE,
    TEST_LEGACY_JAVA_STATUS_RESPONSE,
    TEST_PORT,
)

from tests.common import MockConfigEntry

USER_INPUT = {
    CONF_ADDRESS: TEST_ADDRESS,
}

MANAGEMENT_INPUT = {
    CONF_MANAGEMENT_HOST: TEST_HOST,
    CONF_MANAGEMENT_PORT: 25585,
    CONF_MANAGEMENT_SECRET: "super-secret-token",
    CONF_MANAGEMENT_TLS: False,
}


@pytest.fixture
def mock_msmp_client() -> MagicMock:
    """Patch the MSMP client the config flow uses."""
    with patch(
        "homeassistant.components.minecraft_server.config_flow.MsmpClient",
        autospec=True,
    ) as mock_client_class:
        yield mock_client_class.return_value


async def _async_start_java_management_step(hass: HomeAssistant) -> FlowResult:
    """Walk a Java Edition flow up to the management step."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    with (
        patch(
            "homeassistant.components.minecraft_server.api.BedrockServer.lookup",
            side_effect=ValueError,
        ),
        patch(
            "homeassistant.components.minecraft_server.api.JavaServer.async_lookup",
            return_value=JavaServer(host=TEST_HOST, port=TEST_PORT),
        ),
        patch(
            "homeassistant.components.minecraft_server.api.JavaServer.async_status",
            return_value=TEST_JAVA_STATUS_RESPONSE,
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=USER_INPUT
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "management"
    return result


async def test_full_flow_java(hass: HomeAssistant) -> None:
    """Test config entry in case of a successful connection to a Java Edition server."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    with (
        patch(
            "homeassistant.components.minecraft_server.api.BedrockServer.lookup",
            side_effect=ValueError,
        ),
        patch(
            "homeassistant.components.minecraft_server.api.JavaServer.async_lookup",
            return_value=JavaServer(host=TEST_HOST, port=TEST_PORT),
        ),
        patch(
            "homeassistant.components.minecraft_server.api.JavaServer.async_status",
            return_value=TEST_JAVA_STATUS_RESPONSE,
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input=USER_INPUT,
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "management"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["title"] == USER_INPUT[CONF_ADDRESS]
        assert result["data"][CONF_ADDRESS] == TEST_ADDRESS
        assert result["data"][CONF_TYPE] == MinecraftServerType.JAVA_EDITION


async def test_full_flow_bedrock(hass: HomeAssistant) -> None:
    """Test config entry for successful Bedrock Edition connection."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    with (
        patch(
            "homeassistant.components.minecraft_server.api.BedrockServer.lookup",
            return_value=BedrockServer(host=TEST_HOST, port=TEST_PORT),
        ),
        patch(
            "homeassistant.components.minecraft_server.api.BedrockServer.async_status",
            return_value=TEST_BEDROCK_STATUS_RESPONSE,
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input=USER_INPUT,
        )

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["title"] == USER_INPUT[CONF_ADDRESS]
        assert result["data"][CONF_ADDRESS] == TEST_ADDRESS
        assert result["data"][CONF_TYPE] == MinecraftServerType.BEDROCK_EDITION


async def test_full_flow_legacy_java(hass: HomeAssistant) -> None:
    """Test config entry for successful legacy Java Edition connection."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    with (
        patch(
            "homeassistant.components.minecraft_server.api.BedrockServer.lookup",
            side_effect=ValueError,
        ),
        patch(
            "homeassistant.components.minecraft_server.api.JavaServer.async_lookup",
            side_effect=ValueError,
        ),
        patch(
            "homeassistant.components.minecraft_server.api.LegacyServer.async_lookup",
            return_value=LegacyServer(host=TEST_HOST, port=TEST_PORT),
        ),
        patch(
            "homeassistant.components.minecraft_server.api.LegacyServer.async_status",
            return_value=TEST_LEGACY_JAVA_STATUS_RESPONSE,
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input=USER_INPUT,
        )

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["title"] == USER_INPUT[CONF_ADDRESS]
        assert result["data"][CONF_ADDRESS] == TEST_ADDRESS
        assert result["data"][CONF_TYPE] == MinecraftServerType.LEGACY_JAVA_EDITION


async def test_service_already_configured_java(
    hass: HomeAssistant, java_mock_config_entry: MockConfigEntry
) -> None:
    """Test config flow abort if a Java Edition server is already configured."""
    java_mock_config_entry.add_to_hass(hass)

    with (
        patch(
            "homeassistant.components.minecraft_server.api.BedrockServer.lookup",
            side_effect=ValueError,
        ),
        patch(
            "homeassistant.components.minecraft_server.api.JavaServer.async_lookup",
            return_value=JavaServer(host=TEST_HOST, port=TEST_PORT),
        ),
        patch(
            "homeassistant.components.minecraft_server.api.JavaServer.async_status",
            return_value=TEST_JAVA_STATUS_RESPONSE,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input=USER_INPUT,
        )
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "already_configured"


async def test_service_already_configured_bedrock(
    hass: HomeAssistant, bedrock_mock_config_entry: MockConfigEntry
) -> None:
    """Test config flow abort if a Bedrock Edition server is already configured."""
    bedrock_mock_config_entry.add_to_hass(hass)

    with (
        patch(
            "homeassistant.components.minecraft_server.api.BedrockServer.lookup",
            return_value=BedrockServer(host=TEST_HOST, port=TEST_PORT),
        ),
        patch(
            "homeassistant.components.minecraft_server.api.BedrockServer.async_status",
            return_value=TEST_BEDROCK_STATUS_RESPONSE,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input=USER_INPUT,
        )
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "already_configured"


async def test_service_already_configured_legacy_java(
    hass: HomeAssistant, legacy_java_mock_config_entry: MockConfigEntry
) -> None:
    """Test config flow abort if a legacy Java Edition server is already configured."""
    legacy_java_mock_config_entry.add_to_hass(hass)

    with (
        patch(
            "homeassistant.components.minecraft_server.api.BedrockServer.lookup",
            side_effect=ValueError,
        ),
        patch(
            "homeassistant.components.minecraft_server.api.JavaServer.async_lookup",
            side_effect=ValueError,
        ),
        patch(
            "homeassistant.components.minecraft_server.api.LegacyServer.async_lookup",
            return_value=LegacyServer(host=TEST_HOST, port=TEST_PORT),
        ),
        patch(
            "homeassistant.components.minecraft_server.api.LegacyServer.async_status",
            return_value=TEST_LEGACY_JAVA_STATUS_RESPONSE,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input=USER_INPUT,
        )
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "already_configured"


async def test_recovery_java(hass: HomeAssistant) -> None:
    """Test config flow recovery with a Java Edition server."""
    with (
        patch(
            "homeassistant.components.minecraft_server.api.BedrockServer.lookup",
            side_effect=ValueError,
        ),
        patch(
            "homeassistant.components.minecraft_server.api.JavaServer.async_lookup",
            return_value=JavaServer(host=TEST_HOST, port=TEST_PORT),
        ),
        patch(
            "homeassistant.components.minecraft_server.api.JavaServer.async_status",
            side_effect=OSError,
        ),
        patch(
            "homeassistant.components.minecraft_server.api.LegacyServer.async_lookup",
            side_effect=ValueError,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input=USER_INPUT,
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}

    with (
        patch(
            "homeassistant.components.minecraft_server.api.BedrockServer.lookup",
            side_effect=ValueError,
        ),
        patch(
            "homeassistant.components.minecraft_server.api.JavaServer.async_lookup",
            return_value=JavaServer(host=TEST_HOST, port=TEST_PORT),
        ),
        patch(
            "homeassistant.components.minecraft_server.api.JavaServer.async_status",
            return_value=TEST_JAVA_STATUS_RESPONSE,
        ),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            flow_id=result["flow_id"], user_input=USER_INPUT
        )
        assert result2["type"] is FlowResultType.FORM
        assert result2["step_id"] == "management"

        result2 = await hass.config_entries.flow.async_configure(
            result2["flow_id"], user_input={}
        )

        assert result2["type"] is FlowResultType.CREATE_ENTRY
        assert result2["title"] == USER_INPUT[CONF_ADDRESS]
        assert result2["data"][CONF_ADDRESS] == TEST_ADDRESS
        assert result2["data"][CONF_TYPE] == MinecraftServerType.JAVA_EDITION


async def test_recovery_bedrock(hass: HomeAssistant) -> None:
    """Test config flow recovery with a Bedrock Edition server."""
    with (
        patch(
            "homeassistant.components.minecraft_server.api.BedrockServer.lookup",
            return_value=BedrockServer(host=TEST_HOST, port=TEST_PORT),
        ),
        patch(
            "homeassistant.components.minecraft_server.api.BedrockServer.async_status",
            side_effect=OSError,
        ),
        patch(
            "homeassistant.components.minecraft_server.api.JavaServer.async_lookup",
            side_effect=ValueError,
        ),
        patch(
            "homeassistant.components.minecraft_server.api.LegacyServer.async_lookup",
            side_effect=ValueError,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input=USER_INPUT,
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}

    with (
        patch(
            "homeassistant.components.minecraft_server.api.BedrockServer.lookup",
            return_value=BedrockServer(host=TEST_HOST, port=TEST_PORT),
        ),
        patch(
            "homeassistant.components.minecraft_server.api.BedrockServer.async_status",
            return_value=TEST_BEDROCK_STATUS_RESPONSE,
        ),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            flow_id=result["flow_id"], user_input=USER_INPUT
        )
        assert result2["type"] is FlowResultType.CREATE_ENTRY
        assert result2["title"] == USER_INPUT[CONF_ADDRESS]
        assert result2["data"][CONF_ADDRESS] == TEST_ADDRESS
        assert result2["data"][CONF_TYPE] == MinecraftServerType.BEDROCK_EDITION


async def test_recovery_legacy_java(hass: HomeAssistant) -> None:
    """Test config flow recovery with a legacy Java Edition server."""
    with (
        patch(
            "homeassistant.components.minecraft_server.api.BedrockServer.lookup",
            side_effect=ValueError,
        ),
        patch(
            "homeassistant.components.minecraft_server.api.JavaServer.async_lookup",
            side_effect=ValueError,
        ),
        patch(
            "homeassistant.components.minecraft_server.api.LegacyServer.async_lookup",
            return_value=LegacyServer(host=TEST_HOST, port=TEST_PORT),
        ),
        patch(
            "homeassistant.components.minecraft_server.api.LegacyServer.async_status",
            side_effect=OSError,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input=USER_INPUT,
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}

    with (
        patch(
            "homeassistant.components.minecraft_server.api.BedrockServer.lookup",
            side_effect=ValueError,
        ),
        patch(
            "homeassistant.components.minecraft_server.api.JavaServer.async_lookup",
            side_effect=ValueError,
        ),
        patch(
            "homeassistant.components.minecraft_server.api.LegacyServer.async_lookup",
            return_value=LegacyServer(host=TEST_HOST, port=TEST_PORT),
        ),
        patch(
            "homeassistant.components.minecraft_server.api.LegacyServer.async_status",
            return_value=TEST_LEGACY_JAVA_STATUS_RESPONSE,
        ),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            flow_id=result["flow_id"], user_input=USER_INPUT
        )
        assert result2["type"] is FlowResultType.CREATE_ENTRY
        assert result2["title"] == USER_INPUT[CONF_ADDRESS]
        assert result2["data"][CONF_ADDRESS] == TEST_ADDRESS
        assert result2["data"][CONF_TYPE] == MinecraftServerType.LEGACY_JAVA_EDITION


async def test_full_flow_java_with_management(
    hass: HomeAssistant, mock_msmp_client: MagicMock
) -> None:
    """Test a working management connection is stored in the entry."""
    result = await _async_start_java_management_step(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input=MANAGEMENT_INPUT
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_ADDRESS: TEST_ADDRESS,
        CONF_TYPE: MinecraftServerType.JAVA_EDITION,
        **MANAGEMENT_INPUT,
    }
    mock_msmp_client.get_server_status.assert_awaited_once()
    mock_msmp_client.close.assert_awaited_once()


@pytest.mark.parametrize(
    ("failing_method", "exception", "error"),
    [
        pytest.param("connect", MsmpAuthError, "invalid_auth", id="invalid-auth"),
        pytest.param(
            "connect",
            MsmpConnectionError,
            "management_cannot_connect",
            id="cannot-connect",
        ),
        pytest.param(
            "get_server_status",
            MsmpRequestError,
            "management_request_failed",
            id="status-request-fails",
        ),
    ],
)
async def test_management_errors(
    hass: HomeAssistant,
    mock_msmp_client: MagicMock,
    failing_method: str,
    exception: type[Exception],
    error: str,
) -> None:
    """Test each management failure is reported on the form and the client closed."""
    getattr(mock_msmp_client, failing_method).side_effect = exception
    result = await _async_start_java_management_step(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input=MANAGEMENT_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "management"
    assert result["errors"] == {"base": error}
    mock_msmp_client.close.assert_awaited_once()


async def test_management_error_keeps_secret(
    hass: HomeAssistant, mock_msmp_client: MagicMock
) -> None:
    """Test the secret is still in the form after an error, so a retry is not a skip."""
    mock_msmp_client.connect.side_effect = MsmpConnectionError
    result = await _async_start_java_management_step(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input=MANAGEMENT_INPUT
    )

    secret_key = next(
        key for key in result["data_schema"].schema if key == CONF_MANAGEMENT_SECRET
    )
    assert secret_key.description == {
        "suggested_value": MANAGEMENT_INPUT[CONF_MANAGEMENT_SECRET]
    }
