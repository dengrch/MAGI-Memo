from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from magi_core.constants import GRAPH_FIELD_SEP
from magi_core.operate import (
    _handle_entity_relation_summary,
    _summarize_descriptions,
)
from magi_core.utils import Tokenizer, TokenizerInterface


class _CharacterTokenizer(TokenizerInterface):
    def encode(self, content: str):
        return [ord(character) for character in content]

    def decode(self, tokens):
        return "".join(chr(token) for token in tokens)


def _summary_config(summary: str) -> tuple[dict, AsyncMock]:
    extract = AsyncMock(return_value=summary)
    return (
        {
            "role_llm_funcs": {"extract": extract},
            "addon_params": {},
            "_resolved_summary_language": "English",
            "summary_length_recommended": 200,
            "summary_context_size": 100_000,
            "summary_max_tokens": 100_000,
            "force_llm_summary_on_merge": 3,
            "magi_preserve_atom_order": True,
            "tokenizer": Tokenizer("characters", _CharacterTokenizer()),
        },
        extract,
    )


@pytest.mark.offline
@pytest.mark.asyncio
async def test_magi_threshold_summary_replaces_atom_list_and_keeps_lineage():
    descriptions = [
        "[atom-one] [status=invalid] Alice lived in Paris.",
        "[atom-two] [status=active] Alice lives in Rome.",
        "[atom-three] [status=active] Alice works as an architect.",
    ]
    expected = (
        "Alice formerly lived in Paris. [atom-one] "
        "She now lives in Rome [atom-two] and works as an architect. [atom-three]"
    )
    config, extract = _summary_config(expected)

    description, llm_was_used = await _handle_entity_relation_summary(
        "Entity",
        "Alice",
        descriptions,
        GRAPH_FIELD_SEP,
        config,
    )

    assert llm_was_used is True
    assert description == expected
    assert GRAPH_FIELD_SEP not in description
    prompt = extract.await_args.args[0]
    assert "[atom-one]" in prompt
    assert "Preserve every input Atom lineage tag" in prompt


@pytest.mark.offline
@pytest.mark.asyncio
async def test_untraceable_summary_falls_back_to_original_atom_descriptions():
    descriptions = [
        "[atom-one] [status=invalid] Alice lived in Paris.",
        "[atom-two] [status=active] Alice lives in Rome.",
    ]
    config, _ = _summary_config("Alice lives in Rome. [atom-two]")

    description = await _summarize_descriptions(
        "Entity",
        "Alice",
        descriptions,
        config,
    )

    assert description == GRAPH_FIELD_SEP.join(descriptions)
