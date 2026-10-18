"""Fail-closed parsing of official BOE gas TUR resolutions.

No arbitrary URLs, scripts, shell commands, or customer identifiers are accepted.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import re
import unicodedata

from defusedxml import ElementTree as ET


@dataclass(frozen=True)
class Price:
    fixed_eur_month: Decimal
    variable_eur_kwh: Decimal


@dataclass(frozen=True)
class Publication:
    boe_id: str
    effective: date
    prices: dict[str, Price]


def normalize(value: str) -> str:
    return " ".join("".join(
        ch for ch in unicodedata.normalize("NFKD", value.lower())
        if not unicodedata.combining(ch)
    ).split())


def is_candidate(title: str) -> bool:
    """Filter by subject, never by the issuing ministry's name."""
    text = normalize(title)
    return "tarifa de ultimo recurso" in text and "gas natural" in text


def candidates(summary: dict) -> dict[str, str]:
    """Recursively discover relevant BOE-A document ids in a daily summary."""
    result: dict[str, str] = {}

    def visit(node: object) -> None:
        if isinstance(node, list):
            for value in node:
                visit(value)
        elif isinstance(node, dict):
            title = node.get("titulo")
            doc_id = node.get("identificador")
            if isinstance(title, str) and isinstance(doc_id, str):
                doc_id = doc_id.strip()
                if re.fullmatch(r"BOE-A-\d{4}-\d+", doc_id) and is_candidate(title):
                    result[doc_id] = title
            for value in node.values():
                if isinstance(value, (dict, list)):
                    visit(value)

    visit(summary)
    return result


def _tag(node: ET.Element) -> str:
    return node.tag.rsplit("}", 1)[-1].lower()


def _text(node: ET.Element) -> str:
    return " ".join(" ".join(node.itertext()).split())


def _number(value: str) -> Decimal:
    value = re.sub(r"\s|\u00a0", "", value).replace(".", "").replace(",", ".")
    if not re.fullmatch(r"\d+(?:\.\d+)?", value):
        raise ValueError("Not a valid Spanish decimal")
    try:
        return Decimal(value)
    except InvalidOperation as err:
        raise ValueError("Invalid decimal") from err


_MONTHS = dict(zip(
    ("enero febrero marzo abril mayo junio julio agosto septiembre octubre "
     "noviembre diciembre").split(),
    range(1, 13),
))
_MONTHS["setiembre"] = 9


def effective_date(text: str) -> date:
    clean = normalize(text)
    matches = re.findall(
        r"(?:precios.{0,130}?en vigor desde|tarifa.{0,120}?en vigor desde|"
        r"(?:presente )?resolucion surtira efectos|efectos desde)"
        r".{0,95}?(\d{1,2}) de (enero|febrero|marzo|abril|mayo|junio|julio|"
        r"agosto|septiembre|setiembre|octubre|noviembre|diciembre) de (20\d{2})",
        clean,
    )
    if not matches:
        raise ValueError("Missing explicit effective date")
    dates = {date(int(year), _MONTHS[month], int(day))
             for day, month, year in matches}
    if len(dates) != 1:
        raise ValueError("Ambiguous effective date")
    return dates.pop()


def parse_document(xml: bytes | str, boe_id: str) -> Publication:
    if not re.fullmatch(r"BOE-A-\d{4}-\d+", boe_id):
        raise ValueError("Invalid BOE identifier")
    if len(xml) > 2_000_000:
        raise ValueError("Document exceeds allowed size")
    root = ET.fromstring(xml)
    content = _text(root)
    if not ("tarifa de ultimo recurso" in normalize(content) and "gas natural" in normalize(content)):
        raise ValueError("Document not about the natural gas TUR")
    effective = effective_date(content)
    found: list[dict[str, Price]] = []
    for table in root.iter():
        if _tag(table) != "table":
            continue
        rows = []
        for tr in table.iter():
            if _tag(tr) == "tr":
                cells = [_text(cell) for cell in tr if _tag(cell) in ("td", "th")]
                if cells:
                    rows.append(cells)
        if not rows:
            continue
        header = normalize(" ".join(" ".join(row) for row in rows[:3]))
        if not ("termino fijo" in header and "termino variable" in header):
            continue
        if "cent/kwh" in header or "centimos/kwh" in header:
            divider = Decimal("100")
        elif "€/kwh" in header or "eur/kwh" in header:
            divider = Decimal("1")
        else:
            continue
        prices: dict[str, Price] = {}
        for row in rows:
            if not row:
                continue
            match = re.fullmatch(r"TUR\.?\s*(\d{1,2})", row[0], re.IGNORECASE)
            if match is None:
                continue
            tariff = f"TUR.{int(match.group(1))}"
            if len(row) < 3 or tariff in prices:
                raise ValueError("Duplicated or malformed tariff")
            fixed = _number(row[-2])
            variable = _number(row[-1]) / divider
            if not (Decimal(0) < fixed < Decimal(1_000_000)
                    and Decimal(0) < variable < Decimal(5)):
                raise ValueError("Implausible published price")
            prices[tariff] = Price(fixed, variable)
        if len(prices) >= 3 and "TUR.1" in prices:
            found.append(prices)
    if len(found) != 1:
        raise ValueError(f"Expected exactly one complete TUR price table: {len(found)}")
    return Publication(boe_id, effective, found[0])
