"""Sensor values for the selected official TUR gas tariff."""
from __future__ import annotations

from datetime import date

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import BOE_DOC, CONF_TARIFF, CONF_ENAGAS_ZONE, DOMAIN
from .enagas import SOURCE_URL
from .coordinator import TurGasCoordinator


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator: TurGasCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        TurPriceSensor(coordinator, entry, "variable"),
        TurPriceSensor(coordinator, entry, "fixed"),
        TurDateSensor(coordinator, entry, "valid_from"),
        TurDateSensor(coordinator, entry, "last_checked"),
    ]
    if entry.options.get(CONF_ENAGAS_ZONE):
        entities.extend([TurConversionSensor(coordinator, entry), TurVolumePriceSensor(coordinator, entry)])
    async_add_entities(entities)


class TurBaseSensor(CoordinatorEntity[TurGasCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: TurGasCoordinator, entry, key: str):
        super().__init__(coordinator)
        self.entry = entry
        self.key = key
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_{key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, "official_tur")},
            "name": "Tarifa de último recurso de gas",
            "manufacturer": "BOE (fuente oficial)",
        }

    @property
    def tariff(self) -> str:
        return self.entry.options.get(
            CONF_TARIFF, self.entry.data[CONF_TARIFF]
        )

    @property
    def extra_state_attributes(self):
        record = self.coordinator.current()
        if record is None:
            return None
        return {
            "tur": self.tariff,
            "boe_id": record.boe_id,
            "boe_url": BOE_DOC.format(id=record.boe_id),
            "valid_from": record.effective.isoformat(),
            "source": "Boletín Oficial del Estado",
            "polling_problem": self.coordinator.problem,
        }


class TurPriceSensor(TurBaseSensor):
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry, key):
        super().__init__(coordinator, entry, key)
        if key == "variable":
            self._attr_name = "Precio variable"
            self._attr_icon = "mdi:currency-eur"
            self._attr_native_unit_of_measurement = "€/kWh"
            self._attr_suggested_display_precision = 8
        else:
            self._attr_name = "Término fijo"
            self._attr_icon = "mdi:calendar-month"
            self._attr_native_unit_of_measurement = "€/mes"
            self._attr_suggested_display_precision = 2

    @property
    def native_value(self):
        record = self.coordinator.current()
        if record is None or self.tariff not in record.prices:
            return None
        price = record.prices[self.tariff]
        return (
            price.variable_eur_kwh if self.key == "variable"
            else price.fixed_eur_month
        )


class TurDateSensor(TurBaseSensor):
    _attr_device_class = SensorDeviceClass.DATE

    def __init__(self, coordinator, entry, key):
        super().__init__(coordinator, entry, key)
        self._attr_name = (
            "Vigencia desde" if key == "valid_from"
            else "BOE última comprobación"
        )
        self._attr_icon = "mdi:calendar-check"

    @property
    def native_value(self) -> date | None:
        if self.key == "last_checked":
            return self.coordinator.last_checked
        record = self.coordinator.current()
        return record.effective if record else None


class TurConversionSensor(TurBaseSensor):
    _attr_name = "Factor conversión Enagás"
    _attr_icon = "mdi:gas-cylinder"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "kWh/m³"
    _attr_suggested_display_precision = 7

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "enagas_factor")

    @property
    def native_value(self):
        factor = self.coordinator.current_conversion()
        return factor.value if factor else None

    @property
    def extra_state_attributes(self):
        factor = self.coordinator.conversion
        return {
            "source": "Enagás", "source_url": SOURCE_URL,
            "valid_for_date": factor.measured_on.isoformat() if factor else None,
            "zone": factor.zone if factor else None,
            "pressure_mbar": factor.pressure_mbar if factor else None,
            "reading_type": factor.reading_type if factor else None,
            "problem": self.coordinator.enagas_problem,
            "stale": factor is not None and self.coordinator.current_conversion() is None,
        }


class TurVolumePriceSensor(TurBaseSensor):
    _attr_name = "Precio variable por volumen"
    _attr_icon = "mdi:currency-eur"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "€/m³"
    _attr_suggested_display_precision = 6

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "variable_m3")

    @property
    def native_value(self):
        record = self.coordinator.current()
        factor = self.coordinator.current_conversion()
        if record is None or factor is None or self.tariff not in record.prices:
            return None
        return record.prices[self.tariff].variable_eur_kwh * factor.value

    @property
    def extra_state_attributes(self):
        attrs = super().extra_state_attributes or {}
        factor = self.coordinator.conversion
        return {**attrs, "estimation": True, "conversion_source": SOURCE_URL,
                "conversion_date": factor.measured_on.isoformat() if factor else None,
                "enagas_problem": self.coordinator.enagas_problem,
                "stale": factor is not None and self.coordinator.current_conversion() is None}
