"""Local SQLite backend for Episode and Atom records."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from uuid import uuid4

from magi_core.backend.sqlite.migrations import (
    MIGRATION_1,
    MIGRATION_2,
    MIGRATION_3,
    MIGRATION_4,
    MIGRATION_5,
    MIGRATION_6,
    MIGRATION_7,
)
from magi_core.memory import (
    AtomEvidence,
    AtomMemory,
    AtomRecord,
    CandidateMatch,
    EntityRecord,
    Episode,
    EpisodeKind,
    EpisodeStatus,
    RelationRecord,
    TemporalStatus,
    normalize_name,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _evidence_id(evidence: AtomEvidence) -> str:
    identity = "\x1f".join(
        (
            evidence.atom_id,
            evidence.episode_id,
            str(evidence.span_start),
            str(evidence.span_end),
            evidence.extraction_revision or "",
            evidence.quote or "",
        )
    )
    return "evidence-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


class SQLiteBackend:
    """Own the local transactional record store for one workspace."""

    def __init__(self, path: Path, workspace_id: str) -> None:
        self.path = path
        self.workspace_id = workspace_id
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._initialize_sync)
        self._initialized = True

    def _initialize_sync(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.executescript(MIGRATION_1)
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) "
                "VALUES (?, ?)",
                (1, _utc_now()),
            )
            # Keep additive columns self-healing even when an older build
            # recorded migration 2 before every column was introduced.
            self._ensure_column(
                connection, "entity_registry", "normalized_name", "TEXT"
            )
            self._ensure_column(
                connection,
                "entity_registry",
                "atom_ids_json",
                "TEXT NOT NULL DEFAULT '[]'",
            )
            self._ensure_column(connection, "entity_registry", "summary_cited", "TEXT")
            self._ensure_column(
                connection,
                "entity_registry",
                "summary_embedding_text",
                "TEXT",
            )
            self._ensure_column(
                connection,
                "relation_registry",
                "keywords_json",
                "TEXT NOT NULL DEFAULT '[]'",
            )
            self._ensure_column(
                connection, "relation_registry", "entity_a_name", "TEXT"
            )
            self._ensure_column(
                connection, "relation_registry", "entity_b_name", "TEXT"
            )
            self._ensure_column(
                connection,
                "relation_registry",
                "atom_ids_json",
                "TEXT NOT NULL DEFAULT '[]'",
            )
            self._ensure_column(
                connection, "relation_registry", "summary_cited", "TEXT"
            )
            self._ensure_column(
                connection,
                "relation_registry",
                "summary_embedding_text",
                "TEXT",
            )
            self._ensure_column(connection, "atoms", "updated_at", "TEXT")
            connection.executescript(MIGRATION_2)
            connection.execute(
                "UPDATE entity_registry SET normalized_name = lower(canonical_name) "
                "WHERE normalized_name IS NULL"
            )
            connection.execute(
                "UPDATE atoms SET updated_at = created_at WHERE updated_at IS NULL"
            )
            connection.execute(
                """
                UPDATE relation_registry
                SET entity_a_name = COALESCE(
                        entity_a_name,
                        (SELECT canonical_name FROM entity_registry
                         WHERE entity_id = relation_registry.entity_a_id)
                    ),
                    entity_b_name = COALESCE(
                        entity_b_name,
                        (SELECT canonical_name FROM entity_registry
                         WHERE entity_id = relation_registry.entity_b_id)
                    )
                WHERE workspace_id = ?
                """,
                (self.workspace_id,),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) "
                "VALUES (?, ?)",
                (2, _utc_now()),
            )
            connection.executescript(MIGRATION_3)
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) "
                "VALUES (?, ?)",
                (3, _utc_now()),
            )
            self._ensure_column(connection, "projection_outbox", "owner_id", "TEXT")
            self._ensure_column(
                connection, "projection_outbox", "owner_kind", "TEXT"
            )
            self._ensure_column(
                connection,
                "projection_outbox",
                "owner_snapshot_json",
                "TEXT NOT NULL DEFAULT '{}'",
            )
            self._ensure_column(
                connection, "projection_outbox", "updated_at", "TEXT"
            )
            self._ensure_column(
                connection, "projection_outbox", "next_attempt_at", "TEXT"
            )
            # Builds before schema v4 never produced actionable owner tasks.
            # Discard any legacy aggregate rows rather than pretending they
            # can be replayed without an owner locator.
            connection.execute(
                "DELETE FROM projection_outbox WHERE owner_id IS NULL"
            )
            connection.execute(
                "UPDATE projection_outbox SET updated_at = created_at "
                "WHERE updated_at IS NULL"
            )
            connection.executescript(MIGRATION_4)
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) "
                "VALUES (?, ?)",
                (4, _utc_now()),
            )
            migration_5_applied = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 5"
            ).fetchone()
            if migration_5_applied is None:
                connection.executescript(MIGRATION_5)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (5, _utc_now()),
                )
            connection.executescript(MIGRATION_6)
            self._ensure_column(
                connection,
                "owner_summary_checkpoints",
                "metrics_json",
                "TEXT NOT NULL DEFAULT '{}'",
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) "
                "VALUES (?, ?)",
                (6, _utc_now()),
            )
            connection.executescript(MIGRATION_7)
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) "
                "VALUES (?, ?)",
                (7, _utc_now()),
            )
            self._prune_orphan_projection_owners(connection)

    def _prune_orphan_projection_owners(self, connection: sqlite3.Connection) -> None:
        """Repair empty registry owners left by older hard-delete builds."""

        connection.execute(
            """
            DELETE FROM relation_registry AS relation
            WHERE relation.workspace_id = ?
              AND NOT EXISTS (
                  SELECT 1 FROM atoms
                  WHERE atoms.workspace_id = relation.workspace_id
                    AND atoms.owner_id = relation.relation_id
              )
            """,
            (self.workspace_id,),
        )
        orphan_entity_ids = [
            row["entity_id"]
            for row in connection.execute(
                """
                SELECT entity.entity_id
                FROM entity_registry AS entity
                WHERE entity.workspace_id = ?
                  AND NOT EXISTS (
                      SELECT 1 FROM atoms
                      WHERE atoms.workspace_id = entity.workspace_id
                        AND atoms.owner_id = entity.entity_id
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM relation_registry AS relation
                      WHERE relation.workspace_id = entity.workspace_id
                        AND (relation.entity_a_id = entity.entity_id
                             OR relation.entity_b_id = entity.entity_id)
                  )
                """,
                (self.workspace_id,),
            ).fetchall()
        ]
        if not orphan_entity_ids:
            connection.execute(
                """
                DELETE FROM owner_summary_checkpoints
                WHERE workspace_id = ?
                  AND owner_id NOT IN (
                      SELECT entity_id FROM entity_registry WHERE workspace_id = ?
                      UNION
                      SELECT relation_id FROM relation_registry WHERE workspace_id = ?
                  )
                """,
                (self.workspace_id, self.workspace_id, self.workspace_id),
            )
            return
        placeholders = ",".join("?" for _ in orphan_entity_ids)
        params = (self.workspace_id, *orphan_entity_ids)
        connection.execute(
            f"DELETE FROM memory_embeddings WHERE workspace_id = ? "
            f"AND object_kind = 'entity_name' "
            f"AND owner_id IN ({placeholders})",
            params,
        )
        connection.execute(
            f"DELETE FROM entity_name_fts WHERE workspace_id = ? "
            f"AND entity_id IN ({placeholders})",
            params,
        )
        connection.execute(
            f"DELETE FROM entity_aliases WHERE workspace_id = ? "
            f"AND entity_id IN ({placeholders})",
            params,
        )
        connection.execute(
            f"DELETE FROM entity_registry WHERE workspace_id = ? "
            f"AND entity_id IN ({placeholders})",
            params,
        )
        connection.execute(
            """
            DELETE FROM owner_summary_checkpoints
            WHERE workspace_id = ?
              AND owner_id NOT IN (
                  SELECT entity_id FROM entity_registry WHERE workspace_id = ?
                  UNION
                  SELECT relation_id FROM relation_registry WHERE workspace_id = ?
              )
            """,
            (self.workspace_id, self.workspace_id, self.workspace_id),
        )

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        declaration: str,
    ) -> None:
        columns = {
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    async def finalize(self) -> None:
        self._initialized = False

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise RuntimeError("SQLite backend is not initialized")

    @staticmethod
    def _projection_operation_id(workspace_id: str, owner_id: str) -> str:
        digest = hashlib.sha256(
            f"{workspace_id}\x1f{owner_id}".encode("utf-8")
        ).hexdigest()
        return f"projection-{digest}"

    def _owner_snapshot_sync(
        self,
        connection: sqlite3.Connection,
        owner_id: str,
    ) -> dict[str, Any]:
        """Capture only the graph locator needed if the owner is later deleted."""

        if owner_id.startswith("relation-"):
            row = connection.execute(
                "SELECT entity_a_name, entity_b_name FROM relation_registry "
                "WHERE workspace_id = ? AND relation_id = ?",
                (self.workspace_id, owner_id),
            ).fetchone()
            snapshot: dict[str, Any] = {"owner_kind": "relation"}
            if row is not None:
                source, target = sorted(
                    (str(row["entity_a_name"]), str(row["entity_b_name"]))
                )
                snapshot.update({"source": source, "target": target})
            return snapshot

        row = connection.execute(
            "SELECT canonical_name FROM entity_registry "
            "WHERE workspace_id = ? AND entity_id = ?",
            (self.workspace_id, owner_id),
        ).fetchone()
        snapshot = {"owner_kind": "entity"}
        if row is not None:
            snapshot["canonical_name"] = str(row["canonical_name"])
        return snapshot

    def _queue_owner_projection_sync(
        self,
        connection: sqlite3.Connection,
        owner_id: str,
        *,
        episode_id: str | None = None,
        snapshot: dict[str, Any] | None = None,
    ) -> int:
        """Mark an owner dirty inside the caller's SQLite transaction."""

        now = _utc_now()
        current_snapshot = snapshot or self._owner_snapshot_sync(connection, owner_id)
        if len(current_snapshot) == 1:
            existing = connection.execute(
                "SELECT owner_snapshot_json FROM projection_outbox "
                "WHERE operation_id = ?",
                (self._projection_operation_id(self.workspace_id, owner_id),),
            ).fetchone()
            if existing is not None:
                previous_snapshot = json.loads(
                    existing["owner_snapshot_json"] or "{}"
                )
                if len(previous_snapshot) > len(current_snapshot):
                    current_snapshot = previous_snapshot
        owner_kind = str(
            current_snapshot.get("owner_kind")
            or ("relation" if owner_id.startswith("relation-") else "entity")
        )
        operation_id = self._projection_operation_id(self.workspace_id, owner_id)
        connection.execute(
            """
            INSERT INTO projection_outbox(
                operation_id, workspace_id, episode_id, owner_ids_json,
                target_revision, status, attempts, last_error, created_at,
                applied_at, owner_id, owner_kind, owner_snapshot_json,
                updated_at, next_attempt_at
            ) VALUES (?, ?, ?, ?, 1, 'pending', 0, NULL, ?, NULL, ?, ?, ?, ?, NULL)
            ON CONFLICT(operation_id) DO UPDATE SET
                episode_id = COALESCE(excluded.episode_id, projection_outbox.episode_id),
                owner_ids_json = excluded.owner_ids_json,
                target_revision = projection_outbox.target_revision + 1,
                status = 'pending',
                attempts = 0,
                last_error = NULL,
                applied_at = NULL,
                owner_id = excluded.owner_id,
                owner_kind = excluded.owner_kind,
                owner_snapshot_json = excluded.owner_snapshot_json,
                updated_at = excluded.updated_at,
                next_attempt_at = NULL
            """,
            (
                operation_id,
                self.workspace_id,
                episode_id,
                json.dumps([owner_id], ensure_ascii=False),
                now,
                owner_id,
                owner_kind,
                json.dumps(current_snapshot, ensure_ascii=False, sort_keys=True),
                now,
            ),
        )
        row = connection.execute(
            "SELECT target_revision FROM projection_outbox "
            "WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"failed to queue projection for {owner_id!r}")
        return int(row["target_revision"])

    async def queue_owner_projection(
        self,
        owner_id: str,
        *,
        episode_id: str | None = None,
    ) -> int:
        self._require_initialized()

        def queue() -> int:
            with self._connect() as connection:
                return self._queue_owner_projection_sync(
                    connection, owner_id, episode_id=episode_id
                )

        return await asyncio.to_thread(queue)

    async def list_projection_tasks(
        self,
        *,
        owner_ids: list[str] | tuple[str, ...] | None = None,
        ready_only: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self._require_initialized()
        return await asyncio.to_thread(
            self._list_projection_tasks_sync,
            owner_ids,
            ready_only,
            limit,
        )

    def _list_projection_tasks_sync(
        self,
        owner_ids: list[str] | tuple[str, ...] | None,
        ready_only: bool,
        limit: int,
    ) -> list[dict[str, Any]]:
        clauses = ["workspace_id = ?", "status IN ('pending', 'failed')"]
        params: list[Any] = [self.workspace_id]
        if owner_ids is not None:
            if not owner_ids:
                return []
            placeholders = ",".join("?" for _ in owner_ids)
            clauses.append(f"owner_id IN ({placeholders})")
            params.extend(owner_ids)
        if ready_only:
            clauses.append("(next_attempt_at IS NULL OR next_attempt_at <= ?)")
            params.append(_utc_now())
        params.append(max(1, int(limit)))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM projection_outbox WHERE "
                + " AND ".join(clauses)
                + " ORDER BY CASE owner_kind WHEN 'entity' THEN 0 ELSE 1 END, "
                "updated_at, operation_id LIMIT ?",
                tuple(params),
            ).fetchall()
        tasks = []
        for row in rows:
            item = dict(row)
            item["owner_snapshot"] = json.loads(
                item.pop("owner_snapshot_json") or "{}"
            )
            tasks.append(item)
        return tasks

    async def mark_projection_applied(
        self,
        owner_id: str,
        target_revision: int,
    ) -> bool:
        self._require_initialized()
        return await asyncio.to_thread(
            self._mark_projection_applied_sync, owner_id, target_revision
        )

    def _mark_projection_applied_sync(
        self,
        owner_id: str,
        target_revision: int,
    ) -> bool:
        now = _utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE projection_outbox
                SET status = 'applied', applied_at = ?, updated_at = ?,
                    last_error = NULL, next_attempt_at = NULL
                WHERE workspace_id = ? AND owner_id = ?
                  AND target_revision = ? AND status IN ('pending', 'failed')
                """,
                (now, now, self.workspace_id, owner_id, target_revision),
            )
        return cursor.rowcount == 1

    async def mark_projection_failed(
        self,
        owner_id: str,
        target_revision: int,
        error: str,
    ) -> bool:
        self._require_initialized()
        return await asyncio.to_thread(
            self._mark_projection_failed_sync,
            owner_id,
            target_revision,
            error,
        )

    def _mark_projection_failed_sync(
        self,
        owner_id: str,
        target_revision: int,
        error: str,
    ) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT attempts FROM projection_outbox WHERE workspace_id = ? "
                "AND owner_id = ? AND target_revision = ?",
                (self.workspace_id, owner_id, target_revision),
            ).fetchone()
            if row is None:
                return False
            attempts = int(row["attempts"]) + 1
            now = datetime.now(timezone.utc)
            retry_at = now + timedelta(seconds=min(60, 2 ** min(attempts, 6)))
            cursor = connection.execute(
                """
                UPDATE projection_outbox
                SET status = 'failed', attempts = ?, last_error = ?,
                    updated_at = ?, next_attempt_at = ?
                WHERE workspace_id = ? AND owner_id = ? AND target_revision = ?
                """,
                (
                    attempts,
                    str(error)[:4000],
                    now.isoformat(),
                    retry_at.isoformat(),
                    self.workspace_id,
                    owner_id,
                    target_revision,
                ),
            )
        return cursor.rowcount == 1

    async def get_episode(self, episode_id: str) -> dict[str, Any] | None:
        self._require_initialized()
        return await asyncio.to_thread(self._get_episode_sync, episode_id)

    def _get_episode_sync(self, episode_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM episodes WHERE episode_id = ? AND workspace_id = ?",
                (episode_id, self.workspace_id),
            ).fetchone()
        return dict(row) if row is not None else None

    async def get_episode_by_content_hash(
        self, content_hash: str
    ) -> dict[str, Any] | None:
        """Resolve the canonical Episode already admitted for this content."""

        self._require_initialized()
        return await asyncio.to_thread(
            self._get_episode_by_content_hash_sync, content_hash
        )

    def _get_episode_by_content_hash_sync(
        self, content_hash: str
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM episodes
                WHERE workspace_id = ? AND content_hash = ?
                ORDER BY
                    CASE status
                        WHEN 'indexed' THEN 0
                        WHEN 'processing' THEN 1
                        WHEN 'pending' THEN 2
                        ELSE 3
                    END,
                    created_at ASC
                LIMIT 1
                """,
                (self.workspace_id, content_hash),
            ).fetchone()
        return dict(row) if row is not None else None

    async def put_episode(self, episode: Episode) -> dict[str, Any]:
        self._require_initialized()
        return await asyncio.to_thread(self._put_episode_sync, episode)

    def _put_episode_sync(self, episode: Episode) -> dict[str, Any]:
        now = _utc_now()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT content_hash FROM episodes WHERE episode_id = ?",
                (episode.id,),
            ).fetchone()
            if (
                existing is not None
                and existing["content_hash"] != episode.content_hash
            ):
                raise ValueError(
                    f"episode {episode.id!r} already exists with different content"
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO episodes(
                    episode_id, workspace_id, kind, content, content_hash,
                    reference_at, source_uri, metadata_json, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    episode.id,
                    self.workspace_id,
                    episode.kind.value,
                    episode.content,
                    episode.content_hash,
                    episode.effective_reference_at.astimezone(timezone.utc).isoformat(),
                    episode.source_uri,
                    json.dumps(
                        dict(episode.metadata), ensure_ascii=False, sort_keys=True
                    ),
                    EpisodeStatus.PENDING.value,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM episodes WHERE episode_id = ?",
                (episode.id,),
            ).fetchone()
        if row is None:
            raise RuntimeError(f"failed to persist episode {episode.id!r}")
        return dict(row)

    async def mark_episode(
        self,
        episode_id: str,
        status: EpisodeStatus,
        *,
        track_id: str | None = None,
        error: str | None = None,
    ) -> None:
        self._require_initialized()
        await asyncio.to_thread(
            self._mark_episode_sync,
            episode_id,
            status,
            track_id,
            error,
        )

    def _mark_episode_sync(
        self,
        episode_id: str,
        status: EpisodeStatus,
        track_id: str | None,
        error: str | None,
    ) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE episodes
                SET status = ?, track_id = ?, error = ?, updated_at = ?
                WHERE episode_id = ? AND workspace_id = ?
                """,
                (
                    status.value,
                    track_id,
                    error,
                    _utc_now(),
                    episode_id,
                    self.workspace_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown episode {episode_id!r}")

    async def get_episode_model(self, episode_id: str) -> Episode | None:
        record = await self.get_episode(episode_id)
        if record is None:
            return None
        return Episode(
            id=record["episode_id"],
            kind=EpisodeKind(record["kind"]),
            content=record["content"],
            reference_at=_parse_time(record["reference_at"]),
            source_uri=record["source_uri"],
            metadata=json.loads(record["metadata_json"] or "{}"),
        )

    async def find_entity_exact(self, name: str) -> EntityRecord | None:
        self._require_initialized()
        return await asyncio.to_thread(self._find_entity_exact_sync, name)

    def _find_entity_exact_sync(self, name: str) -> EntityRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT e.* FROM entity_aliases a
                JOIN entity_registry e ON e.entity_id = a.entity_id
                WHERE a.workspace_id = ? AND a.normalized_alias = ?
                  AND e.expired_at IS NULL
                """,
                (self.workspace_id, normalize_name(name)),
            ).fetchone()
        return self._entity_from_row(row) if row is not None else None

    async def search_entity_names(
        self, name: str, *, limit: int = 8
    ) -> list[CandidateMatch]:
        self._require_initialized()
        return await asyncio.to_thread(self._search_entity_names_sync, name, limit)

    def _search_entity_names_sync(self, name: str, limit: int) -> list[CandidateMatch]:
        normalized = normalize_name(name)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT e.entity_id, e.canonical_name, a.normalized_alias
                FROM entity_aliases a
                JOIN entity_registry e ON e.entity_id = a.entity_id
                WHERE a.workspace_id = ? AND e.expired_at IS NULL
                  AND (
                    a.normalized_alias = ?
                    OR a.normalized_alias LIKE ?
                    OR ? LIKE '%' || a.normalized_alias || '%'
                  )
                ORDER BY
                    CASE WHEN a.normalized_alias = ? THEN 0 ELSE 1 END,
                    length(a.normalized_alias)
                LIMIT ?
                """,
                (
                    self.workspace_id,
                    normalized,
                    f"%{normalized}%",
                    normalized,
                    normalized,
                    limit,
                ),
            ).fetchall()
            terms = [term for term in normalized.split() if term]
            fts_rows = []
            if terms:
                match_expression = " AND ".join(
                    f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms
                )
                fts_rows = connection.execute(
                    """
                    SELECT e.entity_id, e.canonical_name, f.name AS normalized_alias
                    FROM entity_name_fts f
                    JOIN entity_registry e ON e.entity_id = f.entity_id
                    WHERE f.workspace_id = ?
                      AND entity_name_fts MATCH ?
                      AND e.expired_at IS NULL
                    ORDER BY bm25(entity_name_fts)
                    LIMIT ?
                    """,
                    (self.workspace_id, match_expression, limit),
                ).fetchall()
        matches: list[CandidateMatch] = []
        seen: set[str] = set()
        for row in rows:
            if row["entity_id"] in seen:
                continue
            seen.add(row["entity_id"])
            alias = row["normalized_alias"]
            score = 1.0 if alias == normalized else 0.8
            matches.append(
                CandidateMatch(
                    object_id=row["entity_id"],
                    score=score,
                    text=row["canonical_name"],
                    match_kind="exact" if score == 1.0 else "lexical",
                )
            )
        for row in fts_rows:
            if row["entity_id"] in seen:
                continue
            seen.add(row["entity_id"])
            matches.append(
                CandidateMatch(
                    object_id=row["entity_id"],
                    score=0.7,
                    text=row["canonical_name"],
                    match_kind="full_text",
                )
            )
        return matches

    async def put_entity(self, entity: EntityRecord) -> EntityRecord:
        self._require_initialized()
        return await asyncio.to_thread(self._put_entity_sync, entity)

    def _put_entity_sync(self, entity: EntityRecord) -> EntityRecord:
        aliases = tuple(dict.fromkeys((entity.canonical_name, *entity.aliases)).keys())
        now = _utc_now()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT aliases_json FROM entity_registry WHERE entity_id = ?",
                (entity.id,),
            ).fetchone()
            if existing is not None:
                aliases = tuple(
                    dict.fromkeys(
                        (
                            *json.loads(existing["aliases_json"] or "[]"),
                            *aliases,
                        )
                    ).keys()
                )
            connection.execute(
                """
                INSERT INTO entity_registry(
                    entity_id, workspace_id, canonical_name, normalized_name,
                    aliases_json, entity_type, revision, created_at, expired_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entity_id) DO UPDATE SET
                    canonical_name = excluded.canonical_name,
                    normalized_name = excluded.normalized_name,
                    aliases_json = excluded.aliases_json,
                    entity_type = COALESCE(excluded.entity_type, entity_registry.entity_type),
                    expired_at = excluded.expired_at
                """,
                (
                    entity.id,
                    self.workspace_id,
                    entity.canonical_name,
                    entity.normalized_name,
                    json.dumps(aliases, ensure_ascii=False),
                    entity.entity_type,
                    entity.revision,
                    entity.created_at.astimezone(timezone.utc).isoformat(),
                    entity.expired_at.astimezone(timezone.utc).isoformat()
                    if entity.expired_at
                    else None,
                ),
            )
            connection.execute(
                "DELETE FROM entity_name_fts WHERE workspace_id = ? AND entity_id = ?",
                (self.workspace_id, entity.id),
            )
            for alias in aliases:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO entity_aliases(
                        workspace_id, entity_id, alias, normalized_alias, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        self.workspace_id,
                        entity.id,
                        alias,
                        normalize_name(alias),
                        now,
                    ),
                )
                connection.execute(
                    "INSERT INTO entity_name_fts(workspace_id, entity_id, name) "
                    "VALUES (?, ?, ?)",
                    (self.workspace_id, entity.id, alias),
                )
            row = connection.execute(
                "SELECT * FROM entity_registry WHERE entity_id = ?", (entity.id,)
            ).fetchone()
        if row is None:
            raise RuntimeError(f"failed to persist entity {entity.id!r}")
        return self._entity_from_row(row)

    async def get_entity(self, entity_id: str) -> EntityRecord | None:
        self._require_initialized()
        return await asyncio.to_thread(self._get_entity_sync, entity_id)

    def _get_entity_sync(self, entity_id: str) -> EntityRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM entity_registry WHERE entity_id = ? AND workspace_id = ?",
                (entity_id, self.workspace_id),
            ).fetchone()
        return self._entity_from_row(row) if row is not None else None

    @staticmethod
    def _entity_from_row(row: sqlite3.Row) -> EntityRecord:
        return EntityRecord(
            id=row["entity_id"],
            workspace_id=row["workspace_id"],
            canonical_name=row["canonical_name"],
            aliases=tuple(json.loads(row["aliases_json"] or "[]")),
            entity_type=row["entity_type"],
            revision=row["revision"],
            created_at=_parse_time(row["created_at"]),
            expired_at=_parse_time(row["expired_at"]),
        )

    async def put_relation(self, relation: RelationRecord) -> RelationRecord:
        self._require_initialized()
        return await asyncio.to_thread(self._put_relation_sync, relation)

    def _put_relation_sync(self, relation: RelationRecord) -> RelationRecord:
        endpoints = sorted(
            (
                (relation.entity_a_id, relation.entity_a_name),
                (relation.entity_b_id, relation.entity_b_name),
            ),
            key=lambda item: item[0],
        )
        (first, first_name), (second, second_name) = endpoints
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO relation_registry(
                    relation_id, workspace_id, entity_a_id, entity_b_id,
                    entity_a_name, entity_b_name, keywords_json, revision,
                    created_at, expired_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workspace_id, entity_a_id, entity_b_id) DO UPDATE SET
                    entity_a_name = excluded.entity_a_name,
                    entity_b_name = excluded.entity_b_name,
                    keywords_json = excluded.keywords_json,
                    expired_at = excluded.expired_at
                """,
                (
                    relation.id,
                    self.workspace_id,
                    first,
                    second,
                    first_name,
                    second_name,
                    json.dumps(relation.keywords, ensure_ascii=False),
                    relation.revision,
                    relation.created_at.astimezone(timezone.utc).isoformat(),
                    relation.expired_at.astimezone(timezone.utc).isoformat()
                    if relation.expired_at
                    else None,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM relation_registry
                WHERE workspace_id = ? AND entity_a_id = ? AND entity_b_id = ?
                """,
                (self.workspace_id, first, second),
            ).fetchone()
        if row is None:
            raise RuntimeError(f"failed to persist relation {relation.id!r}")
        return self._relation_from_row(row)

    async def get_relation(self, relation_id: str) -> RelationRecord | None:
        self._require_initialized()
        return await asyncio.to_thread(self._get_relation_sync, relation_id)

    def _get_relation_sync(self, relation_id: str) -> RelationRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM relation_registry "
                "WHERE relation_id = ? AND workspace_id = ?",
                (relation_id, self.workspace_id),
            ).fetchone()
        return self._relation_from_row(row) if row is not None else None

    @staticmethod
    def _relation_from_row(row: sqlite3.Row) -> RelationRecord:
        return RelationRecord(
            id=row["relation_id"],
            workspace_id=row["workspace_id"],
            entity_a_id=row["entity_a_id"],
            entity_b_id=row["entity_b_id"],
            entity_a_name=row["entity_a_name"] or row["entity_a_id"],
            entity_b_name=row["entity_b_name"] or row["entity_b_id"],
            keywords=tuple(json.loads(row["keywords_json"] or "[]")),
            revision=row["revision"],
            created_at=_parse_time(row["created_at"]),
            expired_at=_parse_time(row["expired_at"]),
        )

    async def put_atom(
        self, atom: AtomRecord, evidence: AtomEvidence
    ) -> tuple[AtomRecord, bool]:
        self._require_initialized()
        return await asyncio.to_thread(self._put_atom_sync, atom, evidence)

    def _put_atom_sync(
        self, atom: AtomRecord, evidence: AtomEvidence
    ) -> tuple[AtomRecord, bool]:
        now = _utc_now()
        with self._connect() as connection:
            atom_id = atom.id
            existing = connection.execute(
                "SELECT * FROM atoms WHERE atom_id = ? AND workspace_id = ?",
                (atom.id, self.workspace_id),
            ).fetchone()
            inserted = existing is None
            if existing is not None and (
                existing["owner_id"] != atom.owner_id
                or existing["normalized_hash"] != atom.normalized_hash
                or existing["valid_at"]
                != (
                    atom.valid_at.astimezone(timezone.utc).isoformat()
                    if atom.valid_at
                    else None
                )
                or existing["invalid_at"]
                != (
                    atom.invalid_at.astimezone(timezone.utc).isoformat()
                    if atom.invalid_at
                    else None
                )
            ):
                raise ValueError(
                    f"atom {atom.id!r} already exists with different semantics"
                )
            if inserted:
                connection.execute(
                    """
                    INSERT INTO atoms(
                        atom_id, workspace_id, kind, owner_kind, owner_id,
                        content, normalized_hash, fingerprint,
                        subject_entity_id, predicate, object_entity_id,
                        relation_keywords_json, valid_at, invalid_at,
                        temporal_text, temporal_precision, created_at, updated_at,
                        expired_at, confidence, importance, support_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        atom.id,
                        self.workspace_id,
                        "relation_fact" if atom.is_relation_atom else "entity_fact",
                        atom.owner_type,
                        atom.owner_id,
                        atom.normalized_content,
                        atom.normalized_hash,
                        atom.fingerprint,
                        atom.subject_entity_id,
                        atom.predicate,
                        atom.object_entity_id,
                        json.dumps(atom.relation_keywords, ensure_ascii=False),
                        atom.valid_at.astimezone(timezone.utc).isoformat()
                        if atom.valid_at
                        else None,
                        atom.invalid_at.astimezone(timezone.utc).isoformat()
                        if atom.invalid_at
                        else None,
                        atom.temporal_text,
                        atom.temporal_precision,
                        atom.created_at.astimezone(timezone.utc).isoformat(),
                        now,
                        atom.expired_at.astimezone(timezone.utc).isoformat()
                        if atom.expired_at
                        else None,
                        atom.confidence,
                        atom.importance,
                    ),
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO atom_evidence(
                    evidence_id, atom_id, episode_id, quote, span_start, span_end,
                    extraction_revision, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _evidence_id(replace(evidence, atom_id=atom_id)),
                    atom_id,
                    evidence.episode_id,
                    evidence.quote,
                    evidence.span_start,
                    evidence.span_end,
                    evidence.extraction_revision,
                    evidence.created_at.astimezone(timezone.utc).isoformat(),
                ),
            )
            connection.execute(
                """
                UPDATE atoms SET support_count = (
                    SELECT count(DISTINCT episode_id) FROM atom_evidence
                    WHERE atom_id = ?
                ), updated_at = ? WHERE atom_id = ?
                """,
                (atom_id, now, atom_id),
            )
            row = connection.execute(
                "SELECT * FROM atoms WHERE atom_id = ?", (atom_id,)
            ).fetchone()
            self._queue_owner_projection_sync(
                connection,
                atom.owner_id,
                episode_id=evidence.episode_id,
            )
        if row is None:
            raise RuntimeError(f"failed to persist atom {atom_id!r}")
        return self._atom_from_row(row), inserted

    async def find_atom_by_fingerprint(self, fingerprint: str) -> AtomRecord | None:
        self._require_initialized()
        return await asyncio.to_thread(self._find_atom_by_fingerprint_sync, fingerprint)

    def _find_atom_by_fingerprint_sync(self, fingerprint: str) -> AtomRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM atoms WHERE workspace_id = ? AND fingerprint = ?",
                (self.workspace_id, fingerprint),
            ).fetchone()
        return self._atom_from_row(row) if row is not None else None

    async def add_atom_evidence(
        self, atom_id: str, evidence: AtomEvidence
    ) -> AtomRecord:
        self._require_initialized()
        return await asyncio.to_thread(self._add_atom_evidence_sync, atom_id, evidence)

    def _add_atom_evidence_sync(
        self, atom_id: str, evidence: AtomEvidence
    ) -> AtomRecord:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO atom_evidence(
                    evidence_id, atom_id, episode_id, quote, span_start, span_end,
                    extraction_revision, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _evidence_id(replace(evidence, atom_id=atom_id)),
                    atom_id,
                    evidence.episode_id,
                    evidence.quote,
                    evidence.span_start,
                    evidence.span_end,
                    evidence.extraction_revision,
                    evidence.created_at.astimezone(timezone.utc).isoformat(),
                ),
            )
            connection.execute(
                """
                UPDATE atoms SET support_count = (
                    SELECT count(DISTINCT episode_id) FROM atom_evidence
                    WHERE atom_id = ?
                ), updated_at = ? WHERE atom_id = ? AND workspace_id = ?
                """,
                (atom_id, _utc_now(), atom_id, self.workspace_id),
            )
            row = connection.execute(
                "SELECT * FROM atoms WHERE atom_id = ? AND workspace_id = ?",
                (atom_id, self.workspace_id),
            ).fetchone()
            if row is not None:
                self._queue_owner_projection_sync(
                    connection,
                    str(row["owner_id"]),
                    episode_id=evidence.episode_id,
                )
        if row is None:
            raise KeyError(f"unknown atom {atom_id!r}")
        return self._atom_from_row(row)

    async def get_atom(self, atom_id: str) -> AtomRecord | None:
        self._require_initialized()
        return await asyncio.to_thread(self._get_atom_sync, atom_id)

    def _get_atom_sync(self, atom_id: str) -> AtomRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM atoms WHERE atom_id = ? AND workspace_id = ?",
                (atom_id, self.workspace_id),
            ).fetchone()
        return self._atom_from_row(row) if row is not None else None

    async def get_atom_memory(self, atom_id: str) -> AtomMemory | None:
        self._require_initialized()
        return await asyncio.to_thread(self._get_atom_memory_sync, atom_id)

    def _get_atom_memory_sync(self, atom_id: str) -> AtomMemory | None:
        with self._connect() as connection:
            atom_row = connection.execute(
                "SELECT * FROM atoms WHERE atom_id = ? AND workspace_id = ?",
                (atom_id, self.workspace_id),
            ).fetchone()
            if atom_row is None:
                return None
            evidence_rows = connection.execute(
                """
                SELECT ae.* FROM atom_evidence ae
                JOIN episodes e ON e.episode_id = ae.episode_id
                WHERE ae.atom_id = ? AND e.workspace_id = ?
                ORDER BY ae.created_at, ae.episode_id, ae.span_start, ae.span_end
                """,
                (atom_id, self.workspace_id),
            ).fetchall()
        evidence = tuple(
            AtomEvidence(
                atom_id=row["atom_id"],
                episode_id=row["episode_id"],
                quote=row["quote"],
                span_start=row["span_start"],
                span_end=row["span_end"],
                extraction_revision=row["extraction_revision"],
                created_at=_parse_time(row["created_at"]),
            )
            for row in evidence_rows
        )
        return AtomMemory(atom=self._atom_from_row(atom_row), evidence=evidence)

    async def list_owner_atoms(
        self,
        owner_id: str,
        *,
        active_only: bool = False,
    ) -> list[AtomRecord]:
        self._require_initialized()
        return await asyncio.to_thread(
            self._list_owner_atoms_sync, owner_id, active_only
        )

    def _list_owner_atoms_sync(
        self,
        owner_id: str,
        active_only: bool = False,
    ) -> list[AtomRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM atoms
                WHERE workspace_id = ? AND owner_id = ?
                ORDER BY rowid
                """,
                (self.workspace_id, owner_id),
            ).fetchall()
        atoms = [self._atom_from_row(row) for row in rows]
        if active_only:
            atoms = [
                atom
                for atom in atoms
                if atom.temporal_status() is TemporalStatus.ACTIVE
            ]
        return atoms

    async def get_owner_summary(self, owner_id: str) -> str | None:
        """Return an optional Reflect summary without treating it as evidence."""

        self._require_initialized()
        return await asyncio.to_thread(self._get_owner_summary_sync, owner_id)

    def _get_owner_summary_sync(self, owner_id: str) -> str | None:
        table, id_column = (
            ("relation_registry", "relation_id")
            if owner_id.startswith("relation-")
            else ("entity_registry", "entity_id")
        )
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT summary_cited FROM {table} "
                f"WHERE workspace_id = ? AND {id_column} = ?",
                (self.workspace_id, owner_id),
            ).fetchone()
        if row is None:
            return None
        value = row["summary_cited"]
        return str(value) if value else None

    async def get_owner_summary_checkpoint(
        self, owner_id: str
    ) -> dict[str, Any] | None:
        self._require_initialized()
        return await asyncio.to_thread(
            self._get_owner_summary_checkpoint_sync,
            owner_id,
        )

    def _get_owner_summary_checkpoint_sync(
        self, owner_id: str
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM owner_summary_checkpoints "
                "WHERE workspace_id = ? AND owner_id = ?",
                (self.workspace_id, owner_id),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["covered_atom_ids"] = json.loads(
            result.pop("covered_atom_ids_json") or "[]"
        )
        result["pending_atom_ids"] = json.loads(
            result.pop("pending_atom_ids_json") or "[]"
        )
        result["metrics"] = json.loads(result.pop("metrics_json") or "{}")
        return result

    async def compare_and_set_owner_summary_checkpoint(
        self,
        owner_id: str,
        *,
        expected_revision: int | None,
        checkpoint_summary: str,
        covered_atom_ids: Sequence[str],
        pending_atom_ids: Sequence[str],
        prompt_version: str,
        model_identity: str,
        incremental_compaction_count: int,
        last_compacted_at: str | None,
        reason: str,
        metrics: Mapping[str, Any] | None = None,
    ) -> bool:
        self._require_initialized()
        return await asyncio.to_thread(
            self._compare_and_set_owner_summary_checkpoint_sync,
            owner_id,
            expected_revision,
            checkpoint_summary,
            tuple(dict.fromkeys(covered_atom_ids)),
            tuple(dict.fromkeys(pending_atom_ids)),
            prompt_version,
            model_identity,
            incremental_compaction_count,
            last_compacted_at,
            reason,
            dict(metrics) if metrics is not None else None,
        )

    def _compare_and_set_owner_summary_checkpoint_sync(
        self,
        owner_id: str,
        expected_revision: int | None,
        checkpoint_summary: str,
        covered_atom_ids: Sequence[str],
        pending_atom_ids: Sequence[str],
        prompt_version: str,
        model_identity: str,
        incremental_compaction_count: int,
        last_compacted_at: str | None,
        reason: str,
        metrics: Mapping[str, Any] | None,
    ) -> bool:
        now = _utc_now()
        covered_json = json.dumps(list(covered_atom_ids), ensure_ascii=False)
        pending_json = json.dumps(list(pending_atom_ids), ensure_ascii=False)
        metrics_json = (
            json.dumps(dict(metrics), ensure_ascii=False, sort_keys=True)
            if metrics is not None
            else None
        )
        with self._connect() as connection:
            if expected_revision is None:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO owner_summary_checkpoints(
                        workspace_id, owner_id, checkpoint_summary,
                        covered_atom_ids_json, pending_atom_ids_json,
                        summary_revision, prompt_version, model_identity,
                        incremental_compaction_count, last_compacted_at,
                        last_reason, metrics_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.workspace_id,
                        owner_id,
                        checkpoint_summary,
                        covered_json,
                        pending_json,
                        prompt_version,
                        model_identity,
                        incremental_compaction_count,
                        last_compacted_at,
                        reason,
                        metrics_json or "{}",
                        now,
                    ),
                )
            else:
                cursor = connection.execute(
                    """
                    UPDATE owner_summary_checkpoints
                    SET checkpoint_summary = ?, covered_atom_ids_json = ?,
                        pending_atom_ids_json = ?, summary_revision = ?,
                        prompt_version = ?, model_identity = ?,
                        incremental_compaction_count = ?, last_compacted_at = ?,
                        last_reason = ?,
                        metrics_json = COALESCE(?, metrics_json), updated_at = ?
                    WHERE workspace_id = ? AND owner_id = ?
                      AND summary_revision = ?
                    """,
                    (
                        checkpoint_summary,
                        covered_json,
                        pending_json,
                        expected_revision + 1,
                        prompt_version,
                        model_identity,
                        incremental_compaction_count,
                        last_compacted_at,
                        reason,
                        metrics_json,
                        now,
                        self.workspace_id,
                        owner_id,
                        expected_revision,
                    ),
                )
        return cursor.rowcount == 1

    async def delete_owner_summary_checkpoint(self, owner_id: str) -> None:
        self._require_initialized()
        await asyncio.to_thread(self._delete_owner_summary_checkpoint_sync, owner_id)

    def _delete_owner_summary_checkpoint_sync(self, owner_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM owner_summary_checkpoints "
                "WHERE workspace_id = ? AND owner_id = ?",
                (self.workspace_id, owner_id),
            )

    async def get_owner_provenance(self, owner_id: str) -> dict[str, list[str]]:
        """Return stable source ids and file paths for graph materialization."""

        self._require_initialized()
        return await asyncio.to_thread(self._get_owner_provenance_sync, owner_id)

    def _get_owner_provenance_sync(self, owner_id: str) -> dict[str, list[str]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT ae.extraction_revision, ae.episode_id, e.source_uri
                FROM atoms a
                JOIN atom_evidence ae ON ae.atom_id = a.atom_id
                JOIN episodes e ON e.episode_id = ae.episode_id
                WHERE a.workspace_id = ? AND a.owner_id = ?
                ORDER BY ae.created_at, ae.episode_id
                """,
                (self.workspace_id, owner_id),
            ).fetchall()
        source_ids = list(
            dict.fromkeys(
                str(row["extraction_revision"] or row["episode_id"])
                for row in rows
            )
        )
        file_paths = list(
            dict.fromkeys(
                str(row["source_uri"] or f"episode://{row['episode_id']}")
                for row in rows
            )
        )
        return {"source_ids": source_ids, "file_paths": file_paths}

    async def set_owner_materialization(
        self,
        owner_id: str,
        *,
        atom_ids: list[str],
        summary_cited: str,
    ) -> None:
        """Persist the last successfully projected owner representation."""

        self._require_initialized()
        await asyncio.to_thread(
            self._set_owner_materialization_sync,
            owner_id,
            atom_ids,
            summary_cited,
        )

    def _set_owner_materialization_sync(
        self,
        owner_id: str,
        atom_ids: list[str],
        summary_cited: str,
    ) -> None:
        table, id_column = (
            ("relation_registry", "relation_id")
            if owner_id.startswith("relation-")
            else ("entity_registry", "entity_id")
        )
        with self._connect() as connection:
            connection.execute(
                f"UPDATE {table} SET atom_ids_json = ?, summary_cited = ?, "
                "summary_embedding_text = ? WHERE workspace_id = ? "
                f"AND {id_column} = ?",
                (
                    json.dumps(atom_ids, ensure_ascii=False),
                    summary_cited,
                    summary_cited,
                    self.workspace_id,
                    owner_id,
                ),
            )

    @staticmethod
    def _atom_from_row(row: sqlite3.Row) -> AtomRecord:
        return AtomRecord(
            id=row["atom_id"],
            workspace_id=row["workspace_id"],
            owner_id=row["owner_id"],
            content=row["content"],
            valid_at=_parse_time(row["valid_at"]),
            invalid_at=_parse_time(row["invalid_at"]),
            created_at=_parse_time(row["created_at"]),
            expired_at=_parse_time(row["expired_at"]),
            subject_entity_id=row["subject_entity_id"],
            predicate=row["predicate"],
            object_entity_id=row["object_entity_id"],
            relation_keywords=tuple(json.loads(row["relation_keywords_json"] or "[]")),
            temporal_text=row["temporal_text"],
            temporal_precision=row["temporal_precision"],
            confidence=row["confidence"],
            importance=row["importance"],
            support_count=row["support_count"],
        )

    async def expire_atom(self, atom_id: str, *, expired_at: datetime) -> None:
        self._require_initialized()
        await asyncio.to_thread(self._expire_atom_sync, atom_id, expired_at)

    def _expire_atom_sync(self, atom_id: str, expired_at: datetime) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT owner_id FROM atoms WHERE atom_id = ? AND workspace_id = ?",
                (atom_id, self.workspace_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown atom {atom_id!r}")
            cursor = connection.execute(
                "UPDATE atoms SET expired_at = ?, updated_at = ? "
                "WHERE atom_id = ? AND workspace_id = ?",
                (
                    expired_at.astimezone(timezone.utc).isoformat(),
                    _utc_now(),
                    atom_id,
                    self.workspace_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown atom {atom_id!r}")
            self._queue_owner_projection_sync(connection, str(row["owner_id"]))

    async def set_atom_invalid_at(self, atom_id: str, *, invalid_at: datetime) -> None:
        self._require_initialized()
        await asyncio.to_thread(self._set_atom_invalid_at_sync, atom_id, invalid_at)

    def _set_atom_invalid_at_sync(self, atom_id: str, invalid_at: datetime) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM atoms WHERE atom_id = ? AND workspace_id = ?",
                (atom_id, self.workspace_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown atom {atom_id!r}")
            updated_atom = replace(self._atom_from_row(row), invalid_at=invalid_at)
            cursor = connection.execute(
                "UPDATE atoms SET invalid_at = ?, fingerprint = ?, updated_at = ? "
                "WHERE atom_id = ? AND workspace_id = ?",
                (
                    invalid_at.astimezone(timezone.utc).isoformat(),
                    updated_atom.fingerprint,
                    _utc_now(),
                    atom_id,
                    self.workspace_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown atom {atom_id!r}")
            self._queue_owner_projection_sync(connection, str(row["owner_id"]))

    async def recent_episode_context(self, *, limit: int = 4) -> list[dict[str, Any]]:
        self._require_initialized()
        return await asyncio.to_thread(self._recent_episode_context_sync, limit)

    def _recent_episode_context_sync(self, limit: int) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT episode_id, kind, content, reference_at, source_uri
                FROM episodes WHERE workspace_id = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (self.workspace_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    async def memory_overview(self) -> dict[str, Any]:
        """Return workspace-level counters for the memory workbench."""

        self._require_initialized()
        return await asyncio.to_thread(self._memory_overview_sync)

    def _memory_overview_sync(self) -> dict[str, Any]:
        now = _utc_now()
        with self._connect() as connection:
            episode_rows = connection.execute(
                """
                SELECT status, count(*) AS count FROM episodes
                WHERE workspace_id = ? GROUP BY status
                """,
                (self.workspace_id,),
            ).fetchall()
            atom_rows = connection.execute(
                """
                SELECT CASE
                    WHEN expired_at IS NOT NULL THEN 'expired'
                    WHEN valid_at IS NOT NULL AND valid_at > ? THEN 'pending'
                    WHEN invalid_at IS NOT NULL AND invalid_at <= ? THEN 'invalid'
                    ELSE 'active'
                END AS temporal_status, count(*) AS count
                FROM atoms WHERE workspace_id = ? GROUP BY temporal_status
                """,
                (now, now, self.workspace_id),
            ).fetchall()

            def scalar(sql: str) -> int:
                return int(connection.execute(sql, (self.workspace_id,)).fetchone()[0])

            outbox_rows = connection.execute(
                """
                SELECT status, count(*) AS count FROM projection_outbox
                WHERE workspace_id = ? GROUP BY status
                """,
                (self.workspace_id,),
            ).fetchall()
        episode_statuses = {row["status"]: row["count"] for row in episode_rows}
        atom_statuses = {row["temporal_status"]: row["count"] for row in atom_rows}
        return {
            "workspace_id": self.workspace_id,
            "database_path": str(self.path),
            "episodes": sum(episode_statuses.values()),
            "episode_statuses": episode_statuses,
            "atoms": sum(atom_statuses.values()),
            "atom_statuses": atom_statuses,
            "evidence": scalar(
                "SELECT count(*) FROM atom_evidence ae JOIN episodes e "
                "ON e.episode_id = ae.episode_id WHERE e.workspace_id = ?"
            ),
            "entities": scalar(
                "SELECT count(*) FROM entity_registry WHERE workspace_id = ?"
            ),
            "relations": scalar(
                "SELECT count(*) FROM relation_registry WHERE workspace_id = ?"
            ),
            "projection_outbox": {row["status"]: row["count"] for row in outbox_rows},
        }

    async def list_episodes(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        kind: str | None = None,
        query: str | None = None,
    ) -> dict[str, Any]:
        """List Episodes with Atom/evidence counts for the WebUI."""

        self._require_initialized()
        return await asyncio.to_thread(
            self._list_episodes_sync, page, page_size, status, kind, query
        )

    def _list_episodes_sync(
        self,
        page: int,
        page_size: int,
        status: str | None,
        kind: str | None,
        query: str | None,
    ) -> dict[str, Any]:
        where = ["e.workspace_id = ?"]
        params: list[Any] = [self.workspace_id]
        if status:
            where.append("e.status = ?")
            params.append(status)
        if kind:
            where.append("e.kind = ?")
            params.append(kind)
        if query:
            where.append(
                "(e.episode_id LIKE ? OR e.content LIKE ? OR e.source_uri LIKE ?)"
            )
            needle = f"%{query}%"
            params.extend((needle, needle, needle))
        where_sql = " AND ".join(where)
        offset = (page - 1) * page_size
        with self._connect() as connection:
            total = int(
                connection.execute(
                    f"SELECT count(*) FROM episodes e WHERE {where_sql}", params
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"""
                SELECT e.*,
                       count(DISTINCT ae.atom_id) AS atom_count,
                       count(ae.atom_id) AS evidence_count
                FROM episodes e
                LEFT JOIN atom_evidence ae ON ae.episode_id = e.episode_id
                WHERE {where_sql}
                GROUP BY e.episode_id
                ORDER BY e.created_at DESC, e.episode_id DESC
                LIMIT ? OFFSET ?
                """,
                (*params, page_size, offset),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            items.append(item)
        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": (total + page_size - 1) // page_size,
        }

    async def get_episode_memory(self, episode_id: str) -> dict[str, Any] | None:
        """Return one Episode and every Atom evidence occurrence it supports."""

        self._require_initialized()
        return await asyncio.to_thread(self._get_episode_memory_sync, episode_id)

    def _get_episode_memory_sync(self, episode_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            episode_row = connection.execute(
                "SELECT * FROM episodes WHERE episode_id = ? AND workspace_id = ?",
                (episode_id, self.workspace_id),
            ).fetchone()
            if episode_row is None:
                return None
            rows = connection.execute(
                """
                SELECT a.*, ae.quote, ae.span_start, ae.span_end,
                       ae.extraction_revision, ae.created_at AS evidence_created_at,
                       COALESCE(er.canonical_name,
                                rr.entity_a_name || ' ↔ ' || rr.entity_b_name,
                                a.owner_id) AS owner_name
                FROM atom_evidence ae
                JOIN atoms a ON a.atom_id = ae.atom_id
                LEFT JOIN entity_registry er ON er.entity_id = a.owner_id
                LEFT JOIN relation_registry rr ON rr.relation_id = a.owner_id
                WHERE ae.episode_id = ? AND a.workspace_id = ?
                ORDER BY ae.created_at, a.rowid
                """,
                (episode_id, self.workspace_id),
            ).fetchall()
        episode = dict(episode_row)
        episode["metadata"] = json.loads(episode.pop("metadata_json") or "{}")
        episode["atoms"] = [self._atom_ui_row(row) for row in rows]
        return episode

    async def list_atoms(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        owner_type: str | None = None,
        temporal_status: str | None = None,
        query: str | None = None,
        episode_id: str | None = None,
    ) -> dict[str, Any]:
        """List Atom records without projecting their payload into Neo4j."""

        self._require_initialized()
        return await asyncio.to_thread(
            self._list_atoms_sync,
            page,
            page_size,
            owner_type,
            temporal_status,
            query,
            episode_id,
        )

    def _list_atoms_sync(
        self,
        page: int,
        page_size: int,
        owner_type: str | None,
        temporal_status: str | None,
        query: str | None,
        episode_id: str | None,
    ) -> dict[str, Any]:
        now = _utc_now()
        status_sql = """CASE
            WHEN a.expired_at IS NOT NULL THEN 'expired'
            WHEN a.valid_at IS NOT NULL AND a.valid_at > ? THEN 'pending'
            WHEN a.invalid_at IS NOT NULL AND a.invalid_at <= ? THEN 'invalid'
            ELSE 'active' END"""
        where = ["a.workspace_id = ?"]
        params: list[Any] = [self.workspace_id]
        if owner_type == "entity":
            where.append("a.owner_id LIKE 'entity-%'")
        elif owner_type == "relation":
            where.append("a.owner_id LIKE 'relation-%'")
        if temporal_status:
            where.append(f"{status_sql} = ?")
            params.extend((now, now, temporal_status))
        if query:
            where.append("(a.atom_id LIKE ? OR a.content LIKE ? OR a.owner_id LIKE ?)")
            needle = f"%{query}%"
            params.extend((needle, needle, needle))
        joins = ""
        if episode_id:
            joins = "JOIN atom_evidence episode_filter ON episode_filter.atom_id = a.atom_id"
            where.append("episode_filter.episode_id = ?")
            params.append(episode_id)
        where_sql = " AND ".join(where)
        offset = (page - 1) * page_size
        with self._connect() as connection:
            total = int(
                connection.execute(
                    f"SELECT count(DISTINCT a.atom_id) FROM atoms a {joins} "
                    f"WHERE {where_sql}",
                    params,
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"""
                SELECT a.*, {status_sql} AS temporal_status,
                       COALESCE(er.canonical_name,
                                rr.entity_a_name || ' ↔ ' || rr.entity_b_name,
                                a.owner_id) AS owner_name,
                       count(DISTINCT ae.episode_id) AS evidence_count
                FROM atoms a {joins}
                LEFT JOIN atom_evidence ae ON ae.atom_id = a.atom_id
                LEFT JOIN entity_registry er ON er.entity_id = a.owner_id
                LEFT JOIN relation_registry rr ON rr.relation_id = a.owner_id
                WHERE {where_sql}
                GROUP BY a.atom_id
                ORDER BY a.created_at DESC, a.rowid DESC
                LIMIT ? OFFSET ?
                """,
                (now, now, *params, page_size, offset),
            ).fetchall()
        return {
            "items": [self._atom_ui_row(row) for row in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": (total + page_size - 1) // page_size,
        }

    async def get_atom_memory_view(self, atom_id: str) -> dict[str, Any] | None:
        """Return an Atom plus all of its Episode evidence for the WebUI."""

        self._require_initialized()
        return await asyncio.to_thread(self._get_atom_memory_view_sync, atom_id)

    def _get_atom_memory_view_sync(self, atom_id: str) -> dict[str, Any] | None:
        now = _utc_now()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT a.*, CASE
                    WHEN a.expired_at IS NOT NULL THEN 'expired'
                    WHEN a.valid_at IS NOT NULL AND a.valid_at > ? THEN 'pending'
                    WHEN a.invalid_at IS NOT NULL AND a.invalid_at <= ? THEN 'invalid'
                    ELSE 'active' END AS temporal_status,
                    COALESCE(er.canonical_name,
                             rr.entity_a_name || ' ↔ ' || rr.entity_b_name,
                             a.owner_id) AS owner_name
                FROM atoms a
                LEFT JOIN entity_registry er ON er.entity_id = a.owner_id
                LEFT JOIN relation_registry rr ON rr.relation_id = a.owner_id
                WHERE a.atom_id = ? AND a.workspace_id = ?
                """,
                (now, now, atom_id, self.workspace_id),
            ).fetchone()
            if row is None:
                return None
            evidence_rows = connection.execute(
                """
                SELECT ae.*, e.kind AS episode_kind, e.content AS episode_content,
                       e.reference_at, e.source_uri, e.status AS episode_status
                FROM atom_evidence ae
                JOIN episodes e ON e.episode_id = ae.episode_id
                WHERE ae.atom_id = ? AND e.workspace_id = ?
                ORDER BY ae.created_at, ae.episode_id
                """,
                (atom_id, self.workspace_id),
            ).fetchall()
        item = self._atom_ui_row(row)
        item["evidence"] = [dict(evidence) for evidence in evidence_rows]
        return item

    @staticmethod
    def _atom_ui_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["relation_keywords"] = json.loads(
            item.pop("relation_keywords_json", "[]") or "[]"
        )
        item["owner_type"] = (
            "relation" if item["owner_id"].startswith("relation-") else "entity"
        )
        return item

    async def backup_and_delete_episode(self, episode_id: str) -> dict[str, Any] | None:
        """Atomically back up and remove one Episode's memory evidence.

        Shared Atoms survive while at least one evidence row remains. Atom
        occurrences that lose their final evidence row and their embeddings
        are removed in the same SQLite transaction.
        """

        self._require_initialized()
        return await asyncio.to_thread(self._backup_and_delete_episode_sync, episode_id)

    def _backup_and_delete_episode_sync(self, episode_id: str) -> dict[str, Any] | None:
        backup_id = f"memory-backup-{uuid4().hex}"
        with self._connect() as connection:
            episode = connection.execute(
                "SELECT * FROM episodes WHERE episode_id = ? AND workspace_id = ?",
                (episode_id, self.workspace_id),
            ).fetchone()
            if episode is None:
                return None
            evidence_rows = connection.execute(
                "SELECT * FROM atom_evidence WHERE episode_id = ?",
                (episode_id,),
            ).fetchall()
            candidate_atom_ids = list(
                dict.fromkeys(row["atom_id"] for row in evidence_rows)
            )
            atom_rows = []
            if candidate_atom_ids:
                placeholders = ",".join("?" for _ in candidate_atom_ids)
                atom_rows = connection.execute(
                    f"SELECT * FROM atoms WHERE workspace_id = ? "
                    f"AND atom_id IN ({placeholders}) ORDER BY rowid",
                    (self.workspace_id, *candidate_atom_ids),
                ).fetchall()
            affected_owner_ids = list(
                dict.fromkeys(row["owner_id"] for row in atom_rows)
            )
            relation_ids = [
                owner_id
                for owner_id in affected_owner_ids
                if owner_id.startswith("relation-")
            ]
            entity_ids = {
                owner_id
                for owner_id in affected_owner_ids
                if owner_id.startswith("entity-")
            }
            relation_rows = []
            if relation_ids:
                placeholders = ",".join("?" for _ in relation_ids)
                relation_rows = connection.execute(
                    f"SELECT * FROM relation_registry WHERE workspace_id = ? "
                    f"AND relation_id IN ({placeholders})",
                    (self.workspace_id, *relation_ids),
                ).fetchall()
                for relation in relation_rows:
                    entity_ids.update(
                        (relation["entity_a_id"], relation["entity_b_id"])
                    )
            projection_owner_ids = list(
                dict.fromkeys(
                    (*affected_owner_ids, *relation_ids, *sorted(entity_ids))
                )
            )
            owner_snapshots = {
                owner_id: self._owner_snapshot_sync(connection, owner_id)
                for owner_id in projection_owner_ids
            }
            entity_rows = []
            alias_rows = []
            entity_embedding_rows: list[dict[str, Any]] = []
            if entity_ids:
                placeholders = ",".join("?" for _ in entity_ids)
                entity_rows = connection.execute(
                    f"SELECT * FROM entity_registry WHERE workspace_id = ? "
                    f"AND entity_id IN ({placeholders})",
                    (self.workspace_id, *entity_ids),
                ).fetchall()
                alias_rows = connection.execute(
                    f"SELECT * FROM entity_aliases WHERE workspace_id = ? "
                    f"AND entity_id IN ({placeholders})",
                    (self.workspace_id, *entity_ids),
                ).fetchall()
                for row in connection.execute(
                    f"SELECT * FROM memory_embeddings WHERE workspace_id = ? "
                    f"AND object_kind = 'entity_name' "
                    f"AND owner_id IN ({placeholders})",
                    (self.workspace_id, *entity_ids),
                ).fetchall():
                    item = dict(row)
                    item["vector"] = base64.b64encode(item["vector"]).decode("ascii")
                    entity_embedding_rows.append(item)

            connection.execute(
                "DELETE FROM atom_evidence WHERE episode_id = ?", (episode_id,)
            )
            orphan_atom_ids = []
            for atom_id in candidate_atom_ids:
                remaining = connection.execute(
                    "SELECT 1 FROM atom_evidence WHERE atom_id = ? LIMIT 1",
                    (atom_id,),
                ).fetchone()
                if remaining is None:
                    orphan_atom_ids.append(atom_id)
                else:
                    connection.execute(
                        """
                        UPDATE atoms SET support_count = (
                            SELECT count(DISTINCT episode_id)
                            FROM atom_evidence WHERE atom_id = ?
                        ), updated_at = ? WHERE atom_id = ?
                        """,
                        (atom_id, _utc_now(), atom_id),
                    )

            orphan_atoms = [
                dict(row) for row in atom_rows if row["atom_id"] in orphan_atom_ids
            ]
            embedding_rows: list[dict[str, Any]] = []
            evolution_rows: list[dict[str, Any]] = []
            if orphan_atom_ids:
                placeholders = ",".join("?" for _ in orphan_atom_ids)
                raw_embeddings = connection.execute(
                    f"SELECT * FROM memory_embeddings WHERE workspace_id = ? "
                    f"AND object_kind = 'atom' AND object_id IN ({placeholders})",
                    (self.workspace_id, *orphan_atom_ids),
                ).fetchall()
                for row in raw_embeddings:
                    item = dict(row)
                    item["vector"] = base64.b64encode(item["vector"]).decode("ascii")
                    embedding_rows.append(item)
                evolution_rows = [
                    dict(row)
                    for row in connection.execute(
                        f"SELECT * FROM atom_evolution WHERE "
                        f"source_atom_id IN ({placeholders}) OR "
                        f"target_atom_id IN ({placeholders})",
                        (*orphan_atom_ids, *orphan_atom_ids),
                    ).fetchall()
                ]
                connection.execute(
                    f"DELETE FROM atom_evolution WHERE "
                    f"source_atom_id IN ({placeholders}) OR "
                    f"target_atom_id IN ({placeholders})",
                    (*orphan_atom_ids, *orphan_atom_ids),
                )
                connection.execute(
                    f"DELETE FROM memory_embeddings WHERE workspace_id = ? "
                    f"AND object_kind = 'atom' AND object_id IN ({placeholders})",
                    (self.workspace_id, *orphan_atom_ids),
                )
                connection.execute(
                    f"DELETE FROM atoms WHERE workspace_id = ? "
                    f"AND atom_id IN ({placeholders})",
                    (self.workspace_id, *orphan_atom_ids),
                )

            # Keep the owner projection metadata in lockstep with the surviving
            # Atom record layer.  The graph may already have removed these
            # owners, but the workbench counters deliberately read the SQLite
            # registries, so leaving empty registry rows behind reports ghost
            # entities and relations after a hard document deletion.
            for owner_id in affected_owner_ids:
                remaining_atom_ids = [
                    row["atom_id"]
                    for row in connection.execute(
                        "SELECT atom_id FROM atoms "
                        "WHERE workspace_id = ? AND owner_id = ? ORDER BY rowid",
                        (self.workspace_id, owner_id),
                    ).fetchall()
                ]
                table, id_column = (
                    ("relation_registry", "relation_id")
                    if owner_id.startswith("relation-")
                    else ("entity_registry", "entity_id")
                )
                connection.execute(
                    f"UPDATE {table} SET atom_ids_json = ? "
                    f"WHERE workspace_id = ? AND {id_column} = ?",
                    (
                        json.dumps(remaining_atom_ids, ensure_ascii=False),
                        self.workspace_id,
                        owner_id,
                    ),
                )

            # Relations without any surviving Atom no longer represent a
            # memory owner.  Remove them before determining whether their
            # endpoint entities are also orphaned.
            orphan_relation_ids = [
                relation_id
                for relation_id in relation_ids
                if connection.execute(
                    "SELECT 1 FROM atoms WHERE workspace_id = ? "
                    "AND owner_id = ? LIMIT 1",
                    (self.workspace_id, relation_id),
                ).fetchone()
                is None
            ]
            if orphan_relation_ids:
                placeholders = ",".join("?" for _ in orphan_relation_ids)
                connection.execute(
                    f"DELETE FROM relation_registry WHERE workspace_id = ? "
                    f"AND relation_id IN ({placeholders})",
                    (self.workspace_id, *orphan_relation_ids),
                )
                connection.execute(
                    f"DELETE FROM owner_summary_checkpoints "
                    f"WHERE workspace_id = ? AND owner_id IN ({placeholders})",
                    (self.workspace_id, *orphan_relation_ids),
                )

            # An entity is removable only when it owns no Atom and is no
            # longer an endpoint of a surviving relation.  This preserves
            # relation-only entities while eliminating empty projection owners.
            orphan_entity_ids = []
            for entity_id in entity_ids:
                has_atoms = connection.execute(
                    "SELECT 1 FROM atoms WHERE workspace_id = ? "
                    "AND owner_id = ? LIMIT 1",
                    (self.workspace_id, entity_id),
                ).fetchone()
                has_relations = connection.execute(
                    "SELECT 1 FROM relation_registry WHERE workspace_id = ? "
                    "AND (entity_a_id = ? OR entity_b_id = ?) LIMIT 1",
                    (self.workspace_id, entity_id, entity_id),
                ).fetchone()
                if has_atoms is None and has_relations is None:
                    orphan_entity_ids.append(entity_id)

            if orphan_entity_ids:
                placeholders = ",".join("?" for _ in orphan_entity_ids)
                connection.execute(
                    f"DELETE FROM memory_embeddings WHERE workspace_id = ? "
                    f"AND object_kind = 'entity_name' "
                    f"AND owner_id IN ({placeholders})",
                    (self.workspace_id, *orphan_entity_ids),
                )
                connection.execute(
                    f"DELETE FROM entity_name_fts WHERE workspace_id = ? "
                    f"AND entity_id IN ({placeholders})",
                    (self.workspace_id, *orphan_entity_ids),
                )
                connection.execute(
                    f"DELETE FROM entity_aliases WHERE workspace_id = ? "
                    f"AND entity_id IN ({placeholders})",
                    (self.workspace_id, *orphan_entity_ids),
                )
                connection.execute(
                    f"DELETE FROM entity_registry WHERE workspace_id = ? "
                    f"AND entity_id IN ({placeholders})",
                    (self.workspace_id, *orphan_entity_ids),
                )
                connection.execute(
                    f"DELETE FROM owner_summary_checkpoints "
                    f"WHERE workspace_id = ? AND owner_id IN ({placeholders})",
                    (self.workspace_id, *orphan_entity_ids),
                )

            payload = {
                "episode": dict(episode),
                "entities": [dict(row) for row in entity_rows],
                "aliases": [dict(row) for row in alias_rows],
                "relations": [dict(row) for row in relation_rows],
                "evidence": [dict(row) for row in evidence_rows],
                "atoms": orphan_atoms,
                "embeddings": [*entity_embedding_rows, *embedding_rows],
                "evolution": evolution_rows,
            }
            connection.execute(
                """
                INSERT INTO memory_deletion_backups(
                    backup_id, workspace_id, episode_id, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    backup_id,
                    self.workspace_id,
                    episode_id,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    _utc_now(),
                ),
            )
            # Keep dirty owner markers after the Episode row is removed.  The
            # outbox owns only an optional diagnostic Episode reference, so
            # clear that foreign key before deleting the Episode itself.
            connection.execute(
                "UPDATE projection_outbox SET episode_id = NULL "
                "WHERE workspace_id = ? AND episode_id = ?",
                (self.workspace_id, episode_id),
            )
            for owner_id in projection_owner_ids:
                self._queue_owner_projection_sync(
                    connection,
                    owner_id,
                    snapshot=owner_snapshots[owner_id],
                )
            connection.execute(
                "DELETE FROM episodes WHERE episode_id = ? AND workspace_id = ?",
                (episode_id, self.workspace_id),
            )
        return {
            "backup_id": backup_id,
            "episode_id": episode_id,
            "affected_owner_ids": projection_owner_ids,
            "removed_atom_ids": orphan_atom_ids,
        }

    async def backup_and_clear(self) -> list[str]:
        self._require_initialized()
        episode_ids = await asyncio.to_thread(self._list_episode_ids_sync)
        backup_ids: list[str] = []
        for episode_id in episode_ids:
            result = await self.backup_and_delete_episode(episode_id)
            if result is not None:
                backup_ids.append(str(result["backup_id"]))
        await asyncio.to_thread(self._clear_registry_sync)
        return backup_ids

    def _clear_registry_sync(self) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM memory_embeddings WHERE workspace_id = ?",
                (self.workspace_id,),
            )
            connection.execute(
                "DELETE FROM entity_name_fts WHERE workspace_id = ?",
                (self.workspace_id,),
            )
            connection.execute(
                "DELETE FROM entity_aliases WHERE workspace_id = ?",
                (self.workspace_id,),
            )
            connection.execute(
                "DELETE FROM relation_registry WHERE workspace_id = ?",
                (self.workspace_id,),
            )
            connection.execute(
                "DELETE FROM entity_registry WHERE workspace_id = ?",
                (self.workspace_id,),
            )
            connection.execute(
                "DELETE FROM workspace_settings WHERE workspace_id = ?",
                (self.workspace_id,),
            )
            connection.execute(
                "DELETE FROM owner_summary_checkpoints WHERE workspace_id = ?",
                (self.workspace_id,),
            )

    def _list_episode_ids_sync(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT episode_id FROM episodes WHERE workspace_id = ? "
                "ORDER BY created_at, episode_id",
                (self.workspace_id,),
            ).fetchall()
        return [row["episode_id"] for row in rows]

    async def restore_episode_backup(self, backup_id: str) -> None:
        self._require_initialized()
        await asyncio.to_thread(self._restore_episode_backup_sync, backup_id)

    def _restore_episode_backup_sync(self, backup_id: str) -> None:
        with self._connect() as connection:
            backup = connection.execute(
                "SELECT * FROM memory_deletion_backups "
                "WHERE backup_id = ? AND workspace_id = ?",
                (backup_id, self.workspace_id),
            ).fetchone()
            if backup is None:
                raise KeyError(f"unknown memory deletion backup {backup_id!r}")
            if backup["restored_at"] is not None:
                return
            payload = json.loads(backup["payload_json"])

            def insert_row(table: str, row: dict[str, Any]) -> None:
                if table == "atom_evidence" and "evidence_id" not in row:
                    row = {
                        "evidence_id": _evidence_id(
                            AtomEvidence(
                                atom_id=str(row["atom_id"]),
                                episode_id=str(row["episode_id"]),
                                quote=row.get("quote"),
                                span_start=int(row.get("span_start", -1)),
                                span_end=int(row.get("span_end", -1)),
                                extraction_revision=row.get("extraction_revision"),
                                created_at=_parse_time(row.get("created_at"))
                                or datetime.now(timezone.utc),
                            )
                        ),
                        **row,
                    }
                columns = list(row)
                placeholders = ",".join("?" for _ in columns)
                connection.execute(
                    f"INSERT OR IGNORE INTO {table} ({','.join(columns)}) "
                    f"VALUES ({placeholders})",
                    tuple(row[column] for column in columns),
                )

            for entity in payload.get("entities", []):
                insert_row("entity_registry", entity)
            for alias in payload.get("aliases", []):
                insert_row("entity_aliases", alias)
                connection.execute(
                    "INSERT INTO entity_name_fts(workspace_id, entity_id, name) "
                    "SELECT ?, ?, ? WHERE NOT EXISTS ("
                    "SELECT 1 FROM entity_name_fts WHERE workspace_id = ? "
                    "AND entity_id = ? AND name = ?)",
                    (
                        alias["workspace_id"],
                        alias["entity_id"],
                        alias["alias"],
                        alias["workspace_id"],
                        alias["entity_id"],
                        alias["alias"],
                    ),
                )
            for relation in payload.get("relations", []):
                insert_row("relation_registry", relation)
            insert_row("episodes", payload["episode"])
            for atom in payload["atoms"]:
                insert_row("atoms", atom)
            for evidence in payload["evidence"]:
                insert_row("atom_evidence", evidence)
            for embedding in payload["embeddings"]:
                embedding = dict(embedding)
                embedding["vector"] = base64.b64decode(embedding["vector"])
                insert_row("memory_embeddings", embedding)
            for evolution in payload["evolution"]:
                insert_row("atom_evolution", evolution)
            affected_atom_ids = {
                evidence["atom_id"] for evidence in payload["evidence"]
            }
            for atom_id in affected_atom_ids:
                connection.execute(
                    """
                    UPDATE atoms SET support_count = (
                        SELECT count(DISTINCT episode_id)
                        FROM atom_evidence WHERE atom_id = ?
                    ), updated_at = ? WHERE atom_id = ?
                    """,
                    (atom_id, _utc_now(), atom_id),
                )
            affected_owner_ids = {
                str(row["owner_id"])
                for atom_id in affected_atom_ids
                if (
                    row := connection.execute(
                        "SELECT owner_id FROM atoms WHERE workspace_id = ? "
                        "AND atom_id = ?",
                        (self.workspace_id, atom_id),
                    ).fetchone()
                )
                is not None
            }
            affected_owner_ids.update(
                str(row["entity_id"]) for row in payload.get("entities", [])
            )
            affected_owner_ids.update(
                str(row["relation_id"]) for row in payload.get("relations", [])
            )
            for owner_id in sorted(affected_owner_ids):
                self._queue_owner_projection_sync(connection, owner_id)
            connection.execute(
                "UPDATE memory_deletion_backups SET restored_at = ? "
                "WHERE backup_id = ?",
                (_utc_now(), backup_id),
            )

    async def add_atom_evolution(
        self,
        source_atom_id: str,
        target_atom_id: str,
        relation_type: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._require_initialized()
        await asyncio.to_thread(
            self._add_atom_evolution_sync,
            source_atom_id,
            target_atom_id,
            relation_type,
            metadata or {},
        )

    def _add_atom_evolution_sync(
        self,
        source_atom_id: str,
        target_atom_id: str,
        relation_type: str,
        metadata: dict[str, Any],
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO atom_evolution(
                    source_atom_id, target_atom_id, relation_type,
                    created_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    source_atom_id,
                    target_atom_id,
                    relation_type,
                    _utc_now(),
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                ),
            )

    async def list_atom_evolutions(self, atom_id: str) -> list[dict[str, Any]]:
        self._require_initialized()
        return await asyncio.to_thread(self._list_atom_evolutions_sync, atom_id)

    def _list_atom_evolutions_sync(self, atom_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT source_atom_id, target_atom_id, relation_type,
                       created_at, metadata_json
                FROM atom_evolution
                WHERE source_atom_id = ? OR target_atom_id = ?
                ORDER BY created_at, source_atom_id, target_atom_id
                """,
                (atom_id, atom_id),
            ).fetchall()
        return [
            {
                "source_atom_id": row["source_atom_id"],
                "target_atom_id": row["target_atom_id"],
                "relation_type": row["relation_type"],
                "created_at": row["created_at"],
                "metadata": json.loads(row["metadata_json"] or "{}"),
            }
            for row in rows
        ]

    async def put_embedding(
        self,
        *,
        object_kind: str,
        object_id: str,
        source_text: str,
        vector: np.ndarray,
        model_name: str,
        owner_id: str | None = None,
    ) -> None:
        self._require_initialized()
        await asyncio.to_thread(
            self._put_embedding_sync,
            object_kind,
            object_id,
            source_text,
            vector,
            model_name,
            owner_id,
        )

    def _put_embedding_sync(
        self,
        object_kind: str,
        object_id: str,
        source_text: str,
        vector: np.ndarray,
        model_name: str,
        owner_id: str | None,
    ) -> None:
        normalized = np.asarray(vector, dtype=np.float32).reshape(-1)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO memory_embeddings(
                    workspace_id, object_kind, object_id, owner_id, model_name,
                    dimensions, vector, source_text, content_hash, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workspace_id, object_kind, object_id, model_name)
                DO UPDATE SET
                    owner_id = excluded.owner_id,
                    dimensions = excluded.dimensions,
                    vector = excluded.vector,
                    source_text = excluded.source_text,
                    content_hash = excluded.content_hash,
                    updated_at = excluded.updated_at
                """,
                (
                    self.workspace_id,
                    object_kind,
                    object_id,
                    owner_id,
                    model_name,
                    int(normalized.size),
                    normalized.tobytes(),
                    source_text,
                    hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
                    _utc_now(),
                ),
            )

    async def search_embeddings(
        self,
        *,
        object_kind: str,
        query_vector: np.ndarray,
        model_name: str,
        owner_id: str | None = None,
        limit: int = 8,
        threshold: float = 0.0,
    ) -> list[CandidateMatch]:
        self._require_initialized()
        return await asyncio.to_thread(
            self._search_embeddings_sync,
            object_kind,
            query_vector,
            model_name,
            owner_id,
            limit,
            threshold,
        )

    def _search_embeddings_sync(
        self,
        object_kind: str,
        query_vector: np.ndarray,
        model_name: str,
        owner_id: str | None,
        limit: int,
        threshold: float,
    ) -> list[CandidateMatch]:
        query = np.asarray(query_vector, dtype=np.float32).reshape(-1)
        query_norm = float(np.linalg.norm(query))
        if query_norm == 0:
            return []
        statement = (
            "SELECT * FROM memory_embeddings WHERE workspace_id = ? "
            "AND object_kind = ? AND model_name = ?"
        )
        parameters: list[Any] = [self.workspace_id, object_kind, model_name]
        if owner_id is not None:
            statement += " AND owner_id = ?"
            parameters.append(owner_id)
        with self._connect() as connection:
            rows = connection.execute(statement, parameters).fetchall()
        matches: list[CandidateMatch] = []
        for row in rows:
            if row["dimensions"] != query.size:
                continue
            vector = np.frombuffer(row["vector"], dtype=np.float32)
            denominator = query_norm * float(np.linalg.norm(vector))
            if denominator == 0:
                continue
            score = float(np.dot(query, vector) / denominator)
            if score >= threshold:
                result_id = (
                    row["owner_id"]
                    if object_kind == "entity_name" and row["owner_id"]
                    else row["object_id"]
                )
                matches.append(
                    CandidateMatch(
                        object_id=result_id,
                        score=score,
                        text=row["source_text"],
                        match_kind="embedding",
                    )
                )
        matches.sort(key=lambda item: item.score, reverse=True)
        deduplicated: list[CandidateMatch] = []
        seen: set[str] = set()
        for match in matches:
            if match.object_id in seen:
                continue
            seen.add(match.object_id)
            deduplicated.append(match)
            if len(deduplicated) >= limit:
                break
        return deduplicated

    async def set_workspace_mode(self, mode: str) -> None:
        self._require_initialized()
        await asyncio.to_thread(self._set_workspace_mode_sync, mode)

    def _set_workspace_mode_sync(self, mode: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO workspace_settings(
                    workspace_id, setting_key, setting_value, updated_at
                ) VALUES (?, 'knowledge_commit_mode', ?, ?)
                ON CONFLICT(workspace_id, setting_key) DO UPDATE SET
                    setting_value = excluded.setting_value,
                    updated_at = excluded.updated_at
                """,
                (self.workspace_id, mode, _utc_now()),
            )

    async def get_workspace_mode(self) -> str | None:
        self._require_initialized()
        return await asyncio.to_thread(self._get_workspace_mode_sync)

    def _get_workspace_mode_sync(self) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT setting_value FROM workspace_settings
                WHERE workspace_id = ? AND setting_key = 'knowledge_commit_mode'
                """,
                (self.workspace_id,),
            ).fetchone()
        return row["setting_value"] if row else None

    async def register_exploration_trace(
        self, exploration_id: str, query: str | None = None
    ) -> dict[str, Any]:
        """Create or enrich one persisted exploration trace."""

        self._require_initialized()
        return await asyncio.to_thread(
            self._register_exploration_trace_sync, exploration_id, query
        )

    def _register_exploration_trace_sync(
        self, exploration_id: str, query: str | None
    ) -> dict[str, Any]:
        now = _utc_now()
        normalized_query = query.strip() if query else None
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO exploration_traces(
                    workspace_id, exploration_id, query, status, created_at, updated_at
                ) VALUES (?, ?, ?, 'active', ?, ?)
                ON CONFLICT(workspace_id, exploration_id) DO UPDATE SET
                    query = COALESCE(excluded.query, exploration_traces.query),
                    updated_at = excluded.updated_at
                """,
                (
                    self.workspace_id,
                    exploration_id,
                    normalized_query,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                """
                SELECT exploration_id, query, status, created_at, updated_at
                FROM exploration_traces
                WHERE workspace_id = ? AND exploration_id = ?
                """,
                (self.workspace_id, exploration_id),
            ).fetchone()
        return dict(row)

    async def append_exploration_event(
        self,
        *,
        exploration_id: str,
        event_type: str,
        agent_id: str,
        call_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Append one ordered event and return its persisted representation."""

        self._require_initialized()
        return await asyncio.to_thread(
            self._append_exploration_event_sync,
            exploration_id,
            event_type,
            agent_id,
            call_id,
            dict(payload),
        )

    def _append_exploration_event_sync(
        self,
        exploration_id: str,
        event_type: str,
        agent_id: str,
        call_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        now = _utc_now()
        payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO exploration_traces(
                    workspace_id, exploration_id, status, created_at, updated_at
                ) VALUES (?, ?, 'active', ?, ?)
                ON CONFLICT(workspace_id, exploration_id) DO UPDATE SET
                    updated_at = excluded.updated_at
                """,
                (self.workspace_id, exploration_id, now, now),
            )
            row = connection.execute(
                """
                SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq
                FROM exploration_events
                WHERE workspace_id = ? AND exploration_id = ?
                """,
                (self.workspace_id, exploration_id),
            ).fetchone()
            seq = int(row["next_seq"])
            connection.execute(
                """
                INSERT INTO exploration_events(
                    workspace_id, exploration_id, seq, event_type,
                    agent_id, call_id, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.workspace_id,
                    exploration_id,
                    seq,
                    event_type,
                    agent_id,
                    call_id,
                    payload_json,
                    now,
                ),
            )
        return {
            "exploration_id": exploration_id,
            "seq": seq,
            "type": event_type,
            "agent_id": agent_id,
            "call_id": call_id,
            "payload": payload,
            "created_at": now,
        }

    async def list_exploration_traces(
        self, *, include_archived: bool = False, limit: int = 50
    ) -> list[dict[str, Any]]:
        self._require_initialized()
        return await asyncio.to_thread(
            self._list_exploration_traces_sync, include_archived, limit
        )

    def _list_exploration_traces_sync(
        self, include_archived: bool, limit: int
    ) -> list[dict[str, Any]]:
        status_filter = "" if include_archived else "AND trace.status = 'active'"
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT trace.exploration_id, trace.query, trace.status,
                       trace.created_at, trace.updated_at,
                       COALESCE(MAX(event.seq), 0) AS last_seq
                FROM exploration_traces AS trace
                LEFT JOIN exploration_events AS event
                  ON event.workspace_id = trace.workspace_id
                 AND event.exploration_id = trace.exploration_id
                WHERE trace.workspace_id = ? {status_filter}
                GROUP BY trace.workspace_id, trace.exploration_id
                ORDER BY trace.updated_at DESC
                LIMIT ?
                """,
                (self.workspace_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    async def get_exploration_events(
        self, exploration_id: str, *, after_seq: int = 0, limit: int = 500
    ) -> list[dict[str, Any]]:
        self._require_initialized()
        return await asyncio.to_thread(
            self._get_exploration_events_sync, exploration_id, after_seq, limit
        )

    def _get_exploration_events_sync(
        self, exploration_id: str, after_seq: int, limit: int
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT exploration_id, seq, event_type, agent_id, call_id,
                       payload_json, created_at
                FROM exploration_events
                WHERE workspace_id = ? AND exploration_id = ? AND seq > ?
                ORDER BY seq ASC
                LIMIT ?
                """,
                (self.workspace_id, exploration_id, after_seq, limit),
            ).fetchall()
        return [
            {
                "exploration_id": row["exploration_id"],
                "seq": row["seq"],
                "type": row["event_type"],
                "agent_id": row["agent_id"],
                "call_id": row["call_id"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    async def archive_exploration_trace(self, exploration_id: str) -> bool:
        self._require_initialized()
        return await asyncio.to_thread(
            self._archive_exploration_trace_sync, exploration_id
        )

    def _archive_exploration_trace_sync(self, exploration_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE exploration_traces
                SET status = 'archived', updated_at = ?
                WHERE workspace_id = ? AND exploration_id = ?
                """,
                (_utc_now(), self.workspace_id, exploration_id),
            )
        return cursor.rowcount > 0
