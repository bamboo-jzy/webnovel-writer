#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
from pathlib import Path
from types import SimpleNamespace

from .test_project_phase import _make_contracts, _make_init_ready
from .test_project_phase import _write_json
from .test_chapter_reloading import accepted_commit


def _ensure_scripts_on_path() -> None:
    scripts_dir = Path(__file__).resolve().parents[2]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


_ensure_scripts_on_path()

import data_modules.doctor as doctor_module  # noqa: E402
from data_modules.projection_log import append_projection_run  # noqa: E402


def test_doctor_init_ready_does_not_require_story_contracts(tmp_path, monkeypatch):
    _make_init_ready(tmp_path)
    monkeypatch.setattr(doctor_module, "_python_checks", lambda: [])

    report = doctor_module.build_doctor_report(tmp_path)

    assert report["ok"] is True
    assert report["phase"] == "init_ready"
    assert not [item for item in report["checks"] if str(item["id"]).startswith("file.contract.")]


def test_doctor_missing_init_file_blocks_with_repair(tmp_path, monkeypatch):
    _make_init_ready(tmp_path)
    (tmp_path / "大纲" / "总纲.md").unlink()
    monkeypatch.setattr(doctor_module, "_python_checks", lambda: [])

    report = doctor_module.build_doctor_report(tmp_path)

    assert report["ok"] is False
    matches = [item for item in report["checks"] if item["id"] == "file.required.大纲/总纲.md"]
    assert matches
    assert matches[0]["status"] == "error"
    assert matches[0]["repair"]


def test_doctor_reports_upstream_dependency_impact(tmp_path, monkeypatch):
    _make_init_ready(tmp_path)
    snapshot = SimpleNamespace(
        phase="init_ready",
        project_root=str(tmp_path),
        target_chapter=2,
        target_volume=1,
        missing_contract_files=(),
        body_revision_stale=False,
        body_revision_uncommitted=False,
        upstream_body_stale=(1,),
        body_evidence={},
        dependency_impacts={"1": ["possible 角色状态 dependency: 主角"]},
        dependency_impact_records={
            "1": [
                {
                    "category": "state",
                    "fact_key": "主角|境界",
                    "reason": "角色状态 changed",
                }
            ]
        },
        volume_plan_stale=False,
        volume_planning_revision="",
        latest_commit=None,
    )
    monkeypatch.setattr(doctor_module, "resolve_project_phase", lambda *args, **kwargs: snapshot)
    monkeypatch.setattr(doctor_module, "_python_checks", lambda: [])
    monkeypatch.setattr(doctor_module, "_sqlite_checks", lambda root: [])
    monkeypatch.setattr(doctor_module, "_rag_checks", lambda root: [])

    report = doctor_module.build_doctor_report(tmp_path)

    matches = [item for item in report["checks"] if item["id"] == "chapter.dependency_impact.1"]
    assert matches
    assert matches[0]["status"] == "error"
    assert "角色状态" in matches[0]["actual"]
    assert "structured_evidence" in matches[0]["actual"]
    assert "主角|境界" in matches[0]["actual"]
    assert report["ok"] is False


def test_doctor_checks_contracts_after_story_system_starts(tmp_path, monkeypatch):
    _make_init_ready(tmp_path)
    _make_contracts(tmp_path, chapter=1)
    (tmp_path / ".story-system" / "reviews" / "chapter_001.review.json").unlink()
    monkeypatch.setattr(doctor_module, "_python_checks", lambda: [])

    report = doctor_module.build_doctor_report(tmp_path)

    assert report["ok"] is False
    contract_checks = [item for item in report["checks"] if item["id"] == "file.contract.review"]
    assert contract_checks
    assert contract_checks[0]["status"] == "error"


def test_doctor_no_project_reports_repair(monkeypatch):
    monkeypatch.setattr(doctor_module, "_python_checks", lambda: [])

    report = doctor_module.build_doctor_report(None)

    assert report["ok"] is False
    assert report["phase"] == "no_project"
    assert report["recommended_actions"]


def test_doctor_warns_when_old_project_has_commit_without_projection_log(tmp_path, monkeypatch):
    _make_init_ready(tmp_path)
    _write_json(
        tmp_path / ".story-system" / "commits" / "chapter_001.commit.json",
        {
            "meta": {"chapter": 1, "status": "accepted"},
            "projection_status": {"state": "done"},
        },
    )
    monkeypatch.setattr(doctor_module, "_python_checks", lambda: [])

    report = doctor_module.build_doctor_report(tmp_path)

    assert report["ok"] is True
    matches = [item for item in report["checks"] if item["id"] == "projection_log.present"]
    assert matches
    assert matches[0]["status"] == "warning"


def test_doctor_blocks_pending_projection_log_run(tmp_path, monkeypatch):
    _make_init_ready(tmp_path)
    commit_payload = {
        "meta": {"chapter": 1, "status": "accepted"},
        "projection_status": {"state": "pending"},
    }
    commit_path = tmp_path / ".story-system" / "commits" / "chapter_001.commit.json"
    _write_json(commit_path, commit_payload)
    append_projection_run(
        tmp_path,
        commit_payload,
        {"state": {"status": "pending"}},
        commit_path=commit_path,
    )
    monkeypatch.setattr(doctor_module, "_python_checks", lambda: [])

    report = doctor_module.build_doctor_report(tmp_path)

    matches = [item for item in report["checks"] if item["id"] == "projection_log.latest_run"]
    assert matches
    assert matches[0]["status"] == "error"
    assert report["ok"] is False


# ---------------------------------------------------------------------------
# 索引 ↔ commit 对账（docs/operations/index-integrity-assessment-2026-09-17.md）
# ---------------------------------------------------------------------------


def _accepted_commit_with_event(root, chapter=1):
    """带一个 accepted_event 的真实 commit：事件文件与 story_events 镜像都会写。"""
    events = [
        {
            "event_id": "evt-open-loop",
            "event_type": "open_loop_created",
            "chapter": chapter,
            "subject": "韩立",
            "payload": {"description": "神秘玉佩为何发热"},
        }
    ]
    return accepted_commit(
        root,
        chapter=chapter,
        payloads={
            "review_result": {"blocking_count": 0},
            "fulfillment_result": {
                "planned_nodes": [],
                "covered_nodes": [],
                "missed_nodes": [],
                "extra_nodes": [],
            },
            "disambiguation_result": {"pending": []},
            "extraction_result": {
                "accepted_events": events,
                "state_deltas": [],
                "entity_deltas": [],
            },
        },
    )


def _index_conn(root):
    import sqlite3

    return sqlite3.connect(str(root / ".webnovel" / "index.db"))


def _doctor_check(report, check_id):
    return [item for item in report["checks"] if item["id"] == check_id]


def test_doctor_index_commit_sync_ok_on_healthy_project(tmp_path, monkeypatch):
    accepted_commit(tmp_path, chapter=1)
    monkeypatch.setattr(doctor_module, "_python_checks", lambda: [])

    report = doctor_module.build_doctor_report(tmp_path)

    matches = _doctor_check(report, "index.commit_sync")
    assert matches
    assert matches[0]["status"] == doctor_module.CHECK_OK
    assert report["ok"] is True


def test_doctor_blocks_when_index_missing_accepted_chapter(tmp_path, monkeypatch):
    accepted_commit(tmp_path, chapter=1)
    with _index_conn(tmp_path) as conn:
        conn.execute("DELETE FROM chapters")
        conn.commit()
    monkeypatch.setattr(doctor_module, "_python_checks", lambda: [])

    report = doctor_module.build_doctor_report(tmp_path)

    matches = _doctor_check(report, "index.commit_sync")
    assert matches
    assert matches[0]["status"] == doctor_module.CHECK_ERROR
    assert matches[0]["severity"] == "blocker"
    assert "missing=[1]" in matches[0]["actual"]
    assert "projections replay" in matches[0]["repair"]
    assert report["ok"] is False


def test_doctor_blocks_when_index_db_unreadable(tmp_path, monkeypatch):
    accepted_commit(tmp_path, chapter=1)
    (tmp_path / ".webnovel" / "index.db").write_text("not a sqlite database", encoding="utf-8")
    monkeypatch.setattr(doctor_module, "_python_checks", lambda: [])

    report = doctor_module.build_doctor_report(tmp_path)

    matches = _doctor_check(report, "index.commit_sync")
    assert matches
    assert matches[0]["status"] == doctor_module.CHECK_ERROR
    assert "index.db" in matches[0]["repair"]
    assert report["ok"] is False


def test_doctor_warns_on_orphan_index_rows(tmp_path, monkeypatch):
    accepted_commit(tmp_path, chapter=1)
    with _index_conn(tmp_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO chapters(chapter, title, word_count) VALUES (7, '不该存在的章', 100)"
        )
        conn.commit()
    monkeypatch.setattr(doctor_module, "_python_checks", lambda: [])

    report = doctor_module.build_doctor_report(tmp_path)

    matches = _doctor_check(report, "index.orphan_rows")
    assert matches
    assert matches[0]["status"] == doctor_module.CHECK_WARNING
    assert "orphan=[7]" in matches[0]["actual"]
    assert report["ok"] is True


def test_doctor_warns_on_degraded_chapter_metadata(tmp_path, monkeypatch):
    # 正文命名为「第0001章.md」→ 索引拿不到标题，且正文缺失时字数为 0（B1/B2）
    accepted_commit(tmp_path, chapter=1)
    monkeypatch.setattr(doctor_module, "_python_checks", lambda: [])

    report = doctor_module.build_doctor_report(tmp_path)

    matches = _doctor_check(report, "index.chapter_metadata")
    assert matches
    assert matches[0]["status"] == doctor_module.CHECK_WARNING
    assert "degraded=[1]" in matches[0]["actual"]
    assert report["ok"] is True


def test_doctor_warns_when_event_mirror_missing_but_chapters_intact(tmp_path, monkeypatch):
    _accepted_commit_with_event(tmp_path, chapter=1)
    with _index_conn(tmp_path) as conn:
        conn.execute("DELETE FROM story_events")
        conn.commit()
    monkeypatch.setattr(doctor_module, "_python_checks", lambda: [])

    report = doctor_module.build_doctor_report(tmp_path)

    matches = _doctor_check(report, "index.story_events_sync")
    assert matches
    assert matches[0]["status"] == doctor_module.CHECK_WARNING
    assert "unmirrored=[1]" in matches[0]["actual"]
    # 章节行还在，所以不会被 commit_sync 发现 —— 这正是这条检查存在的理由
    assert _doctor_check(report, "index.commit_sync")[0]["status"] == doctor_module.CHECK_OK
