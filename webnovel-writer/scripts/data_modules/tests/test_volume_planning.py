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

from data_modules.volume_planning import reload_volume_plan  # noqa: E402
from chapter_outline_loader import volume_planning_revision  # noqa: E402


def _write_state(project_root: Path, progress: dict) -> None:
    state_path = project_root / ".webnovel" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"project_info": {"title": "测试书"}, "progress": progress}, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_volume_artifacts(project_root: Path, detail: str = "卷纲\n") -> None:
    outline_dir = project_root / "大纲"
    outline_dir.mkdir(parents=True, exist_ok=True)
    (outline_dir / "第1卷-节拍表.md").write_text("节拍\n", encoding="utf-8")
    (outline_dir / "第1卷-时间线.md").write_text("时间线\n", encoding="utf-8")
    (outline_dir / "第1卷-详细大纲.md").write_text(detail, encoding="utf-8")


def _read_state(project_root: Path) -> dict:
    return json.loads((project_root / ".webnovel" / "state.json").read_text(encoding="utf-8"))


def test_reload_volume_plan_registers_initial_revision(tmp_path):
    _write_volume_artifacts(tmp_path)
    _write_state(tmp_path, {"current_chapter": 0})

    report = reload_volume_plan(tmp_path, 1, chapters_range="1-50")

    assert report["ok"] is True
    assert report["changed"] is False
    assert report["revision"]
    entry = _read_state(tmp_path)["progress"]["volumes_planned"][0]
    assert entry["planning_revision"] == report["revision"]
    assert entry["chapters_range"] == "1-50"
    assert report["state_backup"]
    backup_dir = Path(report["backup_dir"])
    assert backup_dir.is_dir()
    assert (backup_dir / "第1卷-节拍表.md").is_file()
    assert (backup_dir / "第1卷-时间线.md").is_file()
    assert (backup_dir / "第1卷-详细大纲.md").is_file()
    assert (backup_dir / "state.json").is_file()


def test_reload_volume_plan_marks_only_old_revision_chapters_stale(tmp_path):
    _write_volume_artifacts(tmp_path)
    current_revision = volume_planning_revision(tmp_path, 1)
    _write_state(
        tmp_path,
        {
            "current_chapter": 0,
            "volumes_planned": [{"volume": 1, "chapters_range": "1-3", "planning_revision": "old"}],
            "chapters_planned": [
                {"chapter": 1, "volume": 1, "source_volume_revision": "old"},
                {"chapter": 2, "volume": 1, "source_volume_revision": "old", "status": "ready"},
                {"chapter": 3, "volume": 1, "source_volume_revision": current_revision, "status": "ready"},
                {"chapter": 4, "volume": 2, "source_volume_revision": "old", "status": "ready"},
            ],
        },
    )

    report = reload_volume_plan(tmp_path, 1)

    assert report["ok"] is True
    assert report["changed"] is True
    assert report["stale_chapters"] == [1, 2]
    planned = _read_state(tmp_path)["progress"]["chapters_planned"]
    assert planned[0]["status"] == "stale"
    assert planned[1]["stale_reason"] == "volume_plan_changed"
    assert planned[2]["status"] == "ready"
    assert planned[3]["status"] == "ready"


def test_reload_volume_plan_does_not_change_state_when_revision_is_current(tmp_path):
    _write_volume_artifacts(tmp_path)
    _write_state(tmp_path, {"current_chapter": 0})
    first = reload_volume_plan(tmp_path, 1, chapters_range="1-3")
    before = _read_state(tmp_path)

    report = reload_volume_plan(tmp_path, 1, chapters_range="1-3")

    assert report["ok"] is True
    assert report["changed"] is False
    assert report["stale_chapters"] == []
    after = _read_state(tmp_path)
    assert after["progress"]["volumes_planned"][0]["planning_revision"] == first["revision"]
    assert after["progress"]["volumes_planned"][0].get("previous_revision") == before["progress"]["volumes_planned"][0].get("previous_revision")


def test_reload_volume_plan_backup_only_preserves_state_and_creates_backup(tmp_path):
    _write_volume_artifacts(tmp_path)
    _write_state(tmp_path, {"current_chapter": 0})

    report = reload_volume_plan(tmp_path, 1, backup_only=True)

    assert report["ok"] is True
    assert report["backup_only"] is True
    assert Path(report["backup_dir"]).is_dir()
    assert (Path(report["backup_dir"]) / "第1卷-详细大纲.md").is_file()
    assert "volumes_planned" not in _read_state(tmp_path)["progress"]


def test_reload_volume_plan_rejects_missing_or_empty_artifacts(tmp_path):
    _write_state(tmp_path, {"current_chapter": 0})
    (tmp_path / "大纲").mkdir()
    (tmp_path / "大纲" / "第1卷-节拍表.md").write_text("\n", encoding="utf-8")

    report = reload_volume_plan(tmp_path, 1)

    assert report["ok"] is False
    assert "节拍表" in report["empty"]
    assert "时间线" in report["missing"]
    assert "详细大纲" in report["missing"]
