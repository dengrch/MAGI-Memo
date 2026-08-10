"""Bounded, read-only helpers for the Runtime log summary API."""

from __future__ import annotations

import re
from collections import deque
from pathlib import Path


_LOG_RECORD_PATTERN = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})"
    r" - (?P<logger>[^ ]+) - (?P<level>[A-Z]+) - (?P<message>.*)$"
)
_MAX_LOG_MESSAGE_LENGTH = 20_000


def read_recent_log_entries(
    log_file: Path,
    *,
    limit: int,
    include_access: bool = False,
) -> list[dict[str, str]]:
    """Read newest structured log summaries without exposing file paths."""

    if not log_file.is_file():
        return []
    entries: deque[dict[str, str]] = deque(maxlen=limit)
    current: dict[str, str] | None = None
    with log_file.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            match = _LOG_RECORD_PATTERN.match(line.rstrip())
            if match is None:
                continuation = line.rstrip()
                if current is not None and continuation:
                    current["message"] = (
                        f"{current['message']}\n{continuation}"
                    )[:_MAX_LOG_MESSAGE_LENGTH]
                continue
            item = match.groupdict()
            if not include_access and item["logger"] == "uvicorn.access":
                current = None
                continue
            item["message"] = item["message"][:_MAX_LOG_MESSAGE_LENGTH]
            entries.append(item)
            current = item
    return list(reversed(entries))
