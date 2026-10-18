"""Official Enagás client unit tests without network or Home Assistant."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import unittest

PATH = Path(__file__).resolve().parents[1] / "custom_components/tur_gas_boe/enagas.py"
spec = importlib.util.spec_from_file_location("enagas", PATH)
enagas = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = enagas
spec.loader.exec_module(enagas)


class Body:
    def __init__(self, value):
        self.value = value

    async def iter_chunked(self, size):
        for start in range(0, len(self.value), 7):
            yield self.value[start:start + 7]


class Response:
    status = 200

    def __init__(self, body):
        self.content = Body(body)

    def raise_for_status(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class Session:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, *, params=None, headers=None):
        self.calls.append((url, params))
        return Response(self.response)


class TestEnagas(unittest.IsolatedAsyncioTestCase):
    async def test_official_invoice_factor_precisely(self):
        session = Session(b'{"conversionfactor":"10,611","pcsbimestralcorregido":"10,6110152"}')
        factor = await enagas.get_factor(
            session, "28MA2", 22, "Bimestral", date(2026, 9, 22)
        )
        self.assertEqual(factor.value, Decimal("10.6110152"))
        self.assertEqual(factor.measured_on, date(2026, 9, 22))
        self.assertEqual(
            session.calls[0][1],
            {"municipio": "28MA2", "tipoLectura": "Bimestral",
             "fechaIni": "22/09/2026", "fechaFin": "22/09/2026", "presion": "22"},
        )
        self.assertEqual(Decimal("0.05252590") * factor.value,
                         Decimal("0.557353123293680"))

    async def test_only_complete_json_accepted(self):
        session = Session(b'{"conversionfactor":')
        with self.assertRaises(ValueError):
            await enagas.get_factor(
                session, "28MA2", 22, "Bimestral", date(2026, 9, 22)
            )

    async def test_no_factor_becomes_none(self):
        session = Session(b'{"conversionfactor":null,"pcsbimestralcorregido":null}')
        factor = await enagas.get_factor(
            session, "28MA2", 22, "Bimestral", date(2026, 9, 22)
        )
        self.assertIsNone(factor)

    async def test_disagreeing_factors_rejected(self):
        session = Session(b'{"conversionfactor":"9,511","pcsbimestralcorregido":"10,6110152"}')
        with self.assertRaisesRegex(ValueError, "Inconsistent"):
            await enagas.get_factor(
                session, "28MA2", 22, "Bimestral", date(2026, 9, 22)
            )

    async def test_oversized_response_rejected(self):
        session = Session(b"x" * (enagas.MAX_RESPONSE_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "size limit"):
            await enagas.get_factor(
                session, "28MA2", 22, "Bimestral", date(2026, 9, 22)
            )

    async def test_municipality_mapping(self):
        session = Session(
            '[{"codigo":"28MA2","nombre":"Madrid 2 - Gas Natural Distribución",'
            '"provincia":"Madrid"}]'.encode()
        )
        zones = await enagas.get_zones(session)
        self.assertIn("28MA2", zones)

    async def test_available_pressures(self):
        session = Session(b'["20","22","50","55","100","150"]')
        pressures = await enagas.get_pressures(
            session, "28MA2", date(2026, 9, 22),
        )
        self.assertIn(22, pressures)

    async def test_zone_rejects_injected_identifiers(self):
        for zone in ("", "/evil", "28MA2?extra=1", "HTTPS://EVIL", None):
            with self.assertRaises(ValueError):
                enagas.valid_zone(zone)

    async def test_pressure_rejects_bad_range(self):
        for pressure in (0, -3, 501, True, "22"):
            with self.assertRaises(ValueError):
                enagas.valid_pressure(pressure)

    async def test_safety_bounds(self):
        session = Session(b'{"conversionfactor":"5500"}')
        with self.assertRaisesRegex(ValueError, "Implausible"):
            await enagas.get_factor(
                session, "28MA2", 22, "Bimestral", date(2026, 9, 22)
            )


if __name__ == "__main__":
    unittest.main()
