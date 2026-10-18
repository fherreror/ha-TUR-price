"""Offline-testable recovery and correction safeguards for BOE tariff updates."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
import re

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import ParseError

from .boe import Publication, candidates, normalize, parse_document


def scan_days(start: date, end: date):
    """Yield each calendar date in ascending order, including both endpoints."""
    while start <= end:
        yield start
        start += timedelta(days=1)


def recovery_start(today: date, last_checked: date | None, overlap_days: int = 3) -> date:
    """Rescan a small overlap without ever skipping the days spent offline."""
    if last_checked is None:
        return today - timedelta(days=45)
    return min(today, last_checked - timedelta(days=overlap_days))


def is_correction(title: str) -> bool:
    """Identify a rectification regardless of the issuing department."""
    text = normalize(title)
    return bool(re.search(
        r"\b(corrigen?|correccion(?:es)?|rectifica(?:n|cion)?|errores)\b", text
    ))


@dataclass
class TariffLedger:
    """Safe historical prices plus explicit notices requiring review.

    Partial corrections never overwrite a complete published price table.
    """

    records: dict[str, Publication] = field(default_factory=dict)
    published: dict[str, date] = field(default_factory=dict)
    review: dict[str, str] = field(default_factory=dict)

    def add(self, identifier: str, title: str, day: date, xml: bytes | str) -> str:
        if is_correction(title):
            self.review[identifier] = f"Correction published {day.isoformat()}: {title}"
            return "review"
        try:
            publication = parse_document(xml, identifier)
        except (ValueError, ParseError, DefusedXmlException) as exc:
            self.review[identifier] = (
                f"Unrecognized tariff published {day.isoformat()}: {type(exc).__name__}"
            )
            return "review"
        key = publication.effective.isoformat()
        previous = self.published.get(key, date.min)
        if day < previous:
            return "ignored"
        if (
            day == previous
            and key in self.records
            and self.records[key].boe_id != identifier
        ):
            self.review[identifier] = f"Conflicting tariff for {key} on {day}"
            return "review"
        self.records[key] = publication
        self.published[key] = day
        self.review.pop(identifier, None)
        return "applied"

    def current(self, day: date) -> Publication | None:
        applicable = (r for r in self.records.values() if r.effective <= day)
        return max(applicable, key=lambda r: r.effective, default=None)

    def upcoming(self, day: date) -> Publication | None:
        future = (r for r in self.records.values() if r.effective > day)
        return min(future, key=lambda r: r.effective, default=None)


async def replay_summaries(ledger, start, end, get_summary, get_xml) -> date | None:
    """Replay calendar days, returning the last fully checked date.

    Network failures abort the scan; the coordinator must not advance its
    persisted watermark until the complete scan succeeds.
    """
    last = None
    for day in scan_days(start, end):
        summary = await get_summary(day)
        if summary is not None:
            for identifier, title in candidates(summary).items():
                document = await get_xml(identifier)
                ledger.add(identifier, title, day, document)
        last = day
    return last
