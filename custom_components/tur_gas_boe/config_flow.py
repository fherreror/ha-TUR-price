"""UI configuration for BOE TUR and optional official Enagás gas factor."""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_TARIFF, DOMAIN, TARIFFS, CONF_ENAGAS_ZONE,
    CONF_ENAGAS_PRESSURE, CONF_ENAGAS_READING,
)
from .enagas import get_zones, READING_TYPES, valid_zone, valid_pressure

_LOGGER = logging.getLogger(__name__)


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
                vol.Required(CONF_TARIFF, default="TUR.1"): vol.In(TARIFFS),
            }),
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        return TurGasOptionsFlow()


class TurGasOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        options = self.config_entry.options
        selected = options.get(CONF_ENAGAS_ZONE, "")
        choices = {"": "Desactivado (solo tarifa BOE)"}
        try:
            choices.update(await get_zones(async_get_clientsession(self.hass)))
        except Exception as err:
            _LOGGER.warning("Unable to retrieve Enagás municipality list: %s", err)
            if selected:
                choices[selected] = f"Actual ({selected})"
        errors = {}
        if user_input is not None:
            zone = user_input.get(CONF_ENAGAS_ZONE, "")
            if zone:
                try:
                    valid_zone(zone)
                    valid_pressure(user_input[CONF_ENAGAS_PRESSURE])
                except (ValueError, TypeError, KeyError):
                    errors[CONF_ENAGAS_PRESSURE] = "invalid_pressure"
            if not errors:
                if not zone:
                    user_input.pop(CONF_ENAGAS_PRESSURE, None)
                    user_input.pop(CONF_ENAGAS_READING, None)
                return self.async_create_entry(title="", data=user_input)
        tariff = options.get(CONF_TARIFF, self.config_entry.data[CONF_TARIFF])
        return self.async_show_form(
            step_id="init",
            errors=errors,
            data_schema=vol.Schema({
                vol.Required(CONF_TARIFF, default=tariff): vol.In(TARIFFS),
                vol.Required(CONF_ENAGAS_ZONE, default=selected): vol.In(choices),
                vol.Optional(
                    CONF_ENAGAS_PRESSURE,
                    default=options.get(CONF_ENAGAS_PRESSURE, 22),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=500)),
                vol.Optional(
                    CONF_ENAGAS_READING,
                    default=options.get(CONF_ENAGAS_READING, "Bimestral"),
                ): vol.In(READING_TYPES),
            }),
        )
