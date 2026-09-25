#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""四层方向透传（总纲 → 卷纲 → 章纲 → 正文）。

四层是单向关联：总纲指导卷纲，卷纲指导章纲，章纲指导正文。
任一层变化时，必须检查它与紧邻上下层是否仍同向；不同向时由作者裁决改哪一侧，
裁决结果在相邻节点间逐级上溯。

三个方向透传节点：

| 节点 | 依据（上游） | 产物（下游） | 可改单元 | 既定事实 |
|---|---|---|---|---|
| master_to_volume  | 总纲 | 卷纲 | 最后一卷 | 其余已规划卷 |
| volume_to_chapter | 卷纲 | 章纲 | 最后一章 | 其余已规划章 |
| chapter_to_body   | 章纲 | 正文 | 最后一章 | 其余已有正文章 |

统一规律：每层的可改单元只有一个——最后一个；其余是既定事实。改动一旦溢出
可改单元，就必须先上溯到上一层节点裁决，不得直接写文件。

本模块只做三件事：

1. ``describe_handoff_scope``：给出该节点的可改单元与既定事实清单（机器可判定）。
2. ``check_handoff``：采集两侧的结构化锚点，输出候选偏离（机器候选 + 须裁决项）。
3. ``record_handoff``：把作者裁决落盘到 ``.story-system/handoffs/``。

刻意不做的事：不做语义等价判定。总纲与卷纲是散文，机器只能给出「锚点差集」，
是否真的方向不一致必须由作者裁决。候选清单不是硬判定，不得据此自动改文件。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

try:
    from chapter_outline_loader import (
        find_chapter_outline_file,
        load_chapter_execution_directive,
        load_chapter_plot_structure,
        volume_planning_revision,
    )
    from security_utils import AtomicWriteError, atomic_write_json, create_secure_directory
except ImportError:  # pragma: no cover
    from scripts.chapter_outline_loader import (
        find_chapter_outline_file,
        load_chapter_execution_directive,
        load_chapter_plot_structure,
        volume_planning_revision,
    )
    from scripts.security_utils import AtomicWriteError, atomic_write_json, create_secure_directory

from .volume_planning import volume_artifact_paths

# ---------------------------------------------------------------------------
# 节点与裁决口径
# ---------------------------------------------------------------------------

HANDOFF_NODES = ("master_to_volume", "volume_to_chapter", "chapter_to_body")

# 三个节点共用同一套裁决值：方向不一致时，改哪一侧。
HANDOFF_DECISIONS = frozenset({"align_downstream", "align_upstream", "accepted_deviation"})

# 章-正节点沿用既有的章级裁决值，语义一一对应；读入时归一。
DECISION_ALIASES = {
    "outline_to_body": "align_downstream",
    "body_to_outline": "align_upstream",
}

DECISION_MEANINGS = {
    "align_downstream": "改下游产物以符合上游依据",
    "align_upstream": "改上游依据以符合下游产物（需上溯到上一层节点）",
    "accepted_deviation": "接受偏离，记录裁决，不改任何文件",
}

NODE_SPECS: dict[str, dict[str, str]] = {
    "master_to_volume": {
        "upstream_label": "总纲",
        "downstream_label": "卷纲",
        "unit_label": "卷",
        "editable_rule": "只能修改最后一卷的卷纲；其余已规划卷是既定事实",
        "downstream_skill": "/webnovel-volume-revise {target}",
        "upstream_skill": "/webnovel-outline-revise",
    },
    "volume_to_chapter": {
        "upstream_label": "卷纲",
        "downstream_label": "章纲",
        "unit_label": "章",
        "editable_rule": "只能修改最后一章的章纲；其余已规划章是既定事实",
        "downstream_skill": "/webnovel-chapter-revise {target}",
        "upstream_skill": "/webnovel-volume-revise",
    },
    "chapter_to_body": {
        "upstream_label": "章纲",
        "downstream_label": "正文",
        "unit_label": "章",
        "editable_rule": "只能修改最后一章正文；其余已有正文章是既定事实",
        "downstream_skill": "/webnovel-chapter-reload {target}",
        "upstream_skill": "/webnovel-chapter-revise",
    },
}

_UPSTREAM_NODE = {
    "volume_to_chapter": "master_to_volume",
    "chapter_to_body": "volume_to_chapter",
}

_CHAPTER_RANGE_RE = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")
_VOLUME_TABLE_HEADER = re.compile(r"^\s*\|\s*卷号\s*\|")
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}")
_QUOTED_RE = re.compile(r"[《「『\"“]([^》」』\"”]{2,20})[》」』\"”]")
_STOPWORDS = frozenset({
    "本章", "本卷", "必须", "需要", "以及", "或者", "但是", "并且", "因此", "所以",
    "目标", "阻力", "代价", "时间", "锚点", "钩子", "爽点", "节点", "结论", "建议",
    "原因", "风险", "待定", "不适用", "作者", "确认", "议题", "取舍", "说明",
})


def normalize_decision(value: object) -> str:
    """把章级旧值与统一三值归一；无法识别时返回空串。"""
    text = str(value or "").strip()
    if text in HANDOFF_DECISIONS:
        return text
    return DECISION_ALIASES.get(text, "")


# ---------------------------------------------------------------------------
# 基础读取
# ---------------------------------------------------------------------------


def _read_json(path: Path, *, optional: bool = True) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""


def _state(root: Path) -> dict[str, Any]:
    return _read_json(root / ".webnovel" / "state.json")


def _progress(state: dict[str, Any]) -> dict[str, Any]:
    progress = state.get("progress")
    return progress if isinstance(progress, dict) else {}


def _planned_volume_ids(state: dict[str, Any]) -> list[int]:
    rows = _progress(state).get("volumes_planned")
    if not isinstance(rows, list):
        return []
    return sorted({int(row["volume"]) for row in rows if isinstance(row, dict) and row.get("volume")})


def _planned_chapter_ids(root: Path, state: dict[str, Any]) -> list[int]:
    rows = _progress(state).get("chapters_planned")
    found: set[int] = set()
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and row.get("chapter"):
                found.add(int(row["chapter"]))
    outline_dir = root / "大纲"
    if outline_dir.is_dir():
        for pattern in ("第*章*.md",):
            for path in outline_dir.glob(pattern):
                number = _chapter_number_from_name(path.name)
                if number:
                    found.add(number)
    return sorted(found)


def _chapter_number_from_name(name: str) -> int:
    match = re.match(r"^第0*(\d+)章", name)
    return int(match.group(1)) if match else 0


def _body_chapter_ids(root: Path) -> list[int]:
    found: set[int] = set()
    for folder in ("正文", ".story-system/commits"):
        directory = root / folder
        if not directory.is_dir():
            continue
        for path in directory.glob("*"):
            number = _chapter_number_from_name(path.name)
            if number:
                found.add(number)
    return sorted(found)


def _range_label(chapters: list[int]) -> str:
    if not chapters:
        return ""
    low, high = min(chapters), max(chapters)
    return str(low) if low == high else f"{low}-{high}"


# ---------------------------------------------------------------------------
# 节点 1：describe_handoff_scope
# ---------------------------------------------------------------------------


def _resolve_units(root: Path, node: str, state: dict[str, Any]) -> dict[str, Any]:
    """按节点解析「已存在的单位」集合，进而划出可改单元与既定事实。"""
    if node == "master_to_volume":
        units = _planned_volume_ids(state)
        artifacts = {"volumes_all": units}
    elif node == "volume_to_chapter":
        units = _planned_chapter_ids(root, state)
        artifacts = {"outlines": units}
    elif node == "chapter_to_body":
        units = _body_chapter_ids(root)
        artifacts = {"bodies": units}
    else:
        return {"units": [], "artifacts": {}}
    return {"units": units, "artifacts": artifacts}


def body_edit_boundary(project_root: str | Path, chapter: int) -> dict[str, Any]:
    """章-正节点的末端约束：只允许改最后一章正文。

    只有**已存在正文或 commit** 的章才受约束；尚无正文的章属于首次写作，
    不在本节点边界内（写作流程自己管）。返回 ``allowed=False`` 时调用方应阻断。
    """
    root = Path(project_root)
    existing = _body_chapter_ids(root)
    last = max(existing) if existing else 0
    chapter = int(chapter)
    allowed = chapter not in existing or chapter == last
    blocker: dict[str, Any] = {}
    if not allowed:
        blocker = {
            "code": "handoff_target_locked",
            "message": (
                f"正文只能修改最后一章：第 {chapter} 章已有正文或 commit，"
                f"而当前最后一章是第 {last} 章。第 {chapter} 章的正文是既定事实。"
            ),
            "last_chapter": last,
            "locked_chapters": [value for value in existing if value != last],
            "manual_channel": (
                "机器通道只支持最后一章；第 N 章正文如需改写，只能按章顺序人工处理，"
                "或先上溯方向检查：handoff --node chapter_to_body --target "
                f"{chapter}"
            ),
        }
    return {
        "allowed": allowed,
        "chapter": chapter,
        "existing_chapters": existing,
        "last_chapter": last,
        "blocker": blocker,
    }


def describe_handoff_scope(
    project_root: str | Path, node: str, *, target: int = 0,
) -> dict[str, Any]:
    """划出该节点的可改单元与既定事实，并判定 target 是否落在可改范围内。"""
    root = Path(project_root)
    resolved = str(node or "").strip()
    if resolved not in HANDOFF_NODES:
        return {
            "ok": False,
            "node": resolved,
            "errors": [f"未知方向透传节点：{resolved or '(空)'}"],
            "known_nodes": list(HANDOFF_NODES),
        }

    spec = NODE_SPECS[resolved]
    state = _state(root)
    units = _resolve_units(root, resolved, state)

    existing = units["units"]
    editable = [max(existing)] if existing else []
    locked = [value for value in existing if value not in editable]

    report: dict[str, Any] = {
        "ok": True,
        "node": resolved,
        "action": "scope",
        "upstream_label": spec["upstream_label"],
        "downstream_label": spec["downstream_label"],
        "unit_label": spec["unit_label"],
        "editable_rule": spec["editable_rule"],
        "existing_units": existing,
        "editable_units": editable,
        "editable_range": _range_label(editable),
        "locked_units": locked,
        "locked_range": _range_label(locked),
        "locked_artifacts": units["artifacts"],
        "downstream_skill": spec["downstream_skill"],
        "upstream_skill": spec["upstream_skill"],
    }

    if target:
        target = int(target)
        report.update(target=target, target_allowed=target in editable)
        if target not in existing:
            # 尚无产物的单位（例如还没写正文的章）不在本节点边界内，交由下游流程处理。
            report["target_allowed"] = True
            report["target_pending"] = True
        if not report["target_allowed"]:
            report["blockers"] = [{
                "code": "handoff_target_locked",
                "message": (
                    f"第 {target} {spec['unit_label']}不在可改单元内。"
                    f"{spec['editable_rule']}；当前可改单元为 "
                    f"{report['editable_range'] or '(无)'}。"
                ),
            }]

    parts = [
        f"{resolved}：上游依据为{spec['upstream_label']}，下游产物为{spec['downstream_label']}。",
        f"{spec['editable_rule']}。",
    ]
    if editable:
        parts.append(f"当前可改单元：第 {report['editable_range']} {spec['unit_label']}。")
    else:
        parts.append(f"当前没有可改的{spec['unit_label']}（尚未规划）。")
    if locked:
        parts.append(f"既定事实：第 {report['locked_range']} {spec['unit_label']}，本次不得修改。")
    if report.get("target_pending"):
        parts.append(
            f"第 {report.get('target')} {spec['unit_label']}尚无下游产物，属于首次生成，不在本节点边界内。"
        )
    report["scope_declaration"] = "".join(parts)
    return report


# ---------------------------------------------------------------------------
# 节点 2：check_handoff（锚点采集 + 候选偏离）
# ---------------------------------------------------------------------------


def _terms(text: str) -> set[str]:
    words = {match.group(0) for match in _TOKEN_RE.finditer(text or "")}
    return {word for word in words if word not in _STOPWORDS}


def _quoted_terms(text: str) -> set[str]:
    return {match.group(1).strip() for match in _QUOTED_RE.finditer(text or "")}


def _master_outline_rows(root: Path) -> dict[int, list[str]]:
    """解析 大纲/总纲.md 的卷表，返回 {卷号: [卷名, 章节范围, 核心冲突, 卷末高潮]}。"""
    lines = _read_text(root / "大纲" / "总纲.md").splitlines()
    header = next((i for i, line in enumerate(lines) if _VOLUME_TABLE_HEADER.match(line)), None)
    if header is None:
        return {}
    rows: dict[int, list[str]] = {}
    for line in lines[header + 2:]:
        if not line.strip().startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not cells or not cells[0].isdigit():
            continue
        rows[int(cells[0])] = cells[1:5]
    return rows


def _volume_entry(state: dict[str, Any], volume: int) -> dict[str, Any]:
    rows = _progress(state).get("volumes_planned")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and row.get("volume") == int(volume):
                return row
    return {}


def _chapter_entry(state: dict[str, Any], chapter: int) -> dict[str, Any]:
    rows = _progress(state).get("chapters_planned")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and row.get("chapter") == int(chapter):
                return row
    return {}


def _volume_number_for_chapter(state: dict[str, Any], chapter: int) -> int:
    entry = _chapter_entry(state, chapter)
    if entry.get("volume"):
        return int(entry["volume"])
    rows = _progress(state).get("volumes_planned")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            matched = _CHAPTER_RANGE_RE.match(str(row.get("chapters_range") or ""))
            if matched and int(matched.group(1)) <= chapter <= int(matched.group(2)):
                return int(row["volume"])
    return 0


def _upstream_text(root: Path, node: str, target: int, state: dict[str, Any]) -> tuple[str, list[str]]:
    """采集上游依据的全文与来源文件清单。"""
    sources: list[str] = []
    chunks: list[str] = []
    if node == "master_to_volume":
        path = root / "大纲" / "总纲.md"
        if path.is_file():
            sources.append("大纲/总纲.md")
            chunks.append(_read_text(path))
    elif node == "volume_to_chapter":
        volume = _volume_number_for_chapter(state, target)
        if volume:
            for label, path in volume_artifact_paths(root, volume).items():
                if path.is_file():
                    sources.append(f"大纲/{path.name}")
                    chunks.append(_read_text(path))
            card = root / "大纲" / f"第{volume}卷-章纲规划讨论.md"
            if card.is_file():
                sources.append(f"大纲/{card.name}")
                chunks.append(_read_text(card))
    elif node == "chapter_to_body":
        outline_path, _ = find_chapter_outline_file(root, target)
        if outline_path is not None and outline_path.is_file():
            sources.append(f"大纲/{outline_path.name}")
            chunks.append(_read_text(outline_path))
    return "\n".join(chunks), sources


def _downstream_anchors(root: Path, node: str, target: int, state: dict[str, Any]) -> dict[str, Any]:
    """采集下游产物的结构化锚点。"""
    anchors: dict[str, Any] = {}
    if node == "master_to_volume":
        entry = _volume_entry(state, target)
        anchors["state_chapters_range"] = str(entry.get("chapters_range") or "")
        anchors["planning_revision"] = str(entry.get("planning_revision") or "")
        paths = volume_artifact_paths(root, target)
        anchors["files"] = {label: path.is_file() for label, path in paths.items()}
        writeback = root / "大纲" / f"第{target}卷-总纲写回.json"
        anchors["writeback_present"] = writeback.is_file()
        if writeback.is_file():
            payload = _read_json(writeback)
            raw = payload.get("next_volume_anchor")
            anchors["writeback_anchor"] = raw if isinstance(raw, dict) else {}
    elif node == "volume_to_chapter":
        entry = _chapter_entry(state, target)
        anchors["source_volume_revision"] = str(entry.get("source_volume_revision") or "")
        anchors["chapter_outline_revision"] = str(entry.get("chapter_outline_revision") or "")
        anchors["key_entities"] = load_chapter_execution_directive(root, target).get("key_entities") or []
        structure = load_chapter_plot_structure(root, target)
        anchors["cbn"] = structure.get("cbn") or ""
        anchors["cpns"] = structure.get("cpns") or []
        anchors["cen"] = structure.get("cen") or ""
    elif node == "chapter_to_body":
        outline_path, _ = find_chapter_outline_file(root, target)
        anchors["outline_file"] = outline_path.name if outline_path else ""
        structure = load_chapter_plot_structure(root, target)
        anchors["planned_nodes"] = [
            value for value in [structure.get("cbn"), *(structure.get("cpns") or []), structure.get("cen")]
            if value
        ]
        artifacts = root / ".webnovel" / "tmp" / "fulfillment_result.json"
        anchors["fulfillment_present"] = artifacts.is_file()
    return anchors


def _master_to_volume_candidates(
    root: Path, target: int, state: dict[str, Any], anchors: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    manual: list[dict[str, Any]] = []
    rows = _master_outline_rows(root)
    if not rows:
        candidates.append({
            "kind": "missing_upstream_table",
            "message": "大纲/总纲.md 未找到「## 卷划分」卷表，无法机器比对卷号与章节范围。",
        })
    elif target not in rows:
        candidates.append({
            "kind": "volume_absent_in_master",
            "message": f"总纲卷表中没有第 {target} 卷，卷纲属于超出总纲的规划。",
        })
    else:
        cells = rows[target]
        state_range = anchors.get("state_chapters_range", "")
        master_range = cells[1] if len(cells) > 1 else ""
        if state_range and master_range and state_range != master_range:
            candidates.append({
                "kind": "chapters_range_mismatch",
                "message": f"章节范围不一致：总纲为 {master_range}，卷纲登记为 {state_range}。",
            })
        writeback = anchors.get("writeback_anchor") or {}
        if isinstance(writeback, dict) and writeback:
            writeback_range = str(writeback.get("chapters_range") or "").strip()
            if writeback_range and master_range and writeback_range != master_range:
                candidates.append({
                    "kind": "writeback_range_mismatch",
                    "message": (
                        f"总纲写回 JSON 的章节范围 {writeback_range} 与总纲卷表 {master_range} 不一致。"
                    ),
                })
        manual.append({
            "kind": "semantic_alignment",
            "fields": ["卷名", "核心冲突", "卷末高潮"],
            "upstream": {"卷名": cells[0] if cells else "", "核心冲突": cells[2] if len(cells) > 2 else "",
                         "卷末高潮": cells[3] if len(cells) > 3 else ""},
            "message": "总纲卷表的卷名、核心冲突、卷末高潮是散文表述，须作者确认卷纲是否仍与之同向。",
        })
    return candidates, manual


def _volume_to_chapter_candidates(
    root: Path, target: int, state: dict[str, Any], anchors: dict[str, Any], upstream_text: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    manual: list[dict[str, Any]] = []
    volume = _volume_number_for_chapter(state, target)
    if volume:
        current = volume_planning_revision(root, volume)
        recorded = anchors.get("source_volume_revision", "")
        if current and recorded and current != recorded:
            candidates.append({
                "kind": "volume_revision_stale",
                "message": (
                    f"本章登记的卷纲 revision 为 {recorded}，当前卷纲为 {current}；"
                    "章纲基于旧卷纲，须重过方向检查。"
                ),
            })
    upstream_terms = _terms(upstream_text)
    key_entities = [str(value) for value in anchors.get("key_entities") or []]
    missing = [value for value in key_entities if value and value not in upstream_terms]
    if missing:
        candidates.append({
            "kind": "outline_entity_not_in_upstream",
            "items": missing,
            "message": "章纲的「关键实体」在上游卷纲/决策卡中找不到对应表述，可能是章纲超出卷纲方向。",
        })
    quoted = _quoted_terms(upstream_text)
    node_text = " ".join([
        str(anchors.get("cbn") or ""),
        *[str(value) for value in anchors.get("cpns") or []],
        str(anchors.get("cen") or ""),
    ])
    uncovered = [value for value in sorted(quoted) if value not in node_text]
    if uncovered:
        manual.append({
            "kind": "upstream_term_not_in_outline",
            "items": uncovered[:20],
            "message": "卷纲/决策卡中的专名未在本章 CBN/CPNs/CEN 出现，属低置信提示，须上下文判断。",
        })
    return candidates, manual


def _chapter_to_body_candidates(
    root: Path, target: int, state: dict[str, Any], anchors: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from .chapter_commit_schema import fulfillment_deviation_items

    candidates: list[dict[str, Any]] = []
    manual: list[dict[str, Any]] = []
    if not anchors.get("fulfillment_present"):
        manual.append({
            "kind": "fulfillment_missing",
            "message": (
                "尚未生成 .webnovel/tmp/fulfillment_result.json；"
                "须先运行 review-pipeline 与 data-agent 才可做章-正方向比对。"
            ),
        })
        return candidates, manual
    payload = _read_json(root / ".webnovel" / "tmp" / "fulfillment_result.json")
    for item in fulfillment_deviation_items(payload):
        candidates.append({
            "kind": "fulfillment_deviation",
            "status": str(item.get("status") or ""),
            "node": str(item.get("node") or ""),
            "message": f"正文对章纲节点的履约状态为 {item.get('status')}：{item.get('node')}",
        })
    return candidates, manual


def check_handoff(project_root: str | Path, node: str, *, target: int = 0) -> dict[str, Any]:
    """采集两侧锚点并输出候选偏离；结论一律须作者裁决。"""
    root = Path(project_root)
    scope = describe_handoff_scope(root, node, target=target)
    if not scope.get("ok"):
        return {**scope, "action": "check"}
    if not target:
        return {
            **scope,
            "action": "check",
            "ok": False,
            "errors": ["方向检查需要一个明确的 target（卷号或章号）"],
        }
    if not scope.get("target_allowed"):
        return {
            **scope,
            "action": "check",
            "ok": False,
            "candidates": [],
            "manual_items": [],
            "errors": [item["message"] for item in scope.get("blockers") or []],
        }

    resolved = str(node)
    target = int(target)
    state = _state(root)
    anchors = _downstream_anchors(root, resolved, target, state)
    upstream_text, upstream_sources = _upstream_text(root, resolved, target, state)

    if resolved == "master_to_volume":
        candidates, manual = _master_to_volume_candidates(root, target, state, anchors)
    elif resolved == "volume_to_chapter":
        candidates, manual = _volume_to_chapter_candidates(root, target, state, anchors, upstream_text)
    else:
        candidates, manual = _chapter_to_body_candidates(root, target, state, anchors)

    report = {
        **scope,
        "action": "check",
        "upstream_sources": upstream_sources,
        "downstream_anchors": anchors,
        "candidates": candidates,
        "manual_items": manual,
        "aligned": not candidates,
    }
    report["next_steps"] = _next_steps(resolved, target, report, state)
    return report


def _upstream_target(node: str, target: int, state: dict[str, Any]) -> int:
    """上溯到上一层节点时换算单位：卷-章节点的 target 是章号，总-卷节点要卷号。"""
    if node == "volume_to_chapter":
        return _volume_number_for_chapter(state, target) or target
    return target


def _next_steps(node: str, target: int, report: dict[str, Any], state: dict[str, Any]) -> list[str]:
    spec = NODE_SPECS[node]
    if report.get("aligned"):
        return [
            "候选偏离为空；仍需按「须裁决项」逐项与作者确认散文层是否同向。",
            f"确认同向后继续：{spec['downstream_skill'].format(target=target)}",
        ]
    steps = [
        f"与作者逐项裁决候选偏离；裁决口径：{DECISION_MEANINGS['align_downstream']} / "
        f"{DECISION_MEANINGS['align_upstream']} / {DECISION_MEANINGS['accepted_deviation']}。",
        f"选 align_downstream → {spec['downstream_skill'].format(target=target)}",
    ]
    upstream_node = _UPSTREAM_NODE.get(node)
    if upstream_node:
        upstream_target = _upstream_target(node, target, state)
        upstream_label = NODE_SPECS[upstream_node]["downstream_label"]
        steps.append(
            f"选 align_upstream → 先过上一层节点检查：handoff --node {upstream_node} "
            f"--target {upstream_target}，再由 {spec['upstream_skill']} 执行{upstream_label}改动。"
        )
    else:
        steps.append(f"选 align_upstream → {spec['upstream_skill']} 修改{spec['upstream_label']}")
    steps.append("裁决完成后用 handoff --record 落盘，输入变化后 token 失效需重新预览。")
    return steps


# ---------------------------------------------------------------------------
# 节点 3：record_handoff
# ---------------------------------------------------------------------------


def _handoff_dir(root: Path) -> Path:
    return root / ".story-system" / "handoffs"


def _binding_digest(scope: dict[str, Any], check: dict[str, Any]) -> str:
    """把「当时的边界快照 + 上游来源 + 候选偏离」绑成一个 token。

    任一项变化（例如在上游改了文件、多了一章、候选偏离消失）token 立即失效。
    """
    payload = {
        "node": scope.get("node"),
        "target": scope.get("target"),
        "editable_units": scope.get("editable_units"),
        "locked_units": scope.get("locked_units"),
        "upstream_sources": check.get("upstream_sources"),
        "candidates": check.get("candidates"),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def latest_handoff_record(root: Path, node: str, target: int) -> dict[str, Any]:
    directory = _handoff_dir(root)
    if not directory.is_dir():
        return {}
    records: list[dict[str, Any]] = []
    for path in directory.glob("*.json"):
        payload = _read_json(path)
        if payload.get("node") == node and int(payload.get("target") or 0) == int(target):
            records.append(payload)
    if not records:
        return {}
    records.sort(key=lambda item: str(item.get("confirmed_at") or ""))
    return records[-1]


def record_handoff(
    project_root: str | Path,
    node: str,
    *,
    target: int = 0,
    decision: str = "",
    reason: str = "",
    impact: list[int] | None = None,
    expected_input: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    """预览或记录一次方向透传裁决。只写 .story-system/handoffs/，不改任何创作文件。"""
    root = Path(project_root)
    report: dict[str, Any] = {
        "ok": False,
        "action": "reconcile",
        "node": str(node or ""),
        "target": int(target or 0),
        "dry_run": dry_run,
    }
    try:
        check = check_handoff(root, node, target=target) if target else describe_handoff_scope(root, node)
        if not check.get("ok"):
            raise ValueError("; ".join(check.get("errors") or ["方向检查未通过"]))
        token = _binding_digest(check, check)
        report.update(
            input_token=token,
            candidates=check.get("candidates") or [],
            manual_items=check.get("manual_items") or [],
            upstream_sources=check.get("upstream_sources") or [],
            scope_declaration=check.get("scope_declaration", ""),
        )
        if dry_run:
            report["ok"] = True
            return report
        normalized = normalize_decision(decision)
        if not normalized:
            raise ValueError(
                "reconciliation requires an explicit decision: "
                + " / ".join(sorted(HANDOFF_DECISIONS))
            )
        if not str(reason).strip():
            raise ValueError("reconciliation requires a non-empty reason")
        if impact is None or any(type(value) is not int or value <= 0 for value in impact):
            raise ValueError("reconciliation requires an explicit list of affected unit numbers")
        if not expected_input or expected_input != token:
            raise ValueError("handoff_confirmation_required: preview and confirm the exact input_token")

        record = {
            "decision_id": uuid4().hex,
            "node": str(node),
            "target": int(target),
            "decision": normalized,
            "decision_meaning": DECISION_MEANINGS[normalized],
            "reason": str(reason).strip(),
            "impact": sorted({int(value) for value in impact}),
            "confirmed_at": datetime.now(timezone.utc).isoformat(),
            "input_token": token,
            "candidates": check.get("candidates") or [],
            "manual_items": check.get("manual_items") or [],
            "upstream_sources": check.get("upstream_sources") or [],
            "scope_declaration": check.get("scope_declaration", ""),
            "next_steps": check.get("next_steps") or [],
        }
        target_dir = _handoff_dir(root)
        create_secure_directory(str(target_dir))
        atomic_write_json(target_dir / f"{record['decision_id']}.json", record)
        report.update(
            ok=True,
            decision_id=record["decision_id"],
            decision=normalized,
            decision_meaning=record["decision_meaning"],
            reason=record["reason"],
            impact=record["impact"],
            confirmed_at=record["confirmed_at"],
            next_steps=record["next_steps"],
        )
    except (OSError, ValueError, TypeError, AttributeError, KeyError, AtomicWriteError) as exc:
        report["error"] = str(exc)
    return report


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------


def format_handoff_report(report: dict[str, Any], output_format: str = "text") -> str:
    if output_format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2)

    action = str(report.get("action") or "scope")
    lines = [
        f"{'OK' if report.get('ok') else 'ERROR'} handoff {action}",
        f"node: {report.get('node', '')}",
    ]
    if report.get("target"):
        lines.append(f"target: {report.get('target')}")
    if not report.get("ok"):
        error = report.get("error") or "; ".join(report.get("errors") or [])
        lines.append(f"error: {error}")
        return "\n".join(lines)

    if action == "scope":
        lines.extend([
            f"editable: {report.get('editable_range') or '(无)'}",
            f"locked: {report.get('locked_range') or '(无)'}",
            f"scope: {report.get('scope_declaration', '')}",
        ])
        return "\n".join(lines)

    candidates = report.get("candidates") or []
    manual = report.get("manual_items") or []
    lines.append(f"aligned: {report.get('aligned', False)}")
    lines.append(f"candidates: {len(candidates)}")
    for item in candidates:
        lines.append(f"  - [{item.get('kind')}] {item.get('message', '')}")
    lines.append(f"manual_items: {len(manual)}")
    for item in manual:
        lines.append(f"  - [{item.get('kind')}] {item.get('message', '')}")
    if report.get("input_token"):
        lines.append(f"input_token: {report['input_token']}")
    if report.get("decision_id"):
        lines.append(f"decision_id: {report['decision_id']}")
        lines.append(f"decision: {report.get('decision')} ({report.get('decision_meaning', '')})")
    for step in report.get("next_steps") or []:
        lines.append(f"next: {step}")
    lines.append(f"scope: {report.get('scope_declaration', '')}")
    return "\n".join(lines)
