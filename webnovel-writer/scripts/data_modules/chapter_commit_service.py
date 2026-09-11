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
    chapter_candidates, commit_identity, commit_path, locked_state, read_object, revision_entry,
    upstream_body_blockers,
)

from chapter_outline_loader import volume_num_for_chapter_from_state

from .chapter_commit_schema import (
    DisambiguationResult,
    ExtractionResult,
    FulfillmentResult,
    ReviewResult,
)
from .commit_artifacts import extraction_list
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
        rejected = bool(review.blocking_count) or bool(
            fulfillment.missed_nodes
        ) or bool(disambiguation.pending)
        status = "rejected" if rejected else "accepted"
        volume = volume_num_for_chapter_from_state(self.project_root, chapter) or 1
        accepted_events = EventLogStore(self.project_root).normalize_events(
            chapter, extraction.accepted_events
        )
        extraction_payload = extraction.model_dump()
        extraction_payload["accepted_events"] = accepted_events
        return {
            "meta": {
                "schema_version": "story-system/v1",
                "chapter": chapter,
                "status": status,
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

    def persist_commit(self, payload: Dict[str, Any], *, expected_previous: str = "") -> Path:
        chapter = int(payload["meta"]["chapter"])
        path = commit_path(self.project_root, chapter)
        path.parent.mkdir(parents=True, exist_ok=True)
        with filelock.FileLock(str(path) + ".lock", timeout=10):
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
                previous_extraction = dict(old.get("extraction_result") or {})
                new_extraction = dict(payload["extraction_result"])
                previous_extraction.pop("source", None)
                new_extraction.pop("source", None)
                if previous_extraction != new_extraction:
                    raise ValueError("revision_projection_unsafe: facts changed; incremental projections cannot retract old facts")
                statuses = old.get("projection_status", {})
                if not statuses or any(value not in {"done", "skipped"} for value in statuses.values()):
                    raise ValueError("revision_projection_unsafe: previous projections are incomplete")
            history_ref = ""
            if old:
                history = path.parent / "history" / f"chapter_{chapter:03d}" / f"revision_{commit_identity(old)}.commit.json"
                history.parent.mkdir(parents=True, exist_ok=True)
                if history.exists():
                    if read_object(history) != old:
                        raise ValueError("immutable commit history conflict")
                else:
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
            if old.get("meta", {}).get("status") == "accepted":
                payload["provenance"]["projection_reuse"] = True
                payload["projection_status"] = dict(old["projection_status"])
            if source.get("content_revision") and chapter_body_revision(self.project_root, chapter) != source["content_revision"]:
                raise ValueError("body changed before persist")
            atomic_write_json(path, payload, use_lock=False)
            self._record_committed_revision(payload)
        return path

    def _record_committed_revision(self, payload: dict) -> None:
        state_path = self.project_root / ".webnovel" / "state.json"
        if not state_path.is_file():
            return
        chapter = int(payload["meta"]["chapter"])
        with locked_state(self.project_root) as state:
            entry = revision_entry(state, chapter)
            entry.update(committed_revision=payload["meta"]["content_revision"], content_status="committed", stale_dependencies={})
            entry.pop("stale_reason", None)

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
        if reason in {"not_required", "commit_rejected"}:
            return "skipped"
        if reason.startswith("error:"):
            return f"failed:{reason[6:] or 'writer_error'}"
        return "skipped"

    def apply_projection_writers(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        status = str((payload.get("meta") or {}).get("status") or "")
        if status not in {"accepted", "rejected"}:
            return payload

        self._assert_projection_current(payload)
        if payload.get("provenance", {}).get("projection_reuse"):
            self._record_committed_revision(payload)
            from .projection_log import append_projection_run
            append_projection_run(self.project_root, payload, {name: {"status": status, "reason": "unchanged_fact_projection"} for name, status in payload["projection_status"].items()})
            return payload

        payload.setdefault("projection_status", {})
        if not isinstance(payload["projection_status"], dict):
            payload["projection_status"] = {}

        writers = self._projection_writers()
        required_writers = set(EventProjectionRouter().required_writers(payload))
        writer_results: dict[str, dict[str, Any]] = {}
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
        try:
            from .projection_log import append_projection_run

            append_projection_run(
                self.project_root,
                payload,
                writer_results,
                commit_path=commit_path,
            )
        except Exception:
            pass
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
        if payload.get("provenance", {}).get("projection_reuse"):
            return self.apply_projection_writers(payload)

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
            event_store.write_events(chapter, extraction["accepted_events"])

            proposals = AmendProposalTrigger().check(chapter, extraction["accepted_events"])
            if proposals:
                manager = IndexManager(DataModulesConfig.from_project_root(self.project_root))
                with manager._get_conn() as conn:
                    ensure_override_ledger_columns(conn)
                    persist_amend_proposals(conn, chapter, proposals)
                    conn.commit()

        return self.apply_projection_writers(payload)
