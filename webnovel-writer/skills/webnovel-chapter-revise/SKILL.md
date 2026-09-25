---
name: webnovel-chapter-revise
description: 通过确认式引导修改已有章纲，分析影响范围，定向编辑后刷新章级 Story System 合同并重登记章纲 revision。
allowed-tools: Read Write Edit Grep Bash AskUserQuestion
argument-hint: "[章号] [修改诉求，可选]"
---

# 章纲修改

主 agent 职责：在不静默覆盖作者内容的前提下，引导作者修改指定章节的独立章纲文件，先展示影响分析并取得确认，再备份、定向编辑、校验、刷新章级合同并重登记状态。不修改卷级规划的叙事决策，不直接写正文。

## 用法

```text
/webnovel-chapter-revise 15
/webnovel-chapter-revise 15 把反派出场提前到本章中段
/webnovel-chapter-revise 15 调整章末钩子，改为留下悬念
```

## 硬规则

1. 修改前必须读取目标章纲文件、所属卷三份卷级文件、总纲、相邻章纲和相关状态证据；缺失或为空立即阻断。
2. 先输出修改方案和影响范围，作者确认后才能写入；不得先改文件再询问。
3. 已有非空章纲文件是作者资产，只能定向编辑，禁止整文件重写或静默覆盖人工内容。
4. 明确列出保留项、修改项和无法自动裁决项；确认轮**无上限**，每轮必须带「不再讨论，按当前已确认范围结束」选项；冲突时暂停并让作者选择保留、重做或停止。
5. 章纲内容变化后，必须重新解析章纲字段并刷新该章 Story System 合同，再用 `update-state --chapter-planned` 重登记章纲 revision 与合同 revision；登记命令会自动重算，不得手工拼写 hash。
6. 不修改卷节拍表、卷时间线、卷级详细大纲；发现章纲诉求与卷级规划冲突时，阻断并指向 `/webnovel-volume-revise`。
7. **只允许修改最后一章的章纲**（方向透传「卷-章」节点，依据 `${SKILL_ROOT}/../../references/handoff/direction-handoff.md`）：其余已规划章是既定事实，诉求一律阻断并说明；卷纲也必须与那些章的章纲保持同向。
8. **方向检查必做**：写入前后都要确认新章纲与卷纲同向。报出候选偏离时按方向透传的确认话术与作者裁决，用 `handoff --record` 落盘；裁决为「改卷纲」时停止本 Skill，先过上一层节点检查 `handoff --node master_to_volume`，再由 `/webnovel-volume-revise {volume_id}` 修改卷纲。
9. 校验或合同刷新失败时保留修改前备份，不登记新的可信 revision；不得报告为完成。

## 边界

- 只处理**已存在的独立章纲**（`大纲/第N章-*.md`）。章纲不存在时停止，并指向 `/webnovel-chapter-plan {volume_id} {range}` 生成。
- **只处理最后一章的章纲**。该章不是最后一章时停止，并说明它是既定事实；机器通道只支持最后一章。
- 若该章正文已存在或已有 accepted commit，章纲修订**不**自动改动正文或 commit；完成后明确提示作者运行 `/webnovel-chapter-reload {chapter}` 走正文重载链。
- 章纲修订只影响当前章，不批量修改其他章；影响后续章纲承接的，列入"建议确认"由作者决定是否另行修订。
- 纯人工编辑章纲后，不需要运行本 Skill；下次写作前 `write-gate --stage prewrite` 会以 `chapter_contract_stale` 阻断，此时运行 `/webnovel-chapter-plan` 重刷合同，或用本 Skill 走完确认-刷新-登记闭环。

## 环境准备

```bash
export WORKSPACE_ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
export SKILL_ROOT="${CLAUDE_PLUGIN_ROOT}/skills/webnovel-chapter-revise"
export SCRIPTS_DIR="${CLAUDE_PLUGIN_ROOT}/scripts"
export PROJECT_ROOT="$(python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${WORKSPACE_ROOT}" where)"
```

开始前运行：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" preflight
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" placeholder-scan --format text
```

## Step 1：读取与基线检查

定位目标章纲文件（`大纲/第{chapter}-*.md`，唯一、非空、可读；多个候选时停止并让作者裁决），并确认以下文件存在且非空：

```text
大纲/第{volume_id}卷-节拍表.md
大纲/第{volume_id}卷-时间线.md
大纲/第{volume_id}卷-详细大纲.md
大纲/总纲.md
.webnovel/state.json
```

同时读取：

- 相邻章纲（前一章、后一章）的 CBN/CPNs/CEN、时间锚点和钩子。
- `chapters_planned` 中该章的 `chapter_outline_revision`、`source_volume_revision`、`contract_revision`、`planned_at`。
- `chapter_revisions` 中该章是否已有正文 revision、是否已有 accepted commit。
- 该章引用的关键实体、伏笔和倒计时状态。

先做**章粒度边界与卷-章方向检查**（依据 `${SKILL_ROOT}/../../references/handoff/direction-handoff.md`）：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" handoff \
  --node volume_to_chapter --check --target {chapter_num} --format json
```

- `target_allowed=false`（该章不是最后一章）→ 阻断，不修改任何文件。
- `candidates` 非空（章纲「关键实体」在卷纲与决策卡中找不到依据、卷纲 revision 已过期等）→
  记入 Step 2 的修改方案一并裁决；`manual_items` 是低置信提示，须上下文判断。

再运行写前门禁取当前快照，确认合同是否已过期：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" write-gate \
  --chapter {chapter_num} --stage prewrite --format json
```

若返回 `volume_plan_stale` 或上游正文阻断，先停止本章修订，按修复提示处理前置项。

## Step 2：形成修改方案

将作者诉求拆成：

- 修改目标：要改变的剧情决策或节点。
- 保留项：明确不能改变的目标、CBN/CPNs/CEN、时间锚点、倒计时、钩子或伏笔推进。
- 影响文件：仅目标章纲文件；不得扩展到卷级文件或其他章纲。
- 影响面：与上一章 CEN→本章 CBN、本章 CEN→下一章 CBN 的承接是否仍成立；章内时间是否仍不回跳；倒计时算术是否一致；伏笔是否与卷级规划一致。
- 风险：设定冲突、伏笔提前/延后、正文与章纲偏离（若该章已有正文）。
- 方向检查结论（卷-章，来自 Step 1）：候选偏离逐条、须裁决项，以及作者的裁决口径（`align_downstream` / `align_upstream` / `accepted_deviation`）。

用有限选项向作者确认（话术与「不再讨论」语义沿用 `${SKILL_ROOT}/../webnovel-chapter-plan/references/outlining/chapter-dialogue.md` 的「每轮话术模板」）：

```text
A. 按方案修改本章章纲，并刷新章级合同与状态
B. 只修改章纲文件，暂不刷新合同（后续写作会被 prewrite 门禁阻断）
C. 停止，不修改文件
D. 不再讨论，按当前已确认范围结束
```

如果作者没有明确选择，继续询问，不得自行写入。确认轮无上限；作者选择「不再讨论」即按当前已确认范围结束确认，未确认的改动一律不写入。若诉求与卷级规划冲突，停止并指向 `/webnovel-volume-revise {volume_id}`。

方向检查报出的候选偏离须单独用同一套话术裁决，并在**写入前**落盘：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" handoff \
  --node volume_to_chapter --target {chapter_num} --record \
  --decision {align_downstream|align_upstream|accepted_deviation} \
  --reason "{作者理由}" --impact {chapter_num} \
  --expected-input "{input_token}" --format json
```

- `align_downstream` → 本章章纲向卷纲对齐，继续 Step 3。
- `align_upstream` → **停止本 Skill**：先过上一层节点检查
  `handoff --node master_to_volume --target {volume_id}`，再由 `/webnovel-volume-revise {volume_id}` 修改卷纲；
  卷纲变化后须重跑 `/webnovel-volume-reload {volume_id}`，然后回到本 Skill 重做章纲。
- `accepted_deviation` → 记录后继续，在报告中列为有意保留的偏离。

`--record` 只写 `.story-system/handoffs/`，不改任何章纲、卷纲或正文；它不替代 Step 3 的备份与定向编辑。

## Step 3：确认后备份并定向修改

确认后先备份当前章纲。若项目在 Git 管理下，使用备份管理器（会同时覆盖 `大纲/` 等创作文件）：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" backup \
  -- --chapter {chapter_num} --chapter-title "revise-outline"
```

若 Git 备份不可用，至少把章纲文件复制到 `.webnovel/backups/chapter_{chapter}_outline_{时间戳}/` 下再修改。备份失败则停止，不进行任何编辑。

备份成功后，只对章纲文件做定向编辑：修改受影响的执行指令字段、结构化节点（CBN/CPNs/CEN/必须覆盖节点/本章禁区）或钩子；保留作者其他内容和章节号、标题行。不得改变文件命名规则（`第N章-标题.md`）。

## Step 4：校验章纲

修改后必须检查：

- 章纲文件存在且非空，章节号、标题未变。
- 执行指令字段齐全：目标、阻力、代价、时间锚点、章内时间跨度、与上章时间差、倒计时状态、爽点、Strand、视角/主角、关键实体、本章变化、章末未闭合问题、钩子。
- `CBN/CPNs/CEN/必须覆盖节点/本章禁区` 仍可被 parser 识别；每章固定 1 个 CBN、2-4 个 CPNs、1 个 CEN。
- 与上一章 CEN 的因果/状态承接仍成立；本章 CEN 对下一章 CBN 的承接仍成立。
- 章内时间不回跳；倒计时算术一致；闪回显式标注。
- 伏笔推进与卷级规划一致；不凭空关闭未规划的开放环。
- 不含 `[待...]`、`暂名`、`{占位}` 等当前章不可执行占位符。
- 不发生与设定集、总纲或卷级节拍冲突的能力、关系和事实跳变。

校验失败时恢复备份或修正后重新校验；不得跳过校验直接刷新合同。

校验通过后重跑一次方向检查，确认章纲与卷纲仍同向、且未新增候选偏离：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" handoff \
  --node volume_to_chapter --check --target {chapter_num} --format json
```

新增候选偏离时按 Step 2 的话术重新裁决，不得带着未裁决的偏离进入 Step 5。

## Step 5：刷新章级 Story System 合同

从修改后的章纲重新解析 `CHAPTER_GOAL`（不得手写泛化目标或从摘要猜测），逐章执行：

```bash
GENRE="$(python -X utf8 -c "import json; s=json.load(open('${PROJECT_ROOT}/.webnovel/state.json',encoding='utf-8')); pi=s.get('project_info',{}); print(pi.get('genre') or s.get('project',{}).get('genre',''))")"

python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" \
  story-system "${CHAPTER_GOAL}" \
  --genre "${GENRE}" \
  --chapter {chapter_num} \
  --persist --emit-runtime-contracts --format both
```

合同生成后检查对应的：

```text
.story-system/MASTER_SETTING.json
.story-system/volumes/volume_NNN.json
.story-system/chapters/chapter_NNN.json
.story-system/reviews/chapter_NNN.review.json
```

合同失败时只重跑本章，不把本章标记为完成。runtime builder 必须实际读取本次修改后的章纲节点和执行字段。

## Step 6：重登记章纲状态

只有章纲校验、合同生成和合同文件检查全部通过，才更新状态（登记命令会按当前文件内容自动重算 `chapter_outline_revision` 和 `contract_revision`）：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" update-state -- \
  --chapter-planned {chapter_num} \
  --chapter-outline-file "大纲/第{chapter_num}-标题.md" \
  --volume {volume_id} \
  --volume-revision "{volume_revision}"
```

`{volume_revision}` 取当前卷在 `volumes_planned` 中的最新 `planning_revision`，与 Step 1 读取的基线一致；若基线以来卷纲被人工改动，先停止并指向 `/webnovel-volume-reload {volume_id}`。

登记后再运行一次写前门禁确认合同已不回报过期：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" write-gate \
  --chapter {chapter_num} --stage prewrite --format json
```

若该章已有正文或 accepted commit，门禁可能仍报正文相关状态；这是预期行为，不属于章纲修订失败，需在最终报告中提示正文重载。

## Step 7：收尾与恢复

再次运行：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" placeholder-scan --format text
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" user-report \
  --stage chapter-plan --volume {volume_id} --chapter {chapter_num} --format text
```

- 若该章尚未写作：下一步指向 `/webnovel-write {chapter_num}`。
- 若该章已有正文且本次章纲修订改变了情节决策：提示正文可能与章纲偏离，需运行 `/webnovel-chapter-reload {chapter_num}` 预览并走对账/重审链。

## 作者友好报告契约

最终回复以以下结构输出，不输出原始 JSON、traceback、token 统计或长日志：

```text
总状态：已完成 / 部分完成 / 需要你处理 / 未完成。

一、修改与文件
- 修改了什么、保留了什么、备份在哪里、合同是否已刷新。

二、影响与异常
- 方向检查（卷-章）：可改单元 第 {chapter_num} 章 / 既定事实 第 {a}-{b} 章 / 候选偏离 {逐条，无则写「无」} / 裁决 {align_downstream / align_upstream / accepted_deviation}。
- 上溯：{若裁决为 align_upstream，列出需先过的节点与命令}。
- 已自动处理：章纲 revision、合同 revision、状态重登记。
- 建议确认：相邻章承接、伏笔推进或正文偏离是否需作者另行处理。
- 必须处理：若该章已有正文，需运行 /webnovel-chapter-reload {chapter}。

三、下一步建议
- 未写作章节：/webnovel-write {chapter}
- 已有正文章节：/webnovel-chapter-reload {chapter}
```

故障排查只提示 `.webnovel/logs/run_last.log` 或 `/webnovel-doctor`。
