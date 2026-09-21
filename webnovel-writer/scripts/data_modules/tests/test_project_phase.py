#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys
from pathlib import Path


def _ensure_scripts_on_path() -> None:
    scripts_dir = Path(__file__).resolve().parents[2]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


_ensure_scripts_on_path()

from data_modules.project_phase import (  # noqa: E402
    INIT_REQUIRED_DIRS,
    INIT_REQUIRED_FILES,
    PHASE_CHAPTER_CONTRACT_READY,
    PHASE_DRAFT_IN_PROGRESS,
    PHASE_INIT_READY,
    PHASE_INIT_SCAFFOLDED,
    PHASE_PROJECTION_FAILED,
    PHASE_READY_TO_COMMIT,
    COMMIT_ARTIFACT_FILES,
    resolve_project_phase,
)
from data_modules.projection_log import append_projection_run  # noqa: E402


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _make_init_ready(project_root: Path) -> None:
    for rel in INIT_REQUIRED_DIRS:
        (project_root / rel).mkdir(parents=True, exist_ok=True)
    for rel in INIT_REQUIRED_FILES:
        path = project_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if rel.endswith(".json"):
            _write_json(
                path,
                {
                    "project_info": {"title": "测试书", "genre": "玄幻"},
                    "progress": {"current_chapter": 0},
                },
            )
        else:
            path.write_text("placeholder\n", encoding="utf-8")


def _make_contracts(project_root: Path, chapter: int = 1) -> None:
    _write_json(project_root / ".story-system" / "MASTER_SETTING.json", {"meta": {"contract_type": "MASTER_SETTING"}})
    _write_json(project_root / ".story-system" / "volumes" / "volume_001.json", {"meta": {"volume": 1}})
    _write_json(project_root / ".story-system" / "chapters" / f"chapter_{chapter:03d}.json", {"meta": {"chapter": chapter}})
    _write_json(
        project_root / ".story-system" / "reviews" / f"chapter_{chapter:03d}.review.json",
        {"meta": {"chapter": chapter}},
    )


def test_project_phase_detects_stale_chapter_plan_after_volume_change(tmp_path):
    _make_init_ready(tmp_path)
    _make_contracts(tmp_path, chapter=1)
    outline_dir = tmp_path / "大纲"
    (outline_dir / "第1卷-节拍表.md").write_text("节拍\n", encoding="utf-8")
    (outline_dir / "第1卷-时间线.md").write_text("时间线\n", encoding="utf-8")
    (outline_dir / "第1卷-详细大纲.md").write_text("卷纲\n", encoding="utf-8")
    state_path = tmp_path / ".webnovel" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["progress"]["volumes_planned"] = [
        {"volume": 1, "chapters_range": "1-3", "planning_revision": "old"}
    ]
    state["progress"]["chapters_planned"] = [
        {"chapter": 1, "volume": 1, "source_volume_revision": "old", "status": "ready"}
    ]
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    snapshot = resolve_project_phase(tmp_path, chapter=1)

    assert snapshot.volume_plan_stale is True
    assert snapshot.chapter_contract_stale is True
    assert "chapter_plan_stale_after_volume_change" in snapshot.warnings


def test_project_phase_reports_init_scaffolded_when_core_files_missing(tmp_path):
    _write_json(tmp_path / ".webnovel" / "state.json", {"project_info": {}, "progress": {}})

    snapshot = resolve_project_phase(tmp_path)

    assert snapshot.phase == PHASE_INIT_SCAFFOLDED
    assert "大纲/总纲.md" in snapshot.missing_init_files
    assert snapshot.blocking


def test_project_phase_reports_init_ready_after_init_scaffold(tmp_path):
    _make_init_ready(tmp_path)

    snapshot = resolve_project_phase(tmp_path)

    assert snapshot.phase == PHASE_INIT_READY
    assert snapshot.target_chapter == 1
    assert snapshot.blocking == ()


def test_project_phase_blocks_split_outline_without_revision_evidence(tmp_path):
    _make_init_ready(tmp_path)
    _make_contracts(tmp_path, chapter=1)
    outline_dir = tmp_path / "大纲"
    outline_dir.mkdir(exist_ok=True)
    (outline_dir / "第1章-开端.md").write_text("# 第1章\n\n目标：完成侦查\n", encoding="utf-8")

    snapshot = resolve_project_phase(tmp_path, chapter=1)

    assert snapshot.chapter_contract_stale is True
    assert snapshot.phase == "plan_in_progress"


def test_project_phase_detects_chapter_contract_ready(tmp_path):
    _make_init_ready(tmp_path)
    _make_contracts(tmp_path, chapter=1)

    snapshot = resolve_project_phase(tmp_path)

    assert snapshot.phase == PHASE_CHAPTER_CONTRACT_READY
    assert snapshot.missing_contract_files == ()


def test_project_phase_detects_draft_and_ready_to_commit(tmp_path):
    _make_init_ready(tmp_path)
    _make_contracts(tmp_path, chapter=1)
    (tmp_path / "正文" / "第0001章.md").write_text("正文草稿\n", encoding="utf-8")

    draft_snapshot = resolve_project_phase(tmp_path)
    assert draft_snapshot.phase == PHASE_DRAFT_IN_PROGRESS

    for rel in COMMIT_ARTIFACT_FILES:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")

    ready_snapshot = resolve_project_phase(tmp_path)
    assert ready_snapshot.phase == PHASE_READY_TO_COMMIT


def test_project_phase_detects_projection_failed(tmp_path):
    _make_init_ready(tmp_path)
    _write_json(
        tmp_path / ".story-system" / "commits" / "chapter_001.commit.json",
        {
            "meta": {"chapter": 1, "status": "accepted"},
            "projection_status": {"state": "done", "index": "failed:locked"},
        },
    )

    snapshot = resolve_project_phase(tmp_path)

    assert snapshot.phase == PHASE_PROJECTION_FAILED
    assert "latest_commit_projection_failed" in snapshot.blocking


def test_project_phase_prefers_projection_log_over_commit_status(tmp_path):
    _make_init_ready(tmp_path)
    commit_path = tmp_path / ".story-system" / "commits" / "chapter_001.commit.json"
    commit_payload = {
        "meta": {"chapter": 1, "status": "accepted"},
        "projection_status": {"state": "done", "index": "done", "vector": "done"},
    }
    _write_json(commit_path, commit_payload)
    append_projection_run(
        tmp_path,
        commit_payload,
        {"vector": {"status": "failed:timeout", "error": "timeout"}},
        commit_path=commit_path,
    )

    snapshot = resolve_project_phase(tmp_path)

    assert snapshot.phase == PHASE_PROJECTION_FAILED
    assert snapshot.latest_commit is not None
    assert snapshot.latest_commit.projection_source == "projection_log"
    assert snapshot.latest_commit.projection_status["vector"] == "failed:timeout"


def test_project_phase_treats_projection_log_pending_as_blocking(tmp_path):
    _make_init_ready(tmp_path)
    commit_path = tmp_path / ".story-system" / "commits" / "chapter_001.commit.json"
    commit_payload = {
        "meta": {"chapter": 1, "status": "accepted"},
        "projection_status": {"state": "done"},
    }
    _write_json(commit_path, commit_payload)
    append_projection_run(
        tmp_path,
        commit_payload,
        {"state": {"status": "pending"}},
        commit_path=commit_path,
    )

    snapshot = resolve_project_phase(tmp_path)

    assert snapshot.phase == PHASE_PROJECTION_FAILED
    assert "latest_commit_projection_incomplete" in snapshot.blocking


def test_project_phase_warns_on_degraded_projection(tmp_path):
    """降级跳过不阻断写作，但必须出现在 warnings 里（否则"向量一直是空的"无人知晓）。"""
    _make_init_ready(tmp_path)
    commit_path = tmp_path / ".story-system" / "commits" / "chapter_001.commit.json"
    commit_payload = {
        "meta": {"chapter": 1, "status": "accepted"},
        "projection_status": {"state": "done", "index": "done", "vector": "skipped"},
    }
    _write_json(commit_path, commit_payload)
    append_projection_run(
        tmp_path,
        commit_payload,
        {
            "state": {"status": "done", "result": {"applied": True}},
            "vector": {
                "status": "skipped",
                "result": {
                    "applied": False,
                    "reason": "embedding_unavailable",
                    "detail": "embedding_not_configured",
                },
            },
        },
        commit_path=commit_path,
    )

    snapshot = resolve_project_phase(tmp_path)

    assert snapshot.phase != PHASE_PROJECTION_FAILED
    assert "projection_degraded_vector_embedding_unavailable" in snapshot.warnings
    assert snapshot.latest_commit is not None
    assert snapshot.latest_commit.projection_degraded == {"vector": "embedding_unavailable"}


def test_project_phase_clears_degrade_warning_after_recovery(tmp_path):
    """配好凭证补跑成功后，降级提醒必须消失（同章取最后一次 run）。"""
    _make_init_ready(tmp_path)
    commit_path = tmp_path / ".story-system" / "commits" / "chapter_001.commit.json"
    commit_payload = {
        "meta": {"chapter": 1, "status": "accepted"},
        "projection_status": {"state": "done", "index": "done", "vector": "skipped"},
    }
    _write_json(commit_path, commit_payload)
    append_projection_run(
        tmp_path,
        commit_payload,
        {
            "vector": {
                "status": "skipped",
                "result": {"applied": False, "reason": "embedding_unavailable"},
            }
        },
        commit_path=commit_path,
    )
    append_projection_run(
        tmp_path,
        commit_payload,
        {"vector": {"status": "done", "result": {"applied": True, "stored": 24}}},
        commit_path=commit_path,
    )

    snapshot = resolve_project_phase(tmp_path)

    assert snapshot.latest_commit is not None
    assert snapshot.latest_commit.projection_degraded == {}
    assert not [w for w in snapshot.warnings if w.startswith("projection_degraded_")]


def test_project_phase_ignores_ordinary_skipped_projection(tmp_path):
    """常规 not_required 跳过不该刷出降级提醒，否则每章都是噪音。"""
    _make_init_ready(tmp_path)
    commit_path = tmp_path / ".story-system" / "commits" / "chapter_001.commit.json"
    commit_payload = {
        "meta": {"chapter": 1, "status": "accepted"},
        "projection_status": {"state": "done", "vector": "skipped"},
    }
    _write_json(commit_path, commit_payload)
    append_projection_run(
        tmp_path,
        commit_payload,
        {"vector": {"status": "skipped", "result": {"applied": False, "reason": "not_required"}}},
        commit_path=commit_path,
    )

    snapshot = resolve_project_phase(tmp_path)

    assert snapshot.latest_commit is not None
    assert snapshot.latest_commit.projection_degraded == {}
    assert not [w for w in snapshot.warnings if w.startswith("projection_degraded_")]
