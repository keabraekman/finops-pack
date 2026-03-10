"""Exporter interfaces for finops_pack output formats."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Sequence


class Exporter(ABC):
    """Abstract base class for output exporters."""

    @abstractmethod
    def export(self, data: Sequence[Any], destination: Path) -> None:
        """Export data to the given destination."""
        raise NotImplementedError


class JsonExporter(Exporter):
    """Placeholder interface for JSON export."""

    def export(self, data: Sequence[Any], destination: Path) -> None:
        """Export data as JSON."""
        raise NotImplementedError("JSON export logic is not implemented yet.")


class CsvExporter(Exporter):
    """Placeholder interface for CSV export."""

    def export(self, data: Sequence[Any], destination: Path) -> None:
        """Export data as CSV."""
        raise NotImplementedError("CSV export logic is not implemented yet.")