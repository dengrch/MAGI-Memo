"""Tests for entity extraction gleaning token limit guard."""

import logging
from unittest.mock import AsyncMock

import pytest

from magi_core.utils import Tokenizer, TokenizerInterface


@pytest.fixture
def _propagate_lightrag_logger(monkeypatch):
    """``magi_core.utils.logger`` sets ``propagate = False`` to avoid noisy
    test output; restore propagation locally so ``caplog`` can capture
    WARNING records emitted from inside ``magi_core.operate``."""
    monkeypatch.setattr(logging.getLogger("magi_core"), "propagate", True)


class DummyTokenizer(TokenizerInterface):
    """Simple 1:1 character-to-token mapping for testing."""

    def encode(self, content: str):
        return [ord(ch) for ch in content]

    def decode(self, tokens):
        return "".join(chr(token) for token in tokens)


def _make_global_config(
    entity_extract_max_gleaning: int = 1,
) -> dict:
    """Build a minimal global_config dict for extract_entities."""
    tokenizer = Tokenizer("dummy", DummyTokenizer())
    extract_func = AsyncMock(return_value="")
    return {
        "llm_model_func": extract_func,
        "role_llm_funcs": {
            "extract": extract_func,
            "keyword": extract_func,
            "query": extract_func,
            "vlm": extract_func,
        },
        "entity_extract_max_gleaning": entity_extract_max_gleaning,
        "entity_extract_max_records": 100,
        "entity_extract_max_entities": 40,
        "addon_params": {},
        "tokenizer": tokenizer,
        "llm_model_max_async": 1,
    }


# Minimal valid extraction result that _process_extraction_result can parse
_EXTRACTION_RESULT = (
    "(entity<|#|>TEST_ENTITY<|#|>CONCEPT<|#|>A test entity)<|COMPLETE|>"
)


def _make_chunks(content: str = "Test content.") -> dict[str, dict]:
    return {
        "chunk-001": {
            "tokens": len(content),
            "content": content,
            "full_doc_id": "doc-001",
            "chunk_order_index": 0,
        }
    }


@pytest.mark.offline
@pytest.mark.asyncio
async def test_gleaning_skipped_when_tokens_exceed_limit(
    monkeypatch, caplog, _propagate_lightrag_logger
):
    """Gleaning must be skipped (with a WARNING) when the projected
    gleaning input — system + history(user+assistant) + continue prompt —
    exceeds ``MAX_EXTRACT_INPUT_TOKENS``.  This prevents
    ``context_length_exceeded`` errors from the LLM provider on the second
    round when the initial response was long.
    """
    from magi_core.operate import extract_entities

    # 10 tokens cannot fit any realistic prompt — guard must trip.
    monkeypatch.setenv("MAX_EXTRACT_INPUT_TOKENS", "10")

    global_config = _make_global_config(entity_extract_max_gleaning=1)
    llm_func = global_config["llm_model_func"]
    llm_func.return_value = _EXTRACTION_RESULT

    with caplog.at_level("WARNING", logger="magi_core"):
        await extract_entities(
            chunks=_make_chunks(),
            global_config=global_config,
        )

    # Only the initial extraction round ran; gleaning was skipped.
    assert llm_func.await_count == 1

    warnings_emitted = [
        rec.getMessage()
        for rec in caplog.records
        if rec.levelname == "WARNING"
        and rec.getMessage().startswith("Gleaning stopped for chunk chunk-001:")
    ]
    assert warnings_emitted, (
        "expected a WARNING log explaining gleaning was skipped due to "
        "token limit; got: "
        f"{[r.getMessage() for r in caplog.records]}"
    )
    # Message must surface both the measured token count and the limit so
    # operators can size MAX_EXTRACT_INPUT_TOKENS appropriately.
    msg = warnings_emitted[0]
    assert "exceeded limit (10)" in msg
    assert "Input tokens (" in msg


@pytest.mark.offline
@pytest.mark.asyncio
async def test_gleaning_proceeds_when_tokens_within_limit(monkeypatch):
    """Gleaning runs normally when the projected input fits the cap."""
    from magi_core.operate import extract_entities

    monkeypatch.setenv("MAX_EXTRACT_INPUT_TOKENS", "999999")

    global_config = _make_global_config(entity_extract_max_gleaning=1)
    llm_func = global_config["llm_model_func"]
    llm_func.return_value = _EXTRACTION_RESULT

    await extract_entities(
        chunks=_make_chunks(),
        global_config=global_config,
    )

    # Both rounds run: initial extraction + one gleaning pass.
    assert llm_func.await_count == 2


@pytest.mark.offline
@pytest.mark.asyncio
async def test_no_gleaning_when_max_gleaning_zero(monkeypatch):
    """``entity_extract_max_gleaning=0`` disables gleaning regardless of
    token budget — the guard is downstream of the feature flag."""
    from magi_core.operate import extract_entities

    monkeypatch.setenv("MAX_EXTRACT_INPUT_TOKENS", "999999")

    global_config = _make_global_config(entity_extract_max_gleaning=0)
    llm_func = global_config["llm_model_func"]
    llm_func.return_value = _EXTRACTION_RESULT

    await extract_entities(
        chunks=_make_chunks(),
        global_config=global_config,
    )

    assert llm_func.await_count == 1


@pytest.mark.offline
@pytest.mark.asyncio
async def test_magi_structured_extraction_never_runs_lightrag_gleaning(monkeypatch):
    """MAGI extracts entity, relation, and Atom records in one structured call."""
    from magi_core.operate import extract_entities

    monkeypatch.setenv("MAX_EXTRACT_INPUT_TOKENS", "999999")
    global_config = _make_global_config(entity_extract_max_gleaning=1)
    global_config["magi_memory_enabled"] = True
    llm_func = global_config["llm_model_func"]
    llm_func.return_value = '{"entities": [], "relationships": []}'

    await extract_entities(
        chunks=_make_chunks(),
        global_config=global_config,
    )

    assert llm_func.await_count == 1


@pytest.mark.offline
@pytest.mark.asyncio
async def test_gleaning_guard_disabled_when_max_tokens_zero(monkeypatch):
    """Setting ``MAX_EXTRACT_INPUT_TOKENS=0`` opts out of the guard so
    gleaning always runs regardless of input size — useful for callers
    whose provider has no hard input ceiling."""
    from magi_core.operate import extract_entities

    monkeypatch.setenv("MAX_EXTRACT_INPUT_TOKENS", "0")

    global_config = _make_global_config(entity_extract_max_gleaning=1)
    llm_func = global_config["llm_model_func"]
    llm_func.return_value = _EXTRACTION_RESULT

    await extract_entities(
        chunks=_make_chunks(),
        global_config=global_config,
    )

    # Guard disabled → gleaning still runs even with tight projected input.
    assert llm_func.await_count == 2
