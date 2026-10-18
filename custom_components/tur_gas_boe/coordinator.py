"""Poll official BOE publications and store validated tariff prices locally."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import json
import logging

from aiohttp import ClientError, ClientTimeout
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .boe import Publication, Price, candidates
from .recovery import TariffLedger, recovery_start
from .enagas import ConversionFactor, get_recent_factor
from .const import CONF_ENAGAS_ZONE, CONF_ENAGAS_PRESSURE, CONF_ENAGAS_READING
from .const import BOE_SUMMARY, BOE_XML, DOMAIN, STORE_VERSION, UPDATE_HOURS

_LOGGER = logging.getLogger(__name__)
_TIMEOUT = ClientTimeout(total=20)
_XML_MAX_BYTES = 2_000_000
_JSON_MAX_BYTES = 5_000_000
_HTTP_READ_CHUNK = 64 * 1024


async def _read_bounded_body(response, maximum: int, label: str) -> bytes:
    """Read through EOF, rejecting oversized responses before storing them.

    aiohttp StreamReader.read(n) may return fewer than n bytes even when more
    are coming: a single read is not enough to obtain the full BOE document.
    """
    body = bytearray()
    async for chunk in response.content.iter_chunked(_HTTP_READ_CHUNK):
        if len(body) + len(chunk) > maximum:
            raise ValueError(f"{label} size exceeded ({maximum} bytes)")
        body.extend(chunk)
    return bytes(body)


class BOESummaryFormatError(ValueError):
    """BOE summary cannot be safely interpreted as JSON."""



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
        self.review: dict[str, str] = {}
        self.pending_days: set[date] = set()
        self.conversion: ConversionFactor | None = None
        self.enagas_problem: str | None = None

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
        self.review = dict(raw.get("review", {}))
        for value in raw.get("pending_days", []):
            try:
                self.pending_days.add(date.fromisoformat(value))
            except (ValueError, TypeError):
                _LOGGER.warning("Discarding invalid cached BOE retry date")
        cached = raw.get("enagas_factor")
        if isinstance(cached, dict):
            try:
                from .enagas import valid_zone, valid_pressure, READING_TYPES
                value = Decimal(cached["value"])
                day = date.fromisoformat(cached["date"])
                zone = valid_zone(cached["zone"])
                pressure = valid_pressure(int(cached["pressure"]))
                reading = cached["reading_type"]
                if not Decimal("5") < value < Decimal("20") or reading not in READING_TYPES:
                    raise ValueError("Invalid saved factor")
                self.conversion = ConversionFactor(value, day, zone, pressure, reading)
            except (ValueError, KeyError, TypeError, ArithmeticError):
                _LOGGER.warning("Discarding invalid saved Enagás factor")
        if raw.get("last_checked"):
            try:
                self.last_checked = date.fromisoformat(raw["last_checked"])
            except ValueError:
                pass

    async def _save(self) -> None:
        await self.store.async_save({
            "last_checked": self.last_checked.isoformat() if self.last_checked else None,
            "review": self.review,
            "pending_days": sorted(day.isoformat() for day in self.pending_days),
            "enagas_factor": ({
                "value": str(self.conversion.value),
                "date": self.conversion.measured_on.isoformat(),
                "zone": self.conversion.zone,
                "pressure": self.conversion.pressure_mbar,
                "reading_type": self.conversion.reading_type,
            } if self.conversion is not None else None),
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
            data = await _read_bounded_body(
                response, _JSON_MAX_BYTES, f"BOE summary {url}"
            )
            try:
                parsed = json.loads(data)
            except (json.JSONDecodeError, UnicodeDecodeError) as err:
                _LOGGER.error(
                    "BOE daily summary contains invalid JSON: URL=%s, HTTP=%s, "
                    "content_type=%s, bytes=%s, exception=%s",
                    url, response.status,
                    getattr(response, "headers", {}).get("Content-Type", "unknown"),
                    len(data), err,
                )
                raise BOESummaryFormatError(
                    f"Invalid BOE JSON from {url}: {err}"
                ) from err
            if not isinstance(parsed, dict):
                _LOGGER.error(
                    "BOE summary JSON is not an object: URL=%s, HTTP=%s, bytes=%s",
                    url, response.status, len(data),
                )
                raise BOESummaryFormatError(
                    f"Unexpected BOE summary JSON format for {url}"
                )
            return parsed

    async def _get_xml(self, identifier: str) -> bytes:
        session = async_get_clientsession(self.hass)
        async with session.get(
            BOE_XML.format(id=identifier), timeout=_TIMEOUT
        ) as response:
            response.raise_for_status()
            return await _read_bounded_body(
                response, _XML_MAX_BYTES, f"BOE XML {identifier}"
            )

    async def _scan(self, day: date) -> None:
        url = BOE_SUMMARY.format(date=day.strftime("%Y%m%d"))
        try:
            summary = await self._get_json(url)
        except BOESummaryFormatError:
            self.pending_days.add(day)
            _LOGGER.warning(
                "Invalid BOE summary for %s; scheduling retry: %s", day, url
            )
            return
        except (ClientError, TimeoutError, ValueError) as err:
            _LOGGER.error("BOE summary failed for %s (%s): %s", day, url, err)
            raise
        if summary is None:
            self.pending_days.discard(day)
            return
        ledger = TariffLedger(self.records, self.source_dates, self.review)
        for identifier, title in candidates(summary).items():
            data = await self._get_xml(identifier)
            result = ledger.add(identifier, title, day, data)
            if result == "review":
                _LOGGER.warning(
                    "Official BOE publication %s requires manual review: %s",
                    identifier, ledger.review[identifier],
                )
            elif result == "applied":
                _LOGGER.info("Validated %s published %s", identifier, day)
        self.records, self.source_dates, self.review = (
            ledger.records, ledger.published, ledger.review
        )
        self.pending_days.discard(day)

    async def async_refresh_enagas(self, options: dict) -> None:
        """Retrieve the official factor without affecting the BOE price update."""
        zone = options.get(CONF_ENAGAS_ZONE)
        self.enagas_problem = None
        if not zone:
            self.conversion = None
            return
        pressure = options.get(CONF_ENAGAS_PRESSURE)
        reading = options.get(CONF_ENAGAS_READING, "Bimestral")
        if self.conversion is not None and (
            self.conversion.zone != zone or self.conversion.pressure_mbar != pressure
            or self.conversion.reading_type != reading
        ):
            self.conversion = None
        try:
            self.conversion = await get_recent_factor(
                async_get_clientsession(self.hass),
                zone, pressure, reading, dt_util.now().date(),
            )
            await self._save()
        except (ClientError, TimeoutError, ValueError, TypeError) as err:
            self.enagas_problem = str(err)
            _LOGGER.warning("Enagás factor not available for %s: %s", zone, err)

    async def _async_update_data(self) -> dict:
        today = dt_util.now().date()
        self.problem = None
        modified = False
        try:
            if not self.records:
                # Bootstrap backwards until a current tariff is found;
                # then scan forwards so future-dated publications are retained.
                found_on = None
                for days_back in range(185):
                    day = today - timedelta(days=days_back)
                    await self._scan(day)
                    if self.current(today):
                        found_on = day
                        break
                if found_on is None:
                    raise UpdateFailed(
                        "No applicable official TUR resolution found in 185 days"
                    )
                start = found_on + timedelta(days=1)
            else:
                start = recovery_start(today, self.last_checked)
            if self.pending_days:
                start = min(start, min(self.pending_days))
            cursor = start
            while cursor <= today:
                await self._scan(cursor)
                cursor += timedelta(days=1)
            if self.problem is None:
                self.last_checked = today
            modified = True
        except (ClientError, TimeoutError, ValueError, UpdateFailed) as err:
            if not self.records:
                _LOGGER.error("Initial TUR Gas BOE load failed: %s", err)
                raise UpdateFailed(f"Could not retrieve BOE data: {err}") from err
            # Never advance the watermark across a failed calendar day.
            self.problem = f"BOE polling failed: {err}"
            _LOGGER.error("Retaining last validated TUR after BOE error: %s", self.problem)
            modified = True
        if modified:
            await self._save()
        if self.review and self.problem is None:
            self.problem = (
                f"{len(self.review)} BOE publication(s) require review; "
                "tariffs have not been overwritten by corrections"
            )
        if self.pending_days:
            days = ", ".join(day.isoformat() for day in sorted(self.pending_days))
            _LOGGER.warning("BOE summary days awaiting retry: %s", days)
            self.problem = "; ".join(
                value for value in (self.problem, f"BOE retry pending: {days}") if value
            )
        return {"current": self.current(), "next": self.next(),
                "problem": self.problem, "review": self.review}
