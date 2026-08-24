from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
import tempfile
import time
from typing import Any
import uuid
import zipfile

from .memory_db import MobileMemoryDatabase
from .state_store import DEFAULT_STATE, MobileStateStore


class MobileArchiveManager:
    VERSION = 1
    INITIAL_ARCHIVE_ID = "0" * 32
    INITIAL_ARCHIVE_NAME = "初始存档"

    def __init__(
        self,
        data_dir: Path,
        cache_dir: Path,
        state_store: MobileStateStore,
        memory_db: MobileMemoryDatabase,
    ):
        self._archive_dir = data_dir / "ember" / "archives"
        self._cache_dir = cache_dir
        self._state_store = state_store
        self._memory_db = memory_db
        self._archive_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_initial_archive()

    def _ensure_initial_archive(self) -> None:
        target = self._archive_path(self.INITIAL_ARCHIVE_ID)
        if target.is_file():
            return

        logical_time = datetime.fromisoformat(
            str(DEFAULT_STATE["对应时间"])
        ).timestamp()
        state = dict(DEFAULT_STATE)
        checkpoint = {
            "wall_time": time.time(),
            "logical_time": logical_time,
            "time_accel_factor": 5.0,
            "time_flow_enabled": True,
            "last_interaction_logical_time": logical_time,
        }
        manifest = {
            "version": self.VERSION,
            "id": self.INITIAL_ARCHIVE_ID,
            "name": self.INITIAL_ARCHIVE_NAME,
            "created_at": logical_time,
            "logical_time": state["对应时间"],
            "is_initial": True,
            "preview": {
                "location": state.get("当前位置", ""),
                "action": state.get("当前行为", ""),
                "P": state["P"],
                "A": state["A"],
                "D": state["D"],
            },
        }

        with tempfile.TemporaryDirectory(dir=str(self._cache_dir)) as temporary:
            empty_database = MobileMemoryDatabase(Path(temporary))
            database_path = empty_database.path
            empty_database.close()
            with zipfile.ZipFile(
                target,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
            ) as archive:
                archive.writestr(
                    "manifest.json",
                    json.dumps(manifest, ensure_ascii=False, indent=2),
                )
                archive.writestr(
                    "state.json",
                    json.dumps(state, ensure_ascii=False, indent=2),
                )
                archive.writestr(
                    "checkpoint.json",
                    json.dumps(checkpoint, ensure_ascii=False, indent=2),
                )
                archive.write(database_path, "memory.sqlite3")

    def create(self, display_name: str) -> dict[str, Any]:
        name = display_name.strip()[:40] or time.strftime("存档 %Y-%m-%d %H:%M")
        archive_id = uuid.uuid4().hex
        created_at = time.time()
        self._state_store.persist()
        snapshot = self._state_store.snapshot()
        checkpoint = MobileStateStore.load_checkpoint(
            self._state_store.root_dir.parent
        ) or {}
        manifest = {
            "version": self.VERSION,
            "id": archive_id,
            "name": name,
            "created_at": created_at,
            "logical_time": snapshot["logical_time"],
            "preview": {
                "location": snapshot["state"].get("当前位置", ""),
                "action": snapshot["state"].get("当前行为", ""),
                "P": snapshot["state"].get("P", 5),
                "A": snapshot["state"].get("A", 5),
                "D": snapshot["state"].get("D", 5),
            },
        }
        target = self._archive_path(archive_id)
        with tempfile.TemporaryDirectory(dir=str(self._cache_dir)) as temporary:
            root = Path(temporary)
            database_path = root / "memory.sqlite3"
            self._memory_db.backup_to(database_path)
            with zipfile.ZipFile(
                target,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
            ) as archive:
                archive.writestr(
                    "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
                )
                archive.writestr(
                    "state.json",
                    json.dumps(snapshot["state"], ensure_ascii=False, indent=2),
                )
                archive.writestr(
                    "checkpoint.json",
                    json.dumps(checkpoint, ensure_ascii=False, indent=2),
                )
                archive.write(database_path, "memory.sqlite3")
        return self._slot(target, manifest)

    def list(self) -> list[dict[str, Any]]:
        slots = []
        for path in self._archive_dir.glob("*.ember"):
            try:
                with zipfile.ZipFile(path, "r") as archive:
                    manifest = json.loads(archive.read("manifest.json"))
                slots.append(self._slot(path, manifest))
            except (OSError, ValueError, KeyError, zipfile.BadZipFile):
                continue
        slots.sort(
            key=lambda item: (
                not item.get("is_initial", False),
                -item["created_at"],
            )
        )
        return slots

    def load(self, archive_id: str) -> dict[str, Any]:
        path = self._archive_path(archive_id)
        if not path.is_file():
            raise ValueError("存档不存在")
        with zipfile.ZipFile(path, "r") as archive:
            required = {"manifest.json", "state.json", "checkpoint.json", "memory.sqlite3"}
            if not required.issubset(set(archive.namelist())):
                raise ValueError("存档内容不完整")
            manifest = json.loads(archive.read("manifest.json"))
            if int(manifest.get("version", 0)) != self.VERSION:
                raise ValueError("存档版本不兼容")
            state = json.loads(archive.read("state.json"))
            checkpoint = json.loads(archive.read("checkpoint.json"))
            if not isinstance(state, dict) or not isinstance(checkpoint, dict):
                raise ValueError("存档状态格式不正确")
            for key in ("P", "A", "D"):
                float(state[key])
            float(checkpoint.get("logical_time"))
            with tempfile.TemporaryDirectory(dir=str(self._cache_dir)) as temporary:
                database_path = Path(temporary) / "memory.sqlite3"
                database_path.write_bytes(archive.read("memory.sqlite3"))
                self._memory_db.restore_from(database_path)
        snapshot = self._state_store.restore(state, checkpoint)
        return {"archive": self._slot(path, manifest), "snapshot": snapshot}

    def delete(self, archive_id: str) -> dict[str, Any]:
        if archive_id.strip().lower() == self.INITIAL_ARCHIVE_ID:
            raise ValueError("初始存档不能删除")
        path = self._archive_path(archive_id)
        if not path.is_file():
            raise ValueError("存档不存在")
        path.unlink()
        return {"ok": True, "id": archive_id}

    def _archive_path(self, archive_id: str) -> Path:
        clean = archive_id.strip().lower()
        if len(clean) != 32 or any(value not in "0123456789abcdef" for value in clean):
            raise ValueError("存档 ID 不合法")
        return self._archive_dir / f"{clean}.ember"

    @staticmethod
    def _slot(path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(manifest.get("id", path.stem)),
            "name": str(manifest.get("name", path.stem)),
            "created_at": float(manifest.get("created_at", path.stat().st_mtime)),
            "logical_time": str(manifest.get("logical_time", "")),
            "preview": manifest.get("preview", {}),
            "size": path.stat().st_size,
            "is_initial": bool(manifest.get("is_initial", False)),
        }
