#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys
from pathlib import Path

import pytest

from data_modules.chapter_commit_service import ChapterCommitService
from data_modules.config import DataModulesConfig
from data_modules.index_manager import IndexManager
from .test_chapter_reloading import install_projection_fixture, prepare_inputs, accepted_commit
from data_modules.chapter_reloading import validate_chapter_body, commit_identity, commit_path


def test_revision_reuses_projection_for_reordered_events(tmp_path, monkeypatch):
    payloads = {
        "review_result": {"blocking_count": 0},
        "fulfillment_result": {"planned_nodes": [], "covered_nodes": [], "missed_nodes": [], "extra_nodes": []},
        "disambiguation_result": {"pending": []},
        "extraction_result": {
            "accepted_events": [
                {"event_id": "one", "event_type": "artifact_obtained", "subject": "主角", "payload": {"artifact_id": "玄铁剑", "holder": "主角"}},
                {"event_id": "two", "event_type": "open_loop_created", "subject": "主角", "payload": {"loop_id": "石门", "question": "门后是什么"}},
            ],
            "state_deltas": [],
            "entity_deltas": [],
        },
    }
    old = accepted_commit(tmp_path, payloads=payloads)
    old["projection_status"] = {name: "done" for name in ("state", "index", "summary", "memory", "vector")}
    commit_path(tmp_path, 1).write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "正文" / "第0001章.md").write_text("只调整措辞。", encoding="utf-8")
    inputs = prepare_inputs(
        tmp_path,
        payloads={
            **payloads,
            "extraction_result": {
                **payloads["extraction_result"],
                "accepted_events": [
                    {"event_id": "changed-two", "event_type": "open_loop_created", "subject": "主角", "payload": {"loop_id": "石门", "question": "门后是什么"}},
                    {"event_id": "changed-one", "event_type": "artifact_obtained", "subject": "主角", "payload": {"artifact_id": "玄铁剑", "holder": "主角"}},
                ],
            },
        },
    )
    assert validate_chapter_body(tmp_path, 1)["ok"]
    service = ChapterCommitService(tmp_path)
    payload = service.build_commit(chapter=1, **inputs)
    service.persist_commit(payload, expected_previous=commit_identity(old))

    assert payload["provenance"]["projection_reuse"] is True
    assert not payload["provenance"].get("projection_refresh")


    service = ChapterCommitService(tmp_path)
    payload = service.build_commit(
        chapter=3,
        review_result={"blocking_count": 0},
        fulfillment_result={
            "planned_nodes": ["发现陷阱"],
            "covered_nodes": [],
            "missed_nodes": ["发现陷阱"],
            "extra_nodes": [],
        },
        disambiguation_result={"pending": []},
        extraction_result={"state_deltas": [], "entity_deltas": [], "accepted_events": []},
    )
    assert payload["meta"]["status"] == "rejected"


def test_commit_service_keeps_model_reported_reconciliation_blocked(tmp_path):
    service = ChapterCommitService(tmp_path)
    payload = service.build_commit(
        chapter=3,
        review_result={"blocking_count": 0},
        fulfillment_result={
            "planned_nodes": ["发现陷阱"],
            "covered_nodes": [],
            "missed_nodes": ["发现陷阱"],
            "extra_nodes": [],
            "node_statuses": [{"node_id": "cbn-1", "status": "missed"}],
            "reconciliation": {
                "decision": "accepted_deviation",
                "reason": "本章保留悬念，节点延后到下一章",
                "impact": [4],
            },
        },
        disambiguation_result={"pending": []},
        extraction_result={"state_deltas": [], "entity_deltas": [], "accepted_events": []},
    )
    assert payload["meta"]["status"] == "rejected"


def test_commit_service_accepts_author_reconciliation_bound_to_revision(tmp_path):
    from data_modules.chapter_reloading import reconcile_chapter_body
    from data_modules.write_gates import run_write_gate

    inputs = prepare_inputs(tmp_path)
    inputs["fulfillment_result"].update(
        planned_nodes=["发现陷阱"], covered_nodes=[], missed_nodes=["发现陷阱"],
        node_statuses=[{"node_id": "cbn-1", "status": "missed"}],
    )
    from .test_project_phase import _write_json
    from data_modules.chapter_reloading import artifact_paths
    _write_json(artifact_paths(tmp_path)["fulfillment_result"], inputs["fulfillment_result"])
    assert not validate_chapter_body(tmp_path, 1)["ok"]
    preview = reconcile_chapter_body(tmp_path, 1, dry_run=True)
    assert preview["ok"], preview
    decision = reconcile_chapter_body(
        tmp_path, 1, decision="accepted_deviation", reason="作者确认延后发现陷阱",
        impact=[2], expected_input=preview["input_token"],
    )
    assert decision["ok"], decision
    assert validate_chapter_body(tmp_path, 1)["ok"]
    assert run_write_gate(tmp_path, chapter=1, stage="precommit")["ok"]
    service = ChapterCommitService(tmp_path)
    payload = service.build_commit(chapter=1, **inputs)
    assert payload["meta"]["status"] == "accepted"
    assert payload["outline_snapshot"]["reconciliation"]["decision_id"] == decision["decision_id"]
    service.persist_commit(payload)
    service.apply_projections(payload)
    assert run_write_gate(tmp_path, chapter=1, stage="postcommit")["ok"]

def test_commit_service_accepts_when_all_checks_pass(tmp_path):
    service = ChapterCommitService(tmp_path)
    payload = service.build_commit(
        chapter=3,
        review_result={"blocking_count": 0},
        fulfillment_result={"planned_nodes": ["发现陷阱"], "covered_nodes": ["发现陷阱"], "missed_nodes": [], "extra_nodes": []},
        disambiguation_result={"pending": []},
        extraction_result={"state_deltas": [], "entity_deltas": [], "accepted_events": []},
    )
    assert payload["meta"]["status"] == "accepted"
    assert payload["contract_refs"]["master"] == "MASTER_SETTING.json"
    assert payload["contract_refs"]["volume"] == "volume_001.json"
    assert payload["contract_refs"]["chapter"] == "chapter_003.json"
    assert payload["outline_snapshot"]["covered_nodes"] == ["发现陷阱"]
    assert payload["extraction_result"]["accepted_events"] == []
    assert "accepted_events" not in payload
    assert "state_deltas" not in payload
    assert "entity_deltas" not in payload


def test_commit_service_includes_volume_ref_and_write_fact_provenance(tmp_path):
    service = ChapterCommitService(tmp_path)
    payload = service.build_commit(
        chapter=3,
        review_result={"blocking_count": 0},
        fulfillment_result={"planned_nodes": ["发现陷阱"], "covered_nodes": ["发现陷阱"], "missed_nodes": [], "extra_nodes": []},
        disambiguation_result={"pending": []},
        extraction_result={"state_deltas": [], "entity_deltas": [], "accepted_events": []},
    )

    assert payload["contract_refs"]["volume"] == "volume_001.json"
    assert payload["provenance"]["write_fact_role"] == "chapter_commit"
    assert payload["provenance"]["projection_role"] == "derived_read_models"


def test_commit_service_rejects_malformed_gate_artifacts(tmp_path):
    service = ChapterCommitService(tmp_path)
    valid_fulfillment = {
        "planned_nodes": [],
        "covered_nodes": [],
        "missed_nodes": [],
        "extra_nodes": [],
    }
    valid_disambiguation = {"pending": []}
    valid_extraction = {"state_deltas": [], "entity_deltas": [], "accepted_events": []}

    with pytest.raises(ValueError, match="blocking_count"):
        service.build_commit(
            chapter=3,
            review_result={},
            fulfillment_result=valid_fulfillment,
            disambiguation_result=valid_disambiguation,
            extraction_result=valid_extraction,
        )

    with pytest.raises(ValueError, match="fulfillment_result"):
        service.build_commit(
            chapter=3,
            review_result={"blocking_count": 0},
            fulfillment_result={"fulfillment": {"missed_nodes": ["遗漏节点"]}},
            disambiguation_result=valid_disambiguation,
            extraction_result=valid_extraction,
        )

    with pytest.raises(ValueError, match="disambiguation_result"):
        service.build_commit(
            chapter=3,
            review_result={"blocking_count": 0},
            fulfillment_result=valid_fulfillment,
            disambiguation_result={"disambiguation": {"pending": ["宗主"]}},
            extraction_result=valid_extraction,
        )


def test_commit_service_rejects_nested_extraction_result_shape(tmp_path):
    service = ChapterCommitService(tmp_path)

    with pytest.raises(ValueError, match="top-level"):
        service.build_commit(
            chapter=76,
            review_result={"blocking_count": 0},
            fulfillment_result={
                "planned_nodes": [],
                "covered_nodes": [],
                "missed_nodes": [],
                "extra_nodes": [],
            },
            disambiguation_result={"pending": []},
            extraction_result={
                "chapter": 76,
                "extraction": {
                    "scenes": [{"summary": "场景切分"}],
                    "unresolved_threads": ["未解线索"],
                },
            },
        )


def test_commit_service_rejects_extraction_wrapper_even_with_empty_core_fields(tmp_path):
    service = ChapterCommitService(tmp_path)

    with pytest.raises(ValueError, match="nested under extraction"):
        service.build_commit(
            chapter=76,
            review_result={"blocking_count": 0},
            fulfillment_result={
                "planned_nodes": [],
                "covered_nodes": [],
                "missed_nodes": [],
                "extra_nodes": [],
            },
            disambiguation_result={"pending": []},
            extraction_result={
                "accepted_events": [],
                "state_deltas": [],
                "entity_deltas": [],
                "extraction": {
                    "scenes": [{"summary": "真实场景却被包错层"}],
                    "summary_text": "真实摘要却被包错层",
                },
            },
        )


def test_commit_service_rejects_extraction_result_missing_core_fields(tmp_path):
    service = ChapterCommitService(tmp_path)

    with pytest.raises(ValueError, match="accepted_events"):
        service.build_commit(
            chapter=3,
            review_result={"blocking_count": 0},
            fulfillment_result={
                "planned_nodes": [],
                "covered_nodes": [],
                "missed_nodes": [],
                "extra_nodes": [],
            },
            disambiguation_result={"pending": []},
            extraction_result={"summary_text": "摘要"},
        )


def test_commit_service_rejects_non_object_extraction_items(tmp_path):
    service = ChapterCommitService(tmp_path)

    with pytest.raises(ValueError, match=r"state_deltas\[0\]"):
        service.build_commit(
            chapter=3,
            review_result={"blocking_count": 0},
            fulfillment_result={
                "planned_nodes": [],
                "covered_nodes": [],
                "missed_nodes": [],
                "extra_nodes": [],
            },
            disambiguation_result={"pending": []},
            extraction_result={
                "accepted_events": [],
                "state_deltas": ["realm changed"],
                "entity_deltas": [],
            },
        )


def test_commit_service_rejects_non_object_accepted_event_items(tmp_path):
    service = ChapterCommitService(tmp_path)

    with pytest.raises(ValueError, match=r"accepted_events\[0\]"):
        service.build_commit(
            chapter=3,
            review_result={"blocking_count": 0},
            fulfillment_result={
                "planned_nodes": [],
                "covered_nodes": [],
                "missed_nodes": [],
                "extra_nodes": [],
            },
            disambiguation_result={"pending": []},
            extraction_result={
                "accepted_events": ["not-a-json-object"],
                "state_deltas": [],
                "entity_deltas": [],
            },
        )


def test_commit_service_normalizes_accepted_events_before_projection(tmp_path):
    service = ChapterCommitService(tmp_path)

    payload = service.build_commit(
        chapter=76,
        review_result={"blocking_count": 0},
        fulfillment_result={
            "planned_nodes": [],
            "covered_nodes": [],
            "missed_nodes": [],
            "extra_nodes": [],
        },
        disambiguation_result={"pending": []},
        extraction_result={
            "state_deltas": [],
            "entity_deltas": [],
            "accepted_events": [
                {
                    "type": "mystery_introduction",
                    "characters": ["xiaoyan"],
                    "payload": {"content": "萧炎发现石门背后的新疑点"},
                }
            ],
        },
    )

    event = payload["extraction_result"]["accepted_events"][0]
    assert event["event_id"].startswith("evt-ch076-001-")
    assert event["chapter"] == 76
    assert event["event_type"] == "open_loop_created"
    assert event["subject"] == "xiaoyan"
    assert "accepted_events" not in payload


def test_apply_projections_normalizes_events_before_router_inspection(
    tmp_path, monkeypatch
):
    captured = {}

    class SpyRouter:
        def required_writers(self, payload):
            captured["events"] = list(payload.get("extraction_result", {}).get("accepted_events") or [])
            return []

    monkeypatch.setattr(
        "data_modules.chapter_commit_service.EventProjectionRouter",
        lambda: SpyRouter(),
    )

    service = ChapterCommitService(tmp_path)
    payload = {
        "meta": {"status": "accepted", "chapter": 76},
        "review_result": {"blocking_count": 0},
        "fulfillment_result": {"planned_nodes": [], "covered_nodes": [], "missed_nodes": [], "extra_nodes": []},
        "disambiguation_result": {"pending": []},
        "extraction_result": {
            "accepted_events": [
                {
                    "type": "scene_open",
                    "characters": ["xiaoyan"],
                    "payload": {"content": "萧炎推开石门，新的悬念出现"},
                }
            ],
            "state_deltas": [],
            "entity_deltas": [],
            "summary_text": "",
        },
        "projection_status": {
            "state": "pending",
            "index": "pending",
            "summary": "pending",
            "memory": "pending",
            "vector": "pending",
        },
    }

    service.apply_projections(payload)

    event = captured["events"][0]
    assert event["event_id"].startswith("evt-ch076-001-")
    assert event["chapter"] == 76
    assert event["event_type"] == "open_loop_created"
    assert event["subject"] == "xiaoyan"
    assert payload["extraction_result"]["accepted_events"] == captured["events"]


def test_apply_projections_updates_state_for_rejected_commit(tmp_path):
    import json

    (tmp_path / ".webnovel").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")

    service = ChapterCommitService(tmp_path)
    payload = service.build_commit(
        chapter=7,
        review_result={"blocking_count": 1},
        fulfillment_result={
            "planned_nodes": ["进入坊市"],
            "covered_nodes": ["进入坊市"],
            "missed_nodes": [],
            "extra_nodes": [],
        },
        disambiguation_result={"pending": []},
        extraction_result={"state_deltas": [], "entity_deltas": [], "accepted_events": []},
    )

    projected = service.apply_projections(payload)

    state = json.loads((tmp_path / ".webnovel" / "state.json").read_text(encoding="utf-8"))
    assert projected["projection_status"]["state"] == "done"
    assert state["progress"]["chapter_status"]["7"] == "chapter_rejected"


def test_chapter_commit_cli_builds_and_persists_commit(tmp_path, monkeypatch):
    review_path = tmp_path / "review.json"
    fulfillment_path = tmp_path / "fulfillment.json"
    disambiguation_path = tmp_path / "disambiguation.json"
    extraction_path = tmp_path / "extraction.json"
    review_path.write_text('{"blocking_count": 0}', encoding="utf-8")
    fulfillment_path.write_text(
        '{"planned_nodes": ["发现陷阱"], "covered_nodes": ["发现陷阱"], "missed_nodes": [], "extra_nodes": []}',
        encoding="utf-8",
    )
    disambiguation_path.write_text('{"pending": []}', encoding="utf-8")
    extraction_path.write_text('{"state_deltas": [], "entity_deltas": [], "accepted_events": []}', encoding="utf-8")

    scripts_dir = Path(__file__).resolve().parents[2]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    from chapter_commit import main

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "chapter_commit",
            "--project-root",
            str(tmp_path),
            "--chapter",
            "3",
            "--review-result",
            str(review_path),
            "--fulfillment-result",
            str(fulfillment_path),
            "--disambiguation-result",
            str(disambiguation_path),
            "--extraction-result",
            str(extraction_path),
        ],
    )
    main()

    assert (tmp_path / ".story-system" / "commits" / "chapter_003.commit.json").is_file()


def test_apply_projections_writes_events_and_amend_proposals(tmp_path):
    service = ChapterCommitService(tmp_path)
    payload = service.build_commit(
        chapter=3,
        review_result={"blocking_count": 0},
        fulfillment_result={
            "planned_nodes": ["发现陷阱"],
            "covered_nodes": ["发现陷阱"],
            "missed_nodes": [],
            "extra_nodes": [],
        },
        disambiguation_result={"pending": []},
        extraction_result={
            "state_deltas": [],
            "entity_deltas": [],
            "summary_text": "",
            "accepted_events": [
                {
                    "event_id": "evt-001",
                    "chapter": 3,
                    "event_type": "world_rule_broken",
                    "subject": "金手指",
                    "payload": {
                        "field": "world_rule",
                        "base_value": "每日一次",
                        "proposed_value": "短时失控突破",
                    },
                }
            ],
        },
    )

    service.apply_projections(payload)

    assert (tmp_path / ".story-system" / "events" / "chapter_003.events.json").is_file()
    manager = IndexManager(DataModulesConfig.from_project_root(tmp_path))
    with manager._get_conn() as conn:
        row = conn.execute(
            """
            SELECT record_type, field, override_value, status
            FROM override_contracts
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    assert row["record_type"] == "amend_proposal"
    assert row["field"] == "world_rule"
    assert row["override_value"] == "短时失控突破"
    assert row["status"] == "pending"


def test_writer_status_maps_degrade_reasons_to_skipped(tmp_path):
    """降级原因判 skipped；真故障仍必须是 failed —— 两者不能混为一谈。"""
    service = ChapterCommitService(tmp_path)

    assert service._writer_status({"applied": True}) == "done"

    assert service._writer_status({"applied": False, "reason": "not_required"}) == "skipped"
    assert service._writer_status({"applied": False, "reason": "commit_rejected"}) == "skipped"
    assert service._writer_status({"applied": False, "reason": "embedding_unavailable"}) == "skipped"
    assert service._writer_status({"applied": False, "reason": "vector_projection_disabled"}) == "skipped"

    assert service._writer_status({"applied": False, "reason": "error:store_failed"}) == "failed:store_failed"
    assert service._writer_status({"applied": False, "reason": "error:db_locked"}) == "failed:db_locked"
    # 未登记的原因不得被当成降级。
    assert service._writer_status({"applied": False, "reason": "something_new"}) == "skipped"
    assert (
        service._writer_status({"applied": False, "reason": "error:UNIQUE constraint failed"})
        == "failed:UNIQUE constraint failed"
    )
