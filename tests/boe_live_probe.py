"""Probe the live official BOE API outside Home Assistant.

Only public BOE HTTPS endpoints are contacted. No credentials or personal data.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import json
import re
import sys
import urllib.request

from defusedxml import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "boe", ROOT / "custom_components/tur_gas_boe/boe.py"
)
boe = importlib.util.module_from_spec(spec)
sys.modules["boe"] = boe
spec.loader.exec_module(boe)


def fetch(url: str, accept: str, limit: int = 6_000_000) -> bytes:
    if not url.startswith("https://www.boe.es/"):
        raise RuntimeError("Refusing non-official URL")
    request = urllib.request.Request(
        url, headers={"Accept": accept, "User-Agent": "ha-TUR-price-validation/0.1"}
    )
    with urllib.request.urlopen(request, timeout=35) as response:
        body = response.read(limit + 1)
        if len(body) > limit:
            raise RuntimeError("Oversized BOE response")
        print("GET", url, "=>", response.status,
              response.headers.get("Content-Type"), len(body), "bytes")
        return body


def shape(root):
    by_tag = {}
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        by_tag[tag] = by_tag.get(tag, 0) + 1
    return sorted(by_tag.items(), key=lambda x: -x[1])[:25]


def main() -> None:
    problems = []
    for d, identifier in [
        ("20261001", "BOE-A-2026-20389"),
        ("20260401", "BOE-A-2026-7193"),
    ]:
        print("\nCHECK", d, identifier)
        try:
            summary = fetch(
                f"https://www.boe.es/datosabiertos/api/boe/sumario/{d}",
                "application/json",
            )
            try:
                payload = json.loads(summary)
                matches = boe.candidates(payload)
                print("Summary top-level keys:", list(payload)[:12])
                print("Candidates:", matches)
                if identifier not in matches:
                    problems.append(f"{d}: expected document missing from candidates")
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                print("Summary is not JSON:", exc,
                      "first characters", repr(summary[:180]))
                problems.append(f"{d}: JSON summary format unexpected")
            document = fetch(
                f"https://www.boe.es/diario_boe/xml.php?id={identifier}",
                "application/xml",
            )
            root = ET.fromstring(document)
            print("XML tags:", shape(root))
            for element in root.iter():
                tag = element.tag.rsplit("}", 1)[-1].lower()
                if tag in ("table", "tabla"):
                    print("Sample table tags:",
                          [n.tag.rsplit("}", 1)[-1] for n in list(element)[:5]])
                    print("Sample table text:",
                          " ".join(element.itertext())[:350])
                    break
            publication = boe.parse_document(document, identifier)
            print("Parsed effective date:", publication.effective)
            print("Parsed tariffs:", len(publication.prices))
            print("TUR.1:", publication.prices.get("TUR.1"))
            if d == "20261001":
                assert str(publication.prices["TUR.1"].variable_eur_kwh) == "0.05252590"
                assert str(publication.prices["TUR.1"].fixed_eur_month) == "3.83"
                assert publication.effective.isoformat() == "2026-10-01"
        except Exception as exc:
            print("ERROR:", type(exc).__name__, str(exc))
            problems.append(f"{identifier}: {type(exc).__name__}: {exc}")
    print("\nRESULT", "PASS" if not problems else "FAIL")
    for problem in problems:
        print("ISSUE", problem)
    if problems:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
