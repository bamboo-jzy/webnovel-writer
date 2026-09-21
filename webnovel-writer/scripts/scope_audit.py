#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scope_audit.py - 范围级回扫（批一：S1 Strand 配比 / S4 角色·关系·称谓漂移 / S5 文体漂移）

设计原则（与 `status_reporter` 同一套口径，见 2026-09-18 修正）：

1. **只读**。不改正文、不改 `index.db`（以 `mode=ro` URI 打开，字节级无副作用）、不改 `state.json`。
   输出只有一份报告文件加 stdout。
2. **能判才判，不能判就说不能判**。任何指标在样本不足或字段缺失时，必须显式输出
   「无法判断」，**不得渲染成「正常」**——这是 `health_report.md` 旧实现的假阴性教训。
3. **阈值来源如实披露**。节奏类阈值优先取题材 profile（`references/genre-profiles.md`），
   回落 `config` 默认值时必须标注，不假装专业。
4. **只报告不改写**。不给「应该改成什么」的建议，不碰正文一个字。

三个检查器与供数前提（详见 `references/index/skill-gap-assessment-2026-09-17.md` 附录 B §B.4）：

| 检查器 | 数据来源 | 供数状态 |
|--------|----------|----------|
| S1 Strand 配比 | `state.strand_tracker` × 题材阈值 | ✅ 就绪 |
| S4 漂移 | `index.db` 的 `appearances` / `state_changes` / `relationships` | ✅ 就绪 |
| S5 文体 | 直接统计 `正文/*.md`，**不依赖任何表** | ✅ 就绪 |

批二的 S2（伏笔回收节奏）与 S3（钩子/爽点分布）未实现：它们的供数尚未接通（缺
`target_chapter`；`chapter_reading_power` 从未被写入），做了也只会输出「无法判断」。
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

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
    from data_modules.genre_profile_loader import (
        build_project_info,
        resolve_pacing_thresholds,
    )
except ImportError:  # pragma: no cover
    from scripts.data_modules.genre_profile_loader import (
        build_project_info,
        resolve_pacing_thresholds,
    )

try:
    from status_reporter import StatusReporter, STRAND_RATIO_MIN_SAMPLE
except ImportError:  # pragma: no cover
    from scripts.status_reporter import StatusReporter, STRAND_RATIO_MIN_SAMPLE

try:
    from data_modules.style_metrics import (
        PROFILE_JSON_NAME,
        PROFILE_SCHEMA_VERSION,
        STYLE_BASELINE_MIN_CHAPTERS,
        PHRASE_MIN_COUNT,
        metrics_for_chapter as _metrics_for_chapter,
        phrases_for_chapter as _phrases_for_chapter,
    )
except ImportError:  # pragma: no cover
    from scripts.data_modules.style_metrics import (
        PROFILE_JSON_NAME,
        PROFILE_SCHEMA_VERSION,
        STYLE_BASELINE_MIN_CHAPTERS,
        PHRASE_MIN_COUNT,
        metrics_for_chapter as _metrics_for_chapter,
        phrases_for_chapter as _phrases_for_chapter,
    )


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

CHECK_S1 = "s1"
CHECK_S4 = "s4"
CHECK_S5 = "s5"
ALL_CHECKS: Tuple[str, ...] = (CHECK_S1, CHECK_S4, CHECK_S5)
CHECK_LABELS = {
    CHECK_S1: "S1 Strand 配比",
    CHECK_S4: "S4 角色·关系·称谓漂移",
    CHECK_S5: "S5 文体漂移",
}

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}
SEVERITY_ICON = {"critical": "🔴", "warning": "🟡", "info": "⚪"}

#: S5 基线口径。
#: - `auto`：有可用的文风档案就用档案基线（**跨卷可比**），否则回落本范围中位数。
#: - `profile`：强制要求档案基线；拿不到就**不下偏离结论**，并报一条 warning。
#: - `range`：沿用旧口径，只用本次范围内的中位数（范围=全书时与 auto 等价）。
BASELINE_MODES: Tuple[str, ...] = ("auto", "profile", "range")

# 文体计量原语（切句 / CJK 计数 / 极大重复过滤）统一在 `data_modules/style_metrics.py`，
# 与 `style_profile.py` 共用同一份实现——两套逻辑各写一遍就等于埋两处回归。
# 本文件只从该模块取阈值常量、档案文件常量与两个章节级取数函数，
# S5 的判定与渲染逻辑仍在本文件内。


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _deviation(value: float, baseline: float) -> float:
    if baseline <= 0:
        return 0.0
    return abs(value - baseline) / baseline


def _max_int(values: Any) -> int:
    """从任意 JSON 值里取最大整数，非整数元素/非法值一律忽略（返回 0）。"""
    best = 0
    if not isinstance(values, (list, tuple)):
        return 0
    for item in values:
        try:
            best = max(best, int(item))
        except (TypeError, ValueError):
            continue
    return best


def _clip(text: str, limit: int) -> str:
    """表格里截断长文本，并**显式加省略号**——否则读者会把截断误读成数据缺失。"""
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


class ScopeAuditor:
    """范围级回扫执行器。所有方法只读。"""

    def __init__(
        self,
        project_root: Path,
        *,
        from_chapter: int = 1,
        to_chapter: int = 0,
        dormant_gap: int = 20,
        sentence_tolerance: float = 0.30,
        dialogue_tolerance: float = 0.15,
        baseline_mode: str = "auto",
    ) -> None:
        self.project_root = Path(project_root)
        self.config = DataModulesConfig(project_root=self.project_root)
        self.from_chapter = max(1, int(from_chapter or 1))
        self._to_chapter_arg = int(to_chapter or 0)
        self.dormant_gap = max(1, int(dormant_gap))
        self.sentence_tolerance = float(sentence_tolerance)
        self.dialogue_tolerance = float(dialogue_tolerance)
        mode = str(baseline_mode or "auto").strip().lower()
        self.baseline_mode = mode if mode in BASELINE_MODES else "auto"

        self.state = _read_json(self.config.webnovel_dir / "state.json")
        self.findings: List[Dict[str, Any]] = []
        self.notes: List[str] = []

    # -- 基础设施 ---------------------------------------------------------

    def add_finding(
        self,
        *,
        check: str,
        code: str,
        severity: str,
        title: str,
        detail: str,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.findings.append(
            {
                "check": check,
                "code": code,
                "severity": severity,
                "title": title,
                "detail": detail,
                "evidence": evidence or {},
            }
        )

    def _connect_index(self) -> Optional[sqlite3.Connection]:
        """以只读方式打开 index.db；缺失或损坏返回 None（不抛异常）。"""
        path = self.config.index_db
        if not path.is_file():
            self.add_finding(
                check="all",
                code="index_missing",
                severity="warning",
                title="index.db 不存在",
                detail=(
                    "S1 依赖 state（可用），但 S4 的角色出场/状态变更/关系数据全部来自 index.db。"
                    "该库在 `init` 时不会创建，缺库时本报告的 S4 结论无意义。"
                ),
                evidence={"path": str(path)},
            )
            return None
        try:
            # 只读契约要求字节级无副作用：必须用 URI 的 mode=ro 打开。
            # 仅 `sqlite3.connect(path)` + `PRAGMA query_only=ON` 不够——连接建立/
            # 首次读取时 SQLite 仍会改写文件头（schema 版本等）字节，虽然逻辑上
            # 没写业务数据，但「跑完 index.db 字节不变」的契约会被破坏。
            uri = path.resolve().as_uri() + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON")
            return conn
        except (sqlite3.Error, ValueError) as exc:
            self.add_finding(
                check=CHECK_S4,
                code="index_unreadable",
                severity="warning",
                title="index.db 无法读取",
                detail=f"SQLite 打开失败：{exc}。S4 本次无法给出结论。",
                evidence={"path": str(path)},
            )
            return None

    def _chapter_range(self, conn: Optional[sqlite3.Connection]) -> List[int]:
        chapters: List[int] = []
        if conn is not None:
            try:
                rows = conn.execute("SELECT chapter FROM chapters ORDER BY chapter").fetchall()
                chapters = [int(row[0]) for row in rows]
            except sqlite3.Error:
                chapters = []
        if not chapters:
            # 回落：直接扫正文文件名，避免索引缺行时整份报告空转
            for path in sorted((self.project_root / "正文").rglob("第*章*.md")):
                match = re.match(r"第(\d+)章", path.name)
                if match:
                    chapters.append(int(match.group(1)))
        upper = self._to_chapter_arg or (max(chapters) if chapters else 0)
        return [c for c in sorted(set(chapters)) if self.from_chapter <= c <= upper]

    @property
    def to_chapter(self) -> int:
        return self._to_chapter_arg

    # -- S1 Strand --------------------------------------------------------

    def check_strand(self) -> Dict[str, Any]:
        reporter = StatusReporter(str(self.project_root))
        if not reporter.load_state():
            self.add_finding(
                check=CHECK_S1,
                code="state_missing",
                severity="warning",
                title="state.json 缺失，Strand 无法分析",
                detail="S1 依赖 `.webnovel/state.json` 的 `strand_tracker`。",
            )
            return {"available": False}

        data = reporter.analyze_strand_weave()
        thresholds = reporter.pacing_thresholds()

        if not data.get("has_data"):
            self.add_finding(
                check=CHECK_S1,
                code="no_strand_history",
                severity="warning",
                title="无 Strand 历史数据",
                detail=(
                    "`state.strand_tracker.history` 为空，无法分析三线配比。"
                    "该字段由投影层累积，缺失通常意味着尚无 accepted commit。"
                ),
            )
            return {"available": False, "thresholds": thresholds}

        for violation in data.get("violations", []):
            self.add_finding(
                check=CHECK_S1,
                code="strand_violation",
                severity="warning",
                title="Strand 连续性/占比违规",
                detail=violation,
                evidence={"source": thresholds.get("source")},
            )

        if not data.get("sample_adequate"):
            self.notes.append(
                f"S1：有效样本 {data.get('total_chapters')} 章，占比结论需 "
                f"≥{STRAND_RATIO_MIN_SAMPLE} 章 → **本次不对占比下结论**。"
            )

        return {
            "available": True,
            "total_chapters": data.get("total_chapters"),
            "sample_adequate": data.get("sample_adequate"),
            "ratio_min_sample": STRAND_RATIO_MIN_SAMPLE,
            "thresholds": thresholds,
            "quest": data.get("quest"),
            "fire": data.get("fire"),
            "constellation": data.get("constellation"),
            "max_quest_streak": data.get("max_quest_streak"),
            "max_fire_gap": data.get("max_fire_gap"),
            "max_const_gap": data.get("max_const_gap"),
            "violations": data.get("violations", []),
            "health": data.get("health"),
        }

    # -- S4 漂移 ----------------------------------------------------------

    def check_drift(self, conn: sqlite3.Connection, chapters: Sequence[int]) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "available": True,
            "appearances": [],
            "state_changes": [],
            "relationships": [],
            "alias_drift": [],
            "dormant_entities": [],
            "oscillations": [],
            "data_quality": [],
        }
        if not chapters:
            result["available"] = False
            self.notes.append("S4：章节范围为空，无法分析。")
            return result

        entity_names = {}
        try:
            for row in conn.execute("SELECT id, canonical_name, type FROM entities"):
                entity_names[str(row["id"])] = (
                    str(row["canonical_name"] or row["id"]),
                    str(row["type"] or ""),
                )
        except sqlite3.Error:
            pass

        def label(entity_id: str) -> str:
            name, _ = entity_names.get(entity_id, (entity_id, ""))
            return name

        # --- 出场与称谓 ---
        forms_by_entity: Dict[str, Dict[int, List[str]]] = defaultdict(dict)
        chapters_by_entity: Dict[str, List[int]] = defaultdict(list)
        try:
            rows = conn.execute(
                "SELECT entity_id, chapter, mentions, confidence FROM appearances "
                "WHERE chapter BETWEEN ? AND ? ORDER BY entity_id, chapter",
                (min(chapters), max(chapters)),
            ).fetchall()
        except sqlite3.Error as exc:
            result["available"] = False
            self.add_finding(
                check=CHECK_S4,
                code="appearances_unreadable",
                severity="warning",
                title="appearances 表不可读",
                detail=f"SQLite 查询失败：{exc}。",
            )
            return result

        for row in rows:
            entity_id = str(row["entity_id"])
            chapter = int(row["chapter"])
            try:
                forms = json.loads(row["mentions"] or "[]")
            except json.JSONDecodeError:
                forms = []
            forms = [str(f).strip() for f in forms if str(f).strip()]
            forms_by_entity[entity_id][chapter] = forms
            chapters_by_entity[entity_id].append(chapter)
            result["appearances"].append(
                {
                    "entity_id": entity_id,
                    "name": label(entity_id),
                    "chapter": chapter,
                    "mentions": forms,
                    "confidence": row["confidence"],
                }
            )

        # 称谓漂移：同一实体跨章的表面形式集合发生变化
        for entity_id, per_chapter in sorted(forms_by_entity.items()):
            ordered = sorted(per_chapter.items())
            if len(ordered) < 2:
                continue
            changes = []
            for (prev_ch, prev_forms), (curr_ch, curr_forms) in zip(ordered, ordered[1:]):
                added = [f for f in curr_forms if f not in prev_forms]
                removed = [f for f in prev_forms if f not in curr_forms]
                if added or removed:
                    changes.append(
                        {
                            "from_chapter": prev_ch,
                            "to_chapter": curr_ch,
                            "added": added,
                            "removed": removed,
                        }
                    )
            if changes:
                result["alias_drift"].append(
                    {
                        "entity_id": entity_id,
                        "name": label(entity_id),
                        "entity_type": entity_names.get(entity_id, ("", ""))[1],
                        "changes": changes,
                    }
                )

        # 出场断档：有过多次出场、但已长期未再出现
        last_seen_upper = max(chapters)
        for entity_id, seen in sorted(chapters_by_entity.items()):
            if len(seen) < 2:
                continue
            gap = last_seen_upper - max(seen)
            if gap >= self.dormant_gap:
                result["dormant_entities"].append(
                    {
                        "entity_id": entity_id,
                        "name": label(entity_id),
                        "last_chapter": max(seen),
                        "gap": gap,
                        "appearances": len(seen),
                    }
                )

        # --- 状态变更：振荡与高频翻转 ---
        try:
            change_rows = conn.execute(
                "SELECT entity_id, field, old_value, new_value, chapter FROM state_changes "
                "WHERE chapter BETWEEN ? AND ? ORDER BY entity_id, field, chapter",
                (min(chapters), max(chapters)),
            ).fetchall()
        except sqlite3.Error:
            change_rows = []

        grouped: Dict[Tuple[str, str], List[sqlite3.Row]] = defaultdict(list)
        for row in change_rows:
            grouped[(str(row["entity_id"]), str(row["field"]))].append(row)

        for (entity_id, field), rows_for_field in sorted(grouped.items()):
            chain = [
                {
                    "chapter": int(r["chapter"]),
                    "old": r["old_value"],
                    "new": r["new_value"],
                }
                for r in rows_for_field
            ]
            result["state_changes"].append(
                {
                    "entity_id": entity_id,
                    "name": label(entity_id),
                    "field": field,
                    "chain": chain,
                }
            )

            # 判据是「取值轨迹里出现了重复值」，因此必须把首个变更的 old 值
            # 也算进轨迹——否则 A→B→A 这种「改了又改回去」的典型来回横跳
            # 会因 new 值集合 [B, A] 无重复而被漏掉（假阴性）。
            seen_values = [str(item["old"]) for item in chain[:1]] + [
                str(item["new"]) for item in chain
            ]
            oscillations = [
                value
                for value, count in Counter(seen_values).items()
                if count > 1 and value not in {"", "None"}
            ]
            if oscillations:
                result["oscillations"].append(
                    {
                        "entity_id": entity_id,
                        "name": label(entity_id),
                        "field": field,
                        "repeated_values": oscillations[:3],
                        "changes": len(chain),
                    }
                )

        # --- 关系反复 ---
        try:
            rel_rows = conn.execute(
                "SELECT from_entity, to_entity, type, chapter FROM relationships "
                "WHERE chapter BETWEEN ? AND ? ORDER BY chapter",
                (min(chapters), max(chapters)),
            ).fetchall()
        except sqlite3.Error:
            rel_rows = []

        pair_types: Dict[Tuple[str, str], List[Tuple[int, str]]] = defaultdict(list)
        for row in rel_rows:
            key = (str(row["from_entity"]), str(row["to_entity"]))
            pair_types[key].append((int(row["chapter"]), str(row["type"] or "")))
            result["relationships"].append(
                {
                    "from_entity": key[0],
                    "from_name": label(key[0]),
                    "to_entity": key[1],
                    "to_name": label(key[1]),
                    "type": row["type"],
                    "chapter": row["chapter"],
                }
            )

        for (src, dst), entries in sorted(pair_types.items()):
            if len(entries) < 2:
                continue
            distinct = {text for _, text in entries}
            if len(distinct) > 1:
                result["relationships_repeated"] = result.get(
                    "relationships_repeated", []
                ) + [
                    {
                        "from": label(src),
                        "to": label(dst),
                        "types": sorted(distinct),
                        "chapters": [chapter for chapter, _ in entries],
                    }
                ]

        # --- 数据质量：死列 ---
        try:
            dead = conn.execute(
                "SELECT COUNT(*) FROM entities WHERE first_appearance = 0 AND last_appearance = 0"
            ).fetchone()[0]
            total = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        except sqlite3.Error:
            dead, total = 0, 0
        if total and dead == total:
            result["data_quality"].append(
                {
                    "code": "entities_appearance_columns_dead",
                    "detail": (
                        f"`entities.first_appearance` / `last_appearance` 全表 {total} 行均为 0，"
                        "从未被投影层写过；跨章出场统计只能走 `appearances` 表。"
                    ),
                }
            )
            self.add_finding(
                check=CHECK_S4,
                code="entities_appearance_columns_dead",
                severity="info",
                title="entities 的出场列是死列",
                detail=(
                    "`entities.first_appearance` / `last_appearance` 全为 0，投影层从未写入。"
                    "任何依赖这两列做「角色掉线/首末次出场」的查询都会静默返回空结论。"
                ),
                evidence={"entities": total},
            )

        # 称谓漂移 -> finding。
        #
        # 只把**角色**的称谓变化报为结论：地点/物品的指代在行文中天然多变
        # （「7 号楼电梯轿厢」→「电梯」），报出来全是噪音，反而淹没真正要看的角色改称。
        # 非角色实体的变化仍完整保留在 JSON 与 markdown 表格里，只是不占结论行。
        character_drift = [d for d in result["alias_drift"] if d.get("entity_type") == "角色"]
        non_character_drift = [d for d in result["alias_drift"] if d.get("entity_type") != "角色"]
        result["alias_drift_characters"] = character_drift
        result["alias_drift_non_characters"] = non_character_drift
        if non_character_drift:
            self.notes.append(
                f"S4：另有 {len(non_character_drift)} 个非角色实体（地点/物品/组织）称谓变化，"
                "已列入报告表格但不作为结论项。"
            )

        for item in character_drift:
            detail_bits = []
            for change in item["changes"]:
                if change["added"]:
                    detail_bits.append(
                        f"第{change['to_chapter']}章新增称谓 {'、'.join(change['added'])}"
                    )
                if change["removed"]:
                    detail_bits.append(
                        f"第{change['to_chapter']}章不再出现 {'、'.join(change['removed'])}"
                    )
            self.add_finding(
                check=CHECK_S4,
                code="alias_drift",
                severity="info",
                title=f"{item['name']} 的称谓跨章变化",
                detail=(
                    "；".join(detail_bits)
                    + "。称谓变化可能是合理改称，也可能是设定漂移（如职务写错）——"
                    "本项只做提示，需人工确认。"
                ),
                evidence={"entity_id": item["entity_id"], "changes": item["changes"]},
            )

        for item in result["dormant_entities"]:
            self.add_finding(
                check=CHECK_S4,
                code="dormant_entity",
                severity="info",
                title=f"{item['name']} 已 {item['gap']} 章未出场",
                detail=(
                    f"最后一次出场在第 {item['last_chapter']} 章，共出场 {item['appearances']} 次。"
                    f"阈值 {self.dormant_gap} 章为启发式默认值（题材 profile 未定义该指标）。"
                ),
                evidence=item,
            )

        for item in result["oscillations"]:
            self.add_finding(
                check=CHECK_S4,
                code="state_oscillation",
                severity="warning",
                title=f"{item['name']}.{item['field']} 取值反复",
                detail=(
                    f"字段 `{item['field']}` 共变更 {item['changes']} 次，且出现重复取值 "
                    f"{'、'.join(item['repeated_values'])}——疑似状态来回横跳或改写残留。"
                ),
                evidence=item,
            )

        return result

    # -- S5 文体 ----------------------------------------------------------

    def _load_profile_baseline(self) -> Dict[str, Any]:
        """读 `.webnovel/style_profile.json` 的 `observed` 中位数，作为 S5 的跨卷基线。

        为什么要复用档案：旧口径拿「本次范围内章节的中位数」当基线，审计某一卷时基线
        由该卷自己决定——同一章在「卷内自比」和「全书比」下会得出相反结论，跨卷不可比。
        档案的 `observed` 是全书聚合画像（默认 accepted 口径），当基线才有共同参照。

        返回值恒定含 `usable` 与 `reason`，**任何情况都不抛异常**：文风档案是增强项，
        缺了只是回落到旧口径，不能让整份审计跑不动。
        """
        path = self.config.webnovel_dir / PROFILE_JSON_NAME
        detail: Dict[str, Any] = {"path": str(path), "usable": False, "reason": ""}
        if not path.is_file():
            detail["reason"] = f"未找到文风档案（{path}），先运行 `style-profile build`"
            return detail

        data = _read_json(path)
        if not data:
            detail["reason"] = "文风档案无法解析（JSON 损坏，或根节点不是对象）"
            return detail

        schema = str(data.get("schema_version") or "")
        if schema.split("/")[0] != PROFILE_SCHEMA_VERSION.split("/")[0]:
            detail["reason"] = (
                f"档案 schema `{schema or '(缺失)'}` 与本次预期 `{PROFILE_SCHEMA_VERSION}` 不同族"
            )
            return detail

        observed = data.get("observed") or {}
        summary = observed.get("summary") or {}
        scalars = summary.get("scalars") or {}
        sentence = (scalars.get("avg_sentence") or {}).get("median")
        dialogue = (scalars.get("dialogue_paragraph_ratio") or {}).get("median")

        detail.update(
            {
                "generated_at": data.get("generated_at"),
                "source_mode": observed.get("source_mode"),
                "sampled_chapters": observed.get("sampled_chapters") or [],
                "chapter_count": summary.get("chapter_count"),
                "sampling_note": observed.get("sampling_note"),
            }
        )
        detail["last_chapter"] = _max_int(detail["sampled_chapters"])

        if not observed.get("baseline_adequate"):
            detail["reason"] = (
                f"档案样本不足（{summary.get('chapter_count') or 0} 章 < "
                f"{STYLE_BASELINE_MIN_CHAPTERS} 章），不足以充当基线"
            )
            return detail
        if sentence is None or dialogue is None:
            detail["reason"] = "档案缺少 `observed.summary.scalars` 里的句长/对话占比中位数"
            return detail

        try:
            detail["avg_sentence"] = float(sentence)
            detail["dialogue_paragraph_ratio"] = float(dialogue)
        except (TypeError, ValueError):
            detail["reason"] = "档案里的中位数不是数字（档案被手改过？）"
            return detail

        detail["usable"] = True
        detail["reason"] = ""
        return detail

    def _style_metrics(self, chapter: int) -> Optional[Dict[str, Any]]:
        """单章文体指标。实现见 `data_modules/style_metrics.py`（与文风档案共用一份）。"""
        return _metrics_for_chapter(self.project_root, chapter)

    def _repeated_phrases(self, chapter: int) -> Dict[str, int]:
        """单章极大重复片段。实现见 `data_modules/style_metrics.py`（与文风档案共用一份）。"""
        return _phrases_for_chapter(self.project_root, chapter)

    def check_style(self, chapters: Sequence[int]) -> Dict[str, Any]:
        metrics: List[Dict[str, Any]] = []
        missing: List[int] = []
        for chapter in chapters:
            item = self._style_metrics(chapter)
            if item is None:
                missing.append(chapter)
            else:
                metrics.append(item)

        result: Dict[str, Any] = {
            "available": bool(metrics),
            "min_chapters_for_baseline": STYLE_BASELINE_MIN_CHAPTERS,
            "baseline_mode": self.baseline_mode,
            "chapters": metrics,
            "missing_chapters": missing,
            # 基线是否足以支撑「偏离」结论：由下面的基线解析决定（不是写死的章数比较）。
            "baseline_adequate": False,
            "paragraph_note": "段落长度按中文字符数计（剔除标点与空白）。",
        }

        if not metrics:
            self.add_finding(
                check=CHECK_S5,
                code="no_chapter_text",
                severity="warning",
                title="未找到可比对的正文文件",
                detail=(
                    "S5 直接统计 `正文/` 下的章节文件，未命中任何一章。"
                    "常见原因：正文文件名不符合 `第NNNN章-标题.md` 规约。"
                ),
                evidence={"chapters": list(chapters)},
            )
            return result

        if missing:
            self.add_finding(
                check=CHECK_S5,
                code="chapter_file_missing",
                severity="warning",
                title="部分章节缺少正文文件",
                detail=f"以下章节在 `正文/` 下未找到文件：{', '.join(map(str, missing))}。",
                evidence={"missing": missing},
            )

        # 口癖：跨章重复的片段（极大重复，长度 4–6 字）
        grams_by_chapter: Dict[int, Dict[str, int]] = {}
        for chapter in chapters:
            grams_by_chapter[chapter] = self._repeated_phrases(chapter)
        combined: Counter = Counter()
        for mapping in grams_by_chapter.values():
            for gram, count in mapping.items():
                combined[gram] += count
        result["phrase_min_count"] = PHRASE_MIN_COUNT
        result["top_ngrams"] = [
            {
                "gram": gram,
                "count": count,
                "per_chapter": {
                    str(ch): grams_by_chapter.get(ch, {}).get(gram, 0)
                    for ch in chapters
                    if grams_by_chapter.get(ch, {}).get(gram, 0)
                },
            }
            for gram, count in sorted(
                combined.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0])
            )[:20]
        ]

        # ---- 基线解析：优先复用文风档案，跨卷才可比（N8，2026-09-21） ----
        profile = self._load_profile_baseline()
        range_median_sentence = statistics.median(m["avg_sentence"] for m in metrics)
        range_median_dialogue = statistics.median(m["dialogue_paragraph_ratio"] for m in metrics)

        use_profile = self.baseline_mode in ("auto", "profile") and bool(profile.get("usable"))
        baseline: Dict[str, Any]
        if use_profile:
            baseline = {
                "source": "profile",
                "avg_sentence": float(profile["avg_sentence"]),
                "dialogue_paragraph_ratio": float(profile["dialogue_paragraph_ratio"]),
                "profile": {
                    "generated_at": profile.get("generated_at"),
                    "source_mode": profile.get("source_mode"),
                    "chapter_count": profile.get("chapter_count"),
                    "sampling_note": profile.get("sampling_note"),
                    "last_chapter": profile.get("last_chapter"),
                },
            }
        else:
            baseline = {
                "source": "range_median",
                "avg_sentence": range_median_sentence,
                "dialogue_paragraph_ratio": range_median_dialogue,
            }

        if use_profile:
            adequate = True
        elif self.baseline_mode == "profile":
            # 显式点名要档案基线却拿不到：不接受静默回落，本次不下偏离结论。
            adequate = False
            self.add_finding(
                check=CHECK_S5,
                code="profile_baseline_unavailable",
                severity="warning",
                title="指定的文风档案基线不可用",
                detail=(
                    f"`--baseline profile` 要求用文风档案当基线，但{profile.get('reason')}。"
                    "本次**不对偏离下结论**，上表仅为原始数字。"
                ),
                evidence={"path": profile.get("path"), "reason": profile.get("reason")},
            )
        else:
            adequate = len(metrics) >= STYLE_BASELINE_MIN_CHAPTERS

        result["baseline"] = baseline
        result["baseline_adequate"] = adequate

        if use_profile:
            self.notes.append(
                f"S5 基线取自**文风档案**（{profile.get('chapter_count')} 章，口径 "
                f"`{profile.get('source_mode')}`，生成于 {profile.get('generated_at')}）："
                f"本次范围仅 {len(metrics)} 章，偏离开**以全书档案为参照**下结论，"
                "不与本范围内自比；需要范围自比请显式 `--baseline range`。"
            )
            last_sampled = int(profile.get("last_chapter") or 0)
            upper = max(int(c) for c in chapters)
            if last_sampled and last_sampled < upper:
                self.notes.append(
                    f"S5：档案最新样本为第 {last_sampled} 章，本次审计到第 {upper} 章——"
                    "档案可能落后于正文，建议先重跑 `style-profile build` 再据此判定。"
                )
            if str(profile.get("source_mode") or "") != "accepted":
                self.notes.append(
                    "S5：该档案建立时用了 `--source all` 越权口径（含未提交/被拒稿），"
                    "基线里混入了未定稿写法。"
                )
        elif not adequate and self.baseline_mode != "profile":
            self.notes.append(
                f"S5：有效章节 {len(metrics)} 章，偏离结论需 ≥{STYLE_BASELINE_MIN_CHAPTERS} 章"
                "（或先建文风档案）→ **本次只给数字，不对偏离下结论**。"
            )

        if result["baseline_adequate"]:
            base_sentence = float(baseline["avg_sentence"])
            base_dialogue = float(baseline["dialogue_paragraph_ratio"])
            base_label = "文风档案基线" if use_profile else "全书中位数"
            for item in metrics:
                dev = _deviation(item["avg_sentence"], base_sentence)
                if dev > self.sentence_tolerance:
                    self.add_finding(
                        check=CHECK_S5,
                        code="sentence_length_drift",
                        severity="warning",
                        title=f"第 {item['chapter']} 章平均句长偏离基线",
                        detail=(
                            f"该章平均句长 {item['avg_sentence']} 字，{base_label} "
                            f"{round(base_sentence, 2)} 字，偏离 {dev:.0%}"
                            f"（容忍 {self.sentence_tolerance:.0%}）。"
                        ),
                        evidence=item,
                    )
                ddev = abs(item["dialogue_paragraph_ratio"] - base_dialogue)
                if ddev > self.dialogue_tolerance:
                    self.add_finding(
                        check=CHECK_S5,
                        code="dialogue_ratio_drift",
                        severity="warning",
                        title=f"第 {item['chapter']} 章对话段占比偏离基线",
                        detail=(
                            f"该章对话段占比 {item['dialogue_paragraph_ratio']:.0%}，"
                            f"{base_label} {base_dialogue:.0%}，偏离 "
                            f"{ddev * 100:.1f} 个百分点（容忍 {self.dialogue_tolerance * 100:.0f} 个百分点）。"
                        ),
                        evidence=item,
                    )
        return result

    # -- 编排 -------------------------------------------------------------

    def run(self, checks: Iterable[str]) -> Dict[str, Any]:
        selected = [c for c in ALL_CHECKS if c in set(checks)]
        conn = self._connect_index()
        try:
            chapters = self._chapter_range(conn)
            self._to_chapter_arg = self._to_chapter_arg or (max(chapters) if chapters else 0)
            payload: Dict[str, Any] = {
                "project_root": str(self.project_root),
                "from_chapter": self.from_chapter,
                "to_chapter": self._to_chapter_arg,
                "chapters": chapters,
                "checks": selected,
            }
            if CHECK_S1 in selected:
                payload["s1_strand"] = self.check_strand()
            if CHECK_S4 in selected:
                payload["s4_drift"] = (
                    self.check_drift(conn, chapters) if conn is not None else {"available": False}
                )
            if CHECK_S5 in selected:
                payload["s5_style"] = self.check_style(chapters)
        finally:
            if conn is not None:
                conn.close()

        counts = Counter(f["severity"] for f in self.findings)
        payload["findings"] = sorted(
            self.findings, key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9), f["code"])
        )
        payload["summary"] = {
            "critical": counts.get("critical", 0),
            "warning": counts.get("warning", 0),
            "info": counts.get("info", 0),
            "total": len(self.findings),
        }
        payload["notes"] = self.notes
        return payload


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------

def render_markdown(payload: Dict[str, Any]) -> str:
    lines: List[str] = []
    summary = payload.get("summary", {})
    lines.append("# 范围级回扫报告")
    lines.append("")
    lines.append(f"- 项目路径：`{payload.get('project_root')}`")
    lines.append(
        f"- 章节范围：第 {payload.get('from_chapter')} – {payload.get('to_chapter')} 章"
        f"（实际覆盖 {len(payload.get('chapters') or [])} 章）"
    )
    lines.append(f"- 检查项：{'、'.join(CHECK_LABELS.get(c, c) for c in payload.get('checks', []))}")
    lines.append(
        f"- 结论统计：🔴 {summary.get('critical', 0)} / 🟡 {summary.get('warning', 0)} "
        f"/ ⚪ {summary.get('info', 0)}"
    )
    lines.append("")
    lines.append("> 本报告**只读**：不改正文、不改索引、不改状态。只列事实，不给改写建议。")
    lines.append("")

    if payload.get("notes"):
        lines.append("## ⚠️ 本次不下结论的项")
        lines.append("")
        for note in payload["notes"]:
            lines.append(f"- {note}")
        lines.append("")

    s1 = payload.get("s1_strand")
    if s1:
        lines.append("## S1 Strand 配比")
        lines.append("")
        if not s1.get("available"):
            lines.append("⚠️ 不可用：缺少 `strand_tracker` 历史或 `state.json`。")
        else:
            thresholds = s1.get("thresholds") or {}
            if str(thresholds.get("source", "")).startswith("genre_profile:"):
                lines.append(
                    f"> 阈值来源：题材 profile `{thresholds.get('profile_id')}`"
                    f"（{thresholds.get('profile_name')}，经 {thresholds.get('matched_by')}"
                    f"「{thresholds.get('matched_value')}」命中）"
                )
            else:
                lines.append("> 阈值来源：**config 默认值**（未匹配到题材 profile）")
            lines.append("")
            lines.append("| Strand | 章节数 | 占比 | 状态 |")
            lines.append("|--------|--------|------|------|")
            flag = "➖ 样本不足" if not s1.get("sample_adequate") else ""
            for key, label in (
                ("quest", "Quest（主线）"),
                ("fire", "Fire（感情）"),
                ("constellation", "Constellation（世界观）"),
            ):
                item = s1.get(key) or {}
                suffix = flag or "—"
                lines.append(
                    f"| {label} | {item.get('count')} | {item.get('ratio', 0):.1f}% | {suffix} |"
                )
            lines.append("")
            lines.append("| 连续性 | 实测 | 题材限制 |")
            lines.append("|--------|------|----------|")
            lines.append(
                f"| Quest 最大连续 | {s1.get('max_quest_streak')} 章 | "
                f"≤{thresholds.get('strand_quest_max_consecutive')} |"
            )
            lines.append(
                f"| Fire 最大缺失 | {s1.get('max_fire_gap')} 章 | "
                f"≤{thresholds.get('strand_fire_max_gap')} |"
            )
            lines.append(
                f"| Constellation 最大缺失 | {s1.get('max_const_gap')} 章 | "
                f"≤{thresholds.get('strand_constellation_max_gap')}（题材未定义，取自 config）|"
            )
            lines.append("")
            # 只说「连续性约束」——上面那张占比表的样本不足不等于整体健康，标签必须写清
            # 这一行评的是最大连续/最大断档，否则读者会把 ✅ 健康 误读成「占比也没问题」。
            lines.append(
                f"**连续性约束（最大连续 / 最大断档）**：{s1.get('health')}"
                "（仅评连续性，不含占比；占比另受样本量闸门约束）"
            )
        lines.append("")

    s4 = payload.get("s4_drift")
    if s4:
        lines.append("## S4 角色·关系·称谓漂移")
        lines.append("")
        if not s4.get("available"):
            lines.append("⚠️ 不可用：`index.db` 缺失或不可读。")
        else:
            lines.append(
                f"- 出场记录：{len(s4.get('appearances') or [])} 条；"
                f"状态变更：{len(s4.get('state_changes') or [])} 组；"
                f"关系记录：{len(s4.get('relationships') or [])} 条"
            )
            lines.append("")

            drift = s4.get("alias_drift") or []
            char_drift = s4.get("alias_drift_characters") or []
            lines.append(f"### 称谓漂移（角色 {len(char_drift)} / 全部 {len(drift)}）")
            lines.append("")
            if not drift:
                lines.append("本次范围内未检出跨章称谓变化。")
            else:
                lines.append("| 实体 | 类型 | 变化 |")
                lines.append("|------|------|------|")
                for item in drift:
                    bits = []
                    for change in item["changes"]:
                        if change["added"]:
                            bits.append(f"第{change['to_chapter']}章 +{'/'.join(change['added'])}")
                        if change["removed"]:
                            bits.append(f"第{change['to_chapter']}章 −{'/'.join(change['removed'])}")
                    lines.append(
                        f"| {item['name']} | {item.get('entity_type') or '—'} | {'；'.join(bits)} |"
                    )
                if not char_drift:
                    lines.append("")
                    lines.append(
                        "> 变化**全部来自非角色实体**（地点/物品/组织），其指代在行文中天然多变，"
                        "不作为结论项。"
                    )
            lines.append("")

            changes = s4.get("state_changes") or []
            lines.append(f"### 角色状态变更链（{len(changes)} 组）")
            lines.append("")
            if not changes:
                lines.append("本次范围内无 `state_changes` 记录。")
            else:
                lines.append("| 实体 | 字段 | 变更链 |")
                lines.append("|------|------|--------|")
                for item in changes:
                    chain = " → ".join(
                        f"第{c['chapter']}章 {_clip(str(c['new']), 24)}" for c in item["chain"]
                    )
                    lines.append(f"| {item['name']} | `{item['field']}` | {chain} |")
            lines.append("")

            dormant = s4.get("dormant_entities") or []
            lines.append(f"### 出场断档（{len(dormant)} 个实体）")
            lines.append("")
            if not dormant:
                lines.append("本次范围内无超阈值断档实体。")
            else:
                for item in dormant:
                    lines.append(
                        f"- **{item['name']}**：最后出场第 {item['last_chapter']} 章，"
                        f"已 {item['gap']} 章未出场（阈值 {item['gap']} 起报，启发式）"
                    )
            lines.append("")

            dq = s4.get("data_quality") or []
            if dq:
                lines.append("### 数据质量")
                lines.append("")
                for item in dq:
                    lines.append(f"- `{item['code']}`：{item['detail']}")
                lines.append("")

    s5 = payload.get("s5_style")
    if s5:
        lines.append("## S5 文体漂移")
        lines.append("")
        if not s5.get("available"):
            lines.append("⚠️ 不可用：未找到可统计的正文文件。")
        else:
            lines.append("| 章 | 段落 | 中文字 | 平均句长 | 最长句 | 平均段长 | 最长段 | 对话段占比 |")
            lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|")
            for item in s5["chapters"]:
                lines.append(
                    f"| {item['chapter']} | {item['paragraphs']} | {item['cjk_chars']} | "
                    f"{item['avg_sentence']} | {item['longest_sentence']} | "
                    f"{item['avg_paragraph']} | {item['longest_paragraph']} | "
                    f"{item['dialogue_paragraph_ratio']:.0%} |"
                )
            lines.append("")
            base = s5.get("baseline") or {}
            if base.get("source") == "profile":
                info = base.get("profile") or {}
                lines.append(
                    f"> 偏离基线：**文风档案**（{info.get('chapter_count')} 章，口径 "
                    f"`{info.get('source_mode')}`，生成于 {info.get('generated_at')}）——"
                    f"平均句长基线 {round(base.get('avg_sentence') or 0, 2)} 字、"
                    f"对话段占比基线 {(base.get('dialogue_paragraph_ratio') or 0):.0%}。"
                    "基线为全书口径，**跨卷可比**；代价是结论依赖档案时效。"
                )
                lines.append("")
            elif base.get("avg_sentence") is not None:
                lines.append(
                    f"> 偏离基线：**本次范围内章节的中位数**（平均句长 "
                    f"{round(base.get('avg_sentence') or 0, 2)} 字）——只在本范围内可比，"
                    "跨卷比较请先用 `/webnovel-style-learn` 建立文风档案。"
                )
                lines.append("")
            if not s5.get("baseline_adequate"):
                lines.append(
                    f"> ⚠️ 有效章节 {len(s5['chapters'])} 章，基线需 "
                    f"≥{s5.get('min_chapters_for_baseline')} 章：**本次不对「偏离」下结论**，"
                    "上表仅为原始数字。"
                )
                lines.append("")
            top = s5.get("top_ngrams") or []
            if top:
                lines.append("### 重复片段（口癖候选）")
                lines.append("")
                lines.append(
                    f"> 只列**极大重复**片段（长度 ≥4 字、出现 ≥{s5.get('phrase_min_count')} 次），"
                    "已过滤掉长句里的重叠滑窗；本表是线索不是结论，需人工判断是否为有意口癖。"
                )
                lines.append("")
                lines.append("| 片段 | 合计 | 分布 |")
                lines.append("|------|-----:|------|")
                for item in top[:12]:
                    dist = "、".join(f"第{c}章×{n}" for c, n in item["per_chapter"].items())
                    lines.append(f"| {item['gram']} | {item['count']} | {dist} |")
                lines.append("")

    findings = payload.get("findings") or []
    lines.append("## 结论清单")
    lines.append("")
    if not findings:
        lines.append("本次范围内无结论项。")
    else:
        lines.append("| 级别 | 检查项 | 结论 | 详情 |")
        lines.append("|------|--------|------|------|")
        for item in findings:
            icon = SEVERITY_ICON.get(item["severity"], "")
            lines.append(
                f"| {icon} {item['severity']} | {item['code']} | {item['title']} | "
                f"{item['detail']} |"
            )
    lines.append("")
    return "\n".join(lines)


def render_text(payload: Dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "scope-audit",
        f"project_root: {payload.get('project_root')}",
        f"chapters: {payload.get('from_chapter')}-{payload.get('to_chapter')} "
        f"(covered {len(payload.get('chapters') or [])})",
        f"checks: {','.join(payload.get('checks') or [])}",
        f"findings: critical={summary.get('critical', 0)} warning={summary.get('warning', 0)} "
        f"info={summary.get('info', 0)}",
    ]
    for note in payload.get("notes") or []:
        lines.append(f"NOTE {note}")
    for item in payload.get("findings") or []:
        lines.append(
            f"{item['severity'].upper()} {item['code']}: {item['title']} — {item['detail']}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scope_audit.py",
        description="范围级回扫（只读）：S1 Strand 配比 / S4 角色关系称谓漂移 / S5 文体漂移",
    )
    parser.add_argument("--project-root", default="", help="书项目根目录")
    parser.add_argument("--from-chapter", type=int, default=1, help="起始章（含）")
    parser.add_argument("--to-chapter", type=int, default=0, help="结束章（含）；0 表示最新")
    parser.add_argument(
        "--checks",
        default="s1,s4,s5",
        help="要跑的检查器，逗号分隔（s1 / s4 / s5）",
    )
    parser.add_argument("--format", choices=["text", "json", "markdown"], default="text")
    parser.add_argument("--output", default="", help="markdown 报告输出路径")
    parser.add_argument(
        "--dormant-gap", type=int, default=20, help="出场断档阈值（章），启发式，默认 20"
    )
    parser.add_argument(
        "--sentence-tolerance", type=float, default=0.30, help="平均句长偏离容忍度，默认 0.30"
    )
    parser.add_argument(
        "--dialogue-tolerance",
        type=float,
        default=0.15,
        help="对话段占比偏离容忍度（百分点小数），默认 0.15",
    )
    parser.add_argument(
        "--baseline",
        choices=list(BASELINE_MODES),
        default="auto",
        help=(
            "S5 偏离基线：auto=有文风档案就用档案基线（跨卷可比），否则用本范围中位数；"
            "profile=强制要求档案基线（拿不到就不下偏离结论）；range=只用本范围中位数。默认 auto"
        ),
    )
    parser.add_argument("--json", action="store_true", help="等价于 --format json")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    enable_windows_utf8_stdio()
    args = _build_parser().parse_args(list(argv) if argv is not None else None)

    root = resolve_project_root(args.project_root or None)
    if root is None:
        print(json.dumps({"status": "error", "message": "无法解析书项目根目录",
                          "suggestion": "用 --project-root 指定，或先 /webnovel-init"},
                         ensure_ascii=False))
        return 2

    checks = [c.strip().lower() for c in str(args.checks or "").split(",") if c.strip()]
    unknown = [c for c in checks if c not in ALL_CHECKS]
    if unknown:
        print(json.dumps({"status": "error", "message": f"未知检查器：{','.join(unknown)}",
                          "available": list(ALL_CHECKS)}, ensure_ascii=False))
        return 2

    auditor = ScopeAuditor(
        root,
        from_chapter=args.from_chapter,
        to_chapter=args.to_chapter,
        dormant_gap=args.dormant_gap,
        sentence_tolerance=args.sentence_tolerance,
        dialogue_tolerance=args.dialogue_tolerance,
        baseline_mode=args.baseline,
    )
    payload = auditor.run(checks)

    fmt = "json" if args.json else args.format
    if fmt == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    rendered = render_markdown(payload) if fmt == "markdown" else render_text(payload)

    def _write(path: Path, content: str) -> int:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except OSError as exc:
            print(
                json.dumps(
                    {"status": "error", "message": f"报告写入失败：{exc}"}, ensure_ascii=False
                )
            )
            return 1
        print(f"report: {path}")
        return 0

    # 显式 `--output`：任何非 json 格式都落文件（此前该参数声明了却被静默忽略——
    # `--format markdown --output X` 只会把 markdown 打到 stdout，不写 X）。
    if args.output:
        return _write(Path(args.output), rendered)

    if fmt == "markdown":
        print(rendered)
        return 0

    # 默认（text）：报告**文件**固定写 markdown（给人看/归档），屏幕上给 text 摘要。
    rc = _write(auditor.config.webnovel_dir / "reports" / "scope-audit.md", render_markdown(payload))
    if rc:
        return rc
    print(rendered)
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        enable_windows_utf8_stdio()
    raise SystemExit(main())
