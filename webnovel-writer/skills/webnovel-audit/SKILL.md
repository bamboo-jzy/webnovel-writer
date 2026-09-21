---
name: webnovel-audit
description: 对网文项目做范围级回扫/跨章体检（/webnovel-audit）——检查 Strand 三线配比、角色·关系·称谓漂移、文体漂移，只输出报告不改正文。
version: 0.1.0
allowed-tools: Read Bash
argument-hint: "[--from-chapter N] [--to-chapter N] [--checks s1,s4,s5] [--baseline auto|profile|range] [--format text|json|markdown] [--output PATH]"
---

# Webnovel Audit

## 目标

跨章回扫当前书项目：单章 `review` 只能查这一章的事实一致性，查不到「写到第 80 章时
主线是否连续过久、角色称谓有没有漂、句长/对话占比有没有整体位移」这类**范围级**问题。
本 skill 补这一段。

## 原则

1. **只读**。不改正文、不改索引、不改状态；`index.db` 以 `query_only` 方式打开。只出报告。
2. **能判才判，不能判就说不能判**。样本不足或字段缺失时明确输出「无法判断」，
   **绝不渲染成「正常」**——这是 `health_report.md` 旧实现把「9 条伏笔全缺目标章」
   写成「✅ 所有伏笔进度正常」的教训。
3. **阈值来源如实披露**。节奏类阈值优先取题材 profile（`../../references/genre-profiles.md`），
   回落 `config` 默认值时必须在报告里标注，不假装专业。
4. **只报告不给改写建议**。不评价文笔、不判定「该改成什么」，需要改动时交给
   `/webnovel-chapter-revise` 或 `/webnovel-volume-revise`。
5. 统一用 `python -X utf8`，避免中文路径编码问题。

## 执行

准备路径：

```bash
export WORKSPACE_ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
export SCRIPTS_DIR="${CLAUDE_PLUGIN_ROOT:?}/scripts"
```

全量回扫（默认 `--checks s1,s4,s5`，报告落到 `.webnovel/reports/scope-audit.md`）：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${WORKSPACE_ROOT}" scope-audit
```

常用变体：

```bash
# 只看某几项检查
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${WORKSPACE_ROOT}" scope-audit --checks s1,s5

# 只看某一卷/某一段
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${WORKSPACE_ROOT}" scope-audit --from-chapter 41 --to-chapter 80

# S5 基线：默认优先用文风档案（跨卷可比）；要旧口径的「范围自比」就显式指定
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${WORKSPACE_ROOT}" scope-audit --checks s5 --baseline range

# 机器可读
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${WORKSPACE_ROOT}" scope-audit --format json

# 直接打印 markdown（不落文件）
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${WORKSPACE_ROOT}" scope-audit --format markdown

# 指定输出路径（任何非 json 格式都会落文件；目录不存在会自动创建）
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${WORKSPACE_ROOT}" scope-audit --format markdown --output "${WORKSPACE_ROOT}/.webnovel/reports/scope-audit.md"
```

## 检查项与供数前提

| 检查器 | 内容 | 数据来源 | 供数状态 |
|--------|------|----------|----------|
| **S1 Strand 配比** | Quest/Fire/Constellation 三线占比、主线最大连续、感情线最大断档 | `state.strand_tracker` × `genre-profiles.md` 阈值 | ✅ 就绪 |
| **S4 漂移** | 角色/地点/物品的**称谓跨章变化**、角色状态变更链、出场断档、状态取值反复 | `index.db` 的 `appearances` / `state_changes` / `relationships` | ✅ 就绪 |
| **S5 文体漂移** | 句长/段长分布、对话段占比、高频 4 字片段（口癖候选） | 直接统计 `正文/*.md`，**不依赖任何表**；偏离基线默认取 `.webnovel/style_profile.json` 的 `observed`（缺失时回落本次范围中位数，报告会写明用的是哪一种） | ✅ 就绪 |

**未实现的两项（勿假装有结论）**：

- **S2 伏笔回收节奏**：`state.plot_threads.foreshadowing` 的 9 条全部缺 `target_chapter`，
  逾期在语义上不可计算。数据补齐前做了也只能输出「无法判断」。
- **S3 钩子强度 / 爽点 / 微兑现分布**：`index.db` 的 `chapter_reading_power` 表
  **从建表起从未被写入**（`save-chapter-reading-power` 无任何生产调用方），
  底层的 `get-hook-type-stats` / `get-pattern-usage-stats` 恒返回空。
  需先让产出链生成 `hook_type` / `hook_strength` / `micropayoff` 字段。

取证与处置计划见 `../../references/index/skill-gap-assessment-2026-09-17.md` 附录 B。

## 怎么读报告

### 1. 先看「本次不下结论的项」

报告顶部会列出因**样本不足**或**阈值来源不明**而没有下结论的项：

- **占比结论需要 ≥10 章**（`STRAND_RATIO_MIN_SAMPLE`）。占比目标是整卷/全书口径，
  一本 400 章的书在第 2 章必然是 100% Quest——那不是问题，所以短样本时明确
  **不对占比下结论**，只把连续性检查（连续/断档章数）照常给结论。
- **文体偏离结论需要 ≥3 章**（`STYLE_BASELINE_MIN_CHAPTERS`）。少于 3 章只给原始数字，
  不判「偏离」。

看到这些条目时，**不要把空结论当成「一切正常」**，那是两种不同的东西。

### 2. 再看阈值来源

S1 会标注阈值来自哪个题材 profile、经哪个标签命中，例如：

```text
阈值来源：题材 profile `rules-mystery`（规则怪谈，经 route「规则怪谈」命中）
```

若显示「**config 默认值**」，说明没匹配到题材 profile，结论可能不适用于本书题材——
此时应提示作者检查 `state.project_info` 的 `genre` / `genre_tags`。

### 3. 最后看结论清单

结论分三级：`critical`（必看）、`warning`（该处理）、`info`（需人工确认）。
本 skill 当前产出的多为 `info`——**称谓漂移与出场断档都可能是合理的**（角色本来就改了
称呼、配角本就该下线），报告的作用是让你在写到几十章时能一次看到全部候选，而不是替你裁决。

### 4. S5 的「重复片段」表是线索，不是结论

该表只列**极大重复**片段（长度 ≥4 字、出现 ≥3 次），并且已经过滤掉长句里的重叠滑窗——
否则一段 30 字的句子里每个 4 字窗口都会被算成「重复」，表格会被 `鞋尖朝着` 这类跨词碎片塞满。
即便如此，**命中也不等于口癖**：`二十三点` 完全可能是这本书的核心意象。它的用途是提示
「这几个措辞你在反复用」，判断是否有意为之只能由作者决定。样本少于 3 章时该表尤其不可靠。

## 边界

- **与 `/webnovel-doctor` 的分工**：doctor 查**结构完整性**（文件、JSON、SQLite、依赖是否在位，
  有没有漏章）；audit 查**内容层面的跨章趋势**。两者都只读，都不修。
- **与 `/webnovel-status` 的分工**：`status` 生成 `health_report.md`（单书全量快照，含伏笔紧急度、
  爽点节奏、关系图）；`scope-audit` 是**范围可指定**的回扫，且对每一处结论负责标注数据来源与
  样本是否充分。
- **`review` 仍是单章事实闸门**，本 skill 不替代它：`review` 的结论决定能否 commit，
  audit 的结论只影响你的修改计划。

## 成功标准

- 报告文件生成在 `.webnovel/reports/scope-audit.md`（`--format json/markdown` 默认只打印到屏幕，
  要落文件用 `--output PATH`；该路径是显式参数，不是默认行为的别名）。
- 每一项结论都能追到数据来源；样本不足或缺字段的项必须出现在「本次不下结论的项」里。
- 正文文件与 `index.db` 的 mtime 在运行前后不变。
