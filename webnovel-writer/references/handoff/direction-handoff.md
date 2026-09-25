# 方向透传（总纲 → 卷纲 → 章纲 → 正文）

四层是**单向关联**：总纲指导卷纲，卷纲指导章纲，章纲指导正文。

任一层发生变化时，必须检查它与紧邻上下层是否仍**同向**。不同向时不能一边倒——
由作者裁决改哪一侧；裁决结果在相邻节点之间**逐级上溯**，直到总纲。

## 一、为什么要做这件事

| 若某层被改动却不同步检查 | 后果 |
|---|---|
| 总纲变了，卷纲没跟上 | 后续卷纲生成时依据矛盾，整条线返工 |
| 卷纲变了，章纲没跟上 | 后续章纲承接错误，越写越偏 |
| 章纲变了，正文没跟上 | 情节不连续，读者可见的断裂 |

四个层级的关联是链路式的：**任何一层改动，都要在方向上与其余层对齐**。

## 二、三个方向透传节点

| 节点 | 依据（上游） | 产物（下游） | 可改单元 | 既定事实 |
|---|---|---|---|---|
| `master_to_volume` | 总纲 | 卷纲 | **最后一卷** | 其余已规划卷 |
| `volume_to_chapter` | 卷纲 | 章纲 | **最后一章** | 其余已规划章 |
| `chapter_to_body` | 章纲 | 正文 | **最后一章** | 其余已有正文章 |

统一规律：**每层的可改单元只有一个——最后一个**；其余是既定事实，改动必须上溯。

### 各节点的具体边界

**总-卷**（`master_to_volume`）
- 卷纲在生成时（`/webnovel-plan`，含规划对话）或被修改时（人工编辑后 `/webnovel-volume-reload`，或 `/webnovel-volume-revise`），必须与总纲对齐。
- 只允许改最后一卷的卷纲。总纲也要与**非最后一卷**的已存在卷纲保持同向——那些卷纲是既定事实。
- 不一致时与作者讨论，由作者决定是否修改总纲。

**卷-章**（`volume_to_chapter`）
- 章纲在生成时（`/webnovel-chapter-plan`）或被修改时（`/webnovel-chapter-revise`），必须与卷纲对齐。
- 只允许改最后一章的章纲。卷纲也要与**非最后一章**的已存在章纲保持同向。
- 不一致时与作者讨论，由作者决定是否修改卷纲。
- **若最后一卷的卷纲被修改，必须上溯到总-卷节点**再走一遍。

**章-正**（`chapter_to_body`）
- 正文本修改时（人工修改后 `/webnovel-chapter-reload`），必须与章纲对齐。
- 只允许改最后一章的正文。
- 一致时章纲不变；不一致时与作者讨论，由作者决定是否修改章纲。
- **若章纲被修改，必须上溯到卷-章节点**再走一遍。

## 三、统一裁决口径

三个节点共用同一套三值裁决：

| 裁决 | 含义 | 落到哪一层 |
|---|---|---|
| `align_downstream` | 改下游产物以符合上游依据 | 产物层 |
| `align_upstream` | 改上游依据以符合下游产物 | 依据层（**须先上溯**） |
| `accepted_deviation` | 接受偏离，只记录，不改任何文件 | 不落文件 |

章-正节点的既有章级裁决值是上表的别名，语义一一对应，读取时自动归一：

```text
outline_to_body  →  align_downstream
body_to_outline  →  align_upstream
accepted_deviation  →  同名
```

### 上溯规则

选 `align_upstream` 时，改动落在**依据层**，而依据层本身受更上一层约束：

| 当前节点 | 选 `align_upstream` 时 | 必须先过的上一层节点 |
|---|---|---|
| `master_to_volume` | 改总纲 | 无（终点；总纲的约束是冻结线，走 `/webnovel-outline-revise`） |
| `volume_to_chapter` | 改卷纲 | `master_to_volume` |
| `chapter_to_body` | 改章纲 | `volume_to_chapter` |

命令里的 `target` 单位随节点变化：总-卷节点用**卷号**，卷-章与章-正节点用**章号**。
从上溯到 `master_to_volume` 时，需把章号换算成所属卷号（`handoff` 的 `next_steps` 已给出）。

上溯不是可选项：改卷纲就必然要回答"新卷纲是否仍与总纲同向"。

## 四、CLI

```bash
# 只看边界：可改单元 / 既定事实
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" handoff \
  --node {node} --format text

# 一致性检查：采集两侧锚点，输出候选偏离与须裁决项
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" handoff \
  --node {node} --target {n} --check --format json

# 预览裁决输入（不改任何文件）
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" handoff \
  --node {node} --target {n} --record --dry-run --format json

# 记录作者裁决
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" handoff \
  --node {node} --target {n} --record \
  --decision {align_downstream|align_upstream|accepted_deviation} \
  --reason "{作者理由}" --impact {受影响单位} \
  --expected-input "{input_token}" --format json
```

### 机器能给什么、不能给什么

| 层级组合 | 机器可判定 | 须作者裁决 |
|---|---|---|
| 总-卷 | 卷号是否存在、章节范围是否一致、总纲写回 JSON 是否与卷表一致 | 卷名、核心冲突、卷末高潮的散文表述是否仍同向 |
| 卷-章 | 章纲「关键实体」在上游是否出现；卷纲 revision 是否过期 | 专名未落地是否真为偏离 |
| 章-正 | `fulfillment_result` 的 `missed` / `partial` / `contradicted` / `not_applicable` | 偏离是否可接受 |

**候选清单不是硬判定**。总纲与卷纲是散文，机器只能给出「锚点差集」；
是否真的方向不一致，必须由作者裁决。**不得据候选清单自动改文件。**

`--record` 只写 `.story-system/handoffs/`，不改任何创作文件；`input_token` 绑定当时的
边界快照与上游来源，上游一改即失效，必须重新预览。

## 五、确认话术

与 `/webnovel-plan`、`/webnovel-chapter-plan` 的规划对话共用同一套「不再讨论」语义：
**确认轮无上限，每轮必带结束开关**。每轮固定三段：

```text
本轮议题：{候选偏离项 / 须裁决项}
- 我的建议：{align_downstream / align_upstream / accepted_deviation}
- 理由：{为什么这样改动最小、最不容易返工}
- 风险：{选它会在哪一层、哪一章付代价}
请选：
  A. {align_downstream 的具体动作}
  B. {align_upstream 的具体动作，含需上溯的节点}
  C. 接受偏离，只记录不改
  D. 不再讨论，按当前已确认范围结束
```

规则：

1. **先给建议再问**；禁止只抛开放问题。
2. `D` 选项语义与规划对话一致：只处理已明确项，未决项保持原样、不写入任何文件。
3. 作者未明确选择时继续询问，不得自行写入。
4. 每轮结束把结论落到对应节点的裁决记录（`handoff --record`），保证中断可恢复。

## 六、与既有边界概念的关系

| 概念 | 所属 | 基准 | 关系 |
|---|---|---|---|
| **冻结线** | `/webnovel-outline-revise` | 最新 accepted commit 章号 | 总纲不可推翻的已发布事实 |
| **锁定区** | `/webnovel-volume-revise` | 已存在的独立章纲 | `锁定区 ⊇ 冻结线` |
| **方向透传可改单元** | 本文件 | 各层「最后一个已存在单位」 | 与锁定区同向，边界更明确 |

三者不冲突：方向透传给出**结构边界**（谁可以动），冻结线给出**事实边界**（什么不可以被推翻）。

## 七、报告契约

方向透传结论统一按以下结构向作者输出，不输出原始 JSON 或长日志：

```text
方向检查：{节点名}（{依据} → {产物}）
- 可改单元：第 {n} {单位}
- 既定事实：第 {a}-{b} {单位}（本次未修改）
- 机器候选偏离：{逐条}
- 须裁决项：{逐条}
- 裁决：{align_downstream / align_upstream / accepted_deviation}（作者已确认）
- 上溯：{需转往的上一层节点与命令}（若选 align_upstream）
```

## 八、失败与恢复

- `handoff_target_locked`：目标单位不在可改单元内。按提示改走可改单元，或先上溯处理既定事实。
- `handoff_confirmation_required`：`input_token` 不匹配。重新 `--dry-run` 预览并让作者确认新的 token。
- 候选偏离为空但须裁决项非空：**不等于方向一致**，仍要与作者逐项确认散文层。
- 裁决记录只增不改；需要推翻旧裁决时，重新预览并记录一条新裁决。
