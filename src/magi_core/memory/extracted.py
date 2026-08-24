"""Validation and pipeline projection for caller-extracted memory."""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any


EXTRACTED_MEMORY_SCHEMA = "magi.extracted-memory.v1"


def extracted_payload_to_chunk_results(
    payload: dict[str, Any],
    *,
    source_id: str,
    file_path: str,
) -> list[tuple[dict[str, list[dict[str, Any]]], dict[tuple[str, str], list[dict[str, Any]]]]]:
    """Convert the public extracted-memory payload to the strict commit seam.

    ``source_id`` is a real chunk id created by the retained document pipeline,
    so query citations and document hard-delete recovery keep working exactly as
    they do for LLM-extracted knowledge.
    """

    if not isinstance(payload, dict):
        raise TypeError("extracted memory payload must be an object")
    if payload.get("schema") != EXTRACTED_MEMORY_SCHEMA:
        raise ValueError(
            f"unsupported extracted memory schema {payload.get('schema')!r}"
        )
    entities = payload.get("entities")
    relations = payload.get("relations", [])
    if not isinstance(entities, list) or not entities:
        raise ValueError("extracted memory must contain entities")
    if not isinstance(relations, list):
        raise TypeError("extracted memory relations must be a list")

    now = int(time.time())
    nodes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    normalized_names: dict[str, str] = {}
    for row in entities:
        if not isinstance(row, dict):
            raise TypeError("each extracted entity must be an object")
        name = str(row.get("name") or "").strip()
        if not name:
            raise ValueError("extracted entity name must not be empty")
        normalized = " ".join(name.casefold().split())
        if normalized in normalized_names:
            raise ValueError(f"duplicate extracted entity name {name!r}")
        normalized_names[normalized] = name
        atoms = row.get("atoms")
        if not isinstance(atoms, list):
            raise TypeError(f"Atoms for extracted entity {name!r} must be a list")
        aliases = row.get("aliases") or []
        if not isinstance(aliases, list):
            raise TypeError(f"aliases for entity {name!r} must be a list")
        if not atoms:
            nodes[name].append(
                {
                    "entity_name": name,
                    "entity_type": str(row.get("entity_type") or "UNKNOWN"),
                    "aliases": [
                        str(alias).strip()
                        for alias in aliases
                        if str(alias).strip()
                    ],
                    "description": "",
                    "atom_payload": None,
                    "source_id": source_id,
                    "file_path": file_path,
                    "timestamp": now,
                    "magi_endpoint_only": True,
                }
            )
        for atom in atoms:
            if not isinstance(atom, dict):
                raise TypeError(f"Atom for entity {name!r} must be an object")
            content = str(atom.get("content") or "").strip()
            if not content:
                raise ValueError(f"Atom for entity {name!r} has empty content")
            nodes[name].append(
                {
                    "entity_name": name,
                    "entity_type": str(row.get("entity_type") or "UNKNOWN"),
                    "aliases": [str(alias).strip() for alias in aliases if str(alias).strip()],
                    "description": content,
                    "atom_payload": dict(atom),
                    "source_id": source_id,
                    "file_path": file_path,
                    "timestamp": now,
                }
            )

    edges: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in relations:
        if not isinstance(row, dict):
            raise TypeError("each extracted relation must be an object")
        source_raw = str(row.get("source") or "").strip()
        target_raw = str(row.get("target") or "").strip()
        source = normalized_names.get(" ".join(source_raw.casefold().split()))
        target = normalized_names.get(" ".join(target_raw.casefold().split()))
        if source is None or target is None:
            raise ValueError(
                "every relation endpoint must identify an extracted entity: "
                f"{source_raw!r} -> {target_raw!r}"
            )
        atoms = row.get("atoms")
        if not isinstance(atoms, list) or not atoms:
            raise ValueError(f"extracted relation {source!r}->{target!r} has no Atoms")
        keywords = row.get("keywords") or []
        if not isinstance(keywords, list):
            raise TypeError("relation keywords must be a list")
        keyword_text = ", ".join(
            dict.fromkeys(str(item).strip() for item in keywords if str(item).strip())
        )
        for atom in atoms:
            if not isinstance(atom, dict):
                raise TypeError("relation Atom must be an object")
            content = str(atom.get("content") or "").strip()
            if not content:
                raise ValueError("relation Atom content must not be empty")
            edges[(source, target)].append(
                {
                    "src_id": source,
                    "tgt_id": target,
                    "weight": 1.0,
                    "description": content,
                    "keywords": keyword_text,
                    "atom_payload": dict(atom),
                    "source_id": source_id,
                    "file_path": file_path,
                    "timestamp": now,
                }
            )

    referenced_endpoints = {endpoint for pair in edges for endpoint in pair}
    unsupported = [
        name
        for name, records in nodes.items()
        if records
        and all(record.get("magi_endpoint_only") is True for record in records)
        and name not in referenced_endpoints
    ]
    if unsupported:
        raise ValueError(
            "entities without Entity Atoms must be endpoints of a valid "
            f"relationship; unsupported {unsupported!r}"
        )

    return [(dict(nodes), dict(edges))]
