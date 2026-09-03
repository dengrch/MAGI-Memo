import logging

from magi_core.utils import LightragPathFilter


def _access_record(method: str, path: str, status: int) -> logging.LogRecord:
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:50101", method, path, "1.1", status),
        exc_info=None,
    )


def test_runtime_log_polling_is_filtered() -> None:
    path_filter = LightragPathFilter()

    assert path_filter.filter(_access_record("GET", "/runtime/logs", 200)) is False
    assert (
        path_filter.filter(_access_record("GET", "/runtime/logs?limit=5", 200))
        is False
    )


def test_runtime_log_errors_and_other_access_logs_are_retained() -> None:
    path_filter = LightragPathFilter()

    assert path_filter.filter(_access_record("GET", "/runtime/logs", 500)) is True
    assert path_filter.filter(_access_record("GET", "/runtime/status", 200)) is True


def test_dreaming_status_polling_is_filtered_but_errors_are_retained() -> None:
    path_filter = LightragPathFilter()

    assert path_filter.filter(_access_record("GET", "/dreaming/status", 200)) is False
    assert path_filter.filter(_access_record("GET", "/dreaming/status", 500)) is True


def test_exploration_polling_is_filtered_but_errors_are_retained() -> None:
    path_filter = LightragPathFilter()

    assert path_filter.filter(_access_record("GET", "/memory/explore/traces", 200)) is False
    assert (
        path_filter.filter(
            _access_record(
                "GET",
                "/memory/explore/traces/explore-1/events?after_seq=3",
                200,
            )
        )
        is False
    )
    assert (
        path_filter.filter(_access_record("GET", "/memory/explore/traces", 500))
        is True
    )
