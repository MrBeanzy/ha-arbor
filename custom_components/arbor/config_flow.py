"""Config flow for Arbor Balance."""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ArborApi, ArborAuthError
from .const import (
    CONF_BASE_URL,
    CONF_PASSWORD,
    CONF_USERNAME,
    DEFAULT_BASE_URL,
    DOMAIN,
)


class ArborConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Ask for the school URL and the guardian's login."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            api = ArborApi(
                session,
                user_input[CONF_BASE_URL],
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
            )
            try:
                accounts = await api.get_balances()
            except ArborAuthError:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001
                errors["base"] = "cannot_connect"
            else:
                if not accounts:
                    errors["base"] = "no_accounts"
                else:
                    await self.async_set_unique_id(user_input[CONF_USERNAME].lower())
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=f"Arbor ({user_input[CONF_USERNAME]})",
                        data=user_input,
                    )

        schema = vol.Schema(
            {
                vol.Required(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
