"""Publishing utilities for finops_pack."""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path


def publish_preview_site(
    *,
    preview_dir: str | Path,
    html: str,
    stylesheet_source: str | Path,
    asset_copies: Sequence[tuple[str | Path, str | Path]] = (),
) -> Path:
    """Write a self-contained preview site under the preview directory."""
    preview_root = Path(preview_dir)
    preview_root.mkdir(parents=True, exist_ok=True)

    index_path = preview_root / "index.html"
    index_path.write_text(html, encoding="utf-8")

    stylesheet_path = preview_root / "style.css"
    source_stylesheet_path = Path(stylesheet_source)
    if source_stylesheet_path.resolve() != stylesheet_path.resolve():
        shutil.copyfile(source_stylesheet_path, stylesheet_path)

    for source, destination in asset_copies:
        source_path = Path(source)
        destination_path = Path(destination)
        if not destination_path.is_absolute():
            destination_path = preview_root / destination_path
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        if source_path.resolve() == destination_path.resolve():
            continue
        shutil.copyfile(source_path, destination_path)

    return index_path
