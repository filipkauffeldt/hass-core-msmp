"""The Minecraft Server integration."""

from datetime import timedelta
import logging
from typing import override

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_TYPE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    MinecraftServer,
    MinecraftServerAddressError,
    MinecraftServerData,
    MinecraftServerDataError,
    MinecraftServerType,
)
from .const import (
    CONF_MANAGEMENT_HOST,
    CONF_MANAGEMENT_PORT,
    CONF_MANAGEMENT_SECRET,
    CONF_MANAGEMENT_TLS,
)
from .msmp.client import MsmpClient
from .msmp.exceptions import MsmpAuthError, MsmpConnectionError

type MinecraftServerConfigEntry = ConfigEntry[MinecraftServerCoordinator]

SCAN_INTERVAL = timedelta(seconds=60)

_LOGGER = logging.getLogger(__name__)


class MinecraftServerCoordinator(DataUpdateCoordinator[MinecraftServerData]):
    """Minecraft Server data update coordinator."""

    config_entry: MinecraftServerConfigEntry
    _api: MinecraftServer
    client: MsmpClient | None = None

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: MinecraftServerConfigEntry,
    ) -> None:
        """Initialize coordinator instance."""

        super().__init__(
            hass=hass,
            name=config_entry.title,
            config_entry=config_entry,
            logger=_LOGGER,
            update_interval=SCAN_INTERVAL,
        )

    @override
    async def _async_setup(self) -> None:
        """Set up the Minecraft Server data coordinator."""

        if (
            self.config_entry.data.get(CONF_TYPE, None)
            == MinecraftServerType.JAVA_EDITION
            and CONF_MANAGEMENT_HOST in self.config_entry.data
        ):
            self.client = MsmpClient(
                async_get_clientsession(self.hass),
                self.config_entry.data[CONF_MANAGEMENT_HOST],
                self.config_entry.data[CONF_MANAGEMENT_PORT],
                self.config_entry.data[CONF_MANAGEMENT_SECRET],
                tls=self.config_entry.data[CONF_MANAGEMENT_TLS],
            )

        # Create API instance.
        self._api = MinecraftServer(
            self.hass,
            self.config_entry.data.get(CONF_TYPE, MinecraftServerType.JAVA_EDITION),
            self.config_entry.data[CONF_ADDRESS],
            self.client,
        )

        # Initialize API instance.
        try:
            await self._api.async_initialize()
        except MinecraftServerAddressError as error:
            raise ConfigEntryNotReady(f"Initialization failed: {error}") from error
        except MsmpAuthError as err:
            raise ConfigEntryAuthFailed from err
        except MsmpConnectionError as err:
            raise ConfigEntryNotReady from err

    @override
    async def _async_update_data(self) -> MinecraftServerData:
        """Get updated data from the server."""
        try:
            return await self._api.async_get_data()
        except MinecraftServerDataError as error:
            raise UpdateFailed(error) from error
