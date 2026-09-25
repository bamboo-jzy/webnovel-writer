---
name: webnovel-chapter-plan
description: 按批次先与作者讨论章级决策，再基于已完成的卷纲生成可执行独立章纲，并刷新章节级 Story System 合同。
allowed-tools: Read Write Edit Grep Bash Agent AskUserQuestion
argument-hint: "[卷号] [章节范围，如 1-10]"
---

# 章纲规划

主 agent 职责：读取已完成的卷级规划，按批次生成独立章纲文件，校验章节承接与时间一致性，并在真实章纲落盘后为每章刷新 Story System 合同。落任何章级产物之前，必须先完成该批次的章级规划对话并通过决策卡闸门。不修改卷级规划的叙事决策，不直接写正文。

## 用法

```text
/webnovel-chapter-plan 1
/webnovel-chapter-plan 1 1-10
/webnovel-chapter-plan 1 11-20
```

未指定范围时，使用目标卷在总纲中的完整章节范围；默认每批 10 章，复杂题材 8 章，简单升级流最多 12 章。只重做失败批次，不重写其他批次。

## 硬规则

1. 卷节拍表、卷时间线和卷级详细大纲缺一不可，缺失或为空立即阻断。
2. 新章纲写入 `大纲/第N章-标题.md`，一个章节一个文件；不得把新章纲追加回卷级详细大纲。
3. **已有章纲或正文的章一律不生成、不覆盖**：`大纲/第N章-*.md` 已存在且非空、或该章已有正文/`accepted` commit 时，跳过该章并在报告中列出——它们是既定事实，其章纲只能由 `/webnovel-chapter-revise {chapter_num}` 演进；不存在「用户要求即可覆盖」的例外。
4. 每章必须有真实目标；禁止使用 `{章纲目标}`、`第N章章纲目标` 等占位 query。
5. 先写章纲并通过校验，再按章调用 `story-system --chapter ... --persist --emit-runtime-contracts`。
6. 章纲或合同生成失败时，不更新该章的 `chapters_planned` 状态。
7. 不因卷纲完成而声称可以直接写作；写作前必须确认目标章章纲和章级合同均存在且未过期。
8. **先对话后生成**：Step 1.5-1.6 的章级规划对话闸门未通过，不落任何章级产物（不写章纲文件、不刷新章级合同、不更新 `chapters_planned`），只保留决策卡。
9. 规划对话**无轮次上限**，但每轮必须提供「不再讨论」选项；作者选择即结束对话。首批问全 10 项必答议题，后续批次只问未决项与偏离项。
10. 讨论需要新增角色 / 设定 / 能力时，不在本 skill 内写 canon；阻断并指向 `/webnovel-volume-revise {volume_id}`，由卷级规划裁定后再回到本 skill。
11. **方向透传（卷-章节点）**：本批章纲必须与卷纲同向（依据：`${SKILL_ROOT}/../../references/handoff/direction-handoff.md`）。只允许演进**最后一章**的章纲，其余已规划章是既定事实；发现不一致时与作者讨论，由作者决定是否修改卷纲。判定为「改卷纲」时必须停止本 Skill，改走 `/webnovel-volume-revise {volume_id}`，并按方向透传规则上溯到总-卷节点；卷纲因此变化后，须重跑 `/webnovel-volume-reload {volume_id}` 再回到本 Skill。

## 环境准备

```bash
export WORKSPACE_ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
export SKILL_ROOT="${CLAUDE_PLUGIN_ROOT}/skills/webnovel-chapter-plan"
export SCRIPTS_DIR="${CLAUDE_PLUGIN_ROOT}/scripts"
export PROJECT_ROOT="$(python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${WORKSPACE_ROOT}" where)"

python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" preflight
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" placeholder-scan --format text
```

读取题材时使用 `.webnovel/state.json` 的初始化配置快照；Story System 的合同目录 `.story-system/` 是写作主链，`state.json` 只是兼容投影。

## 章级规划对话闸门（Step 1.5-1.6）

生成任何章级产物之前，必须先与作者完成**本批次**的章级规划对话，并把结论落成决策卡 `大纲/第{volume_id}卷-章纲规划讨论.md`。详细议题口径、话术模板、决策卡模板、命名安全约束与恢复规则见 `${SKILL_ROOT}/references/outlining/chapter-dialogue.md`。

闸门条件（全部满足才能进入 Step 2）：

1. 决策卡存在且非空，且包含本次批次小节。
2. 本批 10 项必答议题全部有结论，未决项为 0：批次切分与本次范围、卷级节拍节点到章号的映射、关键章的章级目标、阻力与代价的递增顺序、每章 CEN 到下章 CBN 的承接链、时间锚点体系与章内时间跨度、倒计时推进节奏、伏笔本批埋收与继续开放清单、爽点与 Strand 分布、本批禁区与新增角色设定边界。
3. `BLOCKER` 为 0，与卷级规划、设定集的冲突均已裁决。
4. 作者做过一次显式确认，决策卡 `状态` 为 `已确认`。
5. 决策卡不含 `[待...]`、`（暂名）`、`{占位}` 占位标记，也不含新能力、新规则、新关系等 canon 级新增定义。

对话规则：

- 每轮 3-5 个议题，先给建议、理由与风险，再给有限选项。
- 每轮必须带「不再讨论，结束对话」选项；作者选择即结束对话，未决项写 `待定` 并进入闸门判定。
- 无轮次上限。每轮结束增量更新决策卡，保证中断可恢复。
- 首批问全 10 项；后续批次只问未决项与偏离项，已决结论沿用决策卡并在新小节注明。
- 不提供跳过对话的开关；作者若表示按建议来，把建议值记为 `作者接受建议`，闸门照常判定。

决策卡文件名不得改动：以 `第N章` 开头会被章纲发现模式当成章纲文件，以 `大纲.md` 结尾会被当成卷级详细大纲，两者都会污染 revision。

未通过闸门：不落任何章级产物，只保留决策卡，并在报告中说明卡点、已完成内容与恢复方式。

### 卷-章方向检查（对话阶段必做）

先读 `${SKILL_ROOT}/../../references/handoff/direction-handoff.md` 的「卷-章」节点口径与「确认话术」。
本批每个目标章的章纲必须与卷纲同向；讨论议题中的「卷级节拍节点到章号的映射」即方向对齐议题。

生成前先确认本批的**可改单元**：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" handoff \
  --node volume_to_chapter --format text
```

- 批次内已存在章纲或正文的章由硬规则 3 跳过，不进入生成。
- 讨论中出现「章纲与卷纲不一致」时，按确认话术与作者逐项裁决，用 `handoff --record` 落盘
  （`--node volume_to_chapter --target {chapter_num}`）。
- 裁决为 `align_downstream` → 调整本批章纲向卷纲对齐，继续生成。
- 裁决为 `align_upstream` → **停止本 Skill**，先过上一层节点检查 `handoff --node master_to_volume`，
  再由 `/webnovel-volume-revise {volume_id}` 修改卷纲；卷纲变化后须重跑 `/webnovel-volume-reload {volume_id}`。
- 裁决为 `accepted_deviation` → 记录后继续，并在决策卡对应条目注明该偏离。

生成完成后的 Step 4 必须用 `--check` 对已完成章逐批复核方向，不得只依赖对话阶段的结论。

## Step 1：确认卷级前置条件

检查以下三个文件均存在且非空：

```text
大纲/第{volume_id}卷-节拍表.md
大纲/第{volume_id}卷-时间线.md
大纲/第{volume_id}卷-详细大纲.md
```

读取总纲、目标卷全部卷级规划、前一批章纲（若有）、最近正文摘要、角色状态、关系、活跃伏笔和开放环。卷级详细大纲若是新格式只包含卷级内容，不得从中自由猜测缺失的章目标；旧项目仍可由 `chapter_outline_loader.py` 使用卷级章节段落作为兼容回退。

### Step 1.5：章级规划对话

先读 `${SKILL_ROOT}/references/outlining/chapter-dialogue.md`，按「每轮话术模板」与作者逐轮讨论本批 10 项必答议题。

- 每轮 3-5 个议题，先给建议、理由与风险，再给有限选项；每轮必须包含「不再讨论，结束对话」选项。
- 无轮次上限；作者选择「不再讨论」即结束对话。
- 首批问全 10 项；后续批次只问未决项与偏离项。
- 每轮结束把结论增量写入 `大纲/第{volume_id}卷-章纲规划讨论.md` 的对应条目与统计行，不整文件重写。
- 作者答复与卷级规划 / 设定集冲突时，当场记 `BLOCKER` 并请作者裁决。
- 需要新增角色 / 设定 / 能力时，停止讨论并指向 `/webnovel-volume-revise {volume_id}`，不在本 skill 内写 canon。

本步只写决策卡，不写章纲文件、卷级产物、`设定集/`、总纲或 `.story-system/`。

### Step 1.6：决策卡闸门

按 `${SKILL_ROOT}/references/outlining/chapter-dialogue.md` 的「闸门清单」逐项核对：决策卡包含本次批次小节、10 项必答议题全有结论且未决项为 0、`BLOCKER` 为 0、决策卡 `状态` 为 `已确认`、无占位标记与 canon 级新增定义。

- 未通过：阻断，不落任何章级产物，只保留决策卡；说明卡点、已完成内容与恢复方式，等作者补齐后重跑本 skill。
- 通过：决策卡结论作为 Step 2-7 的唯一章级决策来源，产物不得偏离已确认结论。

## Step 2：确定章节范围与批次

解析用户给出的卷号和范围，并验证范围落在该卷章节区间内。范围与批次以 Step 1.6 通过闸门的决策卡为准；解析结果与决策卡冲突时回到 Step 1.5 重新讨论，不得自行改范围。按 8-12 章分批，每批明确：

- 本批起止章节（以决策卡「批次切分与本次范围」为准）。
- 本批承接的上一章 CEN 或卷级起点。
- 本批必须完成的卷级节拍节点。
- 本批应推进、回收或继续开放的伏笔。
- 本批的时间区间和倒计时边界。

## Step 3：按批次生成独立章纲

生成前先逐章做**既定事实检查**，按结果分流：

| 现状 | 动作 |
|---|---|
| `大纲/第N章-*.md` 已存在且非空 | **跳过**：不生成、不覆盖，列入报告 |
| 该章已有正文或 `accepted` commit | **跳过**：不生成、不覆盖，列入报告 |
| 两者都没有 | 正常生成本章章纲 |

跳过章不算失败，但报告必须列明跳过的章号、命中原因和调整入口 `/webnovel-chapter-revise {chapter_num}`。同一批次里既有跳过章又有生成章时，只为生成章刷新合同与登记状态。

先读取 `${SKILL_ROOT}/../webnovel-plan/references/outlining/chapter-planning.md` 的结构化节点规范、`${SKILL_ROOT}/../../references/outlining/plot-signal-vs-spoiler.md`；再按需读取 `${SKILL_ROOT}/../../references/reading-power-taxonomy.md`（追读力）、`${SKILL_ROOT}/../../references/shared/cool-points-guide.md`（爽点）、`${SKILL_ROOT}/../webnovel-plan/references/outlining/conflict-design.md`（冲突设计）、`${SKILL_ROOT}/../webnovel-plan/references/outlining/genre-volume-pacing.md`（题材节奏）。生成独立 Markdown 文件，标题须匹配 `第N章*.md`，推荐格式：

```markdown
# 第N章：标题

## 执行指令
- 目标：本章必须完成的剧情动作或状态变化
- 阻力：阻止目标的具体力量
- 代价：推进目标需要付出的代价
- 时间锚点：故事内绝对时间或明确相对时间
- 章内时间跨度：本章经过的故事时间
- 与上章时间差：与上一章结尾的间隔
- 倒计时状态：D-N、已解除或不适用
- 爽点：本章情绪/收益高点
- Strand：quest / fire / constellation
- 反派层级：本章对手层级及作用
- 视角/主角：叙事视角
- 关键实体：角色、势力、地点、物件
- 本章变化：章末相对章初的状态变化
- 章末未闭合问题：必须留到后续的问题
- 钩子：章末推动下一章的钩子

## 结构化节点
- CBN：主体 | 动作/变化 | 对象/结果
- CPNs：
  1. 主体 | 动作/变化 | 对象/结果
  2. 主体 | 动作/变化 | 对象/结果
- CEN：主体 | 动作/变化 | 对象/结果
- 必须覆盖节点：
  - ...
- 本章禁区：
  - ...
```

字段可以按题材扩展，但不能删除上述 parser 依赖字段。每章固定 1 个 `CBN`、2-4 个按时间顺序排列的 `CPNs`、1 个 `CEN`；`必须覆盖节点`最多 4 个；`本章禁区`最多 5 条且只写硬禁区。

**字段内的分隔符禁令（必须遵守）**：`CBN` / `CPNs` / `CEN` / `必须覆盖节点` / `本章禁区` / `关键实体` 这六项的**单行内容里不得出现** `、` `,` `，` `；` `;` `|`——解析器会把它们当条目分隔符，导致一条被拆成多条（实测：禁区写成 5 条会被拆成 6 条而超出上限，`CPNs` 会被拆碎）。条目内需要停顿或并列时用空格或 `／`。`关键实体` 用 `；` 分隔条目是**唯一例外**（该字段的条目分隔符就是 `；`，条目内部仍不得再用）。

另外，`本章变化` 不是解析器识别的字段：紧跟在 `关键实体`（列表字段）之后会被并进 `key_entities`。要把 `本章变化` 放在某个**标量字段之后**（例如紧跟 `代价` 或 `反派层级`），让它被丢弃而不是污染列表。

## Step 4：批次校验

逐批校验后再进入合同刷新：

- 本次批次决策卡存在、`状态` 为 `已确认`、未决项为 0，且生成结果与决策卡结论一致。
- 每章文件存在且非空，章节号、标题和范围正确。
- 目标、阻力、代价、时间锚点、章内时间跨度、与上章时间差、倒计时状态、章末未闭合问题和钩子齐全。
- `CBN/CPNs/CEN/必须覆盖节点/本章禁区` 可被现有 parser 识别。
- 相邻章节满足 `上一章 CEN -> 下一章 CBN` 的因果或状态承接。
- 章内时间不回跳；倒计时算术一致；闪回必须显式标注。
- 伏笔推进与卷级规划一致；不凭空关闭未规划的开放环。
- 不含 `[待...]`、`暂名`、`{占位}` 等当前章不可执行占位符。
- 不发生与设定集、总纲或卷级节拍冲突的能力、关系和事实跳变。

校验失败只标记当前批次失败并保留已通过批次；不得先登记状态或覆盖作者已有文件。

校验通过后，对本批每个新生成章逐个复核卷-章方向：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" handoff \
  --node volume_to_chapter --check --target {chapter_num} --format json
```

`candidates` 非空（例如章纲「关键实体」在卷纲与决策卡中找不到依据、或卷纲 revision 已过期）时，
按「卷-章方向检查」的确认话术与作者裁决后再进入 Step 5。`manual_items` 是低置信提示，须上下文判断，
不得直接当作不一致。

## Step 5：刷新章节级 Story System 合同

从刚写入的真实章纲解析 `CHAPTER_GOAL`，不得手写泛化目标或从摘要猜测。读取题材后逐章执行：

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

合同失败时只重跑对应章节或当前批次，不把失败章节标记为完成。合同写入不会替代章纲校验；runtime builder 必须实际读取本次独立章纲的节点和执行字段。

## Step 6：登记章纲状态

只有章纲校验、合同生成和合同文件检查全部通过，才更新状态。对每章记录独立文件路径、所属卷、章纲 revision、源卷纲 revision 和完成时间：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" update-state -- \
  --chapter-planned {chapter_num} \
  --chapter-outline-file "大纲/第{chapter_num}-标题.md" \
  --volume {volume_id} \
  --volume-revision "{volume_revision}"
```

章纲文件后来被作者编辑时，下一次状态检查必须以实际文件和合同时间为准；不得仅相信旧 state 记录。卷纲文件变化会使依赖它的章纲显示为 `stale`，须重新校验并刷新合同。

## Step 7：收尾与恢复

再次运行：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" placeholder-scan --format text
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" user-report \
  --stage chapter-plan --volume {volume_id} --chapter {start_chapter} --format text
```

最终报告固定三段式：

- 产生的文件与完成情况：列出每个独立章纲和对应合同，以及本次**跳过的章**（已有章纲或已有正文，附命中原因）。
- 问题与异常：区分已自动处理、建议确认、必须处理；说明失败批次和 legacy fallback。
- 下一步建议：只对章纲和合同均通过的章节给出 `/webnovel-write N`；跳过的章给出 `/webnovel-chapter-revise {chapter_num}`；其余章节给出重跑 `/webnovel-chapter-plan {volume_id} {range}`。

## 作者友好过程提示与恢复契约

过程提示只说明当前动作和影响，不直接输出原始 JSON、traceback 或长命令日志。开始时说明：检查卷级前置 → 与作者讨论本批章级决策（可随时选择不再讨论）→ 通过决策卡闸门 → 生成章纲批次 → 校验承接与时间 → 刷新章级合同 → 登记通过章节。

每批结束记录已完成内容、失败批次、自动处理的问题和恢复建议；异常细节写入 `.webnovel/logs/run_last.log`，可用以下命令记录：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" run-log \
  --event chapter-plan-progress \
  --payload-json "{\"stage\": \"chapter-plan\", \"volume\": {volume_id}}" \
  --format text
```

少打扰确认策略：规划对话阶段集中询问（每轮 3-5 个议题，且必带「不再讨论」选项）；生成阶段正常批次不询问；遇到卷纲冲突、作者已有章纲、合同刷新失败或不可自动裁决的时间/伏笔冲突时，使用有限选项让作者选择保留、重做当前批次或停止。卡住时必须说明卡点、已完成内容和恢复建议，不覆盖作者已有章纲。

最终报告遵循作者友好最终报告契约，使用任务化语言和可复制命令：总状态：已完成 / 部分完成 / 需要你处理 / 未完成。以“总状态”开头，分为产生的文件与完成情况、方向检查（卷-章）、问题与异常、下一步建议四段；产生的文件与完成情况必须包含本次批次的决策卡路径与闸门判定结果；不输出原始 JSON、traceback 或长命令日志。

```text
一、产生的文件与完成情况
- ...

二、过程中遇到的问题与异常耗时
- 方向检查（卷-章）：可改单元 第 {chapter_num} 章 / 既定事实 第 {a}-{b} 章 / 候选偏离 {逐条，无则写「无」} / 裁决 {align_downstream / align_upstream / accepted_deviation}。
- 已自动处理：...
- 建议确认：...
- 必须处理：...

三、下一步建议
- ...

不写 token 统计；故障排查只提示 `.webnovel/logs/run_last.log` 或 `/webnovel-doctor`。
```

## 成功标准

1. 目标卷级文件存在且非空。
2. 本次批次的决策卡存在、10 项必答议题未决项为 0、`BLOCKER` 为 0，且 `状态` 为 `已确认`。
3. 每个目标章节拥有独立、可解析、非空章纲，或明确标记为旧项目 fallback。
4. 所有相邻章节的 CEN→CBN、时间线、倒计时、伏笔和占位符校验通过。
5. 每个成功章节均有四类章级 Story System 合同，且合同在章纲之后生成。
6. 只有成功章节进入 `chapters_planned`；失败批次可独立恢复。
7. 报告明确下一步，不把卷纲完成误报为可以写作。
8. 已有章纲或已有正文的章全部被跳过、逐字节未改动，并在报告中列明跳过的章号与原因。
9. 本批新生成章的卷-章方向检查已复核，候选偏离均已裁决并落盘到 `.story-system/handoffs/`（或候选为空）。
