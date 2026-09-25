---
name: webnovel-outline-revise
description: 通过确认式引导修改总纲。以最新 accepted commit 为冻结线做冲突扫描，纯未来/设定增补诉求定向编辑并标记受影响卷 stale；推翻已发布事实的诉求只做阻断提示。
allowed-tools: Read Write Edit Grep Bash AskUserQuestion
argument-hint: "[修改诉求，可选]"
---

# 总纲修改

主 agent 职责：在不推翻已发布正文的前提下，引导作者修改 `大纲/总纲.md`。先计算冻结线并做冲突扫描，把诉求分类为纯未来型/设定增补型/推翻已发布事实型，分别处理。前两类走确认式修订并标记受影响卷 stale；第三类只做阻断提示，不代办、不改写已发布事实。不生成卷纲或章纲，不直接写正文。

## 用法

```text
/webnovel-outline-revise
/webnovel-outline-revise 把结局从悲剧改为大团圆
/webnovel-outline-revise 在第 3 卷后新增一条隐藏势力线
```

## 核心概念：冻结线

总纲修订只能**向前生效**。已发布（已 accepted commit）的正文是冻结事实层，不可被修订推翻。

- **冻结线 = 最新 accepted commit 的章节号**。从 `.story-system/commits/chapter_*.commit.json` 中取 `meta.status == "accepted"` 的最大章节号。
- 冻结线左侧（第 1 章至第 N 章）的正文事实：不可删除、不可推翻、不可改写成"其实没发生"。
- 冻结线右侧（未写/未提交章节的卷纲与章纲）：可修订区。
- 没有任何 accepted commit 时，全书视为可修订区，但仍走确认式流程。

## 硬规则

1. 修改前必须读取总纲、全部卷级文件、设定集、冻结线及冻结线内各章的 accepted commit 摘要与事实提取。
2. 先把诉求分类并做冲突扫描，再输出保留项/修改项/影响卷范围；作者确认后才能写入，不得先改文件再询问。
3. 总纲已有非空内容是作者资产，只能定向编辑，禁止整文件重写或静默覆盖人工内容。
4. **推翻已发布事实型诉求只做阻断提示**：列出具体冲突点，给出两条出路（揭示式翻案登记为伏笔 / 增补填白），但不替作者改写，也不允许按普通修订流程写入。若作者坚持真正推翻已发布正文，明确告知这超出本 Skill 范围——那是重写已发布内容的人工工程。
5. 修改总纲后，必须把受影响卷的 `volumes_planned` 标记为 stale，并在报告中按**方向透传「总-卷」节点**（依据 `${SKILL_ROOT}/../../references/handoff/direction-handoff.md`）分流：只有**最后一卷**可指向 `/webnovel-volume-revise`；非最后一卷的卷纲是既定事实，不得修改，只能调整总纲与之对齐，或登记 `accepted_deviation`。
6. 校验失败时保留修改前备份，不登记 stale；不得报告为完成。

## 边界

- 只修改 `大纲/总纲.md` 及作者明确确认的设定集增量写回；不修改任何卷级文件、章纲、正文或 `.story-system/`（方向裁决记录 `.story-system/handoffs/` 除外）。
- 揭示式翻案只**登记为伏笔/开放环**（写入设定或伏笔清单，供后续卷揭示），不在本 Skill 内生成反转节拍正文。
- 冻结线内已发布正文与新总纲之间无害的细节偏差，可登记为 accepted deviation 关闭，不逼作者逐条处理。

## 方向透传：总-卷节点（本 Skill 是上溯终点）

总纲是四层（总纲 → 卷纲 → 章纲 → 正文）的最上层，也是方向透传上溯的**终点**：本 Skill 之下没有更上层的依据。完整口径见 `${SKILL_ROOT}/../../references/handoff/direction-handoff.md`。

### 修订前：先看既定事实

总纲必须与**非最后一卷**的既有卷纲保持同向——那些卷纲是既定事实，不能为了配合总纲改动而被改写。

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" handoff \
  --node master_to_volume --format text
```

输出里的 `editable_units`（最后一卷）与 `locked_units`（其余已规划卷）就是本次可影响的边界。

### 修订后：按卷分流

| 受影响卷 | 处理 |
|---|---|
| 最后一卷 | 可改卷纲 → `/webnovel-volume-revise {volume_id}`（若涉及重写正文，按方向透传的顺序约束执行） |
| 非最后一卷 | **卷纲锁定**，不得指向 `/webnovel-volume-revise` → 只能把总纲调回与该卷卷纲同向，或登记 `accepted_deviation` |

对非最后一卷报出冲突时，与作者确认是「总纲改动超出既有卷纲范围」并要求调整总纲，不得承诺修改卷纲。

与卷纲/章纲/正文三个节点不同，总纲没有 revision 字段，`handoff --node master_to_volume --check --target {volume_id}` 只能比对卷号存在性与章节范围；卷名、核心冲突、卷末高潮是散文表述，一律须作者裁决。

## 环境准备

```bash
export WORKSPACE_ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
export SKILL_ROOT="${CLAUDE_PLUGIN_ROOT}/skills/webnovel-outline-revise"
export SCRIPTS_DIR="${CLAUDE_PLUGIN_ROOT}/scripts"
export PROJECT_ROOT="$(python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${WORKSPACE_ROOT}" where)"
```

开始前运行：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" preflight
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" placeholder-scan --format text
```

## Step 0：计算冻结线

列出所有 accepted commit，取最大章节号作为冻结线：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" story-events --health --format json
```

同时直接扫描 `${PROJECT_ROOT}/.story-system/commits/chapter_*.commit.json`，筛选 `meta.status == "accepted"`，记录最大章节号 `N`。没有 accepted commit 时 `N = 0`，全书可修订。

## Step 1：读取与基线检查

确认存在且非空：

```text
大纲/总纲.md
大纲/爽点规划.md（若存在）
设定集/（世界观、力量体系、主角卡、金手指、反派设计等）
.webnovel/state.json
```

同时读取：

- 冻结线内各章（第 1 至 N 章）的 accepted commit 摘要与 extraction facts，作为"已发布事实"清单。
- `volumes_planned` 各卷的 `planning_revision`、章节范围和 stale 状态，定位哪些卷在冻结线右侧。
- 活跃伏笔、开放环和未回收设定钩子。

## Step 2：诉求分类与冲突扫描

把作者诉求拆成修改目标与保留项后，逐条对照已发布事实清单，分为三类：

- **纯未来型**：只改变冻结线右侧的剧情决策（结局方向、未写卷核心冲突、卷划分）。安全，可走修订。
- **设定增补型**：补充力量体系上层、新增未登场势力/角色、填已发布内容未明写的空隙。允许，但校验不与已发布事实矛盾。
- **推翻已发布事实型**：要求否定冻结线内已写事实（"其实某人没死"、"某势力其实是好的"、"某段剧情没发生"）。**阻断**。

对推翻型，停止修订流程，只输出阻断提示：

```text
检测到诉求与已发布正文冲突（冻结线：第 N 章）：
- 冲突点 1：第 X 章已写 [事实]，诉求要求改为 [新事实]
- 冲突点 2：...

已发布正文不可修改。可选出路：
A. 揭示式翻案：保留已发布事实为"角色当时认知的真相"，登记一条必须回收的伏笔，
   在冻结线后的卷中揭示隐藏真相。（本 Skill 只登记伏笔，不生成反转正文）
B. 增补填白：不否定已写内容，在已发布内容未明写的空隙中补充设定。
C. 停止：本次不修改总纲。
```

作者选 A 时，把翻案登记为伏笔/开放环（写入设定集或伏笔清单），但**不**在总纲中删除旧事实、**不**生成反转章节内容；选 B 时回到设定增补型流程；选 C 时结束。

## Step 3：确认后定向修改（仅纯未来型/设定增补型）

对前两类型，形成修改方案后向作者确认：

```text
A. 按方案修改总纲，并标记受影响卷为 stale
B. 只修改总纲，暂不写回设定集
C. 停止，不修改文件
```

确认后先备份总纲和状态。若项目在 Git 管理下：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" backup \
  -- --chapter {latest_chapter} --chapter-title "revise-master-outline"
```

Git 备份不可用时，至少把总纲复制到 `.webnovel/backups/master_outline_{时间戳}/` 下。备份失败则停止，不进行任何编辑。

备份成功后定向编辑：只改受影响的核心冲突、卷划分、卷末高潮、结局方向或新增设定边界；保留作者其他内容。设定集增量写回仅在作者确认且方案明确列出时进行。

## Step 4：校验

修改后必须检查：

- 总纲存在且非空，未整文件重写。
- 新总纲不与冻结线内任何已发布事实矛盾（逐条对照 Step 1 的事实清单）。
- 揭示式翻案（若登记）已落实为伏笔/开放环，且未在总纲中删除旧事实。
- 卷划分与各卷章节范围仍一致；无无法解释的时间线回跳。
- 不含 `[待...]`、`暂名`、`{占位}` 等占位符。
- 不新增与设定集冲突的能力上限、关系跳变。

校验失败时恢复备份或修正后重新校验，不登记 stale。

## Step 5：标记受影响卷 stale

总纲没有独立 revision 字段，stale 传播靠显式标记。对冻结线右侧、且叙事决策受本次修订影响的卷，逐卷在 `volumes_planned` 中将 `status` 置为 `stale`、`stale_reason` 置为 `master_outline_changed`，并记录 `stale_at` 日期与受影响章节范围。当前没有专用 CLI 子命令完成此操作，由主流程在备份后用受控的状态更新写入；写入失败则停止并报告。

影响范围必须由作者在 Step 3 确认，不得自动猜测扩大。

## Step 6：写回锚点与收尾

受影响卷重新规划后，用现有机制写回总纲锚点：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" master-outline-sync \
  --volume {volume_id} --format text
```

再次运行：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" placeholder-scan --format text
```

## 作者友好报告契约

最终回复以以下结构输出，不输出原始 JSON、traceback、token 统计或长日志：

```text
总状态：已完成 / 部分完成 / 需要你处理 / 未完成。

一、修改与文件
- 修改了什么、保留了什么、备份在哪里、冻结线是第几章。

二、方向检查（总-卷）
- 可改单元：第 {v} 卷（最后一卷）。
- 既定事实：第 {a}-{b} 卷（本次未修改，其卷纲不得改写）。
- 受影响卷分流：第 {v} 卷 → /webnovel-volume-revise {v}；第 {a}-{b} 卷 → 卷纲锁定，只能调整总纲与之对齐或登记 accepted_deviation。

三、影响与异常
- 诉求分类结果（纯未来型/设定增补型/推翻型的处理结论）。
- 已自动处理：备份、受影响卷 stale 登记。
- 建议确认：揭示式翻案伏笔、accepted deviation、设定写回。
- 必须处理：需重新规划的卷范围。

四、下一步建议
- 最后一卷：/webnovel-volume-revise {volume_id}
- 非最后一卷：不改卷纲；如需一致，请调整总纲或登记 accepted_deviation
```

故障排查只提示 `.webnovel/logs/run_last.log` 或 `/webnovel-doctor`。
