---
name: webnovel-volume-revise
description: 通过确认式引导修改卷纲，分析影响范围并刷新卷纲 revision，安全标记下游章纲与合同过期。
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
5. 修改卷节拍、时间线、核心冲突、卷末承接或设定边界后，必须重新计算 revision 并标记依赖章节 stale。
6. 不得自动覆盖独立章纲，不自动刷新章级合同；下游由 `/webnovel-chapter-plan` 重新规划。
7. 校验失败时保留修改前备份，不登记新的可信 revision；不得报告为完成。

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

## Step 2：形成修改方案

将作者诉求拆成：

- 修改目标：要改变的剧情决策。
- 保留项：明确不能改变的节拍、人物、时间锚点或卷末承接。
- 影响文件：节拍表、时间线、详细大纲、总纲写回或设定集。
- 影响章节：按卷纲变化推断的最小章节范围；无法可靠判断时按本卷未提交章节处理。
- 风险：时间线冲突、设定冲突、伏笔提前/延后、旧章纲和合同过期。

用有限选项向作者确认：

```text
A. 按方案修改，并使受影响章节 stale
B. 只修改卷级文件，暂不写回设定或总纲
C. 停止，不修改文件
```

如果作者没有明确选择，继续询问，不得自行写入。

## Step 3：确认后定向修改

只在确认后编辑必要文件：

- 卷节拍表：只改受影响节拍、危机递增、卷末高潮或承接。
- 卷时间线：同步修改绝对时间、相对间隔、倒计时和事件顺序。
- 卷级详细大纲：同步卷级冲突、角色状态、伏笔和约束，不加入逐章 `目标`、`CBN`、`CPNs`、`CEN` 或章级合同参数。
- 设定/总纲：只有作者确认且修改方案明确列出时才增量写回。

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

## Step 5：收尾与恢复

再次运行：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" placeholder-scan --format text
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" user-report \
  --stage plan --volume {volume_id} --format text
```

受影响章节必须指向：

```text
/webnovel-chapter-plan {volume_id} {affected_range}
```

只有重新生成并校验章纲、刷新章级合同后，才可运行：

```text
/webnovel-write {chapter_num}
```

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

二、影响与异常
- 已自动处理：revision、备份、stale 登记。
- 建议确认：仍需作者判断的剧情或设定冲突。
- 必须处理：需要重新规划的章节范围。

三、下一步建议
- /webnovel-chapter-plan {volume_id} {affected_range}
```

故障排查只提示 `.webnovel/logs/run_last.log` 或 `/webnovel-doctor`。
