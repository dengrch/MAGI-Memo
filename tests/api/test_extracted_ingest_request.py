from __future__ import annotations

import sys

_original_argv = sys.argv[:]
sys.argv = [sys.argv[0]]
from magi_core.api.lightrag_server import ExtractedIngestRequest  # noqa: E402
sys.argv = _original_argv


def test_extracted_request_allows_relations_without_explicit_entities() -> None:
    request = ExtractedIngestRequest.model_validate(
        {
            "content": "Alice joined MAGI.",
            "relations": [
                {
                    "source": "Alice",
                    "target": "MAGI",
                    "atoms": [
                        {
                            "content": "Alice joined MAGI.",
                            "predicate": "member_of",
                        }
                    ],
                }
            ],
        }
    )

    assert request.entities == []
    assert request.relations[0].source == "Alice"
