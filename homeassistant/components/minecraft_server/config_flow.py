"""Config flow for Minecraft Server integration."""

import logging
from typing import Any, override

import probatio
from yarl import URL

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS, CONF_TYPE
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import MinecraftServer, MinecraftServerAddressError, MinecraftServerType
from .const import (
    CONF_MANAGEMENT_HOST,
    CONF_MANAGEMENT_PORT,
    CONF_MANAGEMENT_SECRET,
    CONF_MANAGEMENT_TLS,
    DEFAULT_MANAGEMENT_PORT,
    DOMAIN,
)
from .msmp.client import MsmpClient
from .msmp.exceptions import MsmpAuthError, MsmpConnectionError, MsmpRequestError

DEFAULT_ADDRESS = "localhost:25565"

_LOGGER = logging.getLogger(__name__)


class MinecraftServerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Minecraft Server."""

    VERSION = 3

    def __init__(self) -> None:
        """Initialize the config flow."""
        super().__init__()
        self._config_data: dict[str, Any] = {}

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input:
            address = user_input[CONF_ADDRESS]

            # Abort config flow if service is already configured.
            self._async_abort_entries_match({CONF_ADDRESS: address})

            # Prepare config entry data.
            config_data = {
                CONF_ADDRESS: address,
            }

            # Some Bedrock Edition servers mimic a Java Edition
            # server, therefore check for Bedrock Edition first.
            for server_type in MinecraftServerType:
                api = MinecraftServer(self.hass, server_type, address)

                try:
                    await api.async_initialize()
                except MinecraftServerAddressError as error:
                    _LOGGER.debug(
                        "Initialization of %s server failed: %s",
                        server_type,
                        error,
                    )
                else:
                    if await api.async_is_online():
                        config_data[CONF_TYPE] = server_type

                        # Only Java Edition has a management protocol.
                        if server_type is MinecraftServerType.JAVA_EDITION:
                            self._config_data = config_data
                            return await self.async_step_management()

                        return self.async_create_entry(title=address, data=config_data)

            # Host or port invalid or server not reachable.
            errors["base"] = "cannot_connect"

        # Show configuration form (default form in case of no user_input,
        # form filled with user_input and eventually with errors otherwise).
        return self._show_config_form(user_input, errors)

    async def async_step_management(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Optionally add a management protocol connection."""
        errors: dict[str, str] = {}
        address = self._config_data[CONF_ADDRESS]

        if user_input is not None:
            secret = user_input.get(CONF_MANAGEMENT_SECRET)

            # No secret means the user skipped the management connection.
            if not secret:
                return self.async_create_entry(title=address, data=self._config_data)

            host = user_input[CONF_MANAGEMENT_HOST]
            port = int(user_input[CONF_MANAGEMENT_PORT])
            tls = user_input[CONF_MANAGEMENT_TLS]

            client = MsmpClient(
                async_get_clientsession(self.hass), host, port, secret, tls=tls
            )

            try:
                await client.connect()
                await client.call("minecraft:server/status")
            except MsmpAuthError:
                errors["base"] = "invalid_auth"
            except MsmpConnectionError as error:
                _LOGGER.debug("Management connection failed: %s", error)
                errors["base"] = "management_cannot_connect"
            except MsmpRequestError as error:
                _LOGGER.debug("Management status request failed: %s", error)
                errors["base"] = "management_request_failed"
            else:
                _LOGGER.debug("Management connection to %s:%s succeeded", host, port)
                return self.async_create_entry(
                    title=address,
                    data={
                        **self._config_data,
                        CONF_MANAGEMENT_HOST: host,
                        CONF_MANAGEMENT_PORT: port,
                        CONF_MANAGEMENT_SECRET: secret,
                        CONF_MANAGEMENT_TLS: tls,
                    },
                )
            finally:
                await client.close()
        return self._show_management_form(user_input, errors)

    def _show_config_form(
        self,
        user_input: dict[str, Any] | None = None,
        errors: dict[str, str] | None = None,
    ) -> ConfigFlowResult:
        """Show the setup form to the user."""
        if user_input is None:
            user_input = {}

        return self.async_show_form(
            step_id="user",
            data_schema=probatio.Schema(
                {
                    probatio.Required(
                        CONF_ADDRESS,
                        default=user_input.get(CONF_ADDRESS, DEFAULT_ADDRESS),
                    ): probatio.All(str, probatio.Lower),
                }
            ),
            errors=errors,
            description_placeholders={"minimum_minecraft_version": "1.4"},
        )

    def _show_management_form(
        self,
        user_input: dict[str, Any] | None = None,
        errors: dict[str, str] | None = None,
    ) -> ConfigFlowResult:
        """Show the management connection form to the user."""
        if user_input is None:
            user_input = {}

        address = self._config_data[CONF_ADDRESS]
        default_host = URL(f"//{address}").host or address

        return self.async_show_form(
            step_id="management",
            data_schema=probatio.Schema(
                {
                    probatio.Required(
                        CONF_MANAGEMENT_HOST,
                        default=user_input.get(CONF_MANAGEMENT_HOST, default_host),
                    ): TextSelector(),
                    probatio.Required(
                        CONF_MANAGEMENT_PORT,
                        default=user_input.get(
                            CONF_MANAGEMENT_PORT, DEFAULT_MANAGEMENT_PORT
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1, max=65535, mode=NumberSelectorMode.BOX
                        )
                    ),
                    probatio.Optional(CONF_MANAGEMENT_SECRET): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                    probatio.Required(
                        CONF_MANAGEMENT_TLS,
                        default=user_input.get(CONF_MANAGEMENT_TLS, False),
                    ): BooleanSelector(),
                }
            ),
            errors=errors,
        )
