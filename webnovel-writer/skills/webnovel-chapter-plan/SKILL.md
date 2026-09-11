---
name: webnovel-chapter-plan
description: 基于已完成的卷纲生成可执行独立章纲，并刷新章节级 Story System 合同。
allowed-tools: Read Write Edit Grep Bash Agent AskUserQuestion
argument-hint: "[卷号] [章节范围，如 1-10]"
---

# 章纲规划

主 agent 职责：读取已完成的卷级规划，按批次生成独立章纲文件，校验章节承接与时间一致性，并在真实章纲落盘后为每章刷新 Story System 合同。不修改卷级规划的叙事决策，不直接写正文。

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
3. 已存在且内容非空的独立章纲视为作者资产，除非用户明确要求覆盖，不得自动覆盖。
4. 每章必须有真实目标；禁止使用 `{章纲目标}`、`第N章章纲目标` 等占位 query。
5. 先写章纲并通过校验，再按章调用 `story-system --chapter ... --persist --emit-runtime-contracts`。
6. 章纲或合同生成失败时，不更新该章的 `chapters_planned` 状态。
7. 不因卷纲完成而声称可以直接写作；写作前必须确认目标章章纲和章级合同均存在且未过期。

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

## Step 1：确认卷级前置条件

检查以下三个文件均存在且非空：

```text
大纲/第{volume_id}卷-节拍表.md
大纲/第{volume_id}卷-时间线.md
大纲/第{volume_id}卷-详细大纲.md
```

读取总纲、目标卷全部卷级规划、前一批章纲（若有）、最近正文摘要、角色状态、关系、活跃伏笔和开放环。卷级详细大纲若是新格式只包含卷级内容，不得从中自由猜测缺失的章目标；旧项目仍可由 `chapter_outline_loader.py` 使用卷级章节段落作为兼容回退。

## Step 2：确定章节范围与批次

解析用户给出的卷号和范围，并验证范围落在该卷章节区间内。按 8-12 章分批，每批明确：

- 本批起止章节。
- 本批承接的上一章 CEN 或卷级起点。
- 本批必须完成的卷级节拍节点。
- 本批应推进、回收或继续开放的伏笔。
- 本批的时间区间和倒计时边界。

## Step 3：按批次生成独立章纲

先读取 `${SKILL_ROOT}/../webnovel-plan/references/outlining/chapter-planning.md` 的结构化节点规范、`${SKILL_ROOT}/../../references/outlining/plot-signal-vs-spoiler.md`，再按需读取 `reading-power-taxonomy.md`、爽点、冲突和节奏参考。生成独立 Markdown 文件，标题须匹配 `第N章*.md`，推荐格式：

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

## Step 4：批次校验

逐批校验后再进入合同刷新：

- 每章文件存在且非空，章节号、标题和范围正确。
- 目标、阻力、代价、时间锚点、章内时间跨度、与上章时间差、倒计时状态、章末未闭合问题和钩子齐全。
- `CBN/CPNs/CEN/必须覆盖节点/本章禁区` 可被现有 parser 识别。
- 相邻章节满足 `上一章 CEN -> 下一章 CBN` 的因果或状态承接。
- 章内时间不回跳；倒计时算术一致；闪回必须显式标注。
- 伏笔推进与卷级规划一致；不凭空关闭未规划的开放环。
- 不含 `[待...]`、`暂名`、`{占位}` 等当前章不可执行占位符。
- 不发生与设定集、总纲或卷级节拍冲突的能力、关系和事实跳变。

校验失败只标记当前批次失败并保留已通过批次；不得先登记状态或覆盖作者已有文件。

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

- 产生的文件与完成情况：列出每个独立章纲和对应合同。
- 问题与异常：区分已自动处理、建议确认、必须处理；说明失败批次和 legacy fallback。
- 下一步建议：只对章纲和合同均通过的章节给出 `/webnovel-write N`；其余章节给出重跑 `/webnovel-chapter-plan {volume_id} {range}`。

## 作者友好过程提示与恢复契约

过程提示只说明当前动作和影响，不直接输出原始 JSON、traceback 或长命令日志。开始时说明：检查卷级前置 → 生成章纲批次 → 校验承接与时间 → 刷新章级合同 → 登记通过章节。

每批结束记录已完成内容、失败批次、自动处理的问题和恢复建议；异常细节写入 `.webnovel/logs/run_last.log`，可用以下命令记录：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" run-log \
  --event chapter-plan-progress \
  --payload-json "{\"stage\": \"chapter-plan\", \"volume\": {volume_id}}" \
  --format text
```

少打扰确认策略：正常批次不询问；遇到卷纲冲突、作者已有章纲、合同刷新失败或不可自动裁决的时间/伏笔冲突时，使用有限选项让作者选择保留、重做当前批次或停止。卡住时必须说明卡点、已完成内容和恢复建议，不覆盖作者已有章纲。

最终报告遵循作者友好最终报告契约，使用任务化语言和可复制命令：总状态：已完成 / 部分完成 / 需要你处理 / 未完成。以“总状态”开头，分为产生的文件与完成情况、问题与异常、下一步建议三段；不输出原始 JSON、traceback 或长命令日志。

```text
一、产生的文件与完成情况
- ...

二、过程中遇到的问题与异常耗时
- 已自动处理：...
- 建议确认：...
- 必须处理：...

三、下一步建议
- ...

不写 token 统计；故障排查只提示 `.webnovel/logs/run_last.log` 或 `/webnovel-doctor`。
```


```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" user-report \
  --stage chapter-plan --volume {volume_id} --chapter {start_chapter} --format text
```


1. 目标卷级文件存在且非空。
2. 每个目标章节拥有独立、可解析、非空章纲，或明确标记为旧项目 fallback。
3. 所有相邻章节的 CEN→CBN、时间线、倒计时、伏笔和占位符校验通过。
4. 每个成功章节均有四类章级 Story System 合同，且合同在章纲之后生成。
5. 只有成功章节进入 `chapters_planned`；失败批次可独立恢复。
6. 报告明确下一步，不把卷纲完成误报为可以写作。
