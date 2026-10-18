"""Replay regression: outages, published-before-effective, historical correction."""
from datetime import date
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1] / "custom_components/tur_gas_boe"
pkg = types.ModuleType("tur_gas_boe")
pkg.__path__ = [str(ROOT)]
sys.modules[pkg.__name__] = pkg
for name in ("boe", "recovery"):
    spec = importlib.util.spec_from_file_location(
        f"tur_gas_boe.{name}", ROOT / f"{name}.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)

from tur_gas_boe import recovery  # noqa: E402
from tur_gas_boe.boe import Publication, Price  # noqa: E402

TITLE = "Resolución por la que se publica la tarifa de último recurso de gas natural"
CORRECTED = "Corrección de errores de la tarifa de último recurso de gas natural"
OLD = Publication("BOE-A-2026-7193", date(2026, 4, 1), {
    "TUR.1": Price(Decimal("3.93"), Decimal("0.03822924"))
})
NEW = Publication("BOE-A-2026-20389", date(2026, 10, 1), {
    "TUR.1": Price(Decimal("3.83"), Decimal("0.05252590"))
})


def index(doc_id=None, title=TITLE):
    if not doc_id:
        return {"data": {"sumario": []}}
    return {"data": {"sumario": [{"identificador": doc_id, "titulo": title}]}}


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_and_quarter_change(self):
        ledger = recovery.TariffLedger(
            records={OLD.effective.isoformat(): OLD},
            published={OLD.effective.isoformat(): date(2026, 3, 30)},
        )
        seen = []
        async def summary(day):
            seen.append(day)
            if day == date(2026, 10, 1):
                return index(NEW.boe_id)
            return index()
        async def xml(identifier):
            return b"official XML stub"

        from unittest.mock import patch
        with patch.object(recovery, "parse_document", return_value=NEW):
            end = await recovery.replay_summaries(
                ledger, recovery.recovery_start(date(2026, 10, 7),
                    date(2026, 9, 27)), date(2026, 10, 7), summary, xml
            )
        self.assertEqual(end, date(2026, 10, 7))
        self.assertIn(date(2026, 10, 1), seen)
        self.assertEqual(ledger.current(date(2026, 9, 30)), OLD)
        self.assertEqual(ledger.current(date(2026, 10, 7)), NEW)
        self.assertEqual(len(ledger.records), 2)

    async def test_future_price_held_until_effective(self):
        ledger = recovery.TariffLedger(
            records={OLD.effective.isoformat(): OLD},
            published={OLD.effective.isoformat(): date(2026, 3, 30)},
        )
        async def summary(day):
            return index(NEW.boe_id) if day == date(2026, 9, 29) else index()
        async def xml(identifier):
            return b"fixture"
        from unittest.mock import patch
        with patch.object(recovery, "parse_document", return_value=NEW):
            await recovery.replay_summaries(
                ledger, date(2026, 9, 28), date(2026, 9, 30), summary, xml
            )
        self.assertEqual(ledger.current(date(2026, 9, 30)), OLD)
        self.assertEqual(ledger.upcoming(date(2026, 9, 30)), NEW)

    async def test_network_outage_does_not_advance_watermark(self):
        ledger = recovery.TariffLedger()
        async def summary(day):
            if day == date(2026, 10, 2):
                raise ConnectionError("BOE unavailable")
            return index()
        async def xml(identifier):
            raise AssertionError("No XML expected")
        with self.assertRaises(ConnectionError):
            await recovery.replay_summaries(
                ledger, date(2026, 10, 1), date(2026, 10, 3), summary, xml
            )

    async def test_correction_preserves_prices(self):
        ledger = recovery.TariffLedger(
            records={OLD.effective.isoformat(): OLD},
            published={OLD.effective.isoformat(): date(2026, 3, 30)}
        )
        async def summary(day):
            return index("BOE-A-2016-696", CORRECTED)
        async def xml(identifier):
            return b"<not-a-complete-tariff/>"
        await recovery.replay_summaries(
            ledger, date(2026, 10, 3), date(2026, 10, 3), summary, xml
        )
        self.assertEqual(ledger.current(date(2026, 10, 3)), OLD)
        self.assertIn("BOE-A-2016-696", ledger.review)
        self.assertEqual(len(ledger.records), 1)

    async def test_idempotent_overlap(self):
        ledger = recovery.TariffLedger()
        async def summary(day):
            return index(NEW.boe_id)
        async def xml(identifier):
            return b"fixture"
        from unittest.mock import patch
        with patch.object(recovery, "parse_document", return_value=NEW):
            for _ in range(2):
                await recovery.replay_summaries(
                    ledger, date(2026, 10, 1), date(2026, 10, 1), summary, xml
                )
        self.assertEqual(len(ledger.records), 1)


if __name__ == "__main__":
    unittest.main()
