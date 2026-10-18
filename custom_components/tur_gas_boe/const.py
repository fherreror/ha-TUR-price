"""Constants for the official Spanish regulated natural gas price integration."""
DOMAIN = "tur_gas_boe"
CONF_TARIFF = "tariff"
TARIFFS = [f"TUR.{i}" for i in range(1, 12)]
BOE_SUMMARY = "https://www.boe.es/datosabiertos/api/boe/sumario/{date}"
BOE_XML = "https://www.boe.es/diario_boe/xml.php?id={id}"
BOE_DOC = "https://www.boe.es/buscar/doc.php?id={id}"
STORE_VERSION = 1
UPDATE_HOURS = 12

CONF_ENAGAS_ZONE = "enagas_zone"
CONF_ENAGAS_PRESSURE = "enagas_pressure"
CONF_ENAGAS_READING = "enagas_reading_type"
