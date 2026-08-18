"""Local workspace registry for one MAGI Runtime process."""

from __future__ import annotations

import json
import re
import shutil
import unicodedata
from uuid import uuid4
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock
from typing import Any

from magi_core.workspace import WorkspaceLayout


_WORKSPACE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_WORKSPACE_ID_SEPARATOR = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class WorkspaceRecord:
    id: str
    root: str
    label: str | None = None

    @property
    def path(self) -> Path:
        return Path(self.root)


@dataclass(frozen=True, slots=True)
class WorkspaceDeletion:
    deleted: WorkspaceRecord
    active_workspace_id: str


class WorkspaceManager:
    """Maintain isolated workspace roots below a local workspace home.

    Existing installations can register ``mgc-test`` itself as the default
    workspace root. New workspaces are created below ``mgc-test/workspaces``;
    no existing data is moved implicitly.
    """

    def __init__(
        self,
        home: str | Path,
        *,
        default_workspace_id: str,
        default_workspace_root: str | Path | None = None,
    ) -> None:
        self.home = Path(home).expanduser().resolve()
        self.registry_path = self.home / "workspaces.json"
        self.default_workspace_id = self._validate_id(default_workspace_id)
        self.default_workspace_root = Path(
            default_workspace_root or self.home
        ).expanduser().resolve()
        self._lock = RLock()

    @staticmethod
    def _validate_id(workspace_id: str) -> str:
        value = str(workspace_id).strip()
        if not _WORKSPACE_ID.fullmatch(value):
            raise ValueError(
                "workspace id must start with an alphanumeric character and "
                "contain only letters, numbers, '.', '_' or '-'"
            )
        return value

    def initialize(self) -> None:
        with self._lock:
            self.home.mkdir(parents=True, exist_ok=True)
            active, records, schema = self._read_registry()
            changed = schema != 2
            if self.default_workspace_id not in records:
                records[self.default_workspace_id] = WorkspaceRecord(
                    self.default_workspace_id,
                    str(self.default_workspace_root),
                    "Default",
                )
                changed = True
            if active not in records:
                active = self.default_workspace_id
                changed = True
            if changed:
                self._write(records, active_workspace_id=active)
            self.default_workspace_root.mkdir(parents=True, exist_ok=True)

    @property
    def active_workspace_id(self) -> str:
        """Return the persisted workspace selected by the local user."""

        with self._lock:
            self.initialize()
            active, records, _ = self._read_registry()
            if active not in records:
                raise RuntimeError(
                    f"invalid active workspace in {self.registry_path}"
                )
            return active

    def set_active(self, workspace_id: str) -> WorkspaceRecord:
        """Persist the active workspace after Runtime activation succeeds."""

        with self._lock:
            self.initialize()
            key = self._validate_id(workspace_id)
            _, records, _ = self._read_registry()
            try:
                record = records[key]
            except KeyError as exc:
                raise KeyError(f"unknown MAGI workspace {key!r}") from exc
            self._write(records, active_workspace_id=key)
            return record

    def list(self) -> tuple[WorkspaceRecord, ...]:
        with self._lock:
            self.initialize()
            _, records, _ = self._read_registry()
            return tuple(sorted(records.values(), key=lambda item: item.id))

    def get(self, workspace_id: str) -> WorkspaceRecord:
        with self._lock:
            self.initialize()
            key = self._validate_id(workspace_id)
            _, records, _ = self._read_registry()
            try:
                return records[key]
            except KeyError as exc:
                raise KeyError(f"unknown MAGI workspace {key!r}") from exc

    def delete_capability(self, workspace_id: str) -> tuple[bool, str | None]:
        """Return whether a registry-owned workspace can be deleted safely."""

        with self._lock:
            self.initialize()
            key = self._validate_id(workspace_id)
            _, records, _ = self._read_registry()
            try:
                record = records[key]
            except KeyError as exc:
                raise KeyError(f"unknown MAGI workspace {key!r}") from exc
            if key == self.default_workspace_id:
                return False, "The default workspace cannot be deleted"
            expected = (self.home / "workspaces" / key).resolve()
            if record.path.resolve() != expected:
                return False, "Externally managed workspace roots cannot be deleted"
            if len(records) <= 1:
                return False, "At least one workspace must remain"
            return True, None

    def delete(self, workspace_id: str) -> WorkspaceDeletion:
        """Remove one managed workspace registry entry and its local files.

        Durable backend records must already have been cleared by the runtime.
        The directory is renamed before the registry commit so registry-write
        failures can restore it without exposing a half-deleted workspace.
        """

        with self._lock:
            self.initialize()
            key = self._validate_id(workspace_id)
            active, records, _ = self._read_registry()
            allowed, reason = self.delete_capability(key)
            if not allowed:
                raise ValueError(reason or f"workspace {key!r} cannot be deleted")
            record = records[key]
            remaining = {item_id: item for item_id, item in records.items() if item_id != key}
            if active == key:
                replacement = (
                    self.default_workspace_id
                    if self.default_workspace_id in remaining
                    else sorted(remaining)[0]
                )
            else:
                replacement = active
            if replacement is None or replacement not in remaining:
                raise RuntimeError("workspace registry has no valid active replacement")

            target = record.path.resolve()
            tombstone = target.with_name(f".deleting-{key}-{uuid4().hex}")
            if target.exists():
                target.replace(tombstone)
            try:
                self._write(remaining, active_workspace_id=replacement)
            except BaseException:
                if tombstone.exists():
                    tombstone.replace(target)
                raise
            if tombstone.exists():
                shutil.rmtree(tombstone)
            return WorkspaceDeletion(record, replacement)

    def create(
        self,
        workspace_id: str | None = None,
        *,
        label: str | None = None,
        root: str | Path | None = None,
    ) -> WorkspaceRecord:
        with self._lock:
            self.initialize()
            active, records, _ = self._read_registry()
            normalized_label = str(label).strip() if label else None
            if workspace_id is None:
                if not normalized_label:
                    raise ValueError(
                        "workspace name is required when workspace id is omitted"
                    )
                key = self._available_generated_id(normalized_label, records)
            else:
                key = self._validate_id(workspace_id)
                if key in records:
                    raise ValueError(f"workspace {key!r} already exists")
            target = Path(root).expanduser().resolve() if root else (
                self.home / "workspaces" / key
            ).resolve()
            if target.exists():
                raise ValueError(f"workspace directory already exists: {target}")
            target.mkdir(parents=True, exist_ok=False)
            for directory in ("inputs", "logs", "artifacts", "ragstore"):
                (target / directory).mkdir()
            record = WorkspaceRecord(
                key,
                str(target),
                normalized_label,
            )
            records[key] = record
            self._write(records, active_workspace_id=active)
            return record

    @staticmethod
    def _generated_id_base(label: str) -> str:
        """Build a readable storage identifier without exposing it in the UI."""

        ascii_label = unicodedata.normalize("NFKD", label).encode(
            "ascii", "ignore"
        ).decode("ascii")
        base = _WORKSPACE_ID_SEPARATOR.sub("-", ascii_label.lower()).strip("-")
        return base[:128].rstrip("-") or f"workspace-{uuid4().hex[:8]}"

    def _available_generated_id(
        self,
        label: str,
        records: dict[str, WorkspaceRecord],
    ) -> str:
        base = self._generated_id_base(label)
        if base not in records and not (self.home / "workspaces" / base).exists():
            return base
        suffix = 2
        while True:
            suffix_text = f"-{suffix}"
            candidate = f"{base[: 128 - len(suffix_text)].rstrip('-')}{suffix_text}"
            if candidate not in records and not (
                self.home / "workspaces" / candidate
            ).exists():
                return candidate
            suffix += 1

    def layout(self, workspace_id: str) -> WorkspaceLayout:
        record = self.get(workspace_id)
        return WorkspaceLayout.from_root(record.path, workspace_id=record.id)

    def _read_registry(
        self,
    ) -> tuple[str | None, dict[str, WorkspaceRecord], int | None]:
        if not self.registry_path.exists():
            return None, {}, None
        raw: Any = json.loads(self.registry_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema") not in (1, 2):
            raise RuntimeError(f"invalid workspace registry {self.registry_path}")
        rows = raw.get("workspaces")
        if not isinstance(rows, list):
            raise RuntimeError(f"invalid workspace registry {self.registry_path}")
        result: dict[str, WorkspaceRecord] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise RuntimeError(f"invalid workspace registry {self.registry_path}")
            record = WorkspaceRecord(
                id=self._validate_id(row.get("id", "")),
                root=str(Path(row.get("root", "")).expanduser().resolve()),
                label=row.get("label"),
            )
            result[record.id] = record
        active = raw.get("active_workspace_id")
        if active is not None:
            active = self._validate_id(active)
        return active, result, raw.get("schema")

    def _write(
        self,
        records: dict[str, WorkspaceRecord],
        *,
        active_workspace_id: str | None,
    ) -> None:
        payload = {
            "schema": 2,
            "active_workspace_id": active_workspace_id,
            "workspaces": [
                asdict(records[key]) for key in sorted(records)
            ],
        }
        temporary = self.registry_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.registry_path)
