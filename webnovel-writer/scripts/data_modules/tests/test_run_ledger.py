#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


def _ensure_scripts_on_path() -> None:
    scripts_dir = Path(__file__).resolve().parents[2]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


_ensure_scripts_on_path()

from data_modules.run_ledger import LedgerCorruptionError, build_write_resume_plan, load_ledger, record_write_step  # noqa: E402
from data_modules.chapter_reloading import chapter_body_revision  # noqa: E402


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_project(project_root: Path) -> None:
    (project_root / ".webnovel" / "tmp").mkdir(parents=True, exist_ok=True)
    (project_root / ".story-system" / "commits").mkdir(parents=True, exist_ok=True)
    (project_root / "正文").mkdir(parents=True, exist_ok=True)
    _write_json(project_root / ".webnovel" / "state.json", {"project_info": {"title": "测试书"}, "progress": {}})


def _commit_payload(status: str = "accepted", *, content_revision: str = "") -> dict:
    return {
        "meta": {"chapter": 1, "status": status, "content_revision": content_revision},
        "projection_status": {
            "state": "done",
            "index": "skipped",
            "summary": "skipped",
            "memory": "skipped",
            "vector": "skipped",
        },
    }


def test_run_ledger_corruption_blocks_loading_and_resume(tmp_path: Path) -> None:
    _make_project(tmp_path)
    ledger_path = tmp_path / ".webnovel" / "run_ledger.json"
    ledger_path.write_text('{"schema_version":"webnovel-run-ledger/v1",', encoding="utf-8")

    try:
        load_ledger(tmp_path)
    except LedgerCorruptionError:
        pass
    else:
        raise AssertionError("corrupt ledger must not load as an empty ledger")

    plan = build_write_resume_plan(tmp_path, chapter=1)
    assert plan["blocked"] is True
    assert plan["resume_from"] == "blocked"
    assert plan["needs_user_confirmation"][0]["code"] == "run_ledger_corrupt"


def test_run_ledger_replace_failure_preserves_previous_data(tmp_path, monkeypatch):
    import security_utils

    monkeypatch.delenv("WEBNOVEL_TEST_RELAX_ATOMIC_REPLACE", raising=False)
    record_write_step(tmp_path, chapter=1, step="draft", status="completed")
    path = tmp_path / ".webnovel/run_ledger.json"
    before = path.read_bytes()

    def fail_replace(*args, **kwargs):
        raise PermissionError("file busy")

    with monkeypatch.context() as patch:
        patch.setattr(security_utils, "_replace_with_retry", fail_replace)
        with pytest.raises(security_utils.AtomicWriteError, match="file busy"):
            record_write_step(tmp_path, chapter=1, step="review", status="completed")
    assert path.read_bytes() == before
    assert set(load_ledger(tmp_path)["write"]["chapter_001"]["steps"]) == {"draft"}
    assert not list(path.parent.glob("run_ledger_*.tmp"))
    record_write_step(tmp_path, chapter=1, step="review", status="completed")
    assert set(load_ledger(tmp_path)["write"]["chapter_001"]["steps"]) == {"draft", "review"}


def test_run_ledger_records_write_step_status(tmp_path: Path) -> None:
    _make_project(tmp_path)
    chapter_file = tmp_path / "正文" / "第0001章.md"
    chapter_file.write_text("正文\n", encoding="utf-8")

    entry = record_write_step(
        tmp_path,
        chapter=1,
        step="draft",
        status="completed",
        outputs={"chapter_file": chapter_file},
    )

    assert entry["status"] == "completed"
    assert entry["outputs"]["chapter_file"]["exists"] is True
    assert (tmp_path / ".webnovel" / "run_ledger.json").is_file()


def test_write_resume_skips_completed_draft_and_review(tmp_path: Path) -> None:
    _make_project(tmp_path)
    chapter_file = tmp_path / "正文" / "第0001章.md"
    chapter_file.write_text("正文\n", encoding="utf-8")
    review_path = tmp_path / ".webnovel" / "tmp" / "review_results.json"
    _write_json(review_path, {"blocking_count": 0})

    record_write_step(tmp_path, chapter=1, step="draft", status="completed", outputs={"chapter_file": chapter_file})
    record_write_step(
        tmp_path,
        chapter=1,
        step="review",
        status="completed",
        inputs={"chapter_file": chapter_file},
        outputs={"review_result": review_path},
    )

    plan = build_write_resume_plan(tmp_path, chapter=1)

    actions = {item["step"]: item["action"] for item in plan["steps"]}
    assert actions["draft"] == "skip"
    assert actions["review"] == "skip"
    assert actions["data"] == "run"


def test_write_resume_rechecks_review_when_chapter_file_changed(tmp_path: Path) -> None:
    _make_project(tmp_path)
    chapter_file = tmp_path / "正文" / "第0001章.md"
    chapter_file.write_text("正文 v1\n", encoding="utf-8")
    record_write_step(tmp_path, chapter=1, step="draft", status="completed", outputs={"chapter_file": chapter_file})
    chapter_file.write_text("正文 v2\n", encoding="utf-8")

    plan = build_write_resume_plan(tmp_path, chapter=1)

    actions = {item["step"]: item["action"] for item in plan["steps"]}
    assert actions["draft"] == "run"
    assert actions["review"] == "run"
    assert any(item["code"] == "chapter_file_changed" for item in plan["needs_user_confirmation"])


def test_write_resume_retries_backup_after_commit_done(tmp_path: Path) -> None:
    _make_project(tmp_path)
    chapter_file = tmp_path / "正文" / "第0001章.md"
    chapter_file.write_text("正文\n", encoding="utf-8")
    record_write_step(tmp_path, chapter=1, step="draft", status="completed", outputs={"chapter_file": chapter_file})
    _write_json(tmp_path / ".story-system" / "commits" / "chapter_001.commit.json", _commit_payload("accepted", content_revision=chapter_body_revision(tmp_path, 1)))

    plan = build_write_resume_plan(tmp_path, chapter=1)

    actions = {item["step"]: item["action"] for item in plan["steps"]}
    assert actions["draft"] == "skip"
    assert actions["review"] == "skip"
    assert actions["data"] == "skip"
    assert actions["commit"] == "skip"
    assert actions["projection"] == "skip"
    assert actions["backup"] == "retry"
    assert plan["resume_from"] == "backup"
    assert any(item["code"] == "chapter_already_accepted" for item in plan["needs_user_confirmation"])


def test_write_resume_reruns_commit_after_rejected_commit(tmp_path: Path) -> None:
    _make_project(tmp_path)
    chapter_file = tmp_path / "正文" / "第0001章.md"
    chapter_file.write_text("正文\n", encoding="utf-8")
    review_path = tmp_path / ".webnovel" / "tmp" / "review_results.json"
    _write_json(review_path, {"blocking_count": 1})
    fulfillment_path = tmp_path / ".webnovel" / "tmp" / "fulfillment_result.json"
    disambiguation_path = tmp_path / ".webnovel" / "tmp" / "disambiguation_result.json"
    extraction_path = tmp_path / ".webnovel" / "tmp" / "extraction_result.json"
    _write_json(fulfillment_path, {"planned_nodes": [], "covered_nodes": [], "missed_nodes": [], "extra_nodes": []})
    _write_json(disambiguation_path, {"pending": []})
    _write_json(extraction_path, {"accepted_events": [], "state_deltas": [], "entity_deltas": []})
    record_write_step(
        tmp_path,
        chapter=1,
        step="draft",
        status="completed",
        outputs={"chapter_file": chapter_file},
    )
    record_write_step(
        tmp_path,
        chapter=1,
        step="review",
        status="completed",
        inputs={"chapter_file": chapter_file},
        outputs={"review_result": review_path},
    )
    record_write_step(
        tmp_path,
        chapter=1,
        step="data",
        status="completed",
        inputs={"chapter_file": chapter_file},
        outputs={
            "fulfillment_result": fulfillment_path,
            "disambiguation_result": disambiguation_path,
            "extraction_result": extraction_path,
        },
    )
    _write_json(tmp_path / ".story-system" / "commits" / "chapter_001.commit.json", _commit_payload("rejected", content_revision=chapter_body_revision(tmp_path, 1)))

    plan = build_write_resume_plan(tmp_path, chapter=1)

    actions = {item["step"]: item["action"] for item in plan["steps"]}
    assert actions["commit"] == "run"
    assert actions["projection"] == "run"
    assert plan["resume_from"] == "commit"
    assert any(item["code"] == "chapter_commit_rejected" for item in plan["needs_user_confirmation"])
