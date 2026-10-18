"""UI and options: each user selects the relevant TUR group."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries

from .const import CONF_TARIFF, DOMAIN, TARIFFS


class TurGasConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id("official_boe_tur")
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title="TUR Gas (BOE)", data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_TARIFF, default="TUR.1"): vol.In(TARIFFS)
            }),
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        return TurGasOptionsFlow()


class TurGasOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        tariff = self.config_entry.options.get(
            CONF_TARIFF, self.config_entry.data[CONF_TARIFF]
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_TARIFF, default=tariff): vol.In(TARIFFS)
            }),
        )
