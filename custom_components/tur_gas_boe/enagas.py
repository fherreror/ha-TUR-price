"""Read official Enagás conversion factors through its public website data service.

The service is used by enagas.es but is not a formally documented stable API.
No user accounts, addresses, credentials, or arbitrary URLs are transmitted.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import json
import re
from typing import Any

BASE = (
    "https://www.enagas.es/content/enagas/es/gestion-tecnica-sistema/"
    "energy-data/informacion-comercial/factor-conversion-facturacion/"
    "jcr:content/responsiveGrid/container_copy_copy_/conversionfactor_cop"
)
TOWNS_URL = BASE + ".townitem.json"
PRESSURES_URL = BASE + ".presionlist.json"
FACTOR_URL = BASE + ".conversionfactordto.json"
SOURCE_URL = (
    "https://www.enagas.es/es/gestion-tecnica-sistema/energy-data/"
    "informacion-comercial/factor-conversion-facturacion/"
)
MAX_RESPONSE_BYTES = 1_000_000
CHUNK_SIZE = 64 * 1024
READING_TYPES = ("Mensual", "Bimestral")


@dataclass(frozen=True)
class ConversionFactor:
    """A factor valid for the requested invoice closing date and parameters."""

    value: Decimal
    measured_on: date
    zone: str
    pressure_mbar: int
    reading_type: str


def valid_zone(value: str) -> str:
    """Enagás municipality codes are identifiers, not URL fragments."""
    if not isinstance(value, str) or re.fullmatch(r"[0-9A-Z]{4,8}", value) is None:
        raise ValueError("Invalid Enagás supply-zone code")
    return value


def valid_pressure(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 500:
        raise ValueError("Invalid supply pressure (mbar)")
    return value


def _decimal(text: str) -> Decimal:
    if not isinstance(text, str):
        raise ValueError("Enagás returned no numeric factor")
    cleaned = text.replace(".", "").replace(",", ".").strip()
    if not re.fullmatch(r"\d+(?:\.\d+)?", cleaned):
        raise ValueError("Unexpected Enagás factor format")
    try:
        result = Decimal(cleaned)
    except InvalidOperation as err:
        raise ValueError("Invalid decimal factor") from err
    if not Decimal("5") < result < Decimal("20"):
        raise ValueError("Implausible gas conversion factor")
    return result


async def _get_json(session: Any, url: str, params: dict | None = None) -> Any:
    """Consume the *full* response, checking limits after each received chunk."""
    if url not in (TOWNS_URL, PRESSURES_URL, FACTOR_URL):
        raise ValueError("Unsupported Enagás URL")
    async with asyncio.timeout(12):
        async with session.get(
            url,
            params=params,
            headers={"Accept": "application/json"},
        ) as response:
            if response.status == 404:
                return None
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.content.iter_chunked(CHUNK_SIZE):
                if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise ValueError("Enagás response exceeded size limit")
                body.extend(chunk)
            try:
                return json.loads(body)
            except (ValueError, UnicodeDecodeError) as err:
                raise ValueError("Invalid JSON from Enagás") from err


async def get_zones(session: Any) -> dict[str, str]:
    """Municipality + distributor-specific supply zones, as published by Enagás."""
    data = await _get_json(session, TOWNS_URL)
    if not isinstance(data, list):
        raise ValueError("Enagás did not return a municipality list")
    zones = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        code = entry.get("codigo")
        name = entry.get("nombre")
        province = entry.get("provincia")
        try:
            valid_zone(code)
        except ValueError:
            continue
        if isinstance(name, str) and isinstance(province, str):
            zones[code] = f"{name} ({province})"
    if not zones:
        raise ValueError("Enagás returned no usable supply zones")
    return zones


async def get_pressures(session: Any, zone: str, day: date) -> list[int]:
    """Pressure choices for the specific location and calendar date."""
    valid_zone(zone)
    date_str = day.strftime("%d/%m/%Y")
    data = await _get_json(
        session, PRESSURES_URL,
        {"municipio": zone, "fechaIni": date_str, "fechaFin": date_str},
    )
    if not isinstance(data, list):
        raise ValueError("Enagás did not return pressure options")
    values: list[int] = []
    for item in data:
        try:
            pressure = int(item)
            valid_pressure(pressure)
            if pressure not in values:
                values.append(pressure)
        except (TypeError, ValueError):
            continue
    return sorted(values)


async def get_factor(
    session: Any,
    zone: str,
    pressure_mbar: int,
    reading_type: str,
    day: date,
) -> ConversionFactor | None:
    valid_zone(zone)
    valid_pressure(pressure_mbar)
    if reading_type not in READING_TYPES:
        raise ValueError("Unsupported meter reading type")
    date_str = day.strftime("%d/%m/%Y")
    data = await _get_json(
        session,
        FACTOR_URL,
        {
            "municipio": zone,
            "tipoLectura": reading_type,
            "fechaIni": date_str,
            "fechaFin": date_str,
            "presion": str(pressure_mbar),
        },
    )
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ValueError("Unexpected Enagás factor response")
    precise_field = (
        "pcsbimestralcorregido" if reading_type == "Bimestral"
        else "pcsmensualcorregido"
    )
    precise = data.get(precise_field)
    rounded = data.get("conversionfactor")
    if precise is None and rounded is None:
        return None
    factor = _decimal(precise if precise is not None else rounded)
    if precise is not None and rounded is not None:
        if abs(factor - _decimal(rounded)) > Decimal("0.02"):
            raise ValueError("Inconsistent conversion factors from Enagás")
    return ConversionFactor(factor, day, zone, pressure_mbar, reading_type)


async def get_recent_factor(
    session: Any,
    zone: str,
    pressure_mbar: int,
    reading_type: str,
    today: date,
) -> ConversionFactor:
    """Find the newest published factor, not a future or presumed factor.

    At most eight daily lookups, beginning with yesterday. A network/API
    failure aborts immediately so a previously validated value can be reused.
    """
    for age_days in range(1, 9):
        factor = await get_factor(
            session, zone, pressure_mbar, reading_type,
            today - timedelta(days=age_days),
        )
        if factor is not None:
            return factor
    raise ValueError("No current Enagás conversion factor in the last eight days")
