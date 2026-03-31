"""S3 publishing helpers for finops_pack."""

from __future__ import annotations

import mimetypes
import posixpath
import uuid
import zipfile
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any

MAX_PRESIGN_SECONDS = 7 * 24 * 60 * 60
DEFAULT_BUNDLE_NAME = "report-bundle.zip"


@dataclass(frozen=True)
class PublishAsset:
    """Local file that should be uploaded under the report prefix."""

    source_path: str | Path
    object_name: str
    label: str | None = None
    description: str | None = None
    include_in_index: bool = False
    content_type: str | None = None


@dataclass(frozen=True)
class PublishedReport:
    """Published report metadata returned to the CLI."""

    bucket: str
    client_id: str
    run_id: str
    prefix: str
    report_url: str
    exports_csv_url: str
    exports_json_url: str
    bundle_s3_uri: str
    deleted_prefix_count: int


def write_preview_bundle(*, preview_dir: str | Path, destination: str | Path) -> Path:
    """Write a zipped bundle of the local preview site."""
    destination_path = Path(destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    destination_path.write_bytes(_build_preview_bundle_bytes(preview_dir))
    return destination_path


def publish_report_site_to_s3(
    *,
    session: Any,
    bucket: str,
    client_id: str,
    retention_days: int,
    preview_dir: str | Path,
    assets: Sequence[PublishAsset],
    build_index_html: Callable[[list[dict[str, str]], str], str],
) -> PublishedReport:
    """Upload report artifacts to S3 and return presigned access URLs."""
    s3_client = session.client("s3")
    run_id = _build_run_id()
    prefix = _build_prefix(client_id, run_id)

    _upload_bundle(
        s3_client,
        bucket=bucket,
        key=_build_object_key(prefix, DEFAULT_BUNDLE_NAME),
        preview_dir=preview_dir,
    )

    asset_urls: dict[str, str] = {}
    for asset in assets:
        _upload_file(
            s3_client,
            bucket=bucket,
            key=_build_object_key(prefix, asset.object_name),
            path=asset.source_path,
            content_type=asset.content_type,
        )
        asset_urls[asset.object_name] = _generate_presigned_url(
            s3_client,
            bucket=bucket,
            key=_build_object_key(prefix, asset.object_name),
            retention_days=retention_days,
        )

    stylesheet_url = asset_urls.get("style.css")
    if stylesheet_url is None:
        raise ValueError("style.css must be uploaded before publishing the S3 report index.")

    bundle_url = _generate_presigned_url(
        s3_client,
        bucket=bucket,
        key=_build_object_key(prefix, DEFAULT_BUNDLE_NAME),
        retention_days=retention_days,
    )
    download_links = [
        {
            "label": "Download All",
            "description": "Zipped preview bundle with the report HTML and linked artifacts.",
            "filename": DEFAULT_BUNDLE_NAME,
            "format": "ZIP",
            "href": bundle_url,
            "variant": "primary",
        },
        *_build_external_download_links(assets, asset_urls),
    ]
    index_html = build_index_html(download_links, stylesheet_url)
    index_key = _build_object_key(prefix, "index.html")
    _upload_bytes(
        s3_client,
        bucket=bucket,
        key=index_key,
        body=index_html.encode("utf-8"),
        content_type="text/html; charset=utf-8",
    )

    report_url = _generate_presigned_url(
        s3_client,
        bucket=bucket,
        key=index_key,
        retention_days=retention_days,
    )
    deleted_prefix_count = _delete_expired_client_prefixes(
        s3_client,
        bucket=bucket,
        client_id=client_id,
        current_prefix=prefix,
        retention_days=retention_days,
    )

    exports_csv_url = _find_asset_url(asset_urls, "exports.csv")
    exports_json_url = _find_asset_url(asset_urls, "exports.json")

    return PublishedReport(
        bucket=bucket,
        client_id=client_id,
        run_id=run_id,
        prefix=prefix,
        report_url=report_url,
        exports_csv_url=exports_csv_url,
        exports_json_url=exports_json_url,
        bundle_s3_uri=f"s3://{bucket}/{_build_object_key(prefix, DEFAULT_BUNDLE_NAME)}",
        deleted_prefix_count=deleted_prefix_count,
    )


def _build_run_id(now: datetime | None = None) -> str:
    """Return a timestamped, collision-resistant run ID."""
    current_time = now or datetime.now(UTC)
    return f"{current_time.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"


def _build_prefix(client_id: str, run_id: str) -> str:
    """Return the S3 prefix for a single report run."""
    return posixpath.join(client_id.strip("/"), run_id.strip("/"))


def _build_object_key(prefix: str, object_name: str) -> str:
    """Join an S3 prefix and relative object name."""
    return posixpath.join(prefix.strip("/"), object_name.strip("/"))


def _build_external_download_links(
    assets: Sequence[PublishAsset],
    asset_urls: dict[str, str],
) -> list[dict[str, str]]:
    """Build dashboard download-link payloads with presigned hrefs."""
    download_links: list[dict[str, str]] = []
    for asset in assets:
        if not asset.include_in_index:
            continue
        if asset.label is None or asset.description is None:
            raise ValueError(
                f"{asset.object_name} is marked include_in_index=True "
                "but is missing label/description."
            )
        href = asset_urls.get(asset.object_name)
        if href is None:
            raise ValueError(f"Missing presigned URL for {asset.object_name}.")
        target_path = Path(asset.object_name)
        download_links.append(
            {
                "label": asset.label,
                "description": asset.description,
                "filename": target_path.name,
                "format": target_path.suffix.lstrip(".").upper() or "FILE",
                "href": href,
                "variant": "primary" if target_path.suffix == ".zip" else "default",
            }
        )
    return download_links


def _find_asset_url(asset_urls: dict[str, str], filename: str) -> str:
    """Find a presigned URL by uploaded filename."""
    for object_name, url in asset_urls.items():
        if Path(object_name).name == filename:
            return url
    raise ValueError(f"{filename} must be uploaded for S3 publishing.")


def _upload_bundle(
    s3_client: Any,
    *,
    bucket: str,
    key: str,
    preview_dir: str | Path,
) -> None:
    """Zip the local preview directory and upload it as a bundle."""
    _upload_bytes(
        s3_client,
        bucket=bucket,
        key=key,
        body=_build_preview_bundle_bytes(preview_dir),
        content_type="application/zip",
    )


def _upload_file(
    s3_client: Any,
    *,
    bucket: str,
    key: str,
    path: str | Path,
    content_type: str | None = None,
) -> None:
    """Upload a single local file to S3."""
    source_path = Path(path)
    _upload_bytes(
        s3_client,
        bucket=bucket,
        key=key,
        body=source_path.read_bytes(),
        content_type=content_type or _guess_content_type(source_path),
    )


def _upload_bytes(
    s3_client: Any,
    *,
    bucket: str,
    key: str,
    body: bytes,
    content_type: str,
) -> None:
    """Upload raw bytes to S3 with a stable content type."""
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType=content_type,
    )


def _guess_content_type(path: Path) -> str:
    """Guess a file's content type with safe defaults."""
    if path.suffix == ".csv":
        return "text/csv; charset=utf-8"
    if path.suffix == ".json":
        return "application/json"
    if path.suffix == ".html":
        return "text/html; charset=utf-8"
    if path.suffix == ".css":
        return "text/css; charset=utf-8"
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def _build_preview_bundle_bytes(preview_dir: str | Path) -> bytes:
    """Return the zipped bytes for a preview directory."""
    preview_root = Path(preview_dir)
    bundle_buffer = BytesIO()
    with zipfile.ZipFile(bundle_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(preview_root.rglob("*")):
            if path.is_dir() or path.name == DEFAULT_BUNDLE_NAME:
                continue
            archive.write(path, arcname=path.relative_to(preview_root).as_posix())
    return bundle_buffer.getvalue()


def _generate_presigned_url(
    s3_client: Any,
    *,
    bucket: str,
    key: str,
    retention_days: int,
) -> str:
    """Generate a GET presigned URL capped at the SigV4 seven-day maximum."""
    expires_in = min(retention_days * 24 * 60 * 60, MAX_PRESIGN_SECONDS)
    return s3_client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires_in,
    )


def _delete_expired_client_prefixes(
    s3_client: Any,
    *,
    bucket: str,
    client_id: str,
    current_prefix: str,
    retention_days: int,
) -> int:
    """Delete old run prefixes under a client-id folder."""
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    client_prefix = f"{client_id.strip('/')}/"
    prefix_keys: dict[str, list[str]] = defaultdict(list)
    prefix_last_modified: dict[str, datetime] = {}

    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=client_prefix):
        for item in page.get("Contents", []):
            key = item["Key"]
            relative_key = key[len(client_prefix) :]
            run_segment, separator, _ = relative_key.partition("/")
            if not separator or not run_segment:
                continue
            run_prefix = _build_prefix(client_id, run_segment)
            prefix_keys[run_prefix].append(key)
            last_modified = item["LastModified"]
            previous_last_modified = prefix_last_modified.get(run_prefix)
            if previous_last_modified is None or last_modified > previous_last_modified:
                prefix_last_modified[run_prefix] = last_modified

    expired_prefixes = [
        prefix
        for prefix, last_modified in prefix_last_modified.items()
        if prefix != current_prefix and last_modified < cutoff
    ]
    for prefix in expired_prefixes:
        _delete_objects(s3_client, bucket=bucket, keys=prefix_keys[prefix])

    return len(expired_prefixes)


def _delete_objects(s3_client: Any, *, bucket: str, keys: Sequence[str]) -> None:
    """Delete S3 objects in API-sized batches."""
    batch: list[str] = []
    for key in keys:
        batch.append(key)
        if len(batch) == 1000:
            _delete_object_batch(s3_client, bucket=bucket, keys=batch)
            batch = []
    if batch:
        _delete_object_batch(s3_client, bucket=bucket, keys=batch)


def _delete_object_batch(s3_client: Any, *, bucket: str, keys: Sequence[str]) -> None:
    """Delete one S3 object batch."""
    s3_client.delete_objects(
        Bucket=bucket,
        Delete={"Objects": [{"Key": key} for key in keys]},
    )
