from pathlib import Path

from magi_core.api.runtime_logs import read_recent_log_entries


def test_recent_log_reader_returns_newest_structured_non_access_rows(
    tmp_path: Path,
) -> None:
    log_file = tmp_path / "magi_core.log"
    log_file.write_text(
        "\n".join(
            (
                "2026-08-10 10:00:00,001 - magi_core - INFO - first",
                "traceback continuation is retained",
                "2026-08-10 10:00:01,002 - uvicorn.access - INFO - GET /health",
                "2026-08-10 10:00:02,003 - magi_core - WARNING - second",
                "2026-08-10 10:00:03,004 - uvicorn.error - ERROR - third",
            )
        ),
        encoding="utf-8",
    )

    rows = read_recent_log_entries(log_file, limit=2)

    assert [row["message"] for row in rows] == ["third", "second"]
    assert [row["level"] for row in rows] == ["ERROR", "WARNING"]
    assert all(row["logger"] != "uvicorn.access" for row in rows)


def test_recent_log_reader_keeps_multiline_details(tmp_path: Path) -> None:
    log_file = tmp_path / "magi_core.log"
    log_file.write_text(
        "\n".join(
            (
                "2026-08-10 10:00:00,001 - magi_core - ERROR - failed",
                "Traceback (most recent call last):",
                "  File \"runtime.py\", line 1, in create",
                "ValueError: invalid workspace",
            )
        ),
        encoding="utf-8",
    )

    rows = read_recent_log_entries(log_file, limit=1)

    assert rows[0]["message"] == (
        "failed\nTraceback (most recent call last):\n"
        '  File "runtime.py", line 1, in create\n'
        "ValueError: invalid workspace"
    )
