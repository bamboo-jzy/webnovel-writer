---
name: webnovel-plan
description: 基于总纲生成卷纲、卷级时间线和节拍表，并把新增设定增量写回现有设定集。
allowed-tools: Read Write Edit Bash AskUserQuestion
argument-hint: "[卷号，如 1]"
---

# 卷纲规划

主 agent 职责：基于总纲增量细化卷纲、卷级节拍表与时间线，把新增设定写回设定集并同步总纲。不生成逐章章纲，不刷新章级 Story System 合同，不重做全局故事。

## 执行原则

1. 只做增量补齐，不重写整份总纲或设定集。
2. 先锁定卷级冲突、节奏、时间线和卷末承接，再交由 `/webnovel-chapter-plan` 拆章。
3. 卷级时间线是硬约束；本 skill 不产出章级时间字段。
4. 若发现总纲与设定冲突，先阻断，再等用户裁决。
5. 优先级链：用户明确要求 > 总纲核心冲突与卷末高潮 > 时间线硬约束 > skill 默认流程 > reference 建议。

## 阻断条件

- 项目根不合法或总纲缺失。
- 总纲缺少卷名 / 章节范围 / 核心冲突 / 卷末高潮 → 阻断并请求用户补全。
- 发现设定冲突 → 标记 `BLOCKER`，等待用户裁决。
- 卷级时间线出现无法解释的回跳 → 阻断当前卷。
- 验证失败 → 只重做失败的卷级产物，不覆盖其他卷。

## 环境准备

```bash
export WORKSPACE_ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
export SKILL_ROOT="${CLAUDE_PLUGIN_ROOT}/skills/webnovel-plan"
export SCRIPTS_DIR="${CLAUDE_PLUGIN_ROOT}/scripts"
export PROJECT_ROOT="$(python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${WORKSPACE_ROOT}" where)"

python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" preflight
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" placeholder-scan --format text
```

规划开始和结束都运行 `placeholder-scan`。卷级规划不得把当前章目标写成可供写作直接执行的占位内容。

## 读取策略

每个 reference 只在对应步骤触发时读取，优先用 `Grep` 定位标题后再用 `Read` 读取目标区段。

| 触发 | 读取方式 | 文件 |
|------|---------|------|
| Step 4 | 全文 | `${SKILL_ROOT}/../../templates/output/大纲-卷节拍表.md` |
| Step 5 | 全文 | `${SKILL_ROOT}/../../templates/output/大纲-卷时间线.md` |
| Step 6 | 区段 | `${SKILL_ROOT}/../../references/genre-profiles.md` |
| Step 6 | 全文 | `${SKILL_ROOT}/../../references/shared/strand-weave-pattern.md` |
| Step 6 需要爽点 | 区段 | `${SKILL_ROOT}/../../references/shared/cool-points-guide.md` |
| Step 6 需要冲突 | 区段 | `${SKILL_ROOT}/references/outlining/conflict-design.md` |
| Step 6 特定节奏 | 区段 | `${SKILL_ROOT}/references/outlining/genre-volume-pacing.md` |

CSV 创作参考使用检索，不读取整表：

```bash
python -X utf8 "${SCRIPTS_DIR}/reference_search.py" --skill plan --table 爽点与节奏 --query "{卷级核心冲突}" --genre "${GENRE}"
python -X utf8 "${SCRIPTS_DIR}/reference_search.py" --skill plan --table 桥段套路 --query "{卷级核心冲突}" --genre "${GENRE}"
python -X utf8 "${SCRIPTS_DIR}/reference_search.py" --skill plan --table 命名规则 --query "角色命名" --genre "${GENRE}"
```

## 执行流程

### Step 1：加载项目数据并确认前置条件

读取 `$PROJECT_ROOT/.webnovel/state.json` 和 `$PROJECT_ROOT/大纲/总纲.md`，确认目标卷名、章节范围、核心冲突和卷末高潮；缺少任一关键项则阻断。按需读取 `设定集/世界观.md`、`设定集/力量体系.md`、`设定集/主角卡.md`、`设定集/反派设计.md`、`.webnovel/idea_bank.json`。

非首卷必须读取最近摘要、角色状态、关系状态和开放环，确保跨卷承接：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" knowledge query-entity-state --entity "{protagonist_id}" --at-chapter {上一卷最后章}
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" knowledge query-relationships --entity "{protagonist_id}" --at-chapter {上一卷最后章}
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" memory-contract get-open-loops
```

### Step 2：补齐设定基线

增量补齐可规划的世界边界、力量限制、主角欲望与缺陷、反派层级；不清空、不重写整文件。发现与既有设定冲突时立即记录 `BLOCKER` 并暂停。

### Step 3：确认目标卷

确认卷名、章节范围，以及视角、情感线、题材偏移等特殊要求。只确定卷级边界，不拆出章号目标。

### Step 4：生成卷节拍表

加载 `${SKILL_ROOT}/../../templates/output/大纲-卷节拍表.md`。必须填写中段反转（确无则说明理由）、至少 3 次递增危机、卷末高潮和下一阶段承接。

输出：`大纲/第{volume_id}卷-节拍表.md`

### Step 5：生成卷时间线表

加载 `${SKILL_ROOT}/../../templates/output/大纲-卷时间线.md`。明确时间体系、本卷时间跨度及倒计时事件（标记 D-N）；只记录卷级事件、阶段和锚点。

输出：`大纲/第{volume_id}卷-时间线.md`

### Step 6：生成纯卷级详细大纲

卷纲必须明确卷摘要、关键人物与反派层级、Strand 分布、爽点密度、伏笔规划、约束触发规划、卷中阶段推进和卷末承接。

禁止在此文件中生成 `第X章` 的目标、阻力、代价、时间字段、CBN、CPNs、CEN、钩子或章级合同参数；这些内容由 `/webnovel-chapter-plan` 生成到独立章纲文件。

非首卷必须延续上一卷未回收伏笔、角色关系和主角能力/境界，不得无解释回退或跳级。

输出：`大纲/第{volume_id}卷-详细大纲.md`

### Step 7：把新增设定写回现有设定集

输入卷节拍表、卷时间线表、卷详细大纲和现有设定集。只增量写回新角色、新势力、地点、规则和反派层级；不得覆盖作者已有内容。冲突未裁决时停止。

### Step 8：验证、保存并更新状态

验证三个卷级产物均存在且非空，卷级时间线没有无法解释的回跳，新设定已写回，`BLOCKER=0`，详细大纲没有逐章执行字段或章级合同调用要求。

验证通过后生成 `大纲/第{volume_id}卷-总纲写回.json`，只写明确列出的伏笔和开放环：

```json
{
  "next_volume_anchor": {
    "volume": 2,
    "volume_name": "下一卷卷名",
    "core_conflict": "下一卷核心冲突",
    "volume_end_climax": "下一卷卷末高潮"
  },
  "foreshadow_writeback": [
    {"content": "本卷明确新增的伏笔", "buried_chapter": "", "payoff_chapter": "", "level": "卷级"}
  ],
  "open_loop_writeback": [
    {"content": "本卷结束后仍开放的问题", "buried_chapter": "", "payoff_chapter": "", "level": "持续开放环"}
  ]
}
```

执行最小总纲写回，不生成下一卷或章纲产物：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "$PROJECT_ROOT" master-outline-sync \
  --volume {volume_id} \
  --writeback-file "大纲/第{volume_id}卷-总纲写回.json" \
  --format text
```

仅在上述验证和同步成功后登记卷纲 revision：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "$PROJECT_ROOT" volume-reload \
  --volume {volume_id} \
  --chapters-range "{start}-{end}" \
  --source guided_plan \
  --format text
```

该步骤会记录卷纲内容 revision；若是已有卷纲发生变化，会把依赖该卷的章纲标记为 `stale`。

本 skill 不调用 `story-system --chapter`、`--emit-runtime-contracts` 或其他章级合同命令。Story System 合同目录 `.story-system/` 由 `/webnovel-chapter-plan` 在章纲通过后维护。

## 恢复规则

1. 只重做失败的卷级产物，不覆盖其他卷。
2. 仅在全部卷级验证通过后更新 `volumes_planned`。
3. 章纲和章级合同由 `/webnovel-chapter-plan` 单独生成和恢复。

## 作者友好过程提示与恢复契约

过程提示只说明当前动作和影响，不直接输出原始 JSON、traceback 或长命令日志。少打扰确认策略：正常卷级产物不询问；发现设定冲突、时间线回跳或作者已有内容时，使用有限选项让作者选择保留、重做当前产物或停止。卡住时必须说明卡点、已完成内容和恢复建议，异常细节写入 `.webnovel/logs/run_last.log`，并通过 `run-log` 记录。

开始时说明：检查总纲与设定 → 生成卷节拍表 → 生成卷时间线 → 完成纯卷级详细大纲 → 写回新增设定 → 同步总纲。不要承诺本次生成章纲或写作合同。收尾调用：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" user-report \
  --stage plan --volume {volume_id} --format text
```

最终报告使用任务化语言和可复制命令，固定为“总状态 / 一、产生的文件与完成情况 / 二、过程中遇到的问题与异常耗时 / 三、下一步建议”，总状态：已完成 / 部分完成 / 需要你处理 / 未完成。必须列出三个卷级文件、总纲写回、设定回写、同步状态以及校验结果。该段同时是作者友好最终报告契约。

最终回复必须明确列出：

```text
总状态：已完成 / 部分完成 / 需要你处理 / 未完成。

一、产生的文件与完成情况
- ...

二、过程中遇到的问题与异常耗时
- 已自动处理：...
- 建议确认：...
- 必须处理：...

三、下一步建议
- ...
```

下一步必须明确给出：

```text
/webnovel-chapter-plan {volume_id} {start}-{end}
```

不写 token 统计；故障排查只提示 `.webnovel/logs/run_last.log` 或 `/webnovel-doctor`。
