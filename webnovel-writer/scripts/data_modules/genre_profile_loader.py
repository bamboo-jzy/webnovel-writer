#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
genre_profile_loader.py - 从 references/genre-profiles.md 解析题材阈值

背景（2026-09-18）：
    节奏类阈值此前**只存在于 config.py 的硬编码默认值**（`strand_quest_max_consecutive=5`
    等），而 `references/genre-profiles.md` 按题材定义了另一套（悬疑/推理：`strand_quest_max: 8`）。
    两者从未对接，导致 `status_reporter` 用通用阈值判所有题材 → 误报。

    本模块提供唯一的题材阈值读取入口，供 `status_reporter` 与 `scope_audit` 共用。

约定：
    - 零新依赖：`genre-profiles.md` 的 profile 块只用到两层键值，手写极简解析即可
      （项目未声明 PyYAML 依赖，见 `scripts/requirements.txt`）。
    - 路径解析沿用仓库既有写法 `Path(__file__).resolve().parents[2] / "references"`，
      可用环境变量 `WEBNOVEL_GENRE_PROFILES` 覆盖（测试用）。
    - **永不抛异常**：读不到 / 解析失败一律回落到 `config` 默认值并把 `source` 标为
      `config_default`，让上游能如实告诉作者「阈值来源不明」。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = [
    "GenreProfileMatch",
    "build_project_info",
    "default_profiles_path",
    "load_genre_profiles",
    "resolve_genre_profile",
    "resolve_pacing_thresholds",
]

_PROFILE_HEADER_RE = re.compile(r"^#{2,4}\s")
_NUM_RE = re.compile(r"^-?\d+$")
_FLOAT_RE = re.compile(r"^-?\d+\.\d+$")


def default_profiles_path() -> Path:
    """返回插件内 `references/genre-profiles.md` 的路径。"""
    override = os.environ.get("WEBNOVEL_GENRE_PROFILES", "").strip()
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[2] / "references" / "genre-profiles.md"


def _coerce(raw: str) -> Any:
    """把 YAML 标量文本转为 Python 值（只处理本文件用到的形态）。"""
    text = raw.strip()
    if not text:
        return ""
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [item.strip().strip("'\"") for item in inner.split(",") if item.strip()]
    if _NUM_RE.match(text):
        return int(text)
    if _FLOAT_RE.match(text):
        return float(text)
    return text.strip("'\"")


def _parse_profile_block(block: str) -> Dict[str, Any]:
    """
    解析单个 profile 的 fenced yaml 块（两层嵌套）。

    只认 `key: value` 与 `key:` + 缩进子键两种形态；第三层及更深的嵌套会被忽略，
    因为 `genre-profiles.md` 的阈值字段都在前两层。
    """
    data: Dict[str, Any] = {}
    current_key: Optional[str] = None

    for raw_line in block.splitlines():
        if not raw_line.strip() or raw_line.strip().startswith("#"):
            continue
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            continue
        if ":" not in stripped:
            continue

        indent = len(raw_line) - len(raw_line.lstrip())
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        if not key or key.startswith("-"):
            continue

        if indent == 0:
            if value == "":
                data[key] = {}
                current_key = key
            else:
                data[key] = _coerce(value)
                current_key = None
            continue

        if current_key is None:
            continue
        bucket = data.get(current_key)
        if not isinstance(bucket, dict):
            continue
        if value == "":
            bucket[key] = {}
        else:
            bucket[key] = _coerce(value)

    return data


def load_genre_profiles(path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """
    解析 `genre-profiles.md`，返回 `{profile_id: {...,'_name': str}}`。

    读不到或解析不出任何 profile 时返回空字典（调用方负责回落）。
    """
    profile_path = path or default_profiles_path()
    try:
        text = profile_path.read_text(encoding="utf-8")
    except OSError:
        return {}

    profiles: Dict[str, Dict[str, Any]] = {}
    header: Optional[str] = None
    buffer: List[str] = []

    def _flush() -> None:
        if header is None:
            return
        block = "\n".join(buffer)
        data = _parse_profile_block(block)
        profile_id = str(data.get("id") or "").strip()
        if not profile_id:
            return
        name = str(data.get("name") or "").strip()
        if not name:
            # 兜底：从 "### 2.4 悬疑/推理 (mystery)" 里取中文名
            match = re.match(r"^#{2,4}\s*\S*\s*(.+?)\s*\([^)]*\)\s*$", header)
            name = match.group(1).strip() if match else header.lstrip("# ").strip()
        data["_name"] = name
        data["_header"] = header
        profiles[profile_id] = data

    for line in text.splitlines():
        if _PROFILE_HEADER_RE.match(line):
            _flush()
            header = line.strip()
            buffer = []
            continue
        if header is not None:
            buffer.append(line)
    _flush()

    return profiles


@dataclass(frozen=True)
class GenreProfileMatch:
    """题材匹配结果。`matched_by` 说明了为什么选中它，便于报告如实标注。"""

    profile_id: str
    name: str
    data: Dict[str, Any]
    matched_value: str
    matched_by: str      # "route" | "template" | "canonical" | "label"
    score: int

    def get(self, section: str, key: str) -> Any:
        bucket = self.data.get(section)
        if not isinstance(bucket, dict):
            return None
        return bucket.get(key)


def _candidate_tokens(project_info: Dict[str, Any]) -> List[tuple[str, str]]:
    """
    按优先级产出 (值, 来源) 候选，来源用于报告标注。

    优先级：题材标签（route/template）> 正典题材 > 题材标签串。
    标签更具体（如「规则怪谈」），正典更宽（如「悬疑」），故标签优先。
    """
    candidates: List[tuple[str, str]] = []
    tags = project_info.get("genre_tags")
    if isinstance(tags, dict):
        for tag_key, source in (("route", "route"), ("templates", "template")):
            values = tags.get(tag_key)
            if isinstance(values, (list, tuple)):
                for value in values:
                    text = str(value or "").strip()
                    if text:
                        candidates.append((text, source))
            elif isinstance(values, str) and values.strip():
                candidates.append((values.strip(), source))

    for key in ("genre",):
        text = str(project_info.get(key) or "").strip()
        if text:
            candidates.append((text, "canonical"))

    label = str(project_info.get("genre_label") or "").strip()
    for part in re.split(r"[+/、,，]", label):
        part = part.strip()
        if part:
            candidates.append((part, "label"))

    return candidates


def _score_profile(profile_id: str, name: str, candidate: str) -> int:
    """给单个 profile 对单个候选值打分；0 表示不匹配。"""
    cand = candidate.strip().lower()
    if not cand:
        return 0
    if cand == profile_id.lower():
        return 100
    name_tokens = [token.strip().lower() for token in re.split(r"[/／]", name) if token.strip()]
    if cand in name_tokens:
        return 90
    if cand in name.lower():
        return 70
    if profile_id.lower() in cand:
        return 50
    return 0


def resolve_genre_profile(
    project_info: Optional[Dict[str, Any]],
    profiles: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Optional[GenreProfileMatch]:
    """
    为项目的 `project_info` 选出题材 profile。选不中返回 None（调用方回落 config）。
    """
    info = project_info if isinstance(project_info, dict) else {}
    table = profiles if profiles is not None else load_genre_profiles()
    if not table:
        return None

    best: Optional[GenreProfileMatch] = None
    for candidate, source in _candidate_tokens(info):
        for profile_id, data in table.items():
            name = str(data.get("_name") or profile_id)
            score = _score_profile(profile_id, name, candidate)
            if score <= 0:
                continue
            if best is None or score > best.score:
                best = GenreProfileMatch(
                    profile_id=profile_id,
                    name=name,
                    data=data,
                    matched_value=candidate,
                    matched_by=source,
                    score=score,
                )
    return best


def resolve_pacing_thresholds(
    project_info: Optional[Dict[str, Any]],
    config: Any,
    profiles: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    合并「题材 profile 阈值」与「config 硬编码默认值」，产出可用阈值集。

    返回键：
        strand_quest_max_consecutive / strand_fire_max_gap / strand_constellation_max_gap
        stagnation_threshold / transition_max_consecutive
        source             —— "genre_profile:<id>" 或 "config_default"
        profile_id / profile_name / matched_by / matched_value
        overridden         —— 被题材值覆盖掉的字段名列表（用于报告如实披露）
    """
    match = resolve_genre_profile(project_info, profiles)

    resolved: Dict[str, Any] = {
        "strand_quest_max_consecutive": int(config.strand_quest_max_consecutive),
        "strand_fire_max_gap": int(config.strand_fire_max_gap),
        "strand_constellation_max_gap": int(config.strand_constellation_max_gap),
        "stagnation_threshold": None,
        "transition_max_consecutive": None,
        "source": "config_default",
        "profile_id": None,
        "profile_name": None,
        "matched_by": None,
        "matched_value": None,
        "overridden": [],
    }

    if match is None:
        return resolved

    mapping = {
        "strand_quest_max_consecutive": "strand_quest_max",
        "strand_fire_max_gap": "strand_fire_gap_max",
        "stagnation_threshold": "stagnation_threshold",
        "transition_max_consecutive": "transition_max_consecutive",
    }
    for target_key, profile_key in mapping.items():
        value = match.get("pacing_config", profile_key)
        if isinstance(value, int) and not isinstance(value, bool):
            if resolved.get(target_key) != value:
                resolved["overridden"].append(target_key)
            resolved[target_key] = value

    # Constellation 断档：genre-profiles 未定义该字段，保留 config 默认值并如实标注
    resolved["source"] = f"genre_profile:{match.profile_id}"
    resolved["profile_id"] = match.profile_id
    resolved["profile_name"] = match.name
    resolved["matched_by"] = match.matched_by
    resolved["matched_value"] = match.matched_value
    return resolved


def build_project_info(state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """从 state 里取出 `project_info`，兼容扁平与嵌套两种历史形态。"""
    if not isinstance(state, dict):
        return {}
    info = state.get("project_info")
    if isinstance(info, dict) and info:
        return info
    return {
        "genre": state.get("genre", ""),
        "genre_label": state.get("genre_label", ""),
        "genre_tags": state.get("genre_tags", {}),
    }
