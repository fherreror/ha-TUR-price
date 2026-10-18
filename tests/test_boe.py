"""Offline tests of fail-closed parsing. Live BOE XML integration tests remain pending."""
import importlib.util
from pathlib import Path
import sys
import unittest
from datetime import date
from decimal import Decimal

PATH = Path(__file__).resolve().parents[1] / "custom_components/tur_gas_boe/boe.py"
spec = importlib.util.spec_from_file_location("boe", PATH)
boe = importlib.util.module_from_spec(spec)
sys.modules["boe"] = boe
spec.loader.exec_module(boe)

XML = """<?xml version="1.0" encoding="utf-8"?>
<documento><titulo>Tarifa de último recurso de gas natural</titulo><texto>
<p>Los precios de la tarifa de último recurso de gas natural en vigor desde
las cero horas del día 1 de octubre de 2026 son los siguientes:</p>
<table><thead><tr><th>Tarifa</th><th>Término fijo (€/cliente)/mes</th>
<th>Término variable cent/kWh</th></tr></thead><tbody>
<tr><td>TUR.1</td><td>Consumo inferior a 5.000 kWh</td><td>3,83</td><td>5,252590</td></tr>
<tr><td>TUR.2</td><td>Consumo superior</td><td>8,17</td><td>5,042762</td></tr>
<tr><td>TUR.3</td><td>Consumo superior</td><td>19,32</td><td>4,698799</td></tr>
</tbody></table></texto></documento>"""


class TestBoe(unittest.TestCase):
    def test_discovery_independent_of_issuer(self):
        self.assertTrue(boe.is_candidate(
            "Resolución de una nueva agencia: tarifa de último recurso de gas natural"
        ))
        self.assertFalse(boe.is_candidate(
            "Tarifa de último recurso de electricidad"
        ))

    def test_nested_summary(self):
        data = {"data": {"list": [
            {"identificador": "BOE-A-2026-20389",
             "titulo": "Tarifa de último recurso de gas natural"}
        ]}}
        self.assertEqual(list(boe.candidates(data)), ["BOE-A-2026-20389"])

    def test_price_units_and_effective_date(self):
        item = boe.parse_document(XML, "BOE-A-2026-20389")
        self.assertEqual(item.effective, date(2026, 10, 1))
        self.assertEqual(item.prices["TUR.1"].fixed_eur_month, Decimal("3.83"))
        self.assertEqual(item.prices["TUR.1"].variable_eur_kwh,
                         Decimal("0.05252590"))

    def test_reject_missing_tariff(self):
        with self.assertRaises(ValueError):
            boe.parse_document(
                XML.replace("<td>TUR.3</td>", "<td>OTHER</td>"),
                "BOE-A-2026-20389",
            )

    def test_reject_invalid_identifier(self):
        with self.assertRaises(ValueError):
            boe.parse_document(XML, "https://untrusted.invalid/x")

    def test_reject_entity_expansion(self):
        evil = ('<!DOCTYPE document [<!ENTITY x "BAD">]>'
                '<document>&x;</document>')
        with self.assertRaises(Exception):
            boe.parse_document(evil, "BOE-A-2026-20389")

    def test_reject_invalid_number(self):
        with self.assertRaises(ValueError):
            boe.parse_document(XML.replace("5,252590", "ABC"), "BOE-A-2026-20389")


if __name__ == "__main__":
    unittest.main()
