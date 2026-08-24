from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from magi_core.operate import (
    _process_json_extraction_result,
    _truncate_vdb_content,
)
from magi_core.prompt import PROMPTS


class MagiAtomExtractionTests(unittest.TestCase):
    def test_prompt_contract_and_parser_return_atom_records(self) -> None:
        self.assertIn(
            "Do not write entity or relationship summaries",
            PROMPTS["magi_atom_extraction_json_system_prompt"],
        )
        payload = {
            "entities": [
                {
                    "name": "Alice",
                    "type": "Person",
                    "atoms": [
                        {
                            "content": "Alice joined MAGI.",
                            "valid_at": "2026-08-05T00:00:00+00:00",
                            "invalid_at": None,
                            "temporal_text": "today",
                            "temporal_precision": "day",
                            "confidence": 0.95,
                            "importance": 0.8,
                        }
                    ],
                },
                {
                    "name": "MAGI",
                    "type": "Organization",
                    "atoms": [
                        {
                            "content": "MAGI accepted Alice as a member.",
                            "valid_at": "2026-08-05T00:00:00+00:00",
                            "invalid_at": None,
                        }
                    ],
                },
            ],
            "relationships": [
                {
                    "source": "Alice",
                    "target": "MAGI",
                    "keywords": "membership",
                    "atoms": [
                        {
                            "predicate": "member_of",
                            "content": "Alice became a member of MAGI.",
                            "valid_at": "2026-08-05T00:00:00+00:00",
                            "invalid_at": None,
                        }
                    ],
                }
            ],
        }

        nodes, edges = asyncio.run(
            _process_json_extraction_result(
                json.dumps(payload),
                "chunk-a",
                1,
                magi_memory_enabled=True,
            )
        )
        self.assertEqual(nodes["Alice"][0]["description"], "Alice joined MAGI.")
        self.assertEqual(
            nodes["Alice"][0]["atom_payload"]["temporal_text"], "today"
        )
        relation = edges[("Alice", "MAGI")][0]
        self.assertEqual(relation["atom_payload"]["predicate"], "member_of")
        self.assertNotIn("description", relation["atom_payload"])

    def test_relation_without_entity_container_is_rejected(self) -> None:
        payload = {
            "entities": [
                {
                    "name": "Alice",
                    "type": "Person",
                    "atoms": [{"content": "Alice is a person."}],
                }
            ],
            "relationships": [
                {
                    "source": "Alice",
                    "target": "MAGI",
                    "keywords": "membership",
                    "atoms": [
                        {
                            "predicate": "member_of",
                            "content": "Alice is a member of MAGI.",
                        }
                    ],
                }
            ],
        }

        _, edges = asyncio.run(
            _process_json_extraction_result(
                json.dumps(payload),
                "chunk-missing-endpoint",
                1,
                magi_memory_enabled=True,
            )
        )
        self.assertEqual(edges, {})

    def test_relation_only_endpoint_container_is_retained_without_fake_atom(
        self,
    ) -> None:
        payload = {
            "entities": [
                {"name": "Alice", "type": "Person", "atoms": []},
                {"name": "MAGI", "type": "Organization", "atoms": []},
            ],
            "relationships": [
                {
                    "source": "Alice",
                    "target": "MAGI",
                    "keywords": "membership",
                    "atoms": [
                        {
                            "predicate": "member_of",
                            "content": "Alice is a member of MAGI.",
                        }
                    ],
                }
            ],
        }

        with patch("magi_core.operate.logger.warning") as warning:
            nodes, edges = asyncio.run(
                _process_json_extraction_result(
                    json.dumps(payload),
                    "chunk-relation-only",
                    1,
                    magi_memory_enabled=True,
                )
            )

        self.assertEqual(nodes["Alice"][0]["description"], "")
        self.assertIsNone(nodes["Alice"][0]["atom_payload"])
        self.assertTrue(nodes["Alice"][0]["magi_endpoint_only"])
        self.assertEqual(
            edges[("Alice", "MAGI")][0]["description"],
            "Alice is a member of MAGI.",
        )
        warning_text = "\n".join(str(call.args[0]) for call in warning.call_args_list)
        self.assertIn("current extraction container", warning_text)
        self.assertIn("relationship endpoint candidate", warning_text)

    def test_atom_citation_is_removed_only_from_embedding_text(self) -> None:
        graph_description = (
            "[atom-abc123] [status=active; "
            "valid_at=2026-08-05T00:00:00+00:00] Alice joined MAGI."
        )
        vector_text = _truncate_vdb_content(
            graph_description,
            {"magi_memory_enabled": True},
            "entity:Alice",
        )
        self.assertNotIn("[atom-abc123]", vector_text)
        self.assertIn("[status=active", vector_text)
        self.assertIn("Alice joined MAGI.", vector_text)


if __name__ == "__main__":
    unittest.main()
