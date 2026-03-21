"""Exporter interfaces for finops_pack output formats."""

from __future__ import annotations

import csv
import json
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


class Exporter(ABC):
    """Abstract base class for output exporters."""

    @abstractmethod
    def export(self, data: Sequence[Any], destination: Path) -> None:
        """Export data to the given destination."""
        raise NotImplementedError


class JsonExporter(Exporter):
    """Export dataclass or dict data as JSON."""

    def export(self, data: Sequence[Any], destination: Path) -> None:
        """Export data as JSON."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = [self._normalize_item(item) for item in data]
        destination.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")

    @staticmethod
    def _normalize_item(item: Any) -> Any:
        """Normalize items into JSON-serializable values."""
        if is_dataclass(item) and not isinstance(item, type):
            return asdict(item)
        return item


class CsvExporter(Exporter):
    """Export dict-like rows as CSV."""

    def __init__(self, fieldnames: Sequence[str]) -> None:
        self.fieldnames = list(fieldnames)

    def export(self, data: Sequence[Any], destination: Path) -> None:
        """Export data as CSV."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        rows = [JsonExporter._normalize_item(item) for item in data]

        with destination.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                if not isinstance(row, dict):
                    raise TypeError("CsvExporter expects dict-like rows after normalization.")
                writer.writerow({field: row.get(field, "") for field in self.fieldnames})
