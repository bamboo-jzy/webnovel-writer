#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List

from .chapter_commit_schema import normalize_accepted_events
from .config import SQLITE_BUSY_TIMEOUT_SECONDS
from .story_contracts import StoryContractPaths, read_json_if_exists, write_json


class EventLogStore:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root).expanduser().resolve()
        self.paths = StoryContractPaths.from_project_root(self.project_root)
        # 最近一次事件镜像（index.db 的 story_events 表）写入失败信息；空串表示成功。
        self.last_mirror_error = ""

    @contextmanager
    def _connect(self, *, row_factory: bool = False) -> Iterator[sqlite3.Connection]:
        """统一 SQLite 连接管理，确保连接始终关闭。"""
        db_path = self.project_root / ".webnovel" / "index.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), timeout=SQLITE_BUSY_TIMEOUT_SECONDS)
        if row_factory:
            conn.row_factory = sqlite3.Row
        try:
            conn.execute(f"PRAGMA busy_timeout={int(SQLITE_BUSY_TIMEOUT_SECONDS * 1000)}")
            yield conn
        finally:
            conn.close()

    def write_events(self, chapter: int, events: Any) -> Path:
        normalized = self.normalize_events(chapter, events)
        path = self.paths.event_json(chapter)
        write_json(path, normalized)
        self.last_mirror_error = self._mirror_errors(chapter, normalized)
        return path

    def mirror_events_only(self, chapter: int, events: Any) -> str:
        """只重建 story_events 镜像，不写事件 JSON 文件。

        `projections retry/replay` 用这个入口：修复索引的同时不产生 commit 侧副作用
        （事件 JSON 是提交事实的一部分，归 chapter-commit 写，重放阶段不得改写）。
        事件本身不合法时同样降级为错误字符串，不让整次 retry 失败。
        """
        try:
            normalized = self.normalize_events(chapter, events)
        except ValueError as exc:
            message = str(exc) or exc.__class__.__name__
            self._note_mirror_error(chapter, message)
            self.last_mirror_error = message
            return message
        self.last_mirror_error = self._mirror_errors(chapter, normalized)
        return self.last_mirror_error

    def retract_chapter(self, chapter: int) -> int:
        """删除该章在 index.db 里的 `story_events` 镜像行，返回删除行数。

        镜像用 `INSERT OR IGNORE` 写（event_id 唯一），所以事件内容被改写时旧行
        会一直留着。事实改写（`chapter-commit --allow-fact-revision`）与
        `projections retry --retract` 都需要先撤回再重建。

        只动 index.db 的镜像表：不碰 `.story-system/events/*.events.json`，
        也不碰 commit / 正文（镜像重建不得产生 commit 侧副作用）。
        表还不存在时视为 0 行删除，不让"从未建过镜像"变成错误。
        """
        try:
            chapter = int(chapter or 0)
        except (TypeError, ValueError):
            return 0
        if chapter <= 0:
            return 0
        with self._connect() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("DELETE FROM story_events WHERE chapter = ?", (chapter,))
            except sqlite3.OperationalError:
                return 0
            deleted = int(cursor.rowcount or 0)
            conn.commit()
        return deleted

    def _mirror_errors(self, chapter: int, events: List[Dict[str, Any]]) -> str:
        """把事件写进 index.db 的 story_events 镜像表；失败时降级为可读错误。

        事件 JSON 文件在上一步已经落盘，镜像只是读模型；这里不再让 sqlite 异常
        逃逸（旧行为会让整个 chapter-commit 硬失败，而 commit 其实已经落盘）。
        失败信息通过 `last_mirror_error` 暴露给调用方，并追加到
        `.webnovel/logs/event_mirror_errors.log`，可用 projections retry/replay 重建。
        """
        try:
            self._write_sqlite_mirror(events)
        except (sqlite3.Error, OSError) as exc:
            message = str(exc) or exc.__class__.__name__
            self._note_mirror_error(chapter, message)
            return message
        return ""

    def _note_mirror_error(self, chapter: int, message: str) -> None:
        try:
            log_path = self.project_root / ".webnovel" / "logs" / "event_mirror_errors.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"{stamp}\tchapter={chapter}\t{message}\n")
        except OSError:
            pass

    def read_events(self, chapter: int) -> List[Dict[str, Any]]:
        return list(read_json_if_exists(self.paths.event_json(chapter)) or [])

    def list_recent(self, chapter: int | None = None, limit: int = 200) -> List[Dict[str, Any]]:
        db_path = self.project_root / ".webnovel" / "index.db"
        if not db_path.is_file():
            return []
        with self._connect(row_factory=True) as conn:
            try:
                if chapter is not None:
                    rows = conn.execute(
                        """
                        SELECT event_id, chapter, event_type, subject, payload_json
                        FROM story_events
                        WHERE chapter = ?
                        ORDER BY id DESC
                        LIMIT ?
                        """,
                        (chapter, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT event_id, chapter, event_type, subject, payload_json
                        FROM story_events
                        ORDER BY chapter DESC, id DESC
                        LIMIT ?
                        """,
                        (limit,),
                    ).fetchall()
            except sqlite3.OperationalError:
                return []

        result: List[Dict[str, Any]] = []
        for row in rows:
            payload = {}
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except json.JSONDecodeError:
                payload = {}
            result.append(
                {
                    "event_id": row["event_id"],
                    "chapter": row["chapter"],
                    "event_type": row["event_type"],
                    "subject": row["subject"],
                    "payload": payload,
                }
            )
        return result

    def health(self) -> Dict[str, Any]:
        db_path = self.project_root / ".webnovel" / "index.db"
        file_count = len(list(self.paths.events_dir.glob("chapter_*.events.json")))
        sqlite_rows = 0
        if db_path.is_file():
            with self._connect() as conn:
                try:
                    sqlite_rows = int(
                        conn.execute("SELECT COUNT(*) FROM story_events").fetchone()[0]
                    )
                except sqlite3.OperationalError:
                    sqlite_rows = 0
        return {"ok": True, "sqlite_rows": sqlite_rows, "event_files": file_count}

    def normalize_events(self, chapter: int, events: Any) -> List[Dict[str, Any]]:
        return normalize_accepted_events(chapter, events)

    def _write_sqlite_mirror(self, events: List[Dict[str, Any]]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS story_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    chapter INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_story_events_chapter ON story_events(chapter)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_story_events_type ON story_events(event_type)"
            )
            conn.executemany(
                """
                INSERT OR IGNORE INTO story_events(event_id, chapter, event_type, subject, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        event["event_id"],
                        int(event["chapter"]),
                        event["event_type"],
                        event["subject"],
                        json.dumps(event.get("payload") or {}, ensure_ascii=False),
                    )
                    for event in events
                ],
            )
            conn.commit()
