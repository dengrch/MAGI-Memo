from __future__ import annotations

import asyncio
import json
import unittest
from datetime import datetime, timezone

from magi_core.memory import AtomDecision, AtomRecord
from magi_core.memory.decisions import LLMMemoryDecisionProvider
from magi_core.memory.models import (
    AtomResolutionRequest,
    CandidateMatch,
    EntityResolutionRequest,
)


class DecisionEngine:
    def __init__(self, decision: str) -> None:
        self.decision = decision
        self.calls = 0

    async def _deduplicate(self, prompt: str, **kwargs) -> str:
        self.calls += 1
        requests = json.loads(
            prompt.split("---New Atom requests---\n", 1)[1].split(
                "\n\n---Deduplicated candidate Atom table---", 1
            )[0]
        )
        return json.dumps(
            {
                "decisions": [
                    {
                        "new_atom_id": request["atom"]["id"],
                        "decision": self.decision,
                        "matched_atom_id": "atom-existing",
                        "confidence": 0.9,
                        "reason": "test",
                    }
                    for request in requests
                ]
            }
        )

    def _build_global_config(self):
        return {"role_llm_funcs": {"deduplicate": self._deduplicate}}


class ResolutionEngine:
    def __init__(self) -> None:
        self.calls = 0
        self.requests = []

    async def _resolve(self, prompt: str, **kwargs) -> str:
        self.calls += 1
        self.requests.append(prompt)
        return json.dumps(
            {
                "resolutions": [
                    {
                        "key": "matched",
                        "entity_id": "entity-existing",
                        "confidence": 0.9,
                        "reason": "same entity",
                    }
                ]
            }
        )

    def _build_global_config(self):
        return {"role_llm_funcs": {"resolve": self._resolve}}


class MemoryDecisionTests(unittest.TestCase):
    def test_entity_resolution_skips_llm_when_every_candidate_set_is_empty(
        self,
    ) -> None:
        async def scenario() -> None:
            engine = ResolutionEngine()
            provider = LLMMemoryDecisionProvider(engine)
            request = EntityResolutionRequest(
                key="new",
                name="Alice",
                aliases=("Alice",),
                entity_type="person",
                atom_texts=("Alice joined MAGI.",),
                candidates=(),
            )

            result = await provider.resolve_entities(
                requests=[request], recent_episodes=[]
            )

            self.assertEqual(engine.calls, 0)
            self.assertIsNone(result["new"].canonical_entity_id)
            self.assertEqual(result["new"].canonical_name, "Alice")

        asyncio.run(scenario())

    def test_entity_resolution_sends_only_candidate_bearing_requests(self) -> None:
        async def scenario() -> None:
            engine = ResolutionEngine()
            provider = LLMMemoryDecisionProvider(engine)
            requests = [
                EntityResolutionRequest(
                    key="new",
                    name="Alice",
                    aliases=("Alice",),
                    entity_type="person",
                    atom_texts=("Alice joined MAGI.",),
                    candidates=(),
                ),
                EntityResolutionRequest(
                    key="matched",
                    name="Bob",
                    aliases=("Bob",),
                    entity_type="person",
                    atom_texts=("Bob returned.",),
                    candidates=(
                        CandidateMatch(
                            object_id="entity-existing",
                            score=0.9,
                            text="Robert\nRobert is Bob.",
                            match_kind="embedding",
                        ),
                    ),
                ),
            ]

            result = await provider.resolve_entities(
                requests=requests, recent_episodes=[]
            )

            self.assertEqual(engine.calls, 1)
            self.assertNotIn('"key": "new"', engine.requests[0])
            self.assertEqual(result["matched"].canonical_entity_id, "entity-existing")
            self.assertEqual(result["new"].canonical_name, "Alice")

        asyncio.run(scenario())

    def test_uppercase_prompt_decisions_map_to_domain_enum(self) -> None:
        async def scenario() -> None:
            atom = AtomRecord(
                id="atom-new",
                workspace_id="workspace-a",
                owner_id="entity-a",
                content="Alice joined MAGI.",
                valid_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
            )
            candidates = [
                CandidateMatch(
                    object_id="atom-existing",
                    score=1.0,
                    text="Alice joined MAGI.",
                    match_kind="exact_text",
                )
            ]
            for expected in AtomDecision:
                provider = LLMMemoryDecisionProvider(DecisionEngine(expected.name))
                result = await provider.classify_atom(atom=atom, candidates=candidates)
                self.assertEqual(result.decision, expected)

        asyncio.run(scenario())

    def test_atom_resolution_batches_multiple_atoms_in_one_llm_call(self) -> None:
        async def scenario() -> None:
            engine = DecisionEngine("INDEPENDENT")
            provider = LLMMemoryDecisionProvider(engine)
            reference = datetime(2026, 8, 5, tzinfo=timezone.utc)
            candidates = (
                CandidateMatch(
                    object_id="atom-existing",
                    score=0.9,
                    text="stored candidate",
                    match_kind="embedding",
                ),
            )
            requests = [
                AtomResolutionRequest(
                    atom=AtomRecord(
                        id=f"atom-new-{index}",
                        workspace_id="workspace-a",
                        owner_id="entity-a",
                        content=f"Fact {index}",
                        valid_at=reference,
                    ),
                    candidates=candidates,
                )
                for index in range(2)
            ]

            result = await provider.resolve_atoms(
                requests=requests,
                recent_episodes=[],
            )

            self.assertEqual(engine.calls, 1)
            self.assertEqual(set(result), {"atom-new-0", "atom-new-1"})

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
