from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock
from zipfile import ZipFile

import finops_pack.publish.s3 as s3_publish
from finops_pack.publish import (
    PublishAsset,
    load_previous_summary_from_s3,
    publish_report_site_to_s3,
)


def test_publish_report_site_to_s3_uploads_bundle_and_cleans_up_old_prefixes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    preview_dir = tmp_path / "out"
    downloads_dir = preview_dir / "downloads"
    schedule_dir = preview_dir / "schedule"
    downloads_dir.mkdir(parents=True)
    schedule_dir.mkdir(parents=True)

    (preview_dir / "index.html").write_text("<html>local preview</html>", encoding="utf-8")
    (preview_dir / "style.css").write_text("body { color: black; }", encoding="utf-8")
    (downloads_dir / "exports.csv").write_text("col1,col2\n1,2\n", encoding="utf-8")
    (downloads_dir / "exports.json").write_text('[{"id": 1}]\n', encoding="utf-8")
    (preview_dir / "summary.json").write_text('{"ok": true}\n', encoding="utf-8")
    (schedule_dir / "schedule_recs.csv").write_text("instanceId\ni-123\n", encoding="utf-8")

    fixed_now = datetime(2026, 3, 30, 12, 0, tzinfo=UTC)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return fixed_now if tz is not None else fixed_now.replace(tzinfo=None)

    monkeypatch.setattr(s3_publish, "datetime", FixedDateTime)
    s3_client = Mock()
    paginator = Mock()
    paginator.paginate.return_value = [
        {
            "Contents": [
                {
                    "Key": "acme/old-run/index.html",
                    "LastModified": fixed_now - timedelta(days=10),
                },
                {
                    "Key": "acme/old-run/downloads/exports.csv",
                    "LastModified": fixed_now - timedelta(days=10),
                },
                {
                    "Key": "acme/recent-run/index.html",
                    "LastModified": fixed_now - timedelta(days=2),
                },
            ]
        }
    ]
    s3_client.get_paginator.return_value = paginator
    s3_client.generate_presigned_url.side_effect = lambda _operation, Params, ExpiresIn: (
        f"https://example.com/{Params['Key']}?exp={ExpiresIn}"
    )

    session = Mock()
    session.client.return_value = s3_client

    captured: dict[str, object] = {}

    def build_index_html(download_links: list[dict[str, str]], stylesheet_path: str) -> str:
        captured["download_links"] = download_links
        captured["stylesheet_path"] = stylesheet_path
        return "<html>published</html>"

    result = publish_report_site_to_s3(
        session=session,
        bucket="report-bucket",
        client_id="acme",
        run_id="run-123",
        retention_days=7,
        preview_dir=preview_dir,
        assets=[
            PublishAsset(
                source_path=preview_dir / "style.css",
                object_name="style.css",
            ),
            PublishAsset(
                source_path=downloads_dir / "exports.csv",
                object_name="downloads/exports.csv",
                label="COH Export CSV",
                description="CSV export.",
                include_in_index=True,
            ),
            PublishAsset(
                source_path=downloads_dir / "exports.json",
                object_name="downloads/exports.json",
                label="COH Export JSON",
                description="JSON export.",
                include_in_index=True,
            ),
            PublishAsset(
                source_path=preview_dir / "summary.json",
                object_name="summary.json",
                label="Summary JSON",
                description="Summary output.",
                include_in_index=True,
            ),
        ],
        build_index_html=build_index_html,
    )

    assert result.prefix == "acme/run-123"
    assert result.report_url == "https://example.com/acme/run-123/index.html?exp=604800"
    assert result.exports_csv_url == (
        "https://example.com/acme/run-123/downloads/exports.csv?exp=604800"
    )
    assert result.exports_json_url == (
        "https://example.com/acme/run-123/downloads/exports.json?exp=604800"
    )
    assert result.bundle_s3_uri == "s3://report-bucket/acme/run-123/report-bundle.zip"
    assert result.deleted_prefix_count == 1

    assert captured["stylesheet_path"] == "https://example.com/acme/run-123/style.css?exp=604800"
    download_links = captured["download_links"]
    assert isinstance(download_links, list)
    assert [link["filename"] for link in download_links] == [
        "report-bundle.zip",
        "exports.csv",
        "exports.json",
        "summary.json",
    ]
    assert (
        download_links[0]["href"] == "https://example.com/acme/run-123/report-bundle.zip?exp=604800"
    )
    assert download_links[0]["variant"] == "primary"
    assert download_links[1]["href"] == (
        "https://example.com/acme/run-123/downloads/exports.csv?exp=604800"
    )

    uploaded_keys = [call.kwargs["Key"] for call in s3_client.put_object.call_args_list]
    assert uploaded_keys == [
        "acme/run-123/report-bundle.zip",
        "acme/run-123/style.css",
        "acme/run-123/downloads/exports.csv",
        "acme/run-123/downloads/exports.json",
        "acme/run-123/summary.json",
        "acme/run-123/index.html",
    ]

    bundle_upload = next(
        call for call in s3_client.put_object.call_args_list if call.kwargs["Key"].endswith("zip")
    )
    with ZipFile(BytesIO(bundle_upload.kwargs["Body"])) as archive:
        assert sorted(archive.namelist()) == [
            "downloads/exports.csv",
            "downloads/exports.json",
            "index.html",
            "schedule/schedule_recs.csv",
            "style.css",
            "summary.json",
        ]

    s3_client.delete_objects.assert_called_once_with(
        Bucket="report-bucket",
        Delete={
            "Objects": [
                {"Key": "acme/old-run/index.html"},
                {"Key": "acme/old-run/downloads/exports.csv"},
            ]
        },
    )


def test_load_previous_summary_from_s3_returns_latest_prior_summary() -> None:
    s3_client = Mock()
    paginator = Mock()
    paginator.paginate.return_value = [
        {
            "Contents": [
                {"Key": "acme/20260329T010203Z-prev/summary.json"},
                {"Key": "acme/20260331T010203Z-prev/summary.json"},
            ]
        }
    ]
    s3_client.get_paginator.return_value = paginator
    s3_client.get_object.return_value = {
        "Body": BytesIO(b'{"run": {"generated_at": "2026-03-31 01:02:03 UTC"}}')
    }
    session = Mock()
    session.client.return_value = s3_client

    result = load_previous_summary_from_s3(
        session=session,
        bucket="report-bucket",
        client_id="acme",
        current_run_id="20260401T010203Z-current",
    )

    assert result is not None
    assert result.run_id == "20260331T010203Z-prev"
    assert result.summary_key == "acme/20260331T010203Z-prev/summary.json"
