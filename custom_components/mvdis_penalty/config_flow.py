"""Config flow for Taiwan MVDIS Penalty."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import BooleanSelector

from .client import MvdisBackendClient
from .const import CONF_NOTIFY, DEFAULT_NOTIFY, DOMAIN


class MvdisPenaltyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the Taiwan MVDIS Penalty config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Connect to the installed loopback backend add-on."""
        self._async_abort_entries_match({})
        errors: dict[str, str] = {}
        if user_input is not None:
            client = MvdisBackendClient(async_get_clientsession(self.hass))
            if await client.async_health():
                return self.async_create_entry(title="監理服務罰單", data={})
            errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required("confirm", default=True): BooleanSelector()}
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> MvdisPenaltyOptionsFlow:
        """Return the options flow."""
        return MvdisPenaltyOptionsFlow()


class MvdisPenaltyOptionsFlow(config_entries.OptionsFlowWithReload):
    """Handle integration options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_NOTIFY,
                        default=self.config_entry.options.get(
                            CONF_NOTIFY, DEFAULT_NOTIFY
                        ),
                    ): BooleanSelector()
                }
            ),
        )
