"""Poll official BOE publications and store validated tariff prices locally."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import logging

from aiohttp import ClientError, ClientTimeout
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .boe import Publication, Price, candidates, parse_document
from .const import BOE_SUMMARY, BOE_XML, DOMAIN, STORE_VERSION, UPDATE_HOURS

_LOGGER = logging.getLogger(__name__)
_TIMEOUT = ClientTimeout(total=20)
_XML_MAX_BYTES = 2_000_000
_JSON_MAX_BYTES = 5_000_000


class TurGasCoordinator(DataUpdateCoordinator[dict]):
    """Preserve historical and future BOE prices across restarts."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(
            hass, _LOGGER, name=DOMAIN, update_interval=timedelta(hours=UPDATE_HOURS)
        )
        self.store = Store(hass, STORE_VERSION, f"{DOMAIN}.tariffs")
        self.records: dict[str, Publication] = {}
        self.source_dates: dict[str, date] = {}
        self.last_checked: date | None = None
        self.problem: str | None = None

    async def async_load(self) -> None:
        raw = await self.store.async_load() or {}
        for effective, record in raw.get("records", {}).items():
            try:
                prices = {
                    tariff: Price(Decimal(values["fixed"]), Decimal(values["variable"]))
                    for tariff, values in record["prices"].items()
                }
                parsed = Publication(record["boe_id"], date.fromisoformat(effective), prices)
                self.records[effective] = parsed
                self.source_dates[effective] = date.fromisoformat(
                    record.get("source_date", effective)
                )
            except (ValueError, TypeError, KeyError):
                _LOGGER.warning("Discarding malformed cached tariff for %s", effective)
        if raw.get("last_checked"):
            try:
                self.last_checked = date.fromisoformat(raw["last_checked"])
            except ValueError:
                pass

    async def _save(self) -> None:
        await self.store.async_save({
            "last_checked": self.last_checked.isoformat() if self.last_checked else None,
            "records": {
                effective: {
                    "boe_id": record.boe_id,
                    "source_date": self.source_dates.get(
                        effective, record.effective
                    ).isoformat(),
                    "prices": {
                        tariff: {
                            "fixed": str(price.fixed_eur_month),
                            "variable": str(price.variable_eur_kwh),
                        }
                        for tariff, price in record.prices.items()
                    },
                }
                for effective, record in self.records.items()
            },
        })

    def current(self, today: date | None = None) -> Publication | None:
        today = today or dt_util.now().date()
        applicable = [r for r in self.records.values() if r.effective <= today]
        return max(applicable, key=lambda r: r.effective) if applicable else None

    def next(self, today: date | None = None) -> Publication | None:
        today = today or dt_util.now().date()
        future = [r for r in self.records.values() if r.effective > today]
        return min(future, key=lambda r: r.effective) if future else None

    async def _get_json(self, url: str) -> dict | None:
        session = async_get_clientsession(self.hass)
        async with session.get(
            url, headers={"Accept": "application/json"}, timeout=_TIMEOUT
        ) as response:
            if response.status == 404:
                return None
            response.raise_for_status()
            data = await response.content.read(_JSON_MAX_BYTES + 1)
            if len(data) > _JSON_MAX_BYTES:
                raise ValueError("BOE summary size exceeded")
            import json

            parsed = json.loads(data)
            if not isinstance(parsed, dict):
                raise ValueError("Unexpected BOE summary format")
            return parsed

    async def _get_xml(self, identifier: str) -> bytes:
        session = async_get_clientsession(self.hass)
        async with session.get(
            BOE_XML.format(id=identifier), timeout=_TIMEOUT
        ) as response:
            response.raise_for_status()
            data = await response.content.read(_XML_MAX_BYTES + 1)
            if len(data) > _XML_MAX_BYTES:
                raise ValueError("BOE XML size exceeded")
            return data

    async def _scan(self, day: date) -> None:
        summary = await self._get_json(
            BOE_SUMMARY.format(date=day.strftime("%Y%m%d"))
        )
        if summary is None:
            return
        for identifier, title in candidates(summary).items():
            try:
                publication = parse_document(
                    await self._get_xml(identifier), identifier
                )
            except (ValueError, ET_PARSE_ERROR) as err:
                self.problem = f"Cannot interpret {identifier}: {err}"
                _LOGGER.warning("BOE candidate %s rejected (%s)", identifier, title)
                continue
            effective = publication.effective.isoformat()
            if day < self.source_dates.get(effective, date.min):
                continue
            self.records[effective] = publication
            self.source_dates[effective] = day
            _LOGGER.info("Validated BOE %s effective %s", identifier, effective)

    async def _async_update_data(self) -> dict:
        today = dt_util.now().date()
        self.problem = None
        modified = False
        try:
            if not self.records:
                for days_back in range(185):
                    day = today - timedelta(days=days_back)
                    await self._scan(day)
                    if self.current(today):
                        modified = True
                        break
                if not self.current(today):
                    raise UpdateFailed(
                        "No applicable official TUR resolution found in 185 days"
                    )
                start = today + timedelta(days=1)
            else:
                start = (
                    self.last_checked - timedelta(days=3)
                    if self.last_checked else today - timedelta(days=45)
                )
            cursor = start
            while cursor <= today:
                before = dict(self.records)
                await self._scan(cursor)
                modified |= self.records != before
                cursor += timedelta(days=1)
            if self.problem:
                _LOGGER.warning("BOE tariff parser warning: %s", self.problem)
            else:
                self.last_checked = today
                modified = True
            if modified:
                await self._save()
        except (ClientError, TimeoutError, ValueError, UpdateFailed) as err:
            if not self.records:
                raise UpdateFailed(f"Could not retrieve BOE data: {err}") from err
            self.problem = f"BOE polling failed: {err}"
            _LOGGER.warning("Using last validated TUR: %s", self.problem)
        return {"current": self.current(), "next": self.next(), "problem": self.problem}


# Exceptions raised by defusedxml/ElementTree are caught as nonfatal candidate
# failures, never interpreted as a valid tariff.
from defusedxml.ElementTree import ParseError as ET_PARSE_ERROR  # noqa: E402
