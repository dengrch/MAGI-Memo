"""Default port contract for the integrated MAGI Memo web service."""

from __future__ import annotations

import sys

import pytest

from magi_core.api.config import parse_args
from magi_core.constants import DEFAULT_SERVER_PORT


pytestmark = pytest.mark.offline


def test_integrated_server_defaults_to_magi_port(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["magi-core-server"])
    monkeypatch.delenv("PORT", raising=False)

    args = parse_args()

    assert DEFAULT_SERVER_PORT == 34913
    assert args.port == DEFAULT_SERVER_PORT


def test_port_environment_override_is_honoured(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["magi-core-server"])
    monkeypatch.setenv("PORT", "4100")

    assert parse_args().port == 4100
