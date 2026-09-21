---
name: webnovel-style-learn
description: 学习文风并生成可复用的文风档案（/webnovel-style-learn）——从 正文/ 学「现状画像」，从 文风/ 学「目标画像」，产出脚本段（数字）+ 归纳段（人工），并用摘要注入写作任务书。
version: 0.1.0
allowed-tools: Read Bash
argument-hint: "[build|show|diff] [--source accepted|all] [--max-chapters N]"
---

# Webnovel Style Learn

## 目标

把「文风」从一次性判断变成**可复用档案**，供三个消费方复用：

| 消费方 | 用档案做什么 |
|--------|--------------|
| Step 1 context-agent | 任务书里给「目标画像 + 待收敛项 + 硬约束结论」 |
| Step 4 润色 / 风格适配 | 按待收敛项决定改哪一类句式（细则仍在 `skills/webnovel-write/references/` 下） |
| Step 6 之后的回扫 | S5 文体漂移可与档案基线对照，跨卷可比 |

## 原则

1. **现状 ≠ 目标，永不混用**。`正文/` 学到的是「作者已经写成什么样」（`observed`），
   `文风/` 学到的是「作者要模仿成什么样」（`target`）。把现状当标准，等于把作者正想
   改掉的毛病固化成规则——档案里两者分开存放、分开注入。
2. **数字由脚本出，人只写判断**。句长/段长/对话占比/口癖候选等可复现指标一律由
   `style-profile build` 生成，标记为脚本段、**禁止手改**（下次 build 覆盖）。
   只有「读原文才能得到的判断」（口癖是否有意、节奏观感）才写进归纳段。
3. **能判才判**。样本不足（`正文` 有效章节 < 3）时只说「样本不足」，不给「正常」结论。
4. **不搬运参考文本**。档案只为每个参考文件保留首个段落的 ≤120 字**机械样本**；
   参考书正文留在 `文风/` 目录本身，不进档案、不进任务书。
5. **不写正文**。本 skill 只产档案，改稿交给 `/webnovel-chapter-revise` 或 Step 4 润色。

## 与相邻能力的分工

| 能力 | 学什么 | 落点 |
|------|--------|------|
| `/webnovel-learn` | 单条写作经验（手写描述） | `.webnovel/project_memory.json` 的 `patterns` |
| `设定集/风格契约.md` | 作者手写的硬性文风约定 | 人工维护 |
| **本 skill** | **整体文风画像 + 差异** | `.webnovel/style_profile.json` + `文风/文风档案.md` |
| `/webnovel-audit`（S5） | 单章是否偏离基线（默认**取自本档案**的 observed，跨卷可比） | 只读报告，不改档案 |

## 执行

准备路径：

```bash
export WORKSPACE_ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
export SCRIPTS_DIR="${CLAUDE_PLUGIN_ROOT:?}/scripts"
```

生成档案（默认口径 `accepted`：只统计已提交接收的章）：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${WORKSPACE_ROOT}" style-profile build
```

常用变体：

```bash
# 只看摘要（默认 text 输出就是注入摘要本身）
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${WORKSPACE_ROOT}" style-profile show

# 只看现状与目标的差异
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${WORKSPACE_ROOT}" style-profile diff

# 章节多时收敛样本量
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${WORKSPACE_ROOT}" style-profile build --max-chapters 40

# 越权：连未提交/被拒的草稿一起学（口径风险自负，见失败恢复表）
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${WORKSPACE_ROOT}" style-profile build --source all
```

## 两个学习源怎么准备

### 源 A `正文/`（默认自动）

无需人工准备，但口径由 `accepted` 提交决定：

- 只统计 `.story-system/commits` 里 `status=accepted` 且能读到正文文件的章。
- 提交被拒、还没提交、文件名不合 `第NNNN章*.md` 规约的章会被跳过，并在档案的
  「口径提示」里列出——**跳过不等于不存在**，看到提示就去核对，不要当成已覆盖。
- 超过 `--max-chapters`（默认 60）时按「均匀步长 + 最近 5 章必抽」抽样，抽样口径写进档案。

### 源 B `文风/`（人工放入）

- 放**要模仿的参考文本**：他人作品片段、范文、作者自己的旧作，`.md` / `.txt` 均可，可放子目录。
- **不要放整本书**。该目录会随 Git 版本点提交，且档案只需统计特征；放几十兆原文会让
  备份膨胀、也可能涉及版权。
- 目录不存在或为空时，档案明确输出「无目标画像」，**不得用现状代替目标**。

## 归纳段怎么写

`文风/文风档案.md` 里两个标记之外的部分由本 skill / 作者维护，`build` 不会覆盖。写法：

1. 先 `build` 拿数字，再对着数字抽样读原文（最近 3-5 章 + 参考文本各 1 段）。
2. 只写**可执行**的观察，对齐润色阶段的口径：句式（长短句配比）、对话
   （said tag 用得多不多）、口癖（哪些措辞在反复用）、禁用项（这本书不这么写）。
3. 不写「更有感染力」这类无法执行的评价，也不重复脚本段已有的数字。
4. 与 `设定集/风格契约.md` 冲突时，以作者手写契约为准并在归纳段注明。

## 成功标准

- `.webnovel/style_profile.json` 存在且 `injection_digest` 非空；`schema_version` 为 `style-profile/v1`。
- `文风/文风档案.md` 的脚本段含：现状画像、目标画像（或明确「无目标画像」）、待收敛项、硬约束检查。
- 重跑 `build` 后，归纳段内容逐字保留。
- 档案里的每个数字都能在 `正文/` 或 `文风/` 里找到对应样本；样本不足的项必须显式说明。

## 失败恢复

| 故障 | 恢复方式 |
|------|---------|
| `accepted 提交为空` | 正常情况（还没提交过章）。先完成一章 `/webnovel-write` 的提交链；确需用草稿建画像再显式 `--source all` |
| 章在 accepted 名单里但读不到正文 | 检查正文文件名是否符合 `第NNNN章*.md`；档案会在「口径提示」里列出这些章 |
| `文风/` 不存在或为空 | 不影响现状画像；档案输出「无目标画像」。需要目标画像就先放参考文本再 `build` |
| `无目标画像时仍看到差异表` | 不可能——差异只在目标可用时计算；若看到空表说明本次未启用目标对比 |
| `style_profile.json` 损坏 | 重跑 `build` 重建（档案是派生物，不手工修 json） |
| 归纳段被覆盖 | 检查是否误删了两个 `<!-- STYLE-PROFILE:... -->` 标记；标记缺失时脚本会把整份档案当人工内容保留，不会覆盖，但不会再更新脚本段 |
| 想确认改了哪些 | `diff` 只读已落盘档案，重新对比现状与目标，不重新统计 |

维护缺口与取证记录见 `../../references/index/skill-gap-assessment-2026-09-17.md`。
