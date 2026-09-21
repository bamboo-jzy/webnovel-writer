#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
style_metrics.py - 文体计量原语（`scope_audit` S5 与 `style_profile` 共用）

只做**可复现的字符串统计**，不做判断、不落盘、不改项目任何文件。

分工约定：

- `scope_audit.py` 的 S5 文体漂移：拿这里的单章指标与「全书基线」比，判偏离。
- `style_profile.py` 的文风档案：拿同样的指标 + 跨章聚合，产出可复用的画像。

因此**指标字段名与算法必须逐字段一致**，两侧不得各写一份（历史教训：S5 的切句、
CJK 计数、极大重复过滤三处逻辑都踩过坑，复制一份就等于埋一份回归）。

能判才判：本模块只输出**可直接验证的数字**。章首/章末的「A–E 类型归类」属于定性
判断，需要读原文并由 agent 归纳，不在本模块内做——否则会输出看似精确、实则无据的标签。
"""

from __future__ import annotations

import re
import statistics
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from chapter_paths import find_chapter_file
except ImportError:  # pragma: no cover
    from scripts.chapter_paths import find_chapter_file


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 单章文体指标建立基线所需的最少章数。少于该值只出数字，不出「偏离」结论。
STYLE_BASELINE_MIN_CHAPTERS = 3

#: 口癖候选（重复片段）的最小重复次数。低于该值一律视为偶然措辞。
PHRASE_MIN_COUNT = 3
#: 口癖候选的片段长度区间（字）。同时统计多档长度，再只保留「极大重复」，
#: 否则一段长句里的每个 4 字滑窗都会被算成一次重复，表格会被碎片淹没。
PHRASE_NGRAM_SIZES = (4, 5, 6)

#: 长句阈值（字）。来源：`skills/webnovel-write/references/style-adapter.md`
#: 「>40字的句子 <10%」。
LONG_SENTENCE_THRESHOLD = 40

#: said tag 占比上限。来源同上：「全文 said tag 占比不超过 30%」。
SAID_TAG_MAX_RATIO = 0.30

#: 文风档案（`style_profile.py` 产物）的机读副本文件名与 schema 标识。
#: S5 基线解析、任务书注入（`memory_contract_adapter`）、档案模块三处读的是同一个
#: 文件；常量集中在这里，避免各写一份字符串后改名只改一处。
PROFILE_JSON_NAME = "style_profile.json"
PROFILE_SCHEMA_VERSION = "style-profile/v1"

#: 句中/句末标点（句长切分用）。顿号与分号是句内停顿，不切句。
SENTENCE_BREAK = re.compile(r"[。！？!?…]+")
#: 对话段落的起始引号
DIALOGUE_LEAD = ("“", "「", "『", '"')
CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
MD_NOISE = re.compile(r"^\s*(#{1,6}\s|[-*+]\s|\d+\.\s|>|```|\||---+)")

#: 句内停顿（四字格切分用）。
CLAUSE_BREAK = re.compile(r"[，,、；;：:（）()\s]+")
#: 对话归属动词（said tag）。命中即为「用标签交代说话人」。
SAID_VERBS = (
    "说道", "问道", "答道", "笑道", "冷笑道", "沉声道", "低声道", "淡淡道",
    "开口道", "接口道", "回道", "叹道", "解释道", "喝道", "吼道", "嘟囔道",
)
SAID_TAG = re.compile("|".join(SAID_VERBS))

#: 章末悬念标点（仅作「末段是否留未完感」的粗信号，不下定性结论）。
CLOSING_SUSPENSE = re.compile(r"[？?…]$")


def cjk_len(text: str) -> int:
    """统计文本里的中文字符数（剔除标点、空白与拉丁字符）。"""
    return sum(len(run) for run in CJK_RUN.findall(text))


# ---------------------------------------------------------------------------
# 文本 → 指标
# ---------------------------------------------------------------------------

def split_paragraphs(text: str) -> List[str]:
    """按行切段：丢掉空行与 markdown 噪声行（标题/列表/表格/代码围栏）。"""
    paragraphs: List[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or MD_NOISE.match(line):
            continue
        paragraphs.append(line)
    return paragraphs


def sentence_lengths(paragraphs: Iterable[str]) -> List[int]:
    """段落 → 句长列表（≥2 字的片段才算一句，避免标点残片拉低均值）。"""
    lengths: List[int] = []
    for paragraph in paragraphs:
        for chunk in SENTENCE_BREAK.split(paragraph):
            length = cjk_len(chunk)
            if length >= 2:
                lengths.append(length)
    return lengths


def style_metrics_for_text(
    text: str, *, chapter: int = 0, file_name: str = ""
) -> Optional[Dict[str, Any]]:
    """从正文文本算出单章文体指标；无有效段落时返回 None。

    字段名与 `scope_audit` S5 的 chapters 项保持逐字段一致（新增字段只追加）。
    """
    paragraphs = split_paragraphs(text)
    if not paragraphs:
        return None

    lengths = sentence_lengths(paragraphs)
    dialogue_paragraphs = [p for p in paragraphs if p.startswith(DIALOGUE_LEAD)]
    dialogue_indices = {i for i, p in enumerate(paragraphs) if p.startswith(DIALOGUE_LEAD)}
    paragraph_lengths = [cjk_len(p) for p in paragraphs]
    cjk_total = sum(paragraph_lengths)

    long_sentences = sum(1 for length in lengths if length > LONG_SENTENCE_THRESHOLD)
    # 四字格：句内停顿切出的、恰好 4 个中文字的片段（含成语与四字短语）。
    clause_total = 0
    four_char = 0
    for paragraph in paragraphs:
        for clause in CLAUSE_BREAK.split(paragraph):
            if not clause:
                continue
            clause_total += 1
            if cjk_len(clause) == 4:
                four_char += 1
    # said tag：只算「对话段里出现归属动词」的比例——非对话段里的「说道」多是转述，
    # 计入会把占比虚高，得出「said tag 过多」的假结论。
    said_tagged = sum(1 for i in dialogue_indices if SAID_TAG.search(paragraphs[i]))

    first = paragraphs[0]
    last = paragraphs[-1]
    return {
        "chapter": int(chapter),
        "file": str(file_name),
        "paragraphs": len(paragraphs),
        "cjk_chars": cjk_total,
        "sentences": len(lengths),
        "avg_sentence": round(statistics.fmean(lengths), 2) if lengths else 0.0,
        "median_sentence": round(statistics.median(lengths), 2) if lengths else 0.0,
        "longest_sentence": max(lengths) if lengths else 0,
        "avg_paragraph": round(statistics.fmean(paragraph_lengths), 2),
        "longest_paragraph": max(paragraph_lengths),
        "dialogue_paragraph_ratio": round(len(dialogue_paragraphs) / len(paragraphs), 4),
        "long_sentence_ratio": round(long_sentences / len(lengths), 4) if lengths else 0.0,
        "four_char_ratio": round(four_char / clause_total, 4) if clause_total else 0.0,
        "said_tag_ratio": (
            round(said_tagged / len(dialogue_paragraphs), 4) if dialogue_paragraphs else 0.0
        ),
        "opening_dialogue": bool(paragraphs[0].startswith(DIALOGUE_LEAD)),
        "opening_cjk": paragraph_lengths[0],
        "closing_cjk": paragraph_lengths[-1],
        "closing_suspense": bool(CLOSING_SUSPENSE.search(last.strip())),
    }


def repeated_phrases_for_text(text: str) -> Dict[str, int]:
    """返回文本内「重复片段 → 次数」。

    只保留**极大重复**：若某个片段是另一个更长片段的一部分、且两者次数相同，
    则短的那个没有额外信息（它只是长片段的滑窗），丢弃。

    为什么必须这样：正文里 CJK 连续段可以很长（标点才断词），若直接对每个位置
    取 4-gram 计数，一段 30 字的句子会贡献 27 个互相重叠的 4-gram，表格里出现
    `鞋头对齐` / `鞋尖朝着` 这类**跨词碎片**，真口癖反而被淹没。加极大性过滤后，
    留下的才是「整段反复出现的措辞」。
    """
    from collections import Counter

    counts: Counter = Counter()
    for match in CJK_RUN.finditer(text):
        run = match.group(0)
        # 除固定长度滑窗外，把「整段 CJK 连续段」也当作一个候选片段。
        # 否则一段 8 字的句子反复出现时，8 个位置上的等长滑窗会**互相独立**地
        # 全部通过极大性检查（同长片段之间无从属关系），表格里就会出现一堆重叠变体。
        # 注意：整段长度必须 ≥ 最小片段长度，否则会把「年」这类单字段也当成口癖候选。
        sizes = set(PHRASE_NGRAM_SIZES)
        if len(run) >= min(PHRASE_NGRAM_SIZES):
            sizes.add(len(run))
        for n in sorted(sizes):
            if n > len(run):
                continue
            for i in range(len(run) - n + 1):
                counts[run[i : i + n]] += 1

    candidates = {g: c for g, c in counts.items() if c >= PHRASE_MIN_COUNT}
    kept: Dict[str, int] = {}
    for gram, count in candidates.items():
        # 只要存在更长的候选包含它、且次数相同，它就是该长片段的滑窗，丢弃。
        # 含三层的场景靠「包含关系可传递」自动收敛到最长的那一个。
        if any(
            len(other) > len(gram) and other_count == count and gram in other
            for other, other_count in candidates.items()
        ):
            continue
        kept[gram] = count
    return kept


# ---------------------------------------------------------------------------
# 章节文件 → 指标
# ---------------------------------------------------------------------------

def read_chapter_text(project_root: Path, chapter: int) -> Tuple[str, str]:
    """读章节正文；缺失或不可读返回 ("", "")。"""
    path = find_chapter_file(Path(project_root), chapter)
    if path is None or not path.is_file():
        return "", ""
    try:
        return path.read_text(encoding="utf-8"), path.name
    except OSError:
        return "", ""


def metrics_for_chapter(project_root: Path, chapter: int) -> Optional[Dict[str, Any]]:
    """单章文体指标；缺文件返回 None（调用方负责登记缺失章）。"""
    text, name = read_chapter_text(project_root, chapter)
    if not text:
        return None
    return style_metrics_for_text(text, chapter=chapter, file_name=name)


def phrases_for_chapter(project_root: Path, chapter: int) -> Dict[str, int]:
    """单章的极大重复片段；缺文件返回空字典。"""
    text, _ = read_chapter_text(project_root, chapter)
    if not text:
        return {}
    return repeated_phrases_for_text(text)


# ---------------------------------------------------------------------------
# 聚合
# ---------------------------------------------------------------------------

#: 句长分档（字）：短句爆点 / 常规 / 偏长 / 超长（超长即 style-adapter 的长句红线）。
SENTENCE_BUCKETS: Tuple[Tuple[str, int, Optional[int]], ...] = (
    ("≤10", 0, 10),
    ("11-20", 11, 20),
    ("21-40", 21, 40),
    (">40", 41, None),
)


def _percentile(values: Sequence[float], ratio: float) -> float:
    """线性插值分位数（避免依赖 numpy；values 已排序）。"""
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    position = ratio * (len(values) - 1)
    low = int(position)
    high = min(low + 1, len(values) - 1)
    weight = position - low
    return float(values[low]) * (1 - weight) + float(values[high]) * weight


def summarize_chapters(metrics: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """把逐章指标聚合成画像数字。

    标量项给 `mean` / `median` / `p25` / `p75`，避免只看均值把离群章当成常态。
    """
    if not metrics:
        return {"chapter_count": 0}

    def _stat(key: str) -> Dict[str, float]:
        values = sorted(float(m.get(key) or 0.0) for m in metrics)
        return {
            "mean": round(statistics.fmean(values), 4),
            "median": round(statistics.median(values), 4),
            "p25": round(_percentile(values, 0.25), 4),
            "p75": round(_percentile(values, 0.75), 4),
        }

    scalar_keys = (
        "cjk_chars",
        "paragraphs",
        "sentences",
        "avg_sentence",
        "median_sentence",
        "longest_sentence",
        "avg_paragraph",
        "longest_paragraph",
        "dialogue_paragraph_ratio",
        "long_sentence_ratio",
        "four_char_ratio",
        "said_tag_ratio",
        "opening_cjk",
        "closing_cjk",
    )
    summary: Dict[str, Any] = {
        "chapter_count": len(metrics),
        "first_chapter": min(int(m["chapter"]) for m in metrics),
        "last_chapter": max(int(m["chapter"]) for m in metrics),
        "scalars": {key: _stat(key) for key in scalar_keys},
    }

    # 长句档只能从单章指标直接得到；完整分档由 bucket_histogram() 用逐句长度算。
    summary["long_sentence_ratio_gt40"] = _stat("long_sentence_ratio")["mean"]
    summary["opening_dialogue_ratio"] = round(
        statistics.fmean(1.0 if m.get("opening_dialogue") else 0.0 for m in metrics), 4
    )
    summary["closing_suspense_ratio"] = round(
        statistics.fmean(1.0 if m.get("closing_suspense") else 0.0 for m in metrics), 4
    )
    return summary


def bucket_histogram(lengths: Sequence[int]) -> Dict[str, Dict[str, float]]:
    """句长分档分布（描述性，不是红线）。

    `ratio` 为该档句子占比，`count` 为句数；正文为空时返回全 0，不报错。
    """
    total = len(lengths)
    histogram: Dict[str, Dict[str, float]] = {}
    for label, low, high in SENTENCE_BUCKETS:
        count = sum(
            1
            for length in lengths
            if length >= low and (high is None or length <= high)
        )
        histogram[label] = {
            "count": count,
            "ratio": round(count / total, 4) if total else 0.0,
        }
    return histogram


def top_phrases(
    grams_by_chapter: Dict[int, Dict[str, int]], chapters: Sequence[int], limit: int = 20
) -> List[Dict[str, Any]]:
    """把逐章重复片段汇总成 top 列表（结构同 S5 的 `top_ngrams`）。"""
    from collections import Counter

    combined: Counter = Counter()
    for mapping in grams_by_chapter.values():
        for gram, count in mapping.items():
            combined[gram] += count
    return [
        {
            "gram": gram,
            "count": count,
            "per_chapter": {
                str(ch): grams_by_chapter.get(ch, {}).get(gram, 0)
                for ch in chapters
                if grams_by_chapter.get(ch, {}).get(gram, 0)
            },
        }
        for gram, count in sorted(combined.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))[
            :limit
        ]
    ]
