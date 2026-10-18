"""Live regression test of detecting TUR publications in official BOE daily summaries.

Real publication dates (not the document signature dates or tariff effective dates).
Negative days must not be mistaken for new TUR resolutions.
Runs independently of Home Assistant. No user data or credentials are involved.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "boe", ROOT / "custom_components/tur_gas_boe/boe.py"
)
boe = importlib.util.module_from_spec(spec)
sys.modules["boe"] = boe
spec.loader.exec_module(boe)

# Maps publication date -> (official BOE document identifier, effective date).
# Independently verified against the documents' "Publicado en" metadata.
POSITIVE = {
    "20251227": ("BOE-A-2025-26707", date(2026, 1, 1)),
    "20260330": ("BOE-A-2026-7193", date(2026, 4, 1)),
    "20260701": ("BOE-A-2026-14233", date(2026, 7, 1)),
    "20261001": ("BOE-A-2026-20389", date(2026, 10, 1)),
}

# Before and after publication dates, including days near quarter boundaries.
NEGATIVE = (
    "20251226",
    "20251229",
    "20260328",
    "20260331",
    "20260630",
    "20260702",
    "20260930",
    "20261002",
)


def fetch(path: str, *, accept: str, limit: int = 5_000_000) -> bytes | None:
    url = f"https://www.boe.es/{path}"
    req = Request(
        url,
        headers={"Accept": accept, "User-Agent": "ha-TUR-price-validation/0.1"},
    )
    try:
        with urlopen(req, timeout=35) as response:
            data = response.read(limit + 1)
            if len(data) > limit:
                raise ValueError("Official response exceeded size limit")
            return data
    except HTTPError as err:
        if err.code == 404:
            return None
        raise


def check_day(day: str) -> dict[str, str]:
    data = fetch(
        f"datosabiertos/api/boe/sumario/{day}", accept="application/json"
    )
    if data is None:
        print(f"{day}: no BOE daily summary (404), zero candidates")
        return {}
    summary = json.loads(data)
    found = boe.candidates(summary)
    print(f"{day}: {len(found)} matching TUR resolutions: {list(found)}")
    return found


def main() -> None:
    errors: list[str] = []
    for published, (expected_id, effective_date) in POSITIVE.items():
        try:
            matching = check_day(published)
            if expected_id not in matching:
                raise ValueError(
                    f"expected {expected_id}, found {list(matching)}"
                )
            data = fetch(
                f"diario_boe/xml.php?id={expected_id}",
                accept="application/xml",
            )
            if data is None:
                raise ValueError("Published BOE document XML not found")
            parsed = boe.parse_document(data, expected_id)
            if parsed.effective != effective_date:
                raise ValueError(
                    f"effective {parsed.effective}, expected {effective_date}"
                )
            if len(parsed.prices) != 11 or "TUR.1" not in parsed.prices:
                raise ValueError(f"expected 11 TUR tariffs; got {len(parsed.prices)}")
            if published == "20261001":
                if parsed.prices["TUR.1"].variable_eur_kwh != Decimal("0.05252590"):
                    raise ValueError("October TUR.1 variable price mismatch")
                if parsed.prices["TUR.1"].fixed_eur_month != Decimal("3.83"):
                    raise ValueError("October TUR.1 fixed price mismatch")
            print(f"  PASS: {expected_id}, effective {effective_date}, "
                  f"TUR.1 {parsed.prices['TUR.1'].variable_eur_kwh} €/kWh")
        except Exception as exc:
            errors.append(f"POSITIVE {published}: {type(exc).__name__}: {exc}")

    for published in NEGATIVE:
        try:
            matching = check_day(published)
            if matching:
                raise ValueError(
                    f"unexpected TUR publications: {list(matching)}"
                )
            print(f"  PASS: no new TUR publication on {published}")
        except Exception as exc:
            errors.append(f"NEGATIVE {published}: {type(exc).__name__}: {exc}")

    if errors:
        for error in errors:
            print("FAIL:", error)
        raise SystemExit(f"{len(errors)} BOE publication detection checks failed")
    print(
        f"PASS: {len(POSITIVE)} real positive publication dates, "
        f"{len(NEGATIVE)} negative dates, and {len(POSITIVE)} verified XML documents"
    )


if __name__ == "__main__":
    main()
