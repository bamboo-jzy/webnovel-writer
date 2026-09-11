#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys


def test_update_state_cli_add_review_writes_checkpoint(tmp_path, monkeypatch):
    import update_state as update_state_module

    webnovel_dir = tmp_path / ".webnovel"
    webnovel_dir.mkdir(parents=True, exist_ok=True)

    state = {
        "project_info": {},
        "progress": {"current_chapter": 1, "total_words": 0},
        "protagonist_state": {
            "power": {"realm": "炼气", "layer": 1, "bottleneck": None},
            "location": "村口",
        },
        "relationships": {},
        "world_settings": {},
        "plot_threads": {},
        "review_checkpoints": [],
    }
    state_file = webnovel_dir / "state.json"
    state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    # 避免在测试里创建备份目录/修改权限等非核心行为
    monkeypatch.setattr(update_state_module.StateUpdater, "backup", lambda self: True)

    report_file = "review/report_1_2.md"
    monkeypatch.setattr(
        sys,
        "argv",
        ["update_state", "--project-root", str(tmp_path), "--add-review", "1-2", report_file],
    )
    update_state_module.main()

    updated = json.loads(state_file.read_text(encoding="utf-8"))
    checkpoints = updated.get("review_checkpoints")
    assert isinstance(checkpoints, list)
    assert checkpoints[-1]["chapters"] == "1-2"
    assert checkpoints[-1]["report"] == report_file


def test_mark_chapter_planned_adds_entry_with_volume_revision():
    import update_state as update_state_module

    updater = update_state_module.StateUpdater("unused")
    updater.state = {"progress": {}}

    updater.mark_chapter_planned(3, "大纲/第3章-转折.md", 1, "volume-rev-1")

    planned = updater.state["progress"]["chapters_planned"]
    assert planned == [
        {
            "chapter": 3,
            "volume": 1,
            "outline_file": "大纲/第3章-转折.md",
            "planned_at": planned[0]["planned_at"],
            "updated_at": planned[0]["updated_at"],
            "source_volume_revision": "volume-rev-1",
        }
    ]


def test_mark_chapter_planned_updates_existing_entry_without_duplicate():
    import update_state as update_state_module

    updater = update_state_module.StateUpdater("unused")
    updater.state = {
        "progress": {
            "chapters_planned": [
                {
                    "chapter": 2,
                    "volume": 1,
                    "outline_file": "旧路径",
                    "planned_at": "2026-01-01",
                    "updated_at": "2026-01-01",
                    "source_volume_revision": "old",
                }
            ]
        }
    }

    updater.mark_chapter_planned(2, "大纲/第2章-修订.md", 1, "new")

    planned = updater.state["progress"]["chapters_planned"]
    assert len(planned) == 1
    assert planned[0]["outline_file"] == "大纲/第2章-修订.md"
    assert planned[0]["source_volume_revision"] == "new"
    assert planned[0]["planned_at"] == "2026-01-01"

