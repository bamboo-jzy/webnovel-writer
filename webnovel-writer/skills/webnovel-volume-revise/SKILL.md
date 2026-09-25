---
name: webnovel-volume-revise
description: 通过确认式引导修改卷纲，分析影响范围并刷新卷纲 revision，安全标记下游章纲与合同过期。只演进尚无章纲覆盖的卷级内容，已被章纲落实的部分为既定事实不得修改。
allowed-tools: Read Write Edit Grep Bash AskUserQuestion
argument-hint: "[卷号] [修改诉求，可选]"
---

# 卷纲修改

主 agent 职责：在不静默覆盖作者内容的前提下，引导作者修改指定卷的卷节拍表、卷时间线和卷级详细大纲，先展示影响分析并取得确认，再写入、校验和重载卷纲 revision。不生成逐章章纲，不直接写正文。

## 用法

```text
/webnovel-volume-revise 1
/webnovel-volume-revise 1 调整卷末反派身份，但保留前半卷节奏
/webnovel-volume-revise 1 将倒计时提前五章
```

## 硬规则

1. 修改前必须读取三份卷级文件、总纲、现有设定和目标卷的状态证据；缺失或为空立即阻断。
2. 先输出修改方案和影响范围，作者确认后才能写入；不得先改文件再询问。
3. 已有非空卷纲文件是作者资产，只能定向编辑，禁止整文件重写或静默覆盖人工内容。
4. 明确列出保留项、修改项和无法自动裁决项；冲突时暂停并让作者选择保留、重做或停止。
5. 确认轮无上限，每轮必带「不再讨论」选项；作者选择即按已确认范围结束，不强行追问未决项。
6. 修改卷节拍、时间线、核心冲突、卷末承接或设定边界后，必须重新计算 revision 并标记依赖章节 stale；stale 只是状态层标记，不得借助它改写任何章纲或正文。
7. **只允许修改最后一卷的卷纲**（方向透传「总-卷」节点，依据 `${SKILL_ROOT}/../../references/handoff/direction-handoff.md`）：其余已规划卷是既定事实，修改诉求一律阻断并说明；总纲也必须与那些卷的卷纲保持同向。
8. **锁定区不得修改**：卷纲中已被已存在章纲覆盖的内容是既定事实，只能演进尚无章纲覆盖的部分。作者的锁定区诉求转为修改对应章纲（`/webnovel-chapter-revise {章号}`）。
9. **方向检查必做**：写入前后都要确认新卷纲与总纲同向。不一致时按方向透传的确认话术与作者裁决；裁决为「改总纲」时停止本 Skill，改走 `/webnovel-outline-revise`。
10. 不得写入 `大纲/第{章}章-*.md`、`正文/**`、`.story-system/commits/**`，不自动刷新章级合同；下游按「作用域与内容边界」分流，不得笼统指向 `/webnovel-chapter-plan`。
11. 校验失败时保留修改前备份，不登记新的可信 revision；不得报告为完成。

## 作用域与内容边界（既定事实锁定）

卷纲只演进**尚无章纲覆盖**的部分。已被独立章纲落实的卷级内容是既定事实，不得修改——正文经由章纲与卷纲关联，章纲锁定即意味着正文同样不动。

本节点同时受**两层边界**约束，取交集：

### 一、卷粒度边界（方向透传「总-卷」）

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" handoff \
  --node master_to_volume --check --target {volume_id} --format json
```

- **只允许修改最后一卷的卷纲**。`target_allowed=false` 时阻断并说明该卷是既定事实。
- 总纲也必须与非最后一卷的既有卷纲保持同向；本 Skill 改动只落在最后一卷内。
- `candidates` 非空（章节范围与总纲卷表不一致、总纲写回 JSON 与卷表不一致等）时，
  按 `${SKILL_ROOT}/../../references/handoff/direction-handoff.md` 的确认话术与作者裁决：
  - `align_downstream` → 本次修改向总纲对齐，继续。
  - `align_upstream` → **停止本 Skill**，先运行 `/webnovel-outline-revise` 修改总纲，再重跑本 Skill。
  - `accepted_deviation` → 记录后继续，在报告中列为有意保留的偏离。
- 裁决用 `handoff --record --node master_to_volume --target {volume_id}` 落盘，只写 `.story-system/handoffs/`。

### 二、章粒度边界（锁定区）

### 锁定区判定

先列出本卷已存在的独立章纲（`大纲/第{章}章-*.md` 且内容非空），得到**锁定章集合**，再按以下口径划区：

| 卷纲内容 | 判定 |
|---|---|
| 明确标注章号、或上下文指向锁定章的条目（节拍、时间锚点、倒计时、角色状态、伏笔推进、章级承接） | **锁定区**：不得修改 |
| 跨章卷级要素（卷末高潮、整卷倒计时总长、伏笔弧线、卷主题与核心冲突） | **敏感区**：修改前必须逐项声明对锁定章的影响，确认无冲突才可写入 |
| 只涉及锁定章之后章节的内容 | **可改区**：按正常流程修改 |

无法判断某条目落在哪一区时按最保守处理（视为锁定区），并向作者说明判断依据。

### 与总纲「冻结线」的关系

`/webnovel-outline-revise` 的**冻结线**以最新 accepted commit 为基准；本 Skill 的**锁定区**以已存在的独立章纲为基准。两者是包含关系：

```text
锁定区 ⊇ 冻结线
```

已 accepted 的章必然已有章纲；已有章纲的章未必已 accepted。因此卷纲的可改区比总纲的可修订区更窄——改总纲后逐卷处理时，已存在章纲的章同样只能改章纲，不能改卷纲。

### 写入范围

| 类别 | 内容 |
|---|---|
| 允许写入 | `大纲/第{N}卷-节拍表.md`、`第{N}卷-时间线.md`、`第{N}卷-详细大纲.md`；经逐项确认的 `大纲/总纲.md` 与 `设定集/*`；`.webnovel/state.json`；卷纲备份 |
| 允许标记 | `chapters_planned[].status = stale`（状态层标记，不改任何文件内容） |
| 禁止写入 | `大纲/第{章}章-*.md`（任意章，含尚未存在的章）、`正文/**`、`.story-system/commits/**` |

### 下游分流

| 目标章 | 下游动作 |
|---|---|
| 已有章纲 | **无动作**。锁定区不可改；章纲如需演进，走 `/webnovel-chapter-revise {章号}` |
| 尚无章纲 | `/webnovel-chapter-plan {volume} {range}` 生成新章纲 |

不得对整个 `stale_chapters` 范围笼统建议 `/webnovel-chapter-plan`——已有章纲的章会被它当作待生成章节处理。

## 环境准备

```bash
export WORKSPACE_ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
export SKILL_ROOT="${CLAUDE_PLUGIN_ROOT}/skills/webnovel-volume-revise"
export SCRIPTS_DIR="${CLAUDE_PLUGIN_ROOT}/scripts"
export PROJECT_ROOT="$(python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${WORKSPACE_ROOT}" where)"
```

开始前运行：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" preflight
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" placeholder-scan --format text
```

## Step 1：读取与基线检查

确认以下文件存在且非空：

```text
大纲/第{volume_id}卷-节拍表.md
大纲/第{volume_id}卷-时间线.md
大纲/第{volume_id}卷-详细大纲.md
大纲/总纲.md
.webnovel/state.json
```

同时读取：

- 当前卷的 `volumes_planned.planning_revision`、`updated_at` 和已有 `stale_chapters`。
- `chapters_planned` 中属于目标卷的章节、`source_volume_revision` 和 stale 状态。
- 相关设定、前后卷承接、开放环和已接受章节摘要。

先记录当前卷纲 revision：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" volume-reload \
  --volume {volume_id} --dry-run --format json
```

输出里的 `protected_chapters` / `open_chapters` / `protected_artifacts` 就是本次的**锁定区与可改区**。同时按「作用域与内容边界」的判定口径，为每个锁定章记录章纲文件路径与正文是否存在——这是 Step 2 划区的依据，也是 Step 5 声明「未修改」时的比对清单。

再做**卷粒度边界与总-卷方向检查**：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" handoff \
  --node master_to_volume --check --target {volume_id} --format json
```

`target_allowed=false` 直接阻断；`candidates` / `manual_items` 记入 Step 2 的修改方案，一并裁决。

## Step 2：形成修改方案

将作者诉求拆成：

- 锁定区交集（**先做；只要命中锁定区，后面的条目都不必展开**）：逐条标注诉求落在锁定区 / 敏感区 / 可改区。落在**锁定区**的诉求**不得执行**，改为告知作者该内容已由第 N 章章纲落实、请用 `/webnovel-chapter-revise {章号}` 修改对应章纲；落在**敏感区**的必须附「对锁定章的影响评价」。
- 修改目标：要改变的剧情决策。
- 保留项：明确不能改变的节拍、人物、时间锚点或卷末承接。
- 影响文件：节拍表、时间线、详细大纲、总纲写回或设定集；全部落在「写入范围」的允许清单内。
- 影响章节：按卷纲变化推断的最小章节范围，且只能覆盖可改区（尚无章纲的章）；无法可靠判断时按最保守处理并说明依据。
- 风险：时间线冲突、设定冲突、伏笔提前/延后、旧章纲和合同过期。
- 方向检查结论（总-卷，来自 Step 1）：候选偏离逐条、须裁决项，以及作者的裁决口径（`align_downstream` / `align_upstream` / `accepted_deviation`）。

用有限选项向作者确认（话术与「不再讨论」语义沿用 `${SKILL_ROOT}/../webnovel-plan/references/outlining/volume-dialogue.md` 的「每轮话术模板」）：

```text
A. 按方案修改，并使受影响章节 stale
B. 只修改卷级文件，暂不写回设定或总纲
C. 停止，不修改文件
D. 不再讨论，按当前已确认范围结束
```

确认轮**无上限**：每轮必须先给建议、理由与风险，再给有限选项，且必带 `D` 选项。作者选择 `D` 时只处理已明确项，仍待定的项保持原样、不写入文件。如果作者没有明确选择，继续询问，不得自行写入。

方向检查报出的候选偏离须单独用同一套话术裁决，并在**写入前**落盘：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" handoff \
  --node master_to_volume --target {volume_id} --record \
  --decision {align_downstream|align_upstream|accepted_deviation} \
  --reason "{作者理由}" --impact {volume_id} \
  --expected-input "{input_token}" --format json
```

裁决为 `align_upstream` 时必须停止本 Skill，不得继续写入卷纲。

## Step 2.5：方向裁决落盘（写入前）

只有本步完成才允许进入 Step 3：

- 候选偏离为空 **且** 须裁决项已获作者确认 → 记录结论（候选为空时可用 `--decision align_downstream` 留痕，也可跳过）。
- 候选偏离非空 → 必须先 `handoff --record` 落盘；`input_token` 来自同一份预览，上游文件一变即失效，需重新 `--dry-run` 预览。
- 裁决为 `align_upstream` → 停止本 Skill，指向 `/webnovel-outline-revise`。

`--record` 只写 `.story-system/handoffs/`，不改任何卷级文件；它不替代 Step 3 的备份与定向编辑。

## Step 3：确认后定向修改

只在确认后编辑必要文件，且编辑范围严格限于「写入范围」的允许清单、内容严格限于可改区（敏感区须已通过影响评价）：

- 卷节拍表：只改受影响节拍、危机递增、卷末高潮或承接。
- 卷时间线：同步修改绝对时间、相对间隔、倒计时和事件顺序。
- 卷级详细大纲：同步卷级冲突、角色状态、伏笔和约束，不加入逐章 `目标`、`CBN`、`CPNs`、`CEN` 或章级合同参数。
- 设定/总纲：只有作者确认且修改方案明确列出时才增量写回。

锁定区条目的原文必须逐字保留；发现改动范围溢出到锁定区时立即回退该处编辑并报告。

修改前先备份当前卷纲和状态：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" volume-reload \
  --volume {volume_id} --backup-only --format text
```

备份失败则停止，不进行任何编辑。只在备份成功后定向修改必要文件：

## Step 4：校验并重载

修改后必须检查：

- 三份卷级文件均存在且非空。
- 时间线无无法解释的回跳，倒计时一致。
- 节拍表、详细大纲和总纲承接一致。
- 不新增未确认的实体、能力上限或关系跳变。
- 无 `[待...]`、`暂名`、`{占位}` 等不可执行占位符。
- 纯卷级详细大纲没有逐章执行字段或章级合同命令。
- 新卷纲与总纲同向：重跑 `handoff --node master_to_volume --check --target {volume_id}`，确认候选偏离未新增。

校验通过后执行：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" volume-reload \
  --volume {volume_id} \
  --chapters-range "{start}-{end}" \
  --source guided_revision \
  --format text
```

该命令会计算新的卷纲 revision，并将属于该卷且依赖旧 revision 的 `chapters_planned` 条目标记为：

```json
{
  "status": "stale",
  "stale_reason": "volume_plan_changed"
}
```

修改完成后，revision 证据必须同时能解释卷纲三份文件、依赖章节的 `source_volume_revision` 和已登记的 stale 范围。mtime 只能作为兼容信息，不能决定卷纲是否新鲜。

重载输出里 `protected_chapters` 声明的锁定章，其章纲与正文必须逐字节未变。若任何依赖章节已存在章纲或正文，不得自动刷新章纲、合同或正文——按「下游分流」处理：已有章纲的章不做任何动作（章纲演进走 `/webnovel-chapter-revise`），只有尚无章纲的章才指向 `/webnovel-chapter-plan`。

## Step 5：收尾与恢复

再次运行：
```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" placeholder-scan --format text
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" user-report \
  --stage plan --volume {volume_id} --format text
```

受影响章节按「下游分流」给下一步，**不得笼统给出一个 `/webnovel-chapter-plan` 范围**：

- 锁定章（已有章纲）：给出 `/webnovel-chapter-revise {章号}`，或说明本次未触碰、无需动作。
- 可改章（尚无章纲）：给出 `/webnovel-chapter-plan {volume_id} {range}`。

只有目标章章纲与章级合同均存在且未过期，才可运行：

```text
/webnovel-write {chapter_num}
```

收尾必须输出**作用域声明**：本次实际改动了哪些卷级文件、锁定章集合是哪些、这些章的章纲与正文均未被修改。

人工编辑卷纲时，不应运行本 Skill 代替重载；请直接运行：

```text
/webnovel-volume-reload {volume_id}
```

## 作者友好报告契约

最终回复以以下结构输出，不输出原始 JSON、traceback、token 统计或长日志：

```text
总状态：已完成 / 部分完成 / 需要你处理 / 未完成。

一、修改与文件
- 修改了什么、保留了什么、备份在哪里。

二、作用域声明
- 卷粒度：可改单元 第 {volume_id} 卷（最后一卷）/ 既定事实 第 {a}-{b} 卷（本次未修改）。
- 章粒度——锁定区：第 {n}-{m} 章（已有章纲），其章纲与正文本次均未修改。
- 可改区：本次改动只落在尚无章纲的章节对应的卷级内容。
- 被拒绝的诉求：落在锁定区的修改诉求，及其改走 /webnovel-chapter-revise {章号} 的建议（若有）。
- 方向检查（总-卷）：候选偏离 {逐条，无则写「无」} / 裁决 {align_downstream / align_upstream / accepted_deviation}。

三、影响与异常
- 已自动处理：revision、备份、stale 登记。
- 建议确认：仍需作者判断的剧情或设定冲突。
- 必须处理：尚未有章纲、需要生成章纲的章节范围。

四、下一步建议
- 尚无章纲：/webnovel-chapter-plan {volume_id} {open_range}
- 已有章纲需调整：/webnovel-chapter-revise {chapter}
```

故障排查只提示 `.webnovel/logs/run_last.log` 或 `/webnovel-doctor`。
