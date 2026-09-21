#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import hashlib
import logging
import sqlite3
import threading
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, Dict, List

from .commit_artifacts import extraction_list, extraction_text, resolve_scene_index
from .config import SQLITE_BUSY_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

_DELETE_BATCH = 400


class VectorProjectionWriter:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)
        # 最近一次存储调用留下的 embedding 不可用原因（空串表示不是"不可用"故障）。
        self._last_embedding_diagnosis = ""

    def retract(self, chapter: int) -> dict:
        """删除该章在 `vectors.db` 中的分块（vectors / bm25_index / doc_stats）。

        分块 ID 由内容哈希决定，事实改写后旧分块的 ID 往往已经变化，只靠
        `INSERT OR REPLACE` 会留下"上一版事实"的孤儿行，污染检索结果。
        所以事实改写前必须先撤回该章全部分块。

        只删读模型：不碰正文与 commit。库/表不存在时视为 0 行删除。
        """
        try:
            chapter = int(chapter or 0)
        except (TypeError, ValueError):
            chapter = 0
        if chapter <= 0:
            return {"applied": False, "writer": "vector", "reason": "invalid_chapter", "retracted": 0}

        from .config import DataModulesConfig

        config = DataModulesConfig.from_project_root(self.project_root)
        db_path = Path(config.vector_db)
        if not db_path.is_file():
            return {"applied": False, "writer": "vector", "reason": "no_vectors_db", "retracted": 0}

        removed = 0
        conn = sqlite3.connect(str(db_path), timeout=SQLITE_BUSY_TIMEOUT_SECONDS)
        try:
            cursor = conn.cursor()
            try:
                chunk_ids = [
                    str(row[0])
                    for row in cursor.execute(
                        "SELECT chunk_id FROM vectors WHERE chapter = ?", (chapter,)
                    ).fetchall()
                ]
            except sqlite3.OperationalError:
                return {"applied": False, "writer": "vector", "reason": "no_vectors_table", "retracted": 0}
            for start in range(0, len(chunk_ids), _DELETE_BATCH):
                batch = chunk_ids[start:start + _DELETE_BATCH]
                marks = ",".join("?" * len(batch))
                for table in ("bm25_index", "doc_stats"):
                    try:
                        cursor.execute(f"DELETE FROM {table} WHERE chunk_id IN ({marks})", batch)
                    except sqlite3.OperationalError:
                        continue
            cursor.execute("DELETE FROM vectors WHERE chapter = ?", (chapter,))
            removed = int(cursor.rowcount or 0)
            conn.commit()
        finally:
            conn.close()
        return {
            "applied": removed > 0,
            "writer": "vector",
            "chapter": chapter,
            "retracted": removed,
        }

    def apply(self, commit_payload: dict) -> dict:
        if commit_payload["meta"]["status"] != "accepted":
            return {"applied": False, "writer": "vector", "reason": "commit_rejected"}

        from .config import DataModulesConfig

        config = DataModulesConfig.from_project_root(self.project_root)
        if not getattr(config, "vector_projection_enabled", True):
            # 作者显式关掉向量投影：判为 skipped，不阻塞 postcommit 闸门。
            return {"applied": False, "writer": "vector", "reason": "vector_projection_disabled"}

        chunks = self._collect_chunks(commit_payload)
        if not chunks:
            return {"applied": False, "writer": "vector", "reason": "no_chunks"}

        gap = ""
        config_gap = getattr(config, "embedding_config_gap", None)
        if callable(config_gap):
            gap = str(config_gap() or "")
        if gap:
            # 没配凭证就注定拿不到向量：直接降级，不必白跑一轮网络请求。
            logger.info("vector_projection_degraded: %s", gap)
            return {
                "applied": False,
                "writer": "vector",
                "reason": "embedding_unavailable",
                "detail": gap,
            }

        try:
            stored = self._store_chunks(chunks)
            if stored <= 0:
                diagnosis = self._last_embedding_diagnosis
                if diagnosis:
                    # 凭证无效 / 端点不可达 → 降级为 skipped，而不是让整章投影失败卡死。
                    logger.warning("vector_projection_degraded: %s", diagnosis)
                    return {
                        "applied": False,
                        "writer": "vector",
                        "stored": stored,
                        "reason": "embedding_unavailable",
                        "detail": diagnosis,
                    }
                return {
                    "applied": False,
                    "writer": "vector",
                    "stored": stored,
                    "reason": "error:store_failed",
                }
            return {"applied": True, "writer": "vector", "stored": stored}
        except Exception as exc:
            logger.warning("vector_projection_failed: %s", exc)
            return {"applied": False, "writer": "vector", "reason": f"error:{exc}"}

    def _collect_chunks(self, commit_payload: dict) -> List[Dict[str, Any]]:
        chunks: List[Dict[str, Any]] = []
        chapter = int(commit_payload.get("meta", {}).get("chapter") or 0)

        chunk_counts: Dict[str, int] = {}

        summary_text = extraction_text(commit_payload, "summary_text")
        summary_chunk_id = f"ch{chapter:04d}_summary" if chapter > 0 else ""
        if chapter > 0 and summary_text:
            chunks.append({
                "chunk_id": summary_chunk_id,
                "chapter": chapter,
                "scene_index": 0,
                "content": summary_text,
                "chunk_type": "summary",
                "parent_chunk_id": None,
                "source_file": f"commit:chapter_{chapter:03d}",
            })

        for event in extraction_list(commit_payload, "accepted_events"):
            if not isinstance(event, dict):
                continue
            text = self._event_to_text(event)
            if text:
                evt_chapter = int(event.get("chapter") or chapter)
                event_key = event.get("event_id") or f"{event.get('event_type')}:{event.get('subject')}:{text}"
                chunk_id = self._unique_chunk_id(chunk_counts, "event", evt_chapter, event_key)
                chunks.append({
                    "chunk_id": chunk_id,
                    "chapter": evt_chapter,
                    "scene_index": 0,
                    "content": text,
                    "chunk_type": "event",
                    "parent_chunk_id": f"ch{evt_chapter:04d}_summary",
                    "source_file": f"commit:chapter_{evt_chapter:03d}",
                })

        for delta in extraction_list(commit_payload, "entity_deltas"):
            if not isinstance(delta, dict):
                continue
            text = self._delta_to_text(delta)
            if text:
                d_chapter = int(delta.get("chapter") or chapter)
                delta_key = delta.get("delta_id") or delta.get("entity_id") or text
                chunk_id = self._unique_chunk_id(chunk_counts, "entity_delta", d_chapter, delta_key)
                chunks.append({
                    "chunk_id": chunk_id,
                    "chapter": d_chapter,
                    "scene_index": 0,
                    "content": text,
                    "chunk_type": "entity_delta",
                    "parent_chunk_id": f"ch{d_chapter:04d}_summary",
                    "source_file": f"commit:chapter_{d_chapter:03d}",
                })

        for idx, scene in enumerate(extraction_list(commit_payload, "scenes"), start=1):
            if not isinstance(scene, dict):
                continue
            scene_index = resolve_scene_index(scene, idx)
            text = str(scene.get("summary") or scene.get("content") or "").strip()
            location = str(scene.get("location") or "").strip()
            if location and text:
                text = f"{location}：{text}"
            if not text:
                continue
            chunk_id = self._chunk_id("scene", chapter, scene_index)
            chunks.append({
                "chunk_id": chunk_id,
                "chapter": chapter,
                "scene_index": scene_index,
                "content": text,
                "chunk_type": "scene",
                "parent_chunk_id": summary_chunk_id or None,
                "source_file": f"commit:chapter_{chapter:03d}",
            })

        return chunks

    def _unique_chunk_id(
        self,
        counts: Dict[str, int],
        kind: str,
        chapter: int,
        key: Any,
    ) -> str:
        base_id = self._chunk_id(kind, chapter, key)
        occurrence = counts.get(base_id, 0) + 1
        counts[base_id] = occurrence
        return base_id if occurrence == 1 else f"{base_id}_{occurrence}"

    def _chunk_id(self, kind: str, chapter: int, key: Any) -> str:
        raw = f"{kind}:{chapter}:{key}"
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
        return f"ch{chapter:04d}_{kind}_{digest}"

    def _event_to_text(self, event: dict) -> str:
        chapter = int(event.get("chapter") or 0)
        subject = str(event.get("subject") or "").strip()
        event_type = str(event.get("event_type") or "").strip()
        payload = event.get("payload") or {}

        if event_type == "power_breakthrough":
            new_val = str(
                payload.get("new")
                or payload.get("to")
                or payload.get("new_value")
                or payload.get("new_state")
                or ""
            ).strip()
            return f"第{chapter}章：{subject}突破至{new_val}" if new_val else ""
        elif event_type == "character_state_changed":
            field = str(
                payload.get("field") or payload.get("field_path") or ""
            ).strip()
            new_val = str(
                payload.get("new")
                or payload.get("to")
                or payload.get("new_value")
                or payload.get("new_state")
                or ""
            ).strip()
            description = str(payload.get("description") or "").strip()
            if field and new_val:
                return f"第{chapter}章：{subject}的{field}变为{new_val}"
            if new_val:
                return f"第{chapter}章：{subject}变化为{new_val}"
            if description:
                return f"第{chapter}章：{subject}：{description}"
            return ""
        elif event_type == "relationship_changed":
            to_entity = str(payload.get("to_entity") or payload.get("to") or "").strip()
            rel_type = str(payload.get("relationship_type") or payload.get("type") or "").strip()
            return f"第{chapter}章：{subject}与{to_entity}关系变为{rel_type}" if to_entity else ""
        elif event_type in ("world_rule_revealed", "world_rule_broken"):
            desc = str(
                payload.get("description")
                or payload.get("rule")
                or payload.get("rule_content")
                or ""
            ).strip()
            action = "揭示" if "revealed" in event_type else "打破"
            return f"第{chapter}章：{action}世界规则——{desc}" if desc else ""
        elif event_type == "open_loop_created":
            description = str(
                payload.get("description")
                or payload.get("unanswered_question")
                or payload.get("content")
                or ""
            ).strip()
            return f"第{chapter}章：{subject}埋下悬念——{description}" if description else ""
        elif event_type == "artifact_obtained":
            name = str(payload.get("name") or subject or "").strip()
            owner = str(payload.get("owner") or payload.get("holder") or "").strip()
            return f"第{chapter}章：{owner}获得{name}" if owner else f"第{chapter}章：获得{name}"
        return ""

    def _delta_to_text(self, delta: dict) -> str:
        chapter = int(delta.get("chapter") or 0)
        from_e = str(delta.get("from_entity") or "").strip()
        to_e = str(delta.get("to_entity") or "").strip()
        rel = str(delta.get("relationship_type") or "").strip()

        if from_e and to_e and rel:
            return f"第{chapter}章：{from_e}与{to_e}关系变为{rel}"

        entity_id = str(delta.get("entity_id") or "").strip()
        canonical = str(delta.get("canonical_name") or entity_id).strip()
        if entity_id:
            return f"第{chapter}章：实体变更——{canonical}"
        return ""

    def _run_store_coro(self, coro: Coroutine[Any, Any, int]) -> int:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return int(asyncio.run(coro) or 0)

        result: Dict[str, Any] = {}

        def runner() -> None:
            try:
                result["value"] = asyncio.run(coro)
            except Exception as exc:
                result["error"] = exc

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        thread.join()
        if "error" in result:
            raise result["error"]
        return int(result.get("value") or 0)

    def _store_chunks(self, chunks: List[Dict[str, Any]]) -> int:
        from .config import DataModulesConfig
        from .rag_adapter import RAGAdapter

        config = DataModulesConfig.from_project_root(self.project_root)
        adapter = RAGAdapter(config)
        self._last_embedding_diagnosis = ""
        try:
            stored = self._run_store_coro(adapter.store_chunks(chunks))
        except Exception as exc:
            logger.warning("vector_store_failed: %s", exc)
            return 0
        # adapter 只在"整体不可用"（未配置凭证 / 认证失败）时给出原因；
        # 其余存储失败留空，交由上层按真实错误报 failed。
        self._last_embedding_diagnosis = str(adapter.degraded_mode_reason or "")
        return stored
