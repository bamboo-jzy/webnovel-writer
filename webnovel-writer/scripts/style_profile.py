#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
style_profile.py - 文风档案（学习源 A：正文 / 源 B：文风目录）

定位：把「文风」从一次性判断变成**可复用的档案**。

- 源 A `正文/`：**现状画像**（`observed`）——作者实际写成什么样。
  采样口径：只取 `.story-system/commits` 里 `status=accepted` 的章，避免学到
  被推翻 / 未提交的稿；需要扫全部正文时用 `--source all` 显式越权。
- 源 B `文风/`：**目标画像**（`target`）——作者要模仿的参考文本（他人作品/范文/旧作）。

两者语义不同，必须分开存放、分开注入：现状 **不等于** 目标。把现状当标准，
等于把作者正想改掉的毛病固化成规则。

产物（两个，角色不同）：

| 产物 | 角色 |
|------|------|
| `.webnovel/style_profile.json` | 机读单一真相源，含 `injection_digest`（≤1500 字，供任务书注入） |
| `文风/文风档案.md` | 人读。`script-generated` 段由本脚本覆盖写；段外内容（归纳段）**原样保留** |

用法：

    python -X utf8 style_profile.py --project-root <书项目> build [--source accepted|all]
    python -X utf8 style_profile.py --project-root <书项目> show [--format text|json]
    python -X utf8 style_profile.py --project-root <书项目> diff

只读约束：本脚本只读 `正文/`、`文风/`、`.story-system/commits`，只写
`.webnovel/style_profile.json` 与 `文风/文风档案.md` 两个产物；不碰 `index.db`、
`state.json`、正文一个字。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    from runtime_compat import enable_windows_utf8_stdio
except ImportError:  # pragma: no cover
    from scripts.runtime_compat import enable_windows_utf8_stdio

try:
    from project_locator import resolve_project_root
except ImportError:  # pragma: no cover
    from scripts.project_locator import resolve_project_root

try:
    from data_modules.config import DataModulesConfig
except ImportError:  # pragma: no cover
    from scripts.data_modules.config import DataModulesConfig

try:
    from data_modules.project_phase import scan_commits
except ImportError:  # pragma: no cover
    from scripts.data_modules.project_phase import scan_commits

try:
    from data_modules.style_metrics import (
        LONG_SENTENCE_THRESHOLD,
        PROFILE_JSON_NAME,
        PROFILE_SCHEMA_VERSION,
        SAID_TAG_MAX_RATIO,
        STYLE_BASELINE_MIN_CHAPTERS,
        bucket_histogram,
        read_chapter_text,
        repeated_phrases_for_text,
        sentence_lengths,
        split_paragraphs,
        style_metrics_for_text,
        summarize_chapters,
        top_phrases,
    )
except ImportError:  # pragma: no cover
    from scripts.data_modules.style_metrics import (
        LONG_SENTENCE_THRESHOLD,
        PROFILE_JSON_NAME,
        PROFILE_SCHEMA_VERSION,
        SAID_TAG_MAX_RATIO,
        STYLE_BASELINE_MIN_CHAPTERS,
        bucket_histogram,
        read_chapter_text,
        repeated_phrases_for_text,
        sentence_lengths,
        split_paragraphs,
        style_metrics_for_text,
        summarize_chapters,
        top_phrases,
    )


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

BODY_DIRNAME = "正文"
STYLE_DIRNAME = "文风"
ARCHIVE_FILENAME = "文风档案.md"

#: 抽样上限：正文超过该数量时不再全量统计（读取成本与档案体积都要控）。
DEFAULT_MAX_CHAPTERS = 60
#: 最近 N 章无条件入选——文风是「最近写出来的」最相关，不能只靠均匀抽样。
RECENT_CHAPTERS_ALWAYS = 5
#: 源 B 每个参考文件的机械抽样片段上限（字）。只存短样本，不搬运整本参考书。
REFERENCE_EXCERPT_CHARS = 120
#: 注入摘要上限（字）。与 `memory_contract_adapter._STYLE_CONTRACT_MAX_CHARS` 同量级：
#: 任务书是稀缺资源，档案再全也只能带一份摘要进去。
INJECTION_DIGEST_MAX_CHARS = 1500

#: 档案 markdown 的分区标记：标记内由脚本覆盖写，标记外原样保留。
SCRIPT_BEGIN = "<!-- STYLE-PROFILE:BEGIN script-generated -->"
SCRIPT_END = "<!-- STYLE-PROFILE:END script-generated -->"
HAND_SECTION_TEMPLATE = """## 归纳段（人工/agent 归纳，build 不覆盖）

> 这里写**读原文才能得到的判断**（口癖、节奏观感、值得保留的写法）。
> 脚本段只给数字，不给这类结论；本段由 `/webnovel-style-learn` 或作者手工维护。

- （待填写）
"""

REFERENCE_SUFFIXES = (".md", ".txt")

#: 需要与目标对比的标量指标。差异只描述**方向与幅度**，不判「好坏」——
#: 「该变成什么」由目标画像决定，不由脚本自作主张。
GAP_METRICS: Tuple[Tuple[str, str], ...] = (
    ("avg_sentence", "平均句长（字）"),
    ("median_sentence", "句长中位数（字）"),
    ("avg_paragraph", "平均段长（字）"),
    ("dialogue_paragraph_ratio", "对话段占比"),
    ("long_sentence_ratio", "长句(>40字)占比"),
    ("said_tag_ratio", "said tag 占比"),
    ("four_char_ratio", "四字格密度"),
)

#: 与目标差异达到该幅度才登记为待收敛项。
GAP_NOTABLE_RATIO = 0.15
GAP_SEVERITY = ((0.50, "high"), (0.25, "medium"))


# ---------------------------------------------------------------------------
# 取数
# ---------------------------------------------------------------------------

def _accepted_chapters(project_root: Path) -> Tuple[List[int], List[Dict[str, Any]]]:
    """accepted 提交覆盖的章节 + 其余提交的状态明细（后者用于如实披露跳过了什么）。"""
    accepted: List[int] = []
    skipped: List[Dict[str, Any]] = []
    for commit in scan_commits(project_root):
        if str(commit.status).strip().lower() == "accepted":
            accepted.append(int(commit.chapter))
        else:
            skipped.append({"chapter": int(commit.chapter), "status": str(commit.status)})
    return sorted(set(accepted)), skipped


def _body_chapters(project_root: Path) -> List[int]:
    """扫 `正文/` 下全部章节号（含未提交稿），仅在 `--source all` 时使用。"""
    chapters_dir = project_root / BODY_DIRNAME
    if not chapters_dir.is_dir():
        return []
    numbers: List[int] = []
    for path in chapters_dir.rglob("第*章*.md"):
        match = re.search(r"第0*(\d+)章", path.name)
        if not match:
            continue
        try:
            numbers.append(int(match.group(1)))
        except ValueError:
            continue
    return sorted(set(numbers))


def select_sample(
    chapters: Sequence[int], max_chapters: int, recent: int = RECENT_CHAPTERS_ALWAYS
) -> List[int]:
    """正文抽样：均匀步长取样 + 最近 N 章无条件入选。

    为什么不用「按卷抽」：卷号靠 `volume_num_for_chapter` 的每卷 50 章**启发式**推算，
    书未分卷时该值没有意义；均匀抽样不依赖任何配置，且对「文风随时间漂移」更敏感。
    返回值只做采样记录，不改变统计口径。
    """
    ordered = sorted(set(int(c) for c in chapters))
    if len(ordered) <= max_chapters:
        return ordered

    recent = max(0, min(recent, max_chapters, len(ordered)))
    quota = max_chapters - recent
    picked: set[int] = set()
    if quota > 0:
        step = len(ordered) / quota
        picked.update(ordered[min(len(ordered) - 1, int(index * step))] for index in range(quota))
    picked.update(ordered[len(ordered) - recent :])
    return sorted(picked)


def _collect_body_metrics(
    project_root: Path, chapters: Sequence[int]
) -> Tuple[List[Dict[str, Any]], List[int], Dict[int, Dict[str, int]], List[int]]:
    """逐章只读一次文件，同时得到：指标 / 缺失章 / 重复片段 / 全部句长。"""
    metrics: List[Dict[str, Any]] = []
    missing: List[int] = []
    grams: Dict[int, Dict[str, int]] = {}
    all_lengths: List[int] = []
    for chapter in chapters:
        text, name = read_chapter_text(project_root, chapter)
        if not text:
            missing.append(int(chapter))
            continue
        item = style_metrics_for_text(text, chapter=int(chapter), file_name=name)
        if item is None:
            missing.append(int(chapter))
            continue
        metrics.append(item)
        grams[int(chapter)] = repeated_phrases_for_text(text)
        all_lengths.extend(sentence_lengths(split_paragraphs(text)))
    return metrics, missing, grams, all_lengths


def _reference_files(project_root: Path) -> List[Path]:
    """`文风/` 下的参考文本（递归，跳过档案自身与隐藏文件）。"""
    style_dir = project_root / STYLE_DIRNAME
    if not style_dir.is_dir():
        return []
    files: List[Path] = []
    for path in sorted(style_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in REFERENCE_SUFFIXES:
            continue
        if path.name == ARCHIVE_FILENAME or path.name.startswith("."):
            continue
        files.append(path)
    return files


def _reference_excerpt(text: str) -> str:
    """机械抽样：取首个有效段落的开头若干字。

    刻意不做「精选」——精选需要判断，且不可复现；短样本的作用只是让人一眼认出
    这是哪份文本，不承担风格举证责任。
    """
    paragraphs = split_paragraphs(text)
    if not paragraphs:
        return ""
    first = paragraphs[0]
    if len(first) <= REFERENCE_EXCERPT_CHARS:
        return first
    return first[:REFERENCE_EXCERPT_CHARS].rstrip() + "…"


def _collect_target(project_root: Path) -> Dict[str, Any]:
    files = _reference_files(project_root)
    if not files:
        return {
            "available": False,
            "reason": "文风目录不存在或没有参考文本",
            "directory": str(project_root / STYLE_DIRNAME),
            "files": [],
        }

    items: List[Dict[str, Any]] = []
    unreadable: List[str] = []
    for index, path in enumerate(files):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            unreadable.append(path.name)
            continue
        item = style_metrics_for_text(text, chapter=index + 1, file_name=path.name)
        if item is None:
            unreadable.append(path.name)
            continue
        item["excerpt"] = _reference_excerpt(text)
        item["source_path"] = str(path.relative_to(project_root).as_posix())
        items.append(item)

    if not items:
        return {
            "available": False,
            "reason": "文风目录下的文件都无法解析出有效段落",
            "directory": str(project_root / STYLE_DIRNAME),
            "files": [],
            "unreadable": unreadable,
        }

    summary = summarize_chapters(items)
    # 参考文本按「文件」计数，不按章——覆盖首次/末次章没有意义，避免误读。
    summary.pop("first_chapter", None)
    summary.pop("last_chapter", None)
    summary["file_count"] = len(items)
    return {
        "available": True,
        "directory": str(project_root / STYLE_DIRNAME),
        "files": items,
        "unreadable": unreadable,
        "summary": summary,
        "excerpt_note": (
            f"每个参考文件只保留首个段落的前 {REFERENCE_EXCERPT_CHARS} 字机械样本；"
            "档案不复制参考书正文，避免备份体积与版权风险。"
        ),
    }


# ---------------------------------------------------------------------------
# 计算
# ---------------------------------------------------------------------------

def _median(summary: Dict[str, Any], key: str) -> Optional[float]:
    scalars = summary.get("scalars") or {}
    item = scalars.get(key)
    if not isinstance(item, dict):
        return None
    try:
        return float(item.get("median"))
    except (TypeError, ValueError):
        return None


def _relative_gap(observed: float, target: float) -> Optional[float]:
    if target == 0:
        return None
    return (observed - target) / target


def compute_gaps(observed_summary: Dict[str, Any], target_summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    """现状 vs 目标的差异表。

    只给 `observed - target` 的相对差与幅度分级；`direction` 用「偏长/偏短/偏高/偏低」
    这类中性描述，不写「需要改成 X」——目标画像本身才是要求。
    """
    gaps: List[Dict[str, Any]] = []
    for key, label in GAP_METRICS:
        observed = _median(observed_summary, key)
        target = _median(target_summary, key)
        if observed is None or target is None:
            continue
        delta = _relative_gap(observed, target)
        if delta is None:
            continue
        if abs(delta) < GAP_NOTABLE_RATIO:
            continue
        severity = "low"
        for threshold, name in GAP_SEVERITY:
            if abs(delta) >= threshold:
                severity = name
                break
        gaps.append(
            {
                "metric": key,
                "label": label,
                "observed_median": round(observed, 4),
                "target_median": round(target, 4),
                "relative_delta": round(delta, 4),
                "direction": "高于目标" if delta > 0 else "低于目标",
                "severity": severity,
            }
        )
    gaps.sort(key=lambda item: -abs(item["relative_delta"]))
    return gaps


def hard_constraint_checks(observed_summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    """绝对红线检查（与目标无关）。

    阈值来源：`skills/webnovel-write/references/style-adapter.md`。
    这类检查有意义的前提是**已有正文**；没有正文时不产出结论，避免「0 章也全绿」的假阳性。
    """
    if not observed_summary.get("chapter_count"):
        return []
    checks: List[Dict[str, Any]] = []

    long_ratio = _median(observed_summary, "long_sentence_ratio")
    if long_ratio is not None:
        checks.append(
            {
                "code": "long_sentence_ratio",
                "label": f"长句(>{LONG_SENTENCE_THRESHOLD}字)占比",
                "observed": round(long_ratio, 4),
                "limit": 0.10,
                "passed": long_ratio < 0.10,
                "source": "style-adapter.md（>40字句子 <10%）",
            }
        )

    said_ratio = _median(observed_summary, "said_tag_ratio")
    if said_ratio is not None:
        checks.append(
            {
                "code": "said_tag_ratio",
                "label": "said tag 占比",
                "observed": round(said_ratio, 4),
                "limit": SAID_TAG_MAX_RATIO,
                "passed": said_ratio <= SAID_TAG_MAX_RATIO,
                "source": "style-adapter.md（said tag 占比 ≤30%）",
            }
        )
    return checks


def _pct(value: Optional[float], digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.{digits}f}%"


def _num(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value:g}"


def build_digest(
    observed: Dict[str, Any],
    target: Dict[str, Any],
    gaps: List[Dict[str, Any]],
    constraints: List[Dict[str, Any]],
    generated_at: str,
) -> str:
    """任务书注入用的压缩摘要（≤1500 字）。

    只带「目标 + 现状 → 差距 top3 + 红线」，不带完整统计表：任务书是稀缺资源，
    全量表格既挤占预算，又会让起草阶段陷入数字细节。
    """
    lines: List[str] = [f"文风档案摘要（生成 {generated_at[:10]}）"]

    observed_summary = observed.get("summary") or {}
    if observed_summary.get("chapter_count"):
        lines.append(
            "现状（源 A 正文，{n} 章，口径 {mode}）：平均句长 {sent} 字"
            "（P25 {p25} / P75 {p75}）、平均段长 {para} 字、对话段占比 {dlg}、"
            "长句占比 {long}、said tag 占比 {said}。".format(
                n=observed_summary.get("chapter_count"),
                mode=observed.get("source_mode", "accepted"),
                sent=_num(_median(observed_summary, "avg_sentence")),
                p25=_num((observed_summary.get("scalars", {}).get("avg_sentence") or {}).get("p25")),
                p75=_num((observed_summary.get("scalars", {}).get("avg_sentence") or {}).get("p75")),
                para=_num(_median(observed_summary, "avg_paragraph")),
                dlg=_pct(_median(observed_summary, "dialogue_paragraph_ratio"), 0),
                long=_pct(_median(observed_summary, "long_sentence_ratio")),
                said=_pct(_median(observed_summary, "said_tag_ratio")),
            )
        )
    else:
        lines.append("现状（源 A 正文）：无可用章节，本次不产生现状画像。")

    if target.get("available"):
        target_summary = target.get("summary") or {}
        lines.append(
            "目标（源 B 文风/，{n} 个参考文件）：平均句长 {sent} 字、平均段长 {para} 字、"
            "对话段占比 {dlg}、长句占比 {long}、said tag 占比 {said}。".format(
                n=target_summary.get("file_count", 0),
                sent=_num(_median(target_summary, "avg_sentence")),
                para=_num(_median(target_summary, "avg_paragraph")),
                dlg=_pct(_median(target_summary, "dialogue_paragraph_ratio"), 0),
                long=_pct(_median(target_summary, "long_sentence_ratio")),
                said=_pct(_median(target_summary, "said_tag_ratio")),
            )
        )
    else:
        lines.append(
            "目标（源 B 文风/）：无参考文本 → 本次只有现状画像，"
            "**不得把现状当作目标**（现状可能正是要改掉的部分）。"
        )

    if gaps:
        parts = [
            "{label} {observed:g} → {target:g}（{direction} {delta:+.0%}）".format(
                label=item["label"],
                observed=item["observed_median"],
                target=item["target_median"],
                direction=item["direction"],
                delta=item["relative_delta"],
            )
            for item in gaps[:3]
        ]
        lines.append("待收敛（目标优先，按差异幅度排序）：" + "；".join(parts) + "。")
    elif target.get("available"):
        lines.append("待收敛：现状与目标在已统计指标上无明显差异（阈值 ±15%）。")

    if constraints:
        lines.append(
            "硬约束：" + "；".join(
                f"{item['label']} {_pct(item['observed'])}"
                f"（红线 {_pct(item['limit'], 0)}，{'通过' if item['passed'] else '**未通过**'}）"
                for item in constraints
            ) + "。"
        )

    lines.append("口径：现状（observed）描述「已写成什么样」，目标（target）描述「要模仿成什么样」；冲突以目标为准。")
    digest = "\n".join(lines)
    if len(digest) > INJECTION_DIGEST_MAX_CHARS:
        digest = digest[: INJECTION_DIGEST_MAX_CHARS - 1].rstrip() + "…"
    return digest


def build_profile(project_root: Path, *, source_mode: str, max_chapters: int) -> Dict[str, Any]:
    notes: List[str] = []
    accepted, skipped_commits = _accepted_chapters(project_root)

    if source_mode == "all":
        candidates = _body_chapters(project_root)
        notes.append(
            "本次使用 `--source all`：统计全部正文文件，**包含未提交或被拒的稿**。"
            "默认口径应为 accepted，越权取样会让未定稿的写法进入画像。"
        )
    else:
        candidates = accepted
        if skipped_commits:
            notes.append(
                "已按 accepted 口径跳过 {} 个非 accepted 提交（示例：{}）。".format(
                    len(skipped_commits),
                    "、".join(f"第{item['chapter']}章:{item['status']}" for item in skipped_commits[:5]),
                )
            )
        if not candidates:
            body = _body_chapters(project_root)
            notes.append(
                "没有 accepted 提交，源 A 本次为空（正文文件 {} 个）。"
                "原因通常是尚未提交任何章节；如确需用现有草稿建画像，"
                "显式运行 `build --source all` 并自行承担口径风险。".format(len(body))
            )

    sampled = select_sample(candidates, max_chapters)
    metrics, missing, grams, all_lengths = _collect_body_metrics(project_root, sampled)
    summary = summarize_chapters(metrics)
    sampling_note = (
        "全量统计（{n} 章）".format(n=len(candidates))
        if len(sampled) == len(candidates)
        else "抽样统计：候选 {total} 章 → 取 {picked} 章（均匀步长 + 最近 {recent} 章必抽）".format(
            total=len(candidates), picked=len(sampled), recent=RECENT_CHAPTERS_ALWAYS
        )
    )

    observed = {
        "label": "observed",
        "semantics": "作者实际写成的样子；不是标准，不得直接当作目标",
        "source_mode": source_mode,
        "directory": str(project_root / BODY_DIRNAME),
        "candidate_chapters": candidates,
        "sampled_chapters": sampled,
        "sampling_note": sampling_note,
        "missing_chapters": missing,
        "summary": summary,
        "sentence_length_histogram": bucket_histogram(all_lengths),
        "top_phrases": top_phrases(grams, sampled, limit=20),
        "baseline_adequate": len(metrics) >= STYLE_BASELINE_MIN_CHAPTERS,
    }
    if missing:
        notes.append(
            "以下章节在 accepted 名单内但读不到正文（文件名需符合 `第NNNN章*.md`）：{}。".format(
                "、".join(str(ch) for ch in missing[:10])
            )
        )

    target = _collect_target(project_root)
    gaps = compute_gaps(summary, target.get("summary") or {}) if target.get("available") else []
    constraints = hard_constraint_checks(summary)

    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    profile: Dict[str, Any] = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "generated_at": generated_at,
        "project_root": str(project_root),
        "observed": observed,
        "target": target,
        "gaps": gaps,
        "hard_constraints": constraints,
        "notes": notes,
    }
    profile["injection_digest"] = build_digest(observed, target, gaps, constraints, generated_at)
    return profile


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------

def _format_scalar(summary: Dict[str, Any], key: str, digits: int = 2) -> str:
    scalars = summary.get("scalars") or {}
    item = scalars.get(key) or {}
    median = item.get("median")
    p25 = item.get("p25")
    p75 = item.get("p75")
    if median is None:
        return "—"
    return f"{median:.{digits}f}（P25 {p25:.{digits}f} / P75 {p75:.{digits}f}）"


def _format_ratio(summary: Dict[str, Any], key: str) -> str:
    return _pct(_median(summary, key), 0)


def render_script_section(profile: Dict[str, Any]) -> str:
    observed = profile["observed"]
    target = profile["target"]
    lines: List[str] = []
    lines.append(SCRIPT_BEGIN)
    lines.append("## 文风档案（脚本生成，勿手改）")
    lines.append("")
    lines.append(f"- 生成时间：`{profile['generated_at']}`")
    lines.append(f"- 正样本：`{BODY_DIRNAME}/`，口径 `{observed['source_mode']}`，{observed['sampling_note']}")
    lines.append(f"- 参考文本：`{STYLE_DIRNAME}/`，{target.get('summary', {}).get('file_count', 0)} 个文件")
    lines.append(f"- 机读副本：`.webnovel/{PROFILE_JSON_NAME}`")
    lines.append("")

    lines.append("### 一、现状画像（observed：已写成什么样）")
    lines.append("")
    if not observed["summary"].get("chapter_count"):
        lines.append("> 无可用章节，未产生现状画像。")
    else:
        summary = observed["summary"]
        lines.append("| 指标 | 中位数（四分位） |")
        lines.append("|------|------------------|")
        lines.append(f"| 平均句长（字） | {_format_scalar(summary, 'avg_sentence')} |")
        lines.append(f"| 平均段长（字） | {_format_scalar(summary, 'avg_paragraph')} |")
        lines.append(f"| 每章中文字数 | {_format_scalar(summary, 'cjk_chars', 0)} |")
        lines.append("")
        lines.append(
            "| 比例项 | 中位数 |\n|--------|--------|\n"
            f"| 对话段占比 | {_format_ratio(summary, 'dialogue_paragraph_ratio')} |\n"
            f"| 长句(>{LONG_SENTENCE_THRESHOLD}字)占比 | {_format_ratio(summary, 'long_sentence_ratio')} |\n"
            f"| said tag 占比 | {_format_ratio(summary, 'said_tag_ratio')} |\n"
            f"| 四字格密度 | {_format_ratio(summary, 'four_char_ratio')} |\n"
            f"| 对话开场占比 | {_pct(summary.get('opening_dialogue_ratio'))} |\n"
            f"| 末段悬念标点占比 | {_pct(summary.get('closing_suspense_ratio'))} |"
        )
        lines.append("")
        histogram = observed.get("sentence_length_histogram") or {}
        if histogram:
            lines.append("句长分档：" + "；".join(
                f"{label} {_pct(item['ratio'], 0)}" for label, item in histogram.items()
            ) + "。")
            lines.append("")
        phrases = observed.get("top_phrases") or []
        if phrases:
            lines.append("跨章高频片段（口癖候选，只列极大重复）：" + "；".join(
                f"`{item['gram']}×{item['count']}`" for item in phrases[:10]
            ) + "。")
            lines.append("")
    lines.append(f"- 样本章节：{observed['sampled_chapters'][:20]}{'…' if len(observed['sampled_chapters']) > 20 else ''}")

    lines.append("")
    lines.append("### 二、目标画像（target：要模仿成什么样）")
    lines.append("")
    if not target.get("available"):
        lines.append(f"> {target.get('reason', '无目标画像')}。**本次不产生目标画像，现状不得代替目标。**")
    else:
        summary = target["summary"]
        lines.append("| 指标 | 中位数（四分位） |")
        lines.append("|------|------------------|")
        lines.append(f"| 平均句长（字） | {_format_scalar(summary, 'avg_sentence')} |")
        lines.append(f"| 平均段长（字） | {_format_scalar(summary, 'avg_paragraph')} |")
        lines.append("")
        lines.append(
            "| 比例项 | 中位数 |\n|--------|--------|\n"
            f"| 对话段占比 | {_format_ratio(summary, 'dialogue_paragraph_ratio')} |\n"
            f"| 长句(>{LONG_SENTENCE_THRESHOLD}字)占比 | {_format_ratio(summary, 'long_sentence_ratio')} |\n"
            f"| said tag 占比 | {_format_ratio(summary, 'said_tag_ratio')} |\n"
            f"| 四字格密度 | {_format_ratio(summary, 'four_char_ratio')} |"
        )
        lines.append("")
        lines.append(target.get("excerpt_note", ""))
        lines.append("")
        for item in target["files"][:20]:
            lines.append(
                f"- `{item['source_path']}`：{item['cjk_chars']} 字，平均句长 "
                f"{item['avg_sentence']} 字，对话段占比 {_pct(item['dialogue_paragraph_ratio'], 0)}"
                + (f"，样本：{item['excerpt']}" if item.get("excerpt") else "")
            )
        if target.get("unreadable"):
            lines.append(f"- 无法解析：{'、'.join(target['unreadable'])}")

    lines.append("")
    lines.append("### 三、待收敛项（目标优先）")
    lines.append("")
    gaps = profile.get("gaps") or []
    if not target.get("available"):
        lines.append("> 缺少目标画像，无法计算差异。")
    elif not gaps:
        lines.append("> 已统计指标上现状与目标差异均 <15%，无需收敛项。")
    else:
        lines.append("| 指标 | 现状 | 目标 | 差异 | 幅度 |")
        lines.append("|------|------|------|------|------|")
        for item in gaps:
            lines.append(
                f"| {item['label']} | {item['observed_median']:g} | {item['target_median']:g} | "
                f"{item['relative_delta']:+.0%}（{item['direction']}） | {item['severity']} |"
            )

    lines.append("")
    lines.append("### 四、硬约束检查（阈值来源：`style-adapter.md`）")
    lines.append("")
    constraints = profile.get("hard_constraints") or []
    if not constraints:
        lines.append("> 无正文样本，未做检查（**不表示通过**）。")
    else:
        for item in constraints:
            mark = "✅ 通过" if item["passed"] else "⚠️ 未通过"
            lines.append(
                f"- {item['label']}：{_pct(item['observed'])}（红线 {_pct(item['limit'], 0)}）{mark} — {item['source']}"
            )

    lines.append("")
    lines.append("### 五、注入摘要（任务书实际消费的内容）")
    lines.append("")
    lines.append("```text")
    lines.append(profile.get("injection_digest", ""))
    lines.append("```")

    if profile.get("notes"):
        lines.append("")
        lines.append("### 六、口径提示")
        lines.append("")
        for note in profile["notes"]:
            lines.append(f"- {note}")

    lines.append(SCRIPT_END)
    return "\n".join(lines)


def _split_archive(text: str) -> Optional[Tuple[str, str]]:
    """把档案切成「标记外内容」「脚本段内部」。找不到成对标记返回 None。

    用 `rfind(BEGIN)` + 其后第一个 `END`，而不是 `partition`：说明文字里若出现
    标记字样（人类很容易引用它），`partition` 会从那个引用处切开，把整份旧脚本段
    当成人工内容再拼一次——档案会随每次 build 膨胀。取最后一次出现才是真区块。
    """
    begin = text.rfind(SCRIPT_BEGIN)
    if begin == -1:
        return None
    end = text.find(SCRIPT_END, begin)
    if end == -1:
        return None
    return text[:begin], text[end + len(SCRIPT_END) :]


def _strip_leading_headings(text: str) -> str:
    """去掉档案自带的文档标题（h1）与其后的引言块（`>`），保留正文标题。

    只跳一层 h1：旧档案或人工编辑可能自带 `# 文风档案` 标题与说明块，不去掉会拼出
    双标题；但**不能连 `## 归纳段` 这类正文标题一起去掉**，否则归纳段会退化成裸列表。
    """
    lines = text.splitlines()
    index = 0
    if index < len(lines) and lines[index].lstrip().startswith("# "):
        index += 1
    while index < len(lines):
        line = lines[index].strip()
        if not line or line.startswith(">"):
            index += 1
            continue
        break
    return "\n".join(lines[index:]).strip()


def _extract_hand_section(existing: str) -> str:
    """保留档案里脚本段之外的内容；没有则给骨架。"""
    if not existing:
        return HAND_SECTION_TEMPLATE
    parts = _split_archive(existing)
    if parts is None:
        # 旧档案没有成对标记：整份视为人工内容，只去掉自带标题后原样保留。
        hand = _strip_leading_headings(existing)
        return hand + "\n" if hand else HAND_SECTION_TEMPLATE
    before, after = parts
    hand = _strip_leading_headings((before + after).strip())
    return hand + "\n" if hand else HAND_SECTION_TEMPLATE


def render_archive(profile: Dict[str, Any], existing: str) -> str:
    header = (
        "# 文风档案\n\n"
        "> 本文件由 `webnovel.py style-profile build` 维护。脚本段被一对 "
        "`STYLE-PROFILE` BEGIN / END 注释标记包住，标记内会被覆盖写；"
        "标记外为人工/agent 归纳段，脚本原样保留。\n"
    )
    hand = _extract_hand_section(existing)
    return f"{header}\n{hand}\n{render_script_section(profile)}\n"


def render_text_profile(profile: Dict[str, Any]) -> str:
    lines = [profile.get("injection_digest", "")]
    gaps = profile.get("gaps") or []
    if gaps:
        lines.append("")
        lines.append("待收敛项：")
        for item in gaps:
            lines.append(
                f"  {item['label']}: {item['observed_median']:g} → {item['target_median']:g} "
                f"({item['relative_delta']:+.0%})"
            )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 落盘
# ---------------------------------------------------------------------------

def write_outputs(project_root: Path, profile: Dict[str, Any]) -> Tuple[Path, Path]:
    webnovel_dir = project_root / ".webnovel"
    webnovel_dir.mkdir(parents=True, exist_ok=True)
    json_path = webnovel_dir / PROFILE_JSON_NAME

    style_dir = project_root / STYLE_DIRNAME
    style_dir.mkdir(parents=True, exist_ok=True)
    archive_path = style_dir / ARCHIVE_FILENAME

    existing = ""
    if archive_path.is_file():
        try:
            existing = archive_path.read_text(encoding="utf-8")
        except OSError:
            existing = ""

    json_path.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    archive_path.write_text(render_archive(profile, existing), encoding="utf-8")
    return json_path, archive_path


def load_profile(project_root: Path) -> Optional[Dict[str, Any]]:
    path = project_root / ".webnovel" / PROFILE_JSON_NAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="style_profile.py",
        description="文风档案：从 正文/（现状）与 文风/（目标）学出可复用的风格画像",
    )
    parser.add_argument("--project-root", default="", help="书项目根目录")
    sub = parser.add_subparsers(dest="command")

    build_parser = sub.add_parser("build", help="统计并落盘文风档案")
    build_parser.add_argument(
        "--source",
        choices=["accepted", "all"],
        default="accepted",
        help="源 A 采样口径：accepted 只取已提交接收的章（默认）；all 扫全部正文文件（越权）",
    )
    build_parser.add_argument(
        "--max-chapters",
        type=int,
        default=DEFAULT_MAX_CHAPTERS,
        help=f"源 A 抽样上限章节数，默认 {DEFAULT_MAX_CHAPTERS}",
    )
    build_parser.add_argument("--format", choices=["text", "json"], default="text")

    show_parser = sub.add_parser("show", help="读取已落盘档案")
    show_parser.add_argument("--format", choices=["text", "json"], default="text")

    diff_parser = sub.add_parser("diff", help="只看现状与目标的差异（读已落盘档案）")
    diff_parser.add_argument("--format", choices=["text", "json"], default="text")
    return parser


def _emit_error(message: str, suggestion: str = "") -> int:
    payload: Dict[str, Any] = {"status": "error", "message": message}
    if suggestion:
        payload["suggestion"] = suggestion
    print(json.dumps(payload, ensure_ascii=False))
    return 2


def main(argv: Optional[Sequence[str]] = None) -> int:
    enable_windows_utf8_stdio()
    args = _build_parser().parse_args(list(argv) if argv is not None else None)

    root = resolve_project_root(args.project_root or None)
    if root is None:
        return _emit_error(
            "无法解析书项目根目录",
            "用 --project-root 指定，或先 /webnovel-init 创建项目",
        )

    if args.command == "build":
        profile = build_profile(
            root, source_mode=args.source, max_chapters=max(1, int(args.max_chapters))
        )
        json_path, archive_path = write_outputs(root, profile)
        if args.format == "json":
            print(json.dumps({"status": "success", **profile}, ensure_ascii=False, indent=2))
        else:
            print(render_text_profile(profile))
            print("")
            print(f"profile: {json_path}")
            print(f"archive: {archive_path}")
        return 0

    if args.command == "show":
        profile = load_profile(root)
        if profile is None:
            return _emit_error(
                f"未找到文风档案（{root / '.webnovel' / PROFILE_JSON_NAME}）",
                "先运行 `style-profile build`",
            )
        if args.format == "json":
            print(json.dumps({"status": "success", **profile}, ensure_ascii=False, indent=2))
        else:
            print(render_text_profile(profile))
            observed = profile.get("observed") or {}
            print("")
            print(
                "现状：{n} 章（口径 {mode}）；目标：{m} 个参考文件".format(
                    n=(observed.get("summary") or {}).get("chapter_count", 0),
                    mode=observed.get("source_mode", "accepted"),
                    m=(profile.get("target") or {}).get("summary", {}).get("file_count", 0),
                )
            )
        return 0

    if args.command == "diff":
        profile = load_profile(root)
        if profile is None:
            return _emit_error(
                f"未找到文风档案（{root / '.webnovel' / PROFILE_JSON_NAME}）",
                "先运行 `style-profile build`",
            )
        target = profile.get("target") or {}
        payload = {
            "status": "success",
            "generated_at": profile.get("generated_at"),
            "target_available": bool(target.get("available")),
            "target_reason": target.get("reason", ""),
            "gaps": profile.get("gaps") or [],
            "hard_constraints": profile.get("hard_constraints") or [],
        }
        if args.format == "json":
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        if not target.get("available"):
            print(f"无目标画像（{target.get('reason', '未提供参考文本')}）→ 无法计算差异。")
            print("提示：把参考文本放进 文风/ 后重跑 `style-profile build`。")
            return 0
        gaps = payload["gaps"]
        if not gaps:
            print("已统计指标上现状与目标差异均 <15%。")
            return 0
        for item in gaps:
            print(
                f"{item['label']}: {item['observed_median']:g} → {item['target_median']:g} "
                f"({item['relative_delta']:+.0%}, {item['direction']}, {item['severity']})"
            )
        return 0

    return _emit_error(
        "未指定命令",
        "可用命令：build / show / diff（如 `style-profile build`）",
    )


if __name__ == "__main__":
    if sys.platform == "win32":
        enable_windows_utf8_stdio()
    raise SystemExit(main())
