#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
from pathlib import Path

import pytest


def _ensure_scripts_on_path() -> None:
    scripts_dir = Path(__file__).resolve().parents[1]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


_ensure_scripts_on_path()

from run_behavior_evals import run_behavior_evals  # noqa: E402


def test_run_behavior_evals_fast_suite_passes_for_current_package():
    root = Path(__file__).resolve().parents[2]

    report = run_behavior_evals(root, suite="fast")

    assert report["ok"] is True
    assert report["total"] >= 5


@pytest.mark.parametrize("relative", [
    "README.md",
    "docs/architecture/overview.md",
    "docs/guides/commands.md",
    "webnovel-writer/README.md",
    "webnovel-writer/skills/webnovel-review/SKILL.md",
])
def test_review_product_docs_match_fact_review_scope(relative):
    root = Path(__file__).resolve().parents[3]
    text = (root / relative).read_text(encoding="utf-8")
    assert "章节事实一致性与逻辑审查" in text
    for dimension in ("setting", "timeline", "continuity", "character", "logic"):
        assert dimension in text
    assert "不评分、不评价文笔、不建议情节改动" in text
    for obsolete in ("以下六个审查维度", "多维质量审查", "从爽点、一致性、节奏、OOC", "Quality Review Skill"):
        assert obsolete not in text
