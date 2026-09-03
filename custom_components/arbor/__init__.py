"""Arbor Balance: guardian meal-account balances as sensors."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ArborApi, ArborAuthError, ArborError
from .const import (
    CONF_BASE_URL,
    CONF_PASSWORD,
    CONF_USERNAME,
    DEFAULT_SCAN_MINUTES,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = async_get_clientsession(hass)
    api = ArborApi(
        session,
        entry.data[CONF_BASE_URL],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )

    async def _update() -> dict:
        try:
            return await api.get_data()
        except ArborAuthError as err:
            raise UpdateFailed(f"auth: {err}") from err
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(str(err)) from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=_update,
        update_interval=None,  # no auto-poll; driven by the "Arbor: balance fetch schedule" automation
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded
