"""Exporter interfaces for finops_pack output formats."""

from __future__ import annotations

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
        destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _normalize_item(item: Any) -> Any:
        """Normalize items into JSON-serializable values."""
        if is_dataclass(item) and not isinstance(item, type):
            return asdict(item)
        return item


class CsvExporter(Exporter):
    """Placeholder interface for CSV export."""

    def export(self, data: Sequence[Any], destination: Path) -> None:
        """Export data as CSV."""
        raise NotImplementedError("CSV export logic is not implemented yet.")
