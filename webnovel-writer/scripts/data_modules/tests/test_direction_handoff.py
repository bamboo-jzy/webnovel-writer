#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""四层方向透传（direction_handoff）的行为测试。

覆盖三件事：
1. 三节点的可改单元 / 既定事实划分（统一规律：只有最后一个单位可改）。
2. 候选偏离采集：总-卷的章节范围比对、卷-章的锚点差集、章-正的履约偏离。
3. 章-正节点的末端约束（body_edit_boundary）与裁决落盘。
"""

import json
import sys
from pathlib import Path


def _ensure_scripts_on_path() -> None:
    scripts_dir = Path(__file__).resolve().parents[2]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


_ensure_scripts_on_path()

from data_modules.chapter_reloading import reload_chapter_body  # noqa: E402
from data_modules.direction_handoff import (  # noqa: E402
    HANDOFF_DECISIONS,
    HANDOFF_NODES,
    body_edit_boundary,
    check_handoff,
    describe_handoff_scope,
    format_handoff_report,
    normalize_decision,
    record_handoff,
)

MASTER_OUTLINE = """# 总纲

## 卷划分

| 卷号 | 卷名 | 章节范围 | 核心冲突 | 卷末高潮 |
|------|------|----------|----------|----------|
| 1 | 青云试炼 | 1-3 | 主角与宗门长老的立场对立 | 长老揭露主角身世 |
"""


def _write_state(project_root: Path, progress: dict) -> None:
    state_path = project_root / ".webnovel" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"project_info": {"title": "测试书"}, "progress": progress}, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_volume_artifacts(project_root: Path, volume: int = 1) -> None:
    outline_dir = project_root / "大纲"
    outline_dir.mkdir(parents=True, exist_ok=True)
    (outline_dir / f"第{volume}卷-节拍表.md").write_text(
        f"# 第{volume}卷 节拍表\n\n核心冲突：被「玄阳长老」压制。\n", encoding="utf-8")
    (outline_dir / f"第{volume}卷-时间线.md").write_text(
        f"# 第{volume}卷 时间线\n\n入宗第 1-30 日。\n", encoding="utf-8")
    (outline_dir / f"第{volume}卷-详细大纲.md").write_text(
        f"# 第{volume}卷 详细大纲\n\n卷末高潮：长老揭露主角身世。\n", encoding="utf-8")


def _write_chapter_outline(project_root: Path, chapter: int, title: str, entities: str) -> None:
    outline_dir = project_root / "大纲"
    outline_dir.mkdir(parents=True, exist_ok=True)
    (outline_dir / f"第{chapter}章-{title}.md").write_text(
        f"# 第{chapter}章 {title}\n\n- 目标：推进本章\n- 关键实体：{entities}\n\n"
        f"## 结构节点\n\n- CBN：起点\n- CPNs：\n  - 中段一\n  - 中段二\n- CEN：终点\n",
        encoding="utf-8",
    )


def _build_project(tmp_path: Path, *, chapters=(1, 2), bodies=(1,), volumes=(1,)) -> None:
    (tmp_path / "大纲").mkdir(parents=True, exist_ok=True)
    (tmp_path / "正文").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".story-system" / "commits").mkdir(parents=True, exist_ok=True)
    (tmp_path / "大纲" / "总纲.md").write_text(MASTER_OUTLINE, encoding="utf-8")
    for volume in volumes:
        _write_volume_artifacts(tmp_path, volume)
    for chapter in chapters:
        _write_chapter_outline(tmp_path, chapter, f"第{chapter}章标题", "主角、青云宗")
    for chapter in bodies:
        (tmp_path / "正文" / f"第{chapter}章-第{chapter}章标题.md").write_text(
            f"# 第{chapter}章\n\n正文。\n", encoding="utf-8")
        (tmp_path / ".story-system" / "commits" / f"chapter_{chapter:03d}.commit.json").write_text(
            json.dumps({"meta": {"chapter": chapter, "status": "accepted"}}, ensure_ascii=False),
            encoding="utf-8")
    _write_state(tmp_path, {
        "volumes_planned": [
            {"volume": volume, "chapters_range": "1-3"} for volume in volumes
        ],
        "chapters_planned": [
            {"chapter": chapter, "volume": 1} for chapter in chapters
        ],
    })


# ---------------------------------------------------------------------------
# 节点划分
# ---------------------------------------------------------------------------

def test_master_to_volume_editable_is_last_volume(tmp_path):
    _build_project(tmp_path, volumes=(1, 2, 3), chapters=(), bodies=())

    scope = describe_handoff_scope(tmp_path, "master_to_volume")

    assert scope["ok"] is True
    assert scope["editable_units"] == [3]
    assert scope["locked_units"] == [1, 2]
    assert scope["locked_range"] == "1-2"
    assert "只能修改最后一卷" in scope["editable_rule"]


def test_volume_to_chapter_editable_is_last_chapter(tmp_path):
    _build_project(tmp_path, chapters=(1, 2, 3), bodies=(1,))

    scope = describe_handoff_scope(tmp_path, "volume_to_chapter")

    assert scope["editable_units"] == [3]
    assert scope["locked_units"] == [1, 2]


def test_chapter_to_body_editable_is_last_body_chapter(tmp_path):
    _build_project(tmp_path, chapters=(1, 2, 3), bodies=(1, 2))

    scope = describe_handoff_scope(tmp_path, "chapter_to_body")

    assert scope["editable_units"] == [2]
    assert scope["locked_units"] == [1]


def test_scope_rejects_locked_unit_with_blocker(tmp_path):
    _build_project(tmp_path, chapters=(1, 2))

    scope = describe_handoff_scope(tmp_path, "volume_to_chapter", target=1)

    assert scope["target_allowed"] is False
    assert scope["blockers"][0]["code"] == "handoff_target_locked"
    build = check_handoff(tmp_path, "volume_to_chapter", target=1)
    assert build["ok"] is False
    assert "不在可改单元内" in build["errors"][0]


def test_scope_treats_pending_unit_as_not_constrained(tmp_path):
    _build_project(tmp_path, chapters=(1, 2))

    scope = describe_handoff_scope(tmp_path, "volume_to_chapter", target=5)

    assert scope["target_allowed"] is True
    assert scope["target_pending"] is True


def test_scope_rejects_unknown_node(tmp_path):
    scope = describe_handoff_scope(tmp_path, "master_to_body")

    assert scope["ok"] is False
    assert sorted(scope["known_nodes"]) == sorted(HANDOFF_NODES)


# ---------------------------------------------------------------------------
# 候选偏离
# ---------------------------------------------------------------------------

def test_master_to_volume_detects_chapters_range_mismatch(tmp_path):
    _build_project(tmp_path, chapters=(), bodies=())
    (tmp_path / "大纲" / "总纲.md").write_text(
        MASTER_OUTLINE.replace("| 1-3 |", "| 1-8 |"), encoding="utf-8")

    report = check_handoff(tmp_path, "master_to_volume", target=1)

    kinds = [item["kind"] for item in report["candidates"]]
    assert "chapters_range_mismatch" in kinds
    assert report["aligned"] is False


def test_master_to_volume_aligned_when_range_matches(tmp_path):
    _build_project(tmp_path, chapters=(), bodies=())

    report = check_handoff(tmp_path, "master_to_volume", target=1)

    assert report["candidates"] == []
    assert report["aligned"] is True
    # 散文层永远需要作者裁决
    assert [item["kind"] for item in report["manual_items"]] == ["semantic_alignment"]


def test_master_to_volume_flags_volume_absent_from_master(tmp_path):
    _build_project(tmp_path, volumes=(1, 2), chapters=(), bodies=())

    report = check_handoff(tmp_path, "master_to_volume", target=2)

    kinds = [item["kind"] for item in report["candidates"]]
    assert "volume_absent_in_master" in kinds


def test_volume_to_chapter_flags_entity_missing_upstream(tmp_path):
    _build_project(tmp_path, chapters=(1, 2), bodies=(1,))
    _write_chapter_outline(tmp_path, 2, "暗涌", "天机阁、隐世散修")

    report = check_handoff(tmp_path, "volume_to_chapter", target=2)

    candidate = next(item for item in report["candidates"]
                     if item["kind"] == "outline_entity_not_in_upstream")
    assert "天机阁" in candidate["items"]


def test_volume_to_chapter_flags_stale_volume_revision(tmp_path):
    from chapter_outline_loader import volume_planning_revision

    _build_project(tmp_path, chapters=(1, 2), bodies=(1,))
    revision = volume_planning_revision(tmp_path, 1)
    _write_state(tmp_path, {
        "volumes_planned": [{"volume": 1, "chapters_range": "1-3"}],
        "chapters_planned": [
            {"chapter": 1, "volume": 1, "source_volume_revision": revision},
            {"chapter": 2, "volume": 1, "source_volume_revision": "stale-rev"},
        ],
    })

    report = check_handoff(tmp_path, "volume_to_chapter", target=2)

    kinds = [item["kind"] for item in report["candidates"]]
    assert "volume_revision_stale" in kinds


def test_chapter_to_body_flags_fulfillment_deviation(tmp_path):
    _build_project(tmp_path, chapters=(1,), bodies=(1,))
    (tmp_path / ".webnovel" / "tmp").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".webnovel" / "tmp" / "fulfillment_result.json").write_text(json.dumps({
        "planned_nodes": ["起点", "中段一", "终点"],
        "covered_nodes": ["起点"],
        "missed_nodes": ["中段一"],
    }, ensure_ascii=False), encoding="utf-8")

    report = check_handoff(tmp_path, "chapter_to_body", target=1)

    kinds = [item["kind"] for item in report["candidates"]]
    assert kinds.count("fulfillment_deviation") >= 1
    assert report["aligned"] is False


def test_chapter_to_body_asks_for_fulfillment_when_missing(tmp_path):
    _build_project(tmp_path, chapters=(1,), bodies=(1,))

    report = check_handoff(tmp_path, "chapter_to_body", target=1)

    assert [item["kind"] for item in report["manual_items"]] == ["fulfillment_missing"]
    assert report["candidates"] == []
    assert report["aligned"] is True


# ---------------------------------------------------------------------------
# 裁决
# ---------------------------------------------------------------------------

def test_normalize_decision_maps_legacy_values():
    assert normalize_decision("outline_to_body") == "align_downstream"
    assert normalize_decision("body_to_outline") == "align_upstream"
    assert normalize_decision("accepted_deviation") == "accepted_deviation"
    assert normalize_decision("align_upstream") == "align_upstream"
    assert normalize_decision("nonsense") == ""
    assert HANDOFF_DECISIONS == {"align_downstream", "align_upstream", "accepted_deviation"}


def test_record_handoff_requires_confirmed_token(tmp_path):
    _build_project(tmp_path, chapters=(), bodies=())

    preview = record_handoff(tmp_path, "master_to_volume", target=1, dry_run=True)
    assert preview["ok"] is True
    token = preview["input_token"]

    bad = record_handoff(tmp_path, "master_to_volume", target=1,
                         decision="align_downstream", reason="理由", impact=[1],
                         expected_input="0" * 64)
    assert bad["ok"] is False
    assert "handoff_confirmation_required" in bad["error"]

    good = record_handoff(tmp_path, "master_to_volume", target=1,
                          decision="body_to_outline", reason="以卷纲为准修正总纲",
                          impact=[1], expected_input=token)
    assert good["ok"] is True
    assert good["decision"] == "align_upstream"

    records = sorted((tmp_path / ".story-system" / "handoffs").glob("*.json"))
    assert len(records) == 1
    payload = json.loads(records[0].read_text(encoding="utf-8"))
    assert payload["node"] == "master_to_volume"
    assert payload["target"] == 1
    assert payload["upstream_sources"] == ["大纲/总纲.md"]


def test_record_handoff_rejects_missing_reason_and_impact(tmp_path):
    _build_project(tmp_path, chapters=(), bodies=())
    token = record_handoff(tmp_path, "master_to_volume", target=1, dry_run=True)["input_token"]

    no_reason = record_handoff(tmp_path, "master_to_volume", target=1,
                               decision="align_downstream", reason="  ", impact=[1],
                               expected_input=token)
    assert no_reason["ok"] is False
    assert "non-empty reason" in no_reason["error"]

    no_impact = record_handoff(tmp_path, "master_to_volume", target=1,
                               decision="align_downstream", reason="理由", impact=None,
                               expected_input=token)
    assert no_impact["ok"] is False
    assert "affected unit numbers" in no_impact["error"]


def test_record_handoff_rejected_on_locked_unit(tmp_path):
    _build_project(tmp_path, chapters=(1, 2), bodies=(1,))

    report = record_handoff(tmp_path, "volume_to_chapter", target=1,
                            decision="align_downstream", reason="理由", impact=[1])

    assert report["ok"] is False
    assert "不在可改单元内" in report["error"]


def test_record_handoff_upshift_target_converts_chapter_to_volume(tmp_path):
    _build_project(tmp_path, chapters=(1, 2), bodies=(1,))
    _write_chapter_outline(tmp_path, 2, "暗涌", "天机阁")

    report = check_handoff(tmp_path, "volume_to_chapter", target=2)

    upshift = next(step for step in report["next_steps"] if "align_upstream" in step)
    assert "--node master_to_volume --target 1" in upshift


# ---------------------------------------------------------------------------
# 章-正末端约束
# ---------------------------------------------------------------------------

def test_body_edit_boundary_blocks_non_final_chapter(tmp_path):
    _build_project(tmp_path, chapters=(1, 2, 3), bodies=(1, 2, 3))

    boundary = body_edit_boundary(tmp_path, 2)

    assert boundary["allowed"] is False
    assert boundary["last_chapter"] == 3
    assert boundary["blocker"]["code"] == "handoff_target_locked"


def test_body_edit_boundary_allows_unwritten_chapter(tmp_path):
    _build_project(tmp_path, chapters=(1, 2, 3), bodies=(1,))

    assert body_edit_boundary(tmp_path, 2)["allowed"] is True
    assert body_edit_boundary(tmp_path, 3)["allowed"] is True


def test_reload_chapter_body_blocked_for_locked_chapter(tmp_path):
    _build_project(tmp_path, chapters=(1, 2), bodies=(1, 2))

    report = reload_chapter_body(tmp_path, 1, dry_run=True)

    assert report["ok"] is False
    assert "handoff_target_locked" in report["error"]
    assert report["handoff_boundary"]["last_chapter"] == 2


def test_reload_chapter_body_backup_only_skips_boundary(tmp_path):
    _build_project(tmp_path, chapters=(1, 2), bodies=(1, 2))

    report = reload_chapter_body(tmp_path, 1, backup_only=True)

    assert report["ok"] is True
    assert "handoff_target_locked" not in str(report.get("error", ""))


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------

def test_format_handoff_report_text_and_json(tmp_path):
    _build_project(tmp_path, chapters=(1, 2), bodies=(1,))

    report = check_handoff(tmp_path, "volume_to_chapter", target=2)
    text = format_handoff_report(report)
    assert "handoff check" in text
    assert "node: volume_to_chapter" in text
    assert "scope:" in text

    payload = json.loads(format_handoff_report(report, "json"))
    assert payload["node"] == "volume_to_chapter"


def test_format_handoff_report_prints_error(tmp_path):
    text = format_handoff_report({"ok": False, "node": "bogus", "errors": ["未知方向透传节点：bogus"]})

    assert "ERROR" in text
    assert "未知方向透传节点" in text
