#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..prewrite_validator import PrewriteValidator
from ..project_phase import (
    PHASE_CHAPTER_CONTRACT_READY,
    PHASE_DRAFT_IN_PROGRESS,
    PHASE_READY_TO_COMMIT,
    resolve_project_phase,
)
from ..story_runtime_sources import load_runtime_sources
from . import gate_report, issue


ALLOWED_PREWRITE_PHASES = {
    PHASE_CHAPTER_CONTRACT_READY,
    PHASE_DRAFT_IN_PROGRESS,
    PHASE_READY_TO_COMMIT,
}


def _plot_structure(chapter_contract: dict[str, Any], review_contract: dict[str, Any]) -> dict[str, Any]:
    directive = chapter_contract.get("chapter_directive") if isinstance(chapter_contract, dict) else {}
    if not isinstance(directive, dict):
        directive = {}
    return {
        "mandatory_nodes": list(
            directive.get("must_cover_nodes")
            or directive.get("mandatory_nodes")
            or review_contract.get("must_cover_nodes")
            or review_contract.get("mandatory_nodes")
            or []
        ),
        "prohibitions": list(
            directive.get("forbidden_zones")
            or directive.get("prohibitions")
            or review_contract.get("blocking_rules")
            or []
        ),
    }


def run_prewrite_gate(project_root: Path, chapter: int) -> dict[str, Any]:
    snapshot = resolve_project_phase(project_root, chapter=chapter)
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if snapshot.phase not in ALLOWED_PREWRITE_PHASES:
        errors.append(
            issue(
                "phase_not_ready_for_prewrite",
                message=f"phase {snapshot.phase} is not ready for prewrite",
                impact="写前合同或项目骨架不完整，继续写作容易使用旧上下文或缺失约束。",
                repair="先运行 project-status/doctor，根据 next_action 补齐 init、plan 或 Story System 合同。",
                details=snapshot.to_dict(),
            )
        )

    if snapshot.body_revision_stale or snapshot.body_revision_uncommitted or snapshot.upstream_body_stale or snapshot.body_evidence.get("dependency_stale"):
        affected = snapshot.upstream_body_stale[0] if snapshot.upstream_body_stale else chapter
        errors.append(issue("chapter_body_stale", message="正文或前置章节版本已变更，不得自动覆盖现有正文", repair=f"运行 /webnovel-chapter-reload {affected}", details=snapshot.body_evidence))

    if snapshot.volume_plan_stale:
        errors.append(
            issue(
                "volume_plan_stale",
                message=f"第 {snapshot.target_volume or 1} 卷卷纲已修改，章纲和合同需要重载",
                impact="当前章节记录依赖旧卷纲 revision，继续写作会使用过期的卷级决策。",
                repair=f"先运行 /webnovel-volume-reload {snapshot.target_volume or 1}，再运行 /webnovel-chapter-plan {snapshot.target_volume or 1} {chapter}",
                details={
                    "volume": snapshot.target_volume,
                    "volume_planning_revision": snapshot.volume_planning_revision,
                    "chapter_outline_revision": snapshot.chapter_outline_revision,
                },
            )
        )
    elif "chapter_outline_missing_after_volume_plan" in snapshot.warnings:
        errors.append(
            issue(
                "chapter_outline_missing",
                message=f"第 {chapter} 章尚未完成章纲规划",
                impact="卷纲不能替代章级执行约束，继续写作会缺少本章目标、节点和禁区。",
                repair=f"先运行 /webnovel-chapter-plan {snapshot.target_volume or 1} {chapter}",
                details={
                    "chapter": chapter,
                    "volume": snapshot.target_volume,
                    "outline_source": snapshot.chapter_outline_source,
                },
            )
        )
    elif snapshot.chapter_outline_source == "legacy_volume":
        warnings.append(
            issue(
                "legacy_chapter_outline_fallback",
                message="使用卷级详细大纲中的旧章纲回退",
                severity="warning",
                impact="旧项目尚未迁移为独立章纲文件，章级约束来源较弱。",
                repair=f"可运行 /webnovel-chapter-plan {snapshot.target_volume or 1} {chapter} 生成独立章纲。",
                details={"outline_file": snapshot.chapter_outline_file},
            )
        )

    if snapshot.chapter_contract_stale:
        errors.append(
            issue(
                "chapter_contract_stale",
                message=f"第 {chapter} 章章纲更新晚于 Story System 合同",
                impact="合同不能证明它反映当前章纲，写作上下文可能已经过期。",
                repair=f"先运行 /webnovel-chapter-plan {snapshot.target_volume or 1} {chapter} 刷新合同。",
                details={
                    "outline_file": snapshot.chapter_outline_file,
                    "outline_revision": snapshot.chapter_outline_revision,
                },
            )
        )

    runtime = load_runtime_sources(project_root, chapter)
    contracts = runtime.contracts
    story_contract = {
        "master_setting": contracts.get("master") or {},
        "volume_brief": contracts.get("volume") or {},
        "chapter_brief": contracts.get("chapter") or {},
        "review_contract": contracts.get("review") or {},
    }
    review_contract = contracts.get("review") or {}
    plot_structure = _plot_structure(contracts.get("chapter") or {}, review_contract)

    validation = PrewriteValidator(project_root).build(
        chapter=chapter,
        review_contract=review_contract,
        plot_structure=plot_structure,
        story_contract=story_contract,
    )
    if validation.get("blocking"):
        errors.append(
            issue(
                "prewrite_validator_blocking",
                message="prewrite validator reported blocking issue(s)",
                impact="当前章节写作输入不可信。",
                repair="按 blocking_reasons 补齐合同、消歧 pending 或相关占位符。",
                details=validation,
            )
        )
    elif runtime.fallback_sources:
        warnings.append(
            issue(
                "story_runtime_fallback",
                message="story runtime has fallback sources",
                severity="warning",
                impact="写作上下文可能缺少上一章 accepted commit。",
                repair="确认这是第一章或补齐 accepted commit 后再写。",
                details=list(runtime.fallback_sources),
            )
        )

    return gate_report(
        stage="prewrite",
        project_root=project_root,
        chapter=chapter,
        phase=snapshot.phase,
        errors=errors,
        warnings=warnings,
        details={
            "phase": snapshot.to_dict(),
            "story_runtime": runtime.to_dict(),
            "prewrite_validation": validation,
        },
    )
