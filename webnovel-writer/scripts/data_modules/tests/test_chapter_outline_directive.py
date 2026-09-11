#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json

from chapter_outline_loader import (
    chapter_outline_revision,
    find_chapter_outline_file,
    load_chapter_execution_directive,
    load_chapter_plot_structure,
    volume_planning_revision,
)


def test_load_chapter_execution_directive_from_volume_outline(tmp_path):
    outline_dir = tmp_path / "大纲"
    outline_dir.mkdir()
    (tmp_path / ".webnovel").mkdir()
    (tmp_path / ".webnovel" / "state.json").write_text(
        json.dumps({"progress": {"volumes_planned": [{"volume": 1, "chapters_range": "1-50"}]}}),
        encoding="utf-8",
    )
    (outline_dir / "第1卷-详细大纲.md").write_text(
        "\n".join(
            [
                "### 第一章：债从天降",
                "- 目标：搞清楚借据条款的荒谬",
                "- 阻力：杂役不能随意离开宗门",
                "- 代价：暴露自己懂账",
                "- 时间锚点：D-Day 清晨",
                "- 章内跨度：一炷香",
                "- 倒计时状态：三日内还债",
                "- Strand：债务调查",
                "- 反派层级：小反派",
                "- 关键实体：陆鸣、借据、利息",
                "- CBN：醒来发现债务",
                "- CPNs：检查借据；发现复利陷阱",
                "- CEN：决定去井边打听",
                "- 必须覆盖节点：借据金额；复利算法",
                "- 本章禁区：不得离开宗门；不得提前摊牌",
                "- 章末未闭合问题：谁改了借据？",
                "- 钩子类型：信息钩",
                "- 钩子强度：中",
                "",
                "### 第二章：井边口风",
                "- 目标：打听债主来历",
            ]
        ),
        encoding="utf-8",
    )

    directive = load_chapter_execution_directive(tmp_path, 1)

    assert directive["goal"] == "搞清楚借据条款的荒谬"
    assert directive["time_anchor"] == "D-Day 清晨"
    assert directive["chapter_span"] == "一炷香"
    assert directive["countdown"] == "三日内还债"
    assert directive["cpns"] == ["检查借据", "发现复利陷阱"]
    assert "不得离开宗门" in directive["forbidden_zones"]
    assert "借据" in directive["key_entities"]
    assert directive["chapter_end_open_question"] == "谁改了借据？"


def test_split_chapter_outline_takes_priority_and_exposes_plot_structure(tmp_path):
    outline_dir = tmp_path / "大纲"
    outline_dir.mkdir()
    (tmp_path / ".webnovel").mkdir()
    (tmp_path / ".webnovel" / "state.json").write_text(
        json.dumps({"progress": {"volumes_planned": [{"volume": 1, "chapters_range": "1-50"}]}}),
        encoding="utf-8",
    )
    (outline_dir / "第1卷-详细大纲.md").write_text(
        "### 第一章：旧章\n- 目标：旧目标\n", encoding="utf-8"
    )
    split = outline_dir / "第1章-新章.md"
    split.write_text(
        "\n".join(
            [
                "# 第1章：新章",
                "",
                "## 执行指令",
                "- 目标：新目标",
                "- 关键实体：玉佩、井口",
                "",
                "## 结构化节点",
                "- CBN：发现井口异响",
                "- CPNs：",
                "  1. 检查井壁",
                "  2. 追踪脚印",
                "- CEN：决定封锁井口",
                "- 必须覆盖节点：井壁刻痕；脚印方向",
                "- 本章禁区：不得下井",
            ]
        ),
        encoding="utf-8",
    )

    selected, source = find_chapter_outline_file(tmp_path, 1)
    directive = load_chapter_execution_directive(tmp_path, 1)
    structure = load_chapter_plot_structure(tmp_path, 1)

    assert selected == split
    assert source == "split"
    assert directive["goal"] == "新目标"
    assert directive["key_entities"] == ["玉佩", "井口"]
    assert structure["cbn"] == "发现井口异响"
    assert structure["cpns"] == ["检查井壁", "追踪脚印"]
    assert structure["cen"] == "决定封锁井口"
    assert "不得下井" in structure["prohibitions"]


def test_outline_revisions_are_content_based(tmp_path):
    outline_dir = tmp_path / "大纲"
    outline_dir.mkdir()
    (outline_dir / "第1卷-节拍表.md").write_text("节拍\n", encoding="utf-8")
    (outline_dir / "第1卷-时间线.md").write_text("时间线\n", encoding="utf-8")
    (outline_dir / "第1卷-详细大纲.md").write_text("卷纲\n", encoding="utf-8")
    chapter = outline_dir / "第1章-章.md"
    chapter.write_text("# 第1章：章\n", encoding="utf-8")

    chapter_revision = chapter_outline_revision(tmp_path, 1)
    volume_revision = volume_planning_revision(tmp_path, 1)
    chapter.write_text("# 第1章：更新\n", encoding="utf-8")

    assert chapter_revision
    assert volume_revision
    assert chapter_outline_revision(tmp_path, 1) != chapter_revision
    assert volume_planning_revision(tmp_path, 1) == volume_revision
