#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import filelock

from security_utils import atomic_write_json
from .chapter_reloading import (
    ARTIFACT_FILES, assert_artifact_freshness, body_evidence, chapter_body_revision,
    chapter_candidates, chapter_revision_evidence, commit_identity, commit_path, locked_state, read_object, revision_entry,
    upstream_body_blockers, approved_reconciliation,
)

from chapter_outline_loader import volume_num_for_chapter_from_state

from .artifact_validator import SKIPPABLE_PROJECTION_REASONS
from .chapter_commit_schema import (
    DisambiguationResult,
    ExtractionResult,
    FulfillmentResult,
    ReviewResult,
    fulfillment_deviation_items,
)
from .commit_artifacts import (
    canonical_fact_snapshot,
    extraction_list,
    fact_snapshot_diff,
)
from .config import DataModulesConfig
from .event_log_store import EventLogStore
from .event_projection_router import EventProjectionRouter
from .story_contracts import write_json
from .index_manager import IndexManager
from .override_ledger_service import (
    AmendProposalTrigger,
    ensure_override_ledger_columns,
    persist_amend_proposals,
)


def _projection_input(payload: dict[str, Any]) -> dict[str, Any]:
    extraction = dict(payload.get("extraction_result") or {})
    extraction.pop("source", None)
    events = []
    for event in extraction.get("accepted_events") or []:
        if isinstance(event, dict):
            event = dict(event)
            event.pop("event_id", None)
            events.append(event)
    extraction["accepted_events"] = sorted(
        events,
        key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )
    for field in ("state_deltas", "entity_deltas", "entities_appeared"):
        rows = []
        for row in extraction.get(field) or []:
            if isinstance(row, dict):
                row = dict(row)
                row.pop("source", None)
                rows.append(row)
        extraction[field] = sorted(
            rows,
            key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )
    return extraction


class ChapterCommitService:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)

    def build_commit(
        self,
        chapter: int,
        review_result: Dict[str, Any],
        fulfillment_result: Dict[str, Any],
        disambiguation_result: Dict[str, Any],
        extraction_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        review = ReviewResult.model_validate(review_result)
        fulfillment = FulfillmentResult.model_validate(fulfillment_result)
        disambiguation = DisambiguationResult.model_validate(disambiguation_result)
        extraction = ExtractionResult.model_validate(extraction_result)
        volume = volume_num_for_chapter_from_state(self.project_root, chapter) or 1
        revision_evidence = chapter_revision_evidence(self.project_root, chapter)
        accepted_events = EventLogStore(self.project_root).normalize_events(
            chapter, extraction.accepted_events
        )
        extraction_payload = extraction.model_dump()
        extraction_payload["accepted_events"] = accepted_events
        authorized_reconciliation = approved_reconciliation(
            self.project_root,
            chapter,
            {
                "review_result": review.model_dump(),
                "fulfillment_result": fulfillment.model_dump(),
                "disambiguation_result": disambiguation.model_dump(),
                "extraction_result": extraction_payload,
            },
        )
        rejected = bool(review.blocking_count) or bool(
            fulfillment_deviation_items(fulfillment.model_dump())
            and authorized_reconciliation is None
        ) or bool(disambiguation.pending)
        status = "rejected" if rejected else "accepted"
        return {
            "meta": {
                "schema_version": "story-system/v1",
                "chapter": chapter,
                "status": status,
                "revision_evidence": revision_evidence,
            },
            "contract_refs": {
                "master": "MASTER_SETTING.json",
                "volume": f"volume_{volume:03d}.json",
                "chapter": f"chapter_{chapter:03d}.json",
                "review": f"chapter_{chapter:03d}.review.json",
            },
            "provenance": {
                "write_fact_role": "chapter_commit",
                "projection_role": "derived_read_models",
                "legacy_state_role": "projection_only",
            },
            "outline_snapshot": {
                "planned_nodes": fulfillment.planned_nodes,
                "covered_nodes": fulfillment.covered_nodes,
                "missed_nodes": fulfillment.missed_nodes,
                "extra_nodes": fulfillment.extra_nodes,
                "node_statuses": fulfillment.node_statuses,
                "reconciliation": authorized_reconciliation or {},
                "reported_reconciliation": fulfillment.reconciliation,
            },
            "review_result": review.model_dump(),
            "fulfillment_result": fulfillment.model_dump(),
            "disambiguation_result": disambiguation.model_dump(),
            "extraction_result": extraction_payload,
            "projection_status": {
                "state": "pending",
                "index": "pending",
                "summary": "pending",
                "memory": "pending",
                "vector": "pending",
            },
        }

    def persist_commit(
        self,
        payload: Dict[str, Any],
        *,
        expected_previous: str = "",
        allow_fact_revision: bool = False,
        revision_reason: str = "",
    ) -> Path:
        chapter = int(payload["meta"]["chapter"])
        path = commit_path(self.project_root, chapter)
        path.parent.mkdir(parents=True, exist_ok=True)
        with filelock.FileLock(str(path) + ".lock", timeout=10):
            retract_required = False
            old = read_object(path, optional=True)
            if old and commit_identity(old) == commit_identity(payload):
                self._assert_projection_current(payload)
                atomic_write_json(path, payload, use_lock=False)
                return path
            artifacts = {key: payload.get(key, {}) for key in ARTIFACT_FILES}
            payload.setdefault("provenance", {})
            state = read_object(self.project_root / ".webnovel/state.json", optional=True)
            entry = revision_entry(state, chapter)
            has_reload_validation = bool(entry.get("validation_input"))
            if old.get("meta", {}).get("status") == "accepted" and not has_reload_validation:
                raise ValueError("chapter_artifacts_stale: reload and revalidate before revising an accepted commit")
            if has_reload_validation:
                source = assert_artifact_freshness(self.project_root, chapter, artifacts, require_validated=True)
            else:
                try:
                    source = {"content_revision": chapter_body_revision(self.project_root, chapter)}
                except (OSError, ValueError):
                    source = {"content_revision": ""}
            if payload["meta"]["status"] == "accepted":
                fulfillment = FulfillmentResult.model_validate(artifacts["fulfillment_result"])
                if ReviewResult.model_validate(artifacts["review_result"]).blocking_count or DisambiguationResult.model_validate(artifacts["disambiguation_result"]).pending:
                    raise ValueError("accepted commit has unresolved review/disambiguation blockers")
                if fulfillment_deviation_items(fulfillment.model_dump()):
                    record = approved_reconciliation(self.project_root, chapter, artifacts)
                    if not record or payload.get("outline_snapshot", {}).get("reconciliation") != record:
                        raise ValueError("reconciliation_confirmation_required: author decision is stale or missing")
            if payload["meta"]["status"] == "rejected" and old.get("meta", {}).get("status") == "accepted":
                raise ValueError("rejected artifacts cannot replace accepted chapter facts")
            if old.get("meta", {}).get("content_revision") == source["content_revision"] and all(old.get(key) == payload.get(key) for key in ARTIFACT_FILES):
                if old.get("projection_status") == payload.get("projection_status"):
                    payload.clear()
                    payload.update(old)
                else:
                    payload["meta"].update(old.get("meta", {}))
                    payload["provenance"].update(old.get("provenance", {}))
                    atomic_write_json(path, payload, use_lock=False)
                self._record_committed_revision(payload)
                return path
            if old and expected_previous != commit_identity(old):
                raise ValueError("revision_confirmation_required: pass --expected-previous from reload preview")
            if old.get("meta", {}).get("status") == "accepted":
                previous_snapshot = canonical_fact_snapshot(old)
                current_snapshot = canonical_fact_snapshot(payload)
                fact_diff = fact_snapshot_diff(previous_snapshot, current_snapshot)
                payload["provenance"]["fact_diff"] = fact_diff
                facts_changed = any(fact_diff.values())
                statuses = old.get("projection_status", {})
                projections_incomplete = not statuses or any(
                    value not in {"done", "skipped"} for value in statuses.values()
                )
                if facts_changed or projections_incomplete:
                    if not allow_fact_revision:
                        current_identity = commit_identity(old)
                        raise ValueError(
                            "revision_projection_unsafe: "
                            + (
                                "facts changed; incremental projections cannot retract old facts"
                                if facts_changed
                                else "previous projections are incomplete"
                            )
                            + f" (current commit identity is {current_identity}; rerun with"
                            " --expected-previous <identity> --allow-fact-revision to retract this"
                            " chapter's read models and rebuild them from scratch)"
                        )
                    # 作者显式授权改写已 accepted 的事实：标记整章重建，投影阶段先撤回
                    # 旧的派生行（index/vector/story_events 镜像）再全量重写。
                    retract_required = True
                    payload["provenance"]["retract_required"] = True
                    payload["provenance"]["retract_previous_identity"] = commit_identity(old)
                    payload["provenance"]["retract_reason"] = (
                        str(revision_reason or "").strip()
                        or ("facts changed" if facts_changed else "previous projections incomplete")
                    )
                    payload["provenance"]["retract_previous_projection_status"] = (
                        dict(statuses) if isinstance(statuses, dict) else {}
                    )
                    payload["provenance"].pop("projection_reuse", None)
                    payload["provenance"].pop("projection_refresh", None)
                else:
                    old_extraction = _projection_input(old)
                    new_extraction = _projection_input(payload)
                    if old_extraction == new_extraction:
                        payload["provenance"]["projection_reuse"] = True
                        payload["projection_status"] = dict(old["projection_status"])
                    else:
                        payload["provenance"]["projection_refresh"] = True
            history_ref = ""
            if old:
                history = path.parent / "history" / f"chapter_{chapter:03d}" / f"revision_{commit_identity(old)}.commit.json"
                history.parent.mkdir(parents=True, exist_ok=True)
                if history.exists():
                    if read_object(history) != old:
                        raise ValueError("immutable commit history conflict")
                else:
                    history.parent.mkdir(parents=True, exist_ok=True)
                    with history.open("x", encoding="utf-8") as handle:
                        json.dump(old, handle, ensure_ascii=False, indent=2)
                history_ref = str(history.relative_to(self.project_root))
            payload["meta"]["content_revision"] = source.get("content_revision", "")
            payload["meta"]["revision_number"] = int(old.get("meta", {}).get("revision_number", 0)) + 1
            payload["meta"]["committed_at"] = datetime.now(timezone.utc).isoformat()
            payload["provenance"].update(
                previous_commit=history_ref,
                previous_identity=commit_identity(old),
            )
            if (
                old.get("meta", {}).get("status") == "accepted"
                and not retract_required
                and not payload["provenance"].get("projection_refresh")
            ):
                payload["provenance"]["projection_reuse"] = True
                payload["projection_status"] = dict(old["projection_status"])
            if source.get("content_revision") and chapter_body_revision(self.project_root, chapter) != source["content_revision"]:
                raise ValueError("body changed before persist")
            evidence = payload["meta"].get("revision_evidence")
            if isinstance(evidence, dict) and payload["meta"].get("status") == "accepted":
                evidence["accepted_commit_identity"] = commit_identity(payload)
            atomic_write_json(path, payload, use_lock=False)
            self._record_committed_revision(payload)
        return path

    def _record_committed_revision(self, payload: dict) -> None:
        state_path = self.project_root / ".webnovel" / "state.json"
        if not state_path.is_file():
            return
        chapter = int(payload["meta"]["chapter"])
        with locked_state(self.project_root) as state:
            entries = state.setdefault("progress", {}).setdefault("chapter_revisions", {})
            entry = entries.setdefault(str(chapter), {})
            evidence = payload.get("meta", {}).get("revision_evidence") or {}
            entry.update(
                content_status="committed" if payload["meta"].get("status") == "accepted" else "rejected",
                revision_evidence=evidence,
            )
            if payload["meta"].get("status") == "accepted":
                entry.update(
                    committed_revision=payload["meta"]["content_revision"],
                    stale_dependencies={},
                )
                entry.pop("stale_reason", None)
            for key in ("contract_revision", "chapter_outline_revision"):
                if evidence.get(key):
                    entry[key] = evidence[key]

    def _assert_projection_current(self, payload: dict) -> None:
        chapter = int(payload["meta"]["chapter"])
        current = read_object(commit_path(self.project_root, chapter), optional=True)
        if not current or commit_identity(current) != commit_identity(payload):
            raise ValueError("projection requires the current persisted commit")
        revision = payload["meta"].get("content_revision")
        if not revision:
            if chapter_candidates(self.project_root, chapter):
                raise ValueError(
                    "chapter_body_revision_missing: reload before replaying legacy commit"
                )
            return
        if chapter_body_revision(self.project_root, chapter) != revision:
            raise ValueError("chapter_body_stale: reload instead of replaying stale facts")
        if upstream_body_blockers(self.project_root, chapter):
            raise ValueError("previous_chapter_revision_changed: projection replay blocked")

    def retract_chapter_read_models(self, payload: Dict[str, Any], *, force: bool = False) -> dict[str, Any]:
        """撤回某一章的派生读模型：index 行、向量分块、story_events 镜像。

        为什么必须有这一步：投影写入器只做 upsert，撤不掉"上一版事实"留下的行
        ——`scenes` 有 `UNIQUE(chapter, scene_index)`、向量分块 ID 由内容哈希决定，
        所以改写事实后旧行会挡住重建（撞键）或污染检索。旧实现直接在
        `persist_commit` 里拒绝改写事实（`revision_projection_unsafe`），作者于是
        没有任何正当途径修正已提交的数据；现在改为"显式授权 + 整章重建"。

        触发条件（二者任一）：
        - commit 的 `provenance.retract_required`（`--allow-fact-revision` 写下）
        - `force=True`（`projections retry --retract`，用于修复半途写坏的投影）

        只在读模型层删除；不碰正文、commit、事件 JSON，因此可以安全重跑。
        返回 `{writer: 结果}` 供报告与 projection_log 使用，**不写进 payload**：
        `commit_identity` 含 provenance，投影阶段改动它会让重写校验失败。
        """
        provenance = payload.get("provenance") or {}
        if not force and not provenance.get("retract_required"):
            return {}
        chapter = int((payload.get("meta") or {}).get("chapter") or 0)
        if chapter <= 0:
            return {}

        results: dict[str, Any] = {}
        writers = self._projection_writers()
        for name in ("index", "vector"):
            writer = writers.get(name)
            retract = getattr(writer, "retract", None)
            if retract is None:
                continue
            try:
                results[name] = {"status": "retracted", "chapter": chapter, "result": retract(chapter)}
            except Exception as exc:  # 撤回失败必须让投影显式失败，不能留下半旧的读模型
                results[name] = {"status": "failed", "chapter": chapter, "error": str(exc)}
        try:
            deleted = EventLogStore(self.project_root).retract_chapter(chapter)
            results["event_mirror"] = {"status": "retracted", "chapter": chapter, "deleted": deleted}
        except Exception as exc:
            results["event_mirror"] = {"status": "failed", "chapter": chapter, "error": str(exc)}
        return results

    def _projection_writers(self) -> dict[str, Any]:
        from .index_projection_writer import IndexProjectionWriter
        from .memory_projection_writer import MemoryProjectionWriter
        from .state_projection_writer import StateProjectionWriter
        from .summary_projection_writer import SummaryProjectionWriter
        from .vector_projection_writer import VectorProjectionWriter

        return {
            "state": StateProjectionWriter(self.project_root),
            "index": IndexProjectionWriter(self.project_root),
            "summary": SummaryProjectionWriter(self.project_root),
            "memory": MemoryProjectionWriter(self.project_root),
            "vector": VectorProjectionWriter(self.project_root),
        }

    def _writer_status(self, result: dict[str, Any]) -> str:
        if result.get("applied"):
            return "done"
        reason = str(result.get("reason") or "").strip()
        if reason in SKIPPABLE_PROJECTION_REASONS:
            return "skipped"
        if reason.startswith("error:"):
            return f"failed:{reason[6:] or 'writer_error'}"
        return "skipped"

    def _sync_event_mirror(self, payload: Dict[str, Any], *, write_event_file: bool) -> Dict[str, Any]:
        """把 accepted commit 的事件写进 index.db 的 story_events 镜像（幂等）。

        - `write_event_file=True`：写章主链用，事件 JSON 文件 + sqlite 镜像一起写。
        - `write_event_file=False`：`projections retry/replay` 用，**只重建 sqlite 镜像**，
          不产生任何 commit 侧副作用（事件 JSON 文件保持原样）。
          旧实现只在 `apply_projections` 里写镜像，所以镜像一旦失败就再也补不回来。

        返回可直接并入 projection run 的 writer 结果；失败不抛异常：
        记入 `provenance.event_mirror_error`（随 commit 落盘）并作为 `event_mirror`
        条目暴露给 projection_log，供 postcommit gate 与 doctor 发现。
        """
        meta = payload.get("meta") or {}
        if str(meta.get("status") or "") != "accepted":
            return {}
        chapter = int(meta.get("chapter") or 0)
        if chapter <= 0:
            return {}

        store = EventLogStore(self.project_root)
        events = extraction_list(payload, "accepted_events")
        if write_event_file:
            if not events and not store.paths.event_json(chapter).is_file():
                # 无事件且从未落盘过事件文件：不为了"空镜像"凭空造文件。
                return {}
            store.write_events(chapter, events)
        else:
            if not events:
                # retry 不造文件，也没有事件可补镜像。
                return {}
            store.mirror_events_only(chapter, events)

        error = store.last_mirror_error
        provenance = payload.setdefault("provenance", {})
        if not error:
            provenance.pop("event_mirror_error", None)
            return {}
        provenance["event_mirror_error"] = error
        return {"event_mirror": {"status": "failed", "error": error}}

    def rebuild_event_mirror(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """`projections retry/replay` 的镜像重建入口（不写事件 JSON 文件）。"""
        return self._sync_event_mirror(payload, write_event_file=False)

    def apply_projection_writers(
        self,
        payload: Dict[str, Any],
        *,
        mirror_results: dict[str, Any] | None = None,
        retract_results: dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        status = str((payload.get("meta") or {}).get("status") or "")
        if status not in {"accepted", "rejected"}:
            return payload

        from .projection_log import append_projection_run, read_projection_runs

        read_projection_runs(self.project_root)
        self._assert_projection_current(payload)
        extra_writers: dict[str, Any] = dict(mirror_results or {})
        if payload.get("provenance", {}).get("projection_reuse"):
            self._record_committed_revision(payload)
            append_projection_run(
                self.project_root,
                payload,
                {
                    name: {"status": status, "reason": "unchanged_fact_projection"}
                    for name, status in payload["projection_status"].items()
                }
                | extra_writers,
                retract_results=retract_results,
            )
            return payload

        payload.setdefault("projection_status", {})
        if not isinstance(payload["projection_status"], dict):
            payload["projection_status"] = {}

        writers = self._projection_writers()
        required_writers = set(EventProjectionRouter().required_writers(payload))
        writer_results: dict[str, dict[str, Any]] = dict(extra_writers)
        for name, writer in writers.items():
            if name not in required_writers:
                payload["projection_status"][name] = "skipped"
                writer_results[name] = {"status": "skipped", "reason": "not_required"}
                continue
            try:
                result = writer.apply(payload)
                payload["projection_status"][name] = self._writer_status(result)
                writer_results[name] = {
                    "status": payload["projection_status"][name],
                    "result": result,
                }
            except Exception as exc:
                payload["projection_status"][name] = f"failed:{exc}"
                writer_results[name] = {"status": "failed", "error": str(exc)}
        commit_path = self.persist_commit(payload)
        append_projection_run(
            self.project_root,
            payload,
            writer_results,
            commit_path=commit_path,
            retract_results=retract_results,
        )
        return payload

    def apply_projections(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        status = str((payload.get("meta") or {}).get("status") or "")
        if status not in {"accepted", "rejected"}:
            return payload

        current = read_object(commit_path(self.project_root, int(payload["meta"]["chapter"])), optional=True)
        if not current:
            if status == "accepted":
                chapter = int((payload.get("meta") or {}).get("chapter") or 0)
                extraction = payload.setdefault("extraction_result", {})
                if isinstance(extraction, dict):
                    extraction["accepted_events"] = EventLogStore(self.project_root).normalize_events(
                        chapter, extraction_list(payload, "accepted_events")
                    )
            self.persist_commit(payload)
        self._assert_projection_current(payload)
        # 事实改写/半途失败后的整章重建：先撤回旧的派生行，且必须在事件镜像重建之前
        # （镜像也是被撤回的对象之一）。
        retract_results = self.retract_chapter_read_models(payload)
        if payload.get("provenance", {}).get("projection_reuse"):
            return self.apply_projection_writers(payload, retract_results=retract_results)

        mirror_results: dict[str, Any] = {}
        if status == "accepted":
            chapter = int((payload.get("meta") or {}).get("chapter") or 0)
            event_store = EventLogStore(self.project_root)
            accepted_events = extraction_list(payload, "accepted_events")
            extraction = payload.setdefault("extraction_result", {})
            if not isinstance(extraction, dict):
                extraction = {}
                payload["extraction_result"] = extraction
            extraction["accepted_events"] = event_store.normalize_events(
                chapter, accepted_events
            )
            mirror_results = self._sync_event_mirror(payload, write_event_file=True)

            proposals = AmendProposalTrigger().check(chapter, extraction["accepted_events"])
            if proposals:
                manager = IndexManager(DataModulesConfig.from_project_root(self.project_root))
                with manager._get_conn() as conn:
                    ensure_override_ledger_columns(conn)
                    persist_amend_proposals(conn, chapter, proposals)
                    conn.commit()

        return self.apply_projection_writers(
            payload,
            mirror_results=mirror_results,
            retract_results=retract_results,
        )
