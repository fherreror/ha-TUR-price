"""Execute the REAL coordinator and setup/unload code with minimal HA test doubles.

No Home Assistant server, user credentials, or external network.
"""
from __future__ import annotations

import copy
from datetime import date, datetime
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / "custom_components/tur_gas_boe"
TITLE = "Resolución por la que se publica la tarifa de último recurso de gas natural"
CORRECTION = "Corrección de errores de la tarifa de último recurso de gas natural"
OLD_ID = "BOE-A-2026-14233"
NEW_ID = "BOE-A-2026-20389"


class FakeClientError(Exception):
    pass


class FakeClientTimeout:
    def __init__(self, *, total):
        self.total = total


class FakeStore:
    def __init__(self, hass, version, key):
        self.hass, self.key = hass, key

    async def async_load(self):
        return copy.deepcopy(self.hass.storage.get(self.key))

    async def async_save(self, data):
        self.hass.storage[self.key] = copy.deepcopy(data)
        self.hass.writes += 1


class FakeUpdateFailed(Exception):
    pass


class FakeCoordinatorBase:
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, hass, logger, *, name, update_interval):
        self.hass = hass
        self.data = None

    async def async_config_entry_first_refresh(self):
        self.data = await self._async_update_data()

    async def async_request_refresh(self):
        self.data = await self._async_update_data()


class FakeEntry:
    entry_id = "dummy-entry"
    data = {"tariff": "TUR.1"}
    options = {}

    def __init__(self):
        self.cleanup = []

    def add_update_listener(self, callback):
        return lambda: None

    def async_on_unload(self, callback):
        self.cleanup.append(callback)


class FakeConfigEntries:
    def __init__(self):
        self.forwarded = []
        self.unloaded = []

    async def async_forward_entry_setups(self, entry, platforms):
        self.forwarded.append((entry.entry_id, platforms))

    async def async_unload_platforms(self, entry, platforms):
        self.unloaded.append(entry.entry_id)
        return True


class FakeHass:
    def __init__(self, initial=None):
        self.storage = copy.deepcopy(initial or {})
        self.writes = 0
        self.data = {}
        self.config_entries = FakeConfigEntries()
        self.midnight = None


def module(name, **values):
    obj = types.ModuleType(name)
    obj.__dict__.update(values)
    sys.modules[name] = obj
    return obj


module("aiohttp", ClientError=FakeClientError, ClientTimeout=FakeClientTimeout)
module("homeassistant")
module("homeassistant.core", HomeAssistant=FakeHass)
module("homeassistant.config_entries", ConfigEntry=FakeEntry)
module("homeassistant.const", Platform=types.SimpleNamespace(SENSOR="sensor"))
module("homeassistant.helpers")
module("homeassistant.helpers.aiohttp_client", async_get_clientsession=lambda hass: None)
module("homeassistant.helpers.storage", Store=FakeStore)
module("homeassistant.helpers.update_coordinator",
       DataUpdateCoordinator=FakeCoordinatorBase, UpdateFailed=FakeUpdateFailed)
module("homeassistant.helpers.event",
       async_track_time_change=lambda hass, callback, **kw:
       setattr(hass, "midnight", callback) or (lambda: None))
module("homeassistant.util")
module("homeassistant.util.dt", now=lambda: datetime(2026, 10, 7))

spec = importlib.util.spec_from_file_location(
    "tur_gas_boe", ROOT / "__init__.py",
    submodule_search_locations=[str(ROOT)],
)
integration = importlib.util.module_from_spec(spec)
sys.modules["tur_gas_boe"] = integration
spec.loader.exec_module(integration)

from tur_gas_boe import coordinator as coordinator_module  # noqa: E402


def xml(effective: str, fixed: str, variable: str) -> bytes:
    return f"""<?xml version="1.0" encoding="utf-8"?>
    <doc><titulo>Tarifa de último recurso de gas natural</titulo>
    <p>Los precios de la tarifa de último recurso de gas natural en vigor desde
    {effective} serán los siguientes:</p>
    <table><tr><th>Tarifa</th><th>Término fijo €/cliente/mes</th>
    <th>Término variable cent/kWh</th></tr>
    <tr><td>TUR.1</td><td>{fixed}</td><td>{variable}</td></tr>
    <tr><td>TUR.2</td><td>8,17</td><td>5,04</td></tr>
    <tr><td>TUR.3</td><td>19,32</td><td>4,69</td></tr>
    </table></doc>""".encode("utf-8")


OLD_XML = xml("1 de julio de 2026", "4,00", "4,00")
NEW_XML = xml("1 de octubre de 2026", "3,83", "5,252590")


def cached_old(checked="2026-09-28"):
    return {"tur_gas_boe.tariffs": {
        "last_checked": checked,
        "review": {},
        "records": {"2026-07-01": {
            "boe_id": OLD_ID, "source_date": "2026-07-01",
            "prices": {
                "TUR.1": {"fixed": "4.00", "variable": "0.0400"},
                "TUR.2": {"fixed": "8.17", "variable": "0.0504"},
                "TUR.3": {"fixed": "19.32", "variable": "0.0469"},
            }
        }},
    }}


class FakeBOE:
    def __init__(self, entries=None, failures=None):
        self.entries = entries or {}
        self.failures = set(failures or [])
        self.dates = []

    async def summary(self, url):
        raw = url[-8:]
        day = date(int(raw[:4]), int(raw[4:6]), int(raw[6:]))
        self.dates.append(day)
        if day in self.failures:
            raise FakeClientError("BOE unreachable")
        item = self.entries.get(day)
        if item is None:
            return {"data": []}
        identifier, title = item
        return {"data": [{"identificador": identifier, "titulo": title}]}

    async def document(self, identifier):
        return {OLD_ID: OLD_XML, NEW_ID: NEW_XML,
                "BOE-A-2016-696": "<documento>Corrección de errores</documento>".encode("utf-8")}[identifier]

    def attach(self, coordinator):
        coordinator._get_json = self.summary
        coordinator._get_xml = self.document


class TestCoordinator(unittest.IsolatedAsyncioTestCase):
    async def test_first_install_gets_official_prices(self):
        hass = FakeHass()
        instance = coordinator_module.TurGasCoordinator(hass)
        fake = FakeBOE({date(2026, 10, 1): (NEW_ID, TITLE)})
        fake.attach(instance)
        with patch.object(coordinator_module.dt_util, "now",
                          return_value=datetime(2026, 10, 7)):
            await instance.async_load()
            result = await instance._async_update_data()
        self.assertEqual(result["current"].boe_id, NEW_ID)
        self.assertEqual(result["current"].prices["TUR.1"].variable_eur_kwh,
                         Decimal("0.05252590"))
        self.assertEqual(instance.last_checked, date(2026, 10, 7))
        self.assertEqual(hass.writes, 1)
        self.assertIn(date(2026, 10, 1), fake.dates)

    async def test_offline_recovery_and_restart_restores_history(self):
        hass = FakeHass(cached_old())
        instance = coordinator_module.TurGasCoordinator(hass)
        await instance.async_load()
        fake = FakeBOE({date(2026, 10, 1): (NEW_ID, TITLE)})
        fake.attach(instance)
        with patch.object(coordinator_module.dt_util, "now",
                          return_value=datetime(2026, 10, 7)):
            data = await instance._async_update_data()
        self.assertEqual(data["current"].boe_id, NEW_ID)
        self.assertEqual(instance.current(date(2026, 9, 30)).boe_id, OLD_ID)
        self.assertIn(date(2026, 10, 1), fake.dates)
        self.assertEqual(hass.storage["tur_gas_boe.tariffs"]["last_checked"],
                         "2026-10-07")
        restored = coordinator_module.TurGasCoordinator(hass)
        await restored.async_load()
        self.assertEqual(restored.current(date(2026, 10, 7)).boe_id, NEW_ID)
        self.assertEqual(restored.current(date(2026, 9, 30)).boe_id, OLD_ID)

    async def test_future_price_only_applies_on_effective_date(self):
        hass = FakeHass(cached_old("2026-09-27"))
        instance = coordinator_module.TurGasCoordinator(hass)
        await instance.async_load()
        FakeBOE({date(2026, 9, 29): (NEW_ID, TITLE)}).attach(instance)
        with patch.object(coordinator_module.dt_util, "now",
                          return_value=datetime(2026, 9, 30)):
            data = await instance._async_update_data()
        self.assertEqual(data["current"].boe_id, OLD_ID)
        self.assertEqual(data["next"].boe_id, NEW_ID)
        self.assertEqual(instance.current(date(2026, 10, 1)).boe_id, NEW_ID)

    async def test_network_error_does_not_advance_date_and_retries(self):
        hass = FakeHass(cached_old())
        instance = coordinator_module.TurGasCoordinator(hass)
        await instance.async_load()
        fake = FakeBOE({date(2026, 10, 1): (NEW_ID, TITLE)},
                       {date(2026, 10, 2)})
        fake.attach(instance)
        with patch.object(coordinator_module.dt_util, "now",
                          return_value=datetime(2026, 10, 4)):
            failed = await instance._async_update_data()
            self.assertIsNotNone(failed["problem"])
            self.assertEqual(instance.last_checked, date(2026, 9, 28))
            self.assertEqual(hass.storage["tur_gas_boe.tariffs"]["last_checked"],
                             "2026-09-28")
            fake.failures.clear()
            ok = await instance._async_update_data()
        self.assertIsNone(ok["problem"])
        self.assertEqual(instance.last_checked, date(2026, 10, 4))
        self.assertEqual(ok["current"].boe_id, NEW_ID)

    async def test_incomplete_correction_only_creates_review_alert(self):
        hass = FakeHass(cached_old("2026-10-01"))
        instance = coordinator_module.TurGasCoordinator(hass)
        await instance.async_load()
        FakeBOE({date(2026, 10, 2):
                 ("BOE-A-2016-696", CORRECTION)}).attach(instance)
        with patch.object(coordinator_module.dt_util, "now",
                          return_value=datetime(2026, 10, 3)):
            result = await instance._async_update_data()
        self.assertEqual(result["current"].boe_id, OLD_ID)
        self.assertIn("BOE-A-2016-696", result["review"])
        self.assertIsNotNone(result["problem"])
        restored = coordinator_module.TurGasCoordinator(hass)
        await restored.async_load()
        self.assertIn("BOE-A-2016-696", restored.review)

    async def test_malformed_boe_json_is_logged_and_retried(self):
        hass = FakeHass(cached_old())
        instance = coordinator_module.TurGasCoordinator(hass)
        await instance.async_load()
        fake = FakeBOE({date(2026, 10, 1): (NEW_ID, TITLE)})
        original_summary = fake.summary
        broken = {date(2026, 9, 30)}

        async def summary(url):
            raw = url[-8:]
            day = date(int(raw[:4]), int(raw[4:6]), int(raw[6:]))
            if day in broken:
                raise coordinator_module.BOESummaryFormatError("Invalid JSON")
            return await original_summary(url)

        instance._get_json = summary
        instance._get_xml = fake.document
        with patch.object(coordinator_module.dt_util, "now",
                          return_value=datetime(2026, 10, 7)):
            with self.assertLogs(coordinator_module.__name__, level="WARNING"):
                first = await instance._async_update_data()
        self.assertEqual(first["current"].boe_id, NEW_ID)
        self.assertIn(date(2026, 9, 30), instance.pending_days)
        self.assertIn("BOE retry pending", first["problem"])
        restarted = coordinator_module.TurGasCoordinator(hass)
        await restarted.async_load()
        self.assertIn(date(2026, 9, 30), restarted.pending_days)
        broken.clear()
        restarted._get_json = summary
        restarted._get_xml = fake.document
        with patch.object(coordinator_module.dt_util, "now",
                          return_value=datetime(2026, 10, 8)):
            second = await restarted._async_update_data()
        self.assertIsNone(second["problem"])
        self.assertFalse(restarted.pending_days)

    async def test_unavailable_boe_on_first_start_fails(self):
        instance = coordinator_module.TurGasCoordinator(FakeHass())
        FakeBOE(failures={date(2026, 10, 7)}).attach(instance)
        with patch.object(coordinator_module.dt_util, "now",
                          return_value=datetime(2026, 10, 7)):
            with self.assertRaises(FakeUpdateFailed):
                await instance._async_update_data()
        self.assertIsNone(instance.last_checked)

    async def test_full_setup_midnight_refresh_unload(self):
        hass = FakeHass(cached_old("2026-10-05"))
        entry = FakeEntry()
        fake = FakeBOE({date(2026, 10, 1): (NEW_ID, TITLE)})
        with patch.object(coordinator_module.TurGasCoordinator,
                          "_get_json", new=fake.summary), patch.object(
                          coordinator_module.TurGasCoordinator,
                          "_get_xml", new=fake.document), patch.object(
                          coordinator_module.dt_util, "now",
                          return_value=datetime(2026, 10, 7)):
            self.assertTrue(await integration.async_setup_entry(hass, entry))
            self.assertEqual(len(hass.config_entries.forwarded), 1)
            self.assertIn("dummy-entry", hass.data["tur_gas_boe"])
            self.assertEqual(len(entry.cleanup), 2)
            self.assertIsNotNone(hass.midnight)
            await hass.midnight(None)
            self.assertTrue(await integration.async_unload_entry(hass, entry))
            self.assertNotIn("dummy-entry", hass.data["tur_gas_boe"])

    async def test_boe_json_stream_reads_all_chunks(self):
        import json

        class SplitBody:
            async def iter_chunked(self, size):
                full = json.dumps({
                    "data": [{"identificador": NEW_ID, "titulo": TITLE}],
                    "padding": "x" * 200000,
                }).encode("utf-8")
                for start in range(0, len(full), 15925):
                    yield full[start:start + 15925]

        class Response:
            status = 200
            headers = {"Content-Type": "application/json"}
            content = SplitBody()

            def raise_for_status(self):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class Session:
            def get(self, *args, **kwargs):
                return Response()

        instance = coordinator_module.TurGasCoordinator(FakeHass())
        with patch.object(coordinator_module, "async_get_clientsession",
                          return_value=Session()):
            result = await instance._get_json(
                "https://www.boe.es/datosabiertos/api/boe/sumario/20261001"
            )
        self.assertEqual(result["data"][0]["identificador"], NEW_ID)
        self.assertEqual(len(result["padding"]), 200000)

    async def test_boe_xml_stream_reads_all_chunks(self):
        class SplitBody:
            async def iter_chunked(self, size):
                for start in range(0, len(NEW_XML), 7):
                    yield NEW_XML[start:start + 7]

        class Response:
            status = 200
            content = SplitBody()

            def raise_for_status(self):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class Session:
            def get(self, *args, **kwargs):
                return Response()

        instance = coordinator_module.TurGasCoordinator(FakeHass())
        with patch.object(coordinator_module, "async_get_clientsession",
                          return_value=Session()):
            result = await instance._get_xml(NEW_ID)
        self.assertEqual(result, NEW_XML)

    async def test_oversized_stream_is_rejected(self):
        class HugeBody:
            async def iter_chunked(self, size):
                yield b"x" * coordinator_module._JSON_MAX_BYTES
                yield b"!"

        class Response:
            status = 200
            content = HugeBody()

            def raise_for_status(self):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class Session:
            def get(self, *args, **kwargs):
                return Response()

        instance = coordinator_module.TurGasCoordinator(FakeHass())
        with patch.object(coordinator_module, "async_get_clientsession",
                          return_value=Session()):
            with self.assertRaisesRegex(ValueError, "size exceeded"):
                await instance._get_json(
                    "https://www.boe.es/datosabiertos/api/boe/sumario/20261001"
                )

    async def test_malformed_boe_daily_summary_is_skipped_and_retried(self):
        class StubContent:
            async def iter_chunked(self, size):
                yield b'{"status": {"value": invalid json}}'

        class StubResponse:
            status = 200
            content = StubContent()

            def raise_for_status(self):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class StubSession:
            def get(self, *args, **kwargs):
                return StubResponse()

        hass = FakeHass(cached_old("2026-09-28"))
        instance = coordinator_module.TurGasCoordinator(hass)
        await instance.async_load()
        bad_day = date(2026, 9, 30)
        fake = FakeBOE({date(2026, 10, 1): (NEW_ID, TITLE)})
        normal = fake.summary
        original_get_json = instance._get_json

        async def summary_with_bad_day(url):
            if url.endswith(bad_day.strftime("%Y%m%d")):
                return await original_get_json(url)
            return await normal(url)

        with patch.object(coordinator_module, "async_get_clientsession",
                          return_value=StubSession()):
            instance._get_json = summary_with_bad_day
            instance._get_xml = fake.document
            with patch.object(coordinator_module.dt_util, "now",
                              return_value=datetime(2026, 10, 4)):
                state = await instance._async_update_data()
        self.assertEqual(state["current"].boe_id, NEW_ID)
        self.assertIn("2026-09-30", state["problem"])
        self.assertEqual(instance.last_checked, date(2026, 10, 4))
        self.assertEqual(hass.storage["tur_gas_boe.tariffs"]["last_checked"],
                         "2026-10-04")
        self.assertIn(date(2026, 9, 30), instance.pending_days)


if __name__ == "__main__":
    unittest.main()
