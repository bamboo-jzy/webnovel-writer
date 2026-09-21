import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from data_modules.chapter_commit_service import ChapterCommitService
from data_modules.chapter_reloading import (
    artifact_paths, assert_artifact_freshness, body_evidence, chapter_body_revision,
    commit_identity, commit_path, read_object, reload_chapter_body, revision_entry,
    reconcile_chapter_body, approved_reconciliation, chapter_dependency_impacts,
    chapter_dependency_impact_records,
    validate_chapter_body,
)
from data_modules.project_phase import contract_files_for_chapter, resolve_project_phase
from data_modules.projections import retry_projection
from data_modules.write_gates import run_write_gate
from .test_project_phase import _make_init_ready, _write_json


def prepare_inputs(root, chapter=1, payloads=None):
    if not (root / ".webnovel/state.json").exists():
        _make_init_ready(root)
    body = root / "正文" / f"第{chapter:04d}章.md"
    body.parent.mkdir(parents=True, exist_ok=True)
    if not body.exists():
        body.write_text("正文：主角发现石门。", encoding="utf-8")
    for path in contract_files_for_chapter(root, chapter).values():
        if not path.exists():
            _write_json(path, {})
    reload = reload_chapter_body(root, chapter)
    assert reload["ok"], reload
    payloads = deepcopy(payloads or {
        "review_result": {"blocking_count": 0},
        "fulfillment_result": {"planned_nodes": [], "covered_nodes": [], "missed_nodes": [], "extra_nodes": []},
        "disambiguation_result": {"pending": []},
        "extraction_result": {"accepted_events": [], "state_deltas": [], "entity_deltas": []},
    })
    for key, path in artifact_paths(root).items():
        payloads[key]["source"] = reload["validation_input"]
        _write_json(path, payloads[key])
    return payloads


def accepted_commit(root, chapter=1, payloads=None):
    inputs = prepare_inputs(root, chapter, payloads)
    assert validate_chapter_body(root, chapter)["ok"]
    service = ChapterCommitService(root)
    payload = service.build_commit(chapter=chapter, **inputs)
    service.persist_commit(payload)
    return service.apply_projections(payload)


def install_projection_fixture(root, payload):
    chapter = payload["meta"]["chapter"]
    body = root / "正文" / f"第{chapter:04d}章.md"
    body.parent.mkdir(parents=True, exist_ok=True)
    if not body.exists():
        body.write_text("投影测试正文。", encoding="utf-8")
    if not (root / ".webnovel/state.json").exists():
        _write_json(root / ".webnovel/state.json", {})
    payload["meta"]["content_revision"] = chapter_body_revision(root, chapter)
    _write_json(commit_path(root, chapter), payload)


def test_reload_preview_backup_and_hash(tmp_path):
    prepare_inputs(tmp_path)
    path = tmp_path / "正文/第0001章.md"
    signature = chapter_body_revision(tmp_path, 1)
    os.utime(path, (1, 1))
    assert chapter_body_revision(tmp_path, 1) == signature
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert reload_chapter_body(tmp_path, 1, dry_run=True)["ok"]
    assert before == {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    state_before = (tmp_path / ".webnovel/state.json").read_bytes()
    report = reload_chapter_body(tmp_path, 1, backup_only=True)
    assert report["ok"]
    assert (tmp_path / ".webnovel/state.json").read_bytes() == state_before
    assert (Path(report["backup_dir"]) / "正文/第0001章.md").read_bytes() == path.read_bytes()


@pytest.mark.parametrize("failure", ["empty", "duplicate", "missing", "encoding", "state"])
def test_reload_rejects_bad_inputs_without_state_change(tmp_path, failure):
    prepare_inputs(tmp_path)
    path = tmp_path / "正文/第0001章.md"
    if failure == "empty":
        path.write_text("  \n", encoding="utf-8")
    elif failure == "duplicate":
        (path.parent / "第1章-副本.md").write_bytes(path.read_bytes())
    elif failure == "missing":
        path.unlink()
    elif failure == "encoding":
        path.write_bytes(b"\xff")
    else:
        (tmp_path / ".webnovel/state.json").write_text("[]", encoding="utf-8")
    before = (tmp_path / ".webnovel/state.json").read_bytes()
    assert not reload_chapter_body(tmp_path, 1)["ok"]
    assert (tmp_path / ".webnovel/state.json").read_bytes() == before


def test_old_artifacts_cannot_validate_edited_body(tmp_path):
    inputs = prepare_inputs(tmp_path)
    assert validate_chapter_body(tmp_path, 1)["ok"]
    path = tmp_path / "正文/第0001章.md"
    path.write_text("修改后的正文。", encoding="utf-8")
    assert not validate_chapter_body(tmp_path, 1)["ok"]
    assert body_evidence(tmp_path, 1)["body_revision_stale"]
    reload = reload_chapter_body(tmp_path, 1)
    assert reload["ok"]
    assert not validate_chapter_body(tmp_path, 1)["ok"]
    assert not run_write_gate(tmp_path, chapter=1, stage="precommit")["ok"]
    payload = ChapterCommitService(tmp_path).build_commit(chapter=1, **inputs)
    with pytest.raises(ValueError, match="stale"):
        ChapterCommitService(tmp_path).persist_commit(payload)
    assert not commit_path(tmp_path, 1).exists()


def test_validation_and_commit_detect_artifact_tampering(tmp_path):
    inputs = prepare_inputs(tmp_path)
    assert validate_chapter_body(tmp_path, 1)["ok"]
    inputs["extraction_result"]["summary_text"] = "未经本轮校验的新摘要"
    service = ChapterCommitService(tmp_path)
    with pytest.raises(ValueError, match="exact artifacts"):
        service.persist_commit(service.build_commit(chapter=1, **inputs))


def test_same_input_reload_is_idempotent(tmp_path):
    inputs = prepare_inputs(tmp_path)
    assert validate_chapter_body(tmp_path, 1)["ok"]
    report = reload_chapter_body(tmp_path, 1)
    assert report["validation_input"] == inputs["review_result"]["source"]
    assert report["content_status"] == "validated_pending_commit"
    assert_artifact_freshness(tmp_path, 1, inputs, require_validated=True)


def test_revision_history_and_no_fact_replay(tmp_path, monkeypatch):
    old = accepted_commit(tmp_path)
    old_bytes = commit_path(tmp_path, 1).read_bytes()
    body = tmp_path / "正文/第0001章.md"
    body.write_text("正文措辞调整，不新增事实。", encoding="utf-8")
    assert not retry_projection(tmp_path, chapter=1)["ok"]
    inputs = prepare_inputs(tmp_path)
    assert validate_chapter_body(tmp_path, 1)["ok"]
    service = ChapterCommitService(tmp_path)
    payload = service.build_commit(chapter=1, **inputs)
    with pytest.raises(ValueError, match="confirmation"):
        service.persist_commit(payload)
    assert commit_path(tmp_path, 1).read_bytes() == old_bytes
    service.persist_commit(payload, expected_previous=commit_identity(old))
    monkeypatch.setattr(service, "_projection_writers", lambda: pytest.fail("unchanged facts must not replay"))
    service.apply_projections(payload)
    assert payload["meta"]["revision_number"] == 2
    history = tmp_path / payload["provenance"]["previous_commit"]
    assert read_object(history) == old
    before_history = history.read_bytes()
    service.apply_projection_writers(payload)
    assert history.read_bytes() == before_history
    assert run_write_gate(tmp_path, chapter=1, stage="postcommit")["ok"]
    assert body.read_text(encoding="utf-8") == "正文措辞调整，不新增事实。"


def test_projection_refreshes_summary_without_fact_replay(tmp_path):
    old = accepted_commit(tmp_path)
    path = commit_path(tmp_path, 1)
    old_bytes = path.read_bytes()
    (tmp_path / "正文/第0001章.md").write_text("正文措辞调整。", encoding="utf-8")
    inputs = prepare_inputs(tmp_path)
    inputs["extraction_result"]["summary_text"] = "更新后的摘要"
    _write_json(artifact_paths(tmp_path)["extraction_result"], inputs["extraction_result"])
    assert validate_chapter_body(tmp_path, 1)["ok"]
    service = ChapterCommitService(tmp_path)
    payload = service.build_commit(chapter=1, **inputs)
    service.persist_commit(payload, expected_previous=commit_identity(old))

    assert path.read_bytes() != old_bytes
    assert payload["provenance"]["projection_refresh"] is True
    assert not payload["provenance"].get("projection_reuse")
    history = tmp_path / payload["provenance"]["previous_commit"]
    assert read_object(history) == old


def test_changed_facts_block_revision_without_overwriting_accepted(tmp_path):
    old = accepted_commit(tmp_path)
    path = commit_path(tmp_path, 1)
    old_bytes = path.read_bytes()
    (tmp_path / "正文/第0001章.md").write_text("主角获得新物品。", encoding="utf-8")
    inputs = prepare_inputs(
        tmp_path,
        payloads={
            "review_result": {"blocking_count": 0},
            "fulfillment_result": {"planned_nodes": [], "covered_nodes": [], "missed_nodes": [], "extra_nodes": []},
            "disambiguation_result": {"pending": []},
            "extraction_result": {
                "accepted_events": [],
                "state_deltas": [{"entity_id": "主角", "field": "inventory", "old": [], "new": ["玄铁剑"]}],
                "entity_deltas": [],
            },
        },
    )
    assert validate_chapter_body(tmp_path, 1)["ok"]
    service = ChapterCommitService(tmp_path)
    with pytest.raises(ValueError, match="revision_projection_unsafe"):
        service.persist_commit(service.build_commit(chapter=1, **inputs), expected_previous=commit_identity(old))
    assert path.read_bytes() == old_bytes
    assert not list((path.parent / "history").rglob("*.json"))


def test_downstream_stale_persists_and_blocks_writing(tmp_path):
    accepted_commit(tmp_path)
    _write_json(tmp_path / ".story-system/chapters/chapter_002.json", {})
    (tmp_path / "正文/第0001章.md").write_text("作者修改。", encoding="utf-8")
    before_reload = run_write_gate(tmp_path, chapter=2, stage="prewrite")
    assert not before_reload["ok"]
    first = reload_chapter_body(tmp_path, 1)
    assert first["stale_chapters"] == [2]
    reload_chapter_body(tmp_path, 1)
    state = read_object(tmp_path / ".webnovel/state.json")
    assert revision_entry(state, 2)["stale_reason"] == "previous_chapter_revision_changed"
    assert not run_write_gate(tmp_path, chapter=2, stage="prewrite")["ok"]
    assert resolve_project_phase(tmp_path, 1).phase != "chapter_committed"


def test_downstream_stale_records_dependency_impact_reasons(tmp_path):
    accepted_commit(tmp_path)
    _write_json(tmp_path / ".story-system/chapters/chapter_002.json", {})
    (tmp_path / "大纲" / "第2章-追踪.md").write_text("# 第2章\n主角发现石门后继续追踪。", encoding="utf-8")
    (tmp_path / "正文" / "第0001章.md").write_text("作者修改。", encoding="utf-8")

    report = reload_chapter_body(tmp_path, 1)

    assert report["ok"]
    state = read_object(tmp_path / ".webnovel/state.json")
    impacts = revision_entry(state, 2)["dependency_impacts"]["1"]
    assert impacts
    assert "previous chapter 1 body revision changed" in impacts[0]


def test_downstream_stale_records_structured_dependency_impact(tmp_path):
    accepted_commit(
        tmp_path,
        payloads={
            "review_result": {"blocking_count": 0},
            "fulfillment_result": {"planned_nodes": [], "covered_nodes": [], "missed_nodes": [], "extra_nodes": []},
            "disambiguation_result": {"pending": []},
            "extraction_result": {
                "accepted_events": [
                    {"event_type": "artifact_obtained", "subject": "主角", "payload": {"artifact_id": "玄铁剑"}}
                ],
                "state_deltas": [],
                "entity_deltas": [],
            },
        },
    )
    _write_json(tmp_path / ".story-system/chapters/chapter_002.json", {})
    (tmp_path / "大纲" / "第2章-物件.md").write_text("# 第2章\n主角继续使用玄铁剑。", encoding="utf-8")
    (tmp_path / "正文" / "第0001章.md").write_text("作者修改。", encoding="utf-8")

    report = reload_chapter_body(tmp_path, 1)

    assert report["ok"]
    impacts = revision_entry(read_object(tmp_path / ".webnovel/state.json"), 2)["dependency_impacts"]["1"]
    assert any("物件持有" in reason and "玄铁剑" in reason for reason in impacts)


def test_failed_backup_preserves_state(tmp_path, monkeypatch):
    prepare_inputs(tmp_path)
    state = (tmp_path / ".webnovel/state.json").read_bytes()
    monkeypatch.setattr("data_modules.chapter_reloading.shutil.copy2", lambda *args: (_ for _ in ()).throw(OSError("denied")))
    assert not reload_chapter_body(tmp_path, 1)["ok"]
    assert (tmp_path / ".webnovel/state.json").read_bytes() == state


def test_contract_change_requires_new_review(tmp_path):
    inputs = prepare_inputs(tmp_path)
    assert validate_chapter_body(tmp_path, 1)["ok"]
    _write_json(tmp_path / ".story-system/chapters/chapter_001.json", {"new": "goal"})
    with pytest.raises(ValueError, match="contracts changed"):
        assert_artifact_freshness(tmp_path, 1, inputs, require_validated=True)




def test_reconciliation_token_expires_when_artifact_changes(tmp_path):
    inputs = prepare_inputs(tmp_path)
    inputs["fulfillment_result"].update(
        planned_nodes=["延后节点"], covered_nodes=[], missed_nodes=["延后节点"]
    )
    _write_json(artifact_paths(tmp_path)["fulfillment_result"], inputs["fulfillment_result"])
    preview = reconcile_chapter_body(tmp_path, 1, dry_run=True)
    assert preview["ok"]

    inputs["fulfillment_result"]["missed_nodes"] = ["另一个节点"]
    _write_json(artifact_paths(tmp_path)["fulfillment_result"], inputs["fulfillment_result"])
    decision = reconcile_chapter_body(
        tmp_path,
        1,
        decision="accepted_deviation",
        reason="作者确认",
        impact=[2],
        expected_input=preview["input_token"],
    )

    assert decision["ok"] is False
    assert "confirmation" in decision["error"]


def test_reconciliation_record_cannot_authorize_changed_body_or_malformed_reference(tmp_path):
    inputs = prepare_inputs(tmp_path)
    inputs["fulfillment_result"].update(
        planned_nodes=["延后节点"], covered_nodes=[], missed_nodes=["延后节点"]
    )
    _write_json(artifact_paths(tmp_path)["fulfillment_result"], inputs["fulfillment_result"])
    preview = reconcile_chapter_body(tmp_path, 1, dry_run=True)
    decision = reconcile_chapter_body(
        tmp_path,
        1,
        decision="accepted_deviation",
        reason="作者确认",
        impact=[2],
        expected_input=preview["input_token"],
    )
    assert decision["ok"]

    (tmp_path / "正文" / "第0001章.md").write_text("正文已改变", encoding="utf-8")
    assert approved_reconciliation(tmp_path, 1, inputs) is None

    state = read_object(tmp_path / ".webnovel/state.json")
    state["progress"]["chapter_revisions"]["1"]["reconciliation_id"] = "../outside"
    _write_json(tmp_path / ".webnovel/state.json", state)
    assert approved_reconciliation(tmp_path, 1, inputs) is None


def test_legacy_commit_requires_reload_not_implicit_trust(tmp_path):
    _make_init_ready(tmp_path)
    _write_json(commit_path(tmp_path, 1), {"meta": {"chapter": 1, "status": "accepted"}})
    (tmp_path / "正文/第0001章.md").write_text("手改正文", encoding="utf-8")
    assert body_evidence(tmp_path, 1)["body_revision_stale"]
    assert not retry_projection(tmp_path, chapter=1)["ok"]


def test_structured_dependency_impact_marks_proven_transitive_chain(tmp_path):
    _make_init_ready(tmp_path)
    _write_json(tmp_path / ".story-system/chapters/chapter_002.json", {})
    _write_json(tmp_path / ".story-system/chapters/chapter_003.json", {})
    (tmp_path / "大纲" / "第2章-物件.md").write_text("# 第2章\n主角继续使用玄铁剑。", encoding="utf-8")
    state_path = tmp_path / ".webnovel/state.json"
    state = read_object(state_path)
    state.setdefault("progress", {}).setdefault("chapter_revisions", {})["3"] = {
        "dependency_impact_records": {
            "2": [
                {
                    "source_chapter": 2,
                    "dependent_chapter": 3,
                    "category": "artifact",
                    "fact_key": "玄铁剑",
                    "reason": "物件持有事实changed: 玄铁剑",
                    "before": None,
                    "after": [{"artifact_id": "玄铁剑"}],
                    "direct": True,
                    "transitive": False,
                }
            ]
        }
    }
    _write_json(state_path, state)

    records = chapter_dependency_impact_records(
        tmp_path,
        1,
        [2, 3],
        fact_diff={
            "added": [{"category": "artifact", "key": "玄铁剑", "before": None, "after": [{"artifact_id": "玄铁剑"}], "source": {}}],
            "removed": [],
            "changed": [],
        },
    )

    transitive = records["3"]
    assert any(row["transitive"] and row["propagation_path"] == [1, 2, 3] for row in transitive)
    assert not any(row["transitive"] for row in records["2"])



    accepted_commit(
        tmp_path,
        payloads={
            "review_result": {"blocking_count": 0},
            "fulfillment_result": {"planned_nodes": [], "covered_nodes": [], "missed_nodes": [], "extra_nodes": []},
            "disambiguation_result": {"pending": []},
            "extraction_result": {
                "accepted_events": [
                    {"event_type": "artifact_obtained", "subject": "主角", "payload": {"artifact_id": "玄铁剑"}}
                ],
                "state_deltas": [],
                "entity_deltas": [],
            },
        },
    )
    (tmp_path / ".story-system/chapters/chapter_002.json").write_text("{}", encoding="utf-8")
    (tmp_path / "大纲" / "第2章-无关.md").write_text("# 第2章\n主角走进一间空屋。", encoding="utf-8")

    impacts = chapter_dependency_impacts(tmp_path, 1, [2])

    assert impacts["2"] == ["previous chapter 1 body revision changed; downstream review required"]
