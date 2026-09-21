---
name: webnovel-write
description: 产出可发布章节，完整执行上下文→起草→审查→润色→提交→备份。
allowed-tools: Read Write Edit Grep Bash Agent AskUserQuestion
argument-hint: "[章号] [--fast|--minimal]"
---

# 写章流程

## 目标

产出可发布章节到 `正文/第{NNNN}章-{title}.md`。默认 2000-2500 字，用户/大纲另有要求时从之。

## 模式

| 模式 | 流程 |
|------|------|
| 默认 | Step 1→2→3→4→5→6 |
| `--fast` | Step 1→2→3(轻量)→4→5→6 |
| `--minimal` | Step 1→2→3(写 no-review artifact)→4(仅排版)→5→6 |

## 硬规则

- 禁止并步、跳步、伪造审查
- 必须使用 `Agent` 工具调用指定 subagent；不得用主流程口头代替 subagent 输出
- 每个正文版本审查一次；blocking issue 修复或润色造成内容变化时，必须重载新版本并重新审查
- 失败只补跑失败步骤，不回退
- 参考资料按步骤按需加载

## 优先级

用户要求 > 状态机硬门槛 > 项目约束（总纲/设定/记忆）> skill 流程 > reference 建议

## CSV 检索（Step 2 按需）

```bash
python -X utf8 "${SCRIPTS_DIR}/reference_search.py" --skill write --table {表名} --query "{关键词}" --genre {题材}
```

触发条件：新角色→命名规则，战斗→场景写法，多角色对话→写作技法，情感描写→写作技法，高频桥段→场景写法。

## 执行流程

### 准备：预检

```bash
export WORKSPACE_ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
export SCRIPTS_DIR="${CLAUDE_PLUGIN_ROOT:?}/scripts"
export SKILL_ROOT="${CLAUDE_PLUGIN_ROOT:?}/skills/webnovel-write"

python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${WORKSPACE_ROOT}" preflight
export PROJECT_ROOT="$(python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${WORKSPACE_ROOT}" where)"

python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" placeholder-scan --format text
```

### 准备：确认章纲与合同的新鲜度

genre 从 `.webnovel/state.json` 的初始化配置快照读取。写作前必须先确认目标章已有非空章纲；卷纲完成不等于章纲完成。新流程优先读取 `大纲/第{chapter_num}章-*.md`，旧项目才允许从卷级详细大纲读取对应章节作为 fallback。

若目标章没有章纲，立即阻断并提示：

```text
第 12 章尚未完成章纲规划，请先运行 `/webnovel-chapter-plan 1 12`。
```

章纲存在但晚于任一章级 Story System 合同时，也必须阻断；先让作者重新运行 `/webnovel-chapter-plan {volume_id} {chapter_num}` 刷新合同。若当前卷纲 revision 与章纲登记的 `source_volume_revision` 不一致，也必须阻断，先运行 `/webnovel-volume-reload {volume_id}`，再运行 `/webnovel-chapter-plan {volume_id} {chapter_num}`。不得从卷纲摘要、正文摘要或 `dynamic_context` 自由猜测本章目标，也不得使用 `{章纲目标}`、`第N章章纲目标` 等占位 query。

从真实章纲执行字段解析 `CHAPTER_GOAL` 后，才允许运行：

```bash
GENRE="$(python -X utf8 -c "import json,sys; s=json.load(open('${PROJECT_ROOT}/.webnovel/state.json',encoding='utf-8')); pi=s.get('project_info',{}); print(pi.get('genre') or s.get('project',{}).get('genre',''))")"

python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" \
  story-system "${CHAPTER_GOAL}" --genre "${GENRE}" --chapter {chapter_num} --persist --emit-runtime-contracts --format both

python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" \
  write-gate --chapter {chapter_num} --stage prewrite --format json
```

必备文件：非空章纲、`MASTER_SETTING.json`（调性/禁忌）、`volume_{NNN}.json`（卷级节奏）、`chapter_{NNN}.json` 和 `chapter_{NNN}.review.json`。缺失、过期或新流程只有卷级产物时均阻断。

`chapter_{NNN}.json` 必须优先检查顶层 `chapter_directive`。`chapter_focus` 只能来自 `chapter_directive.goal` 或真实 query，不得从 `dynamic_context` 的参考摘要继承。

写作任务书排序必须固定为：
1. 本章硬性约束：`chapter_directive.goal/time_anchor/chapter_span/countdown/chapter_end_open_question`
2. CBN/CPNs/CEN 与 `must_cover_nodes`
3. 本章禁区：`forbidden_zones`，违反即不通过
4. 风格指引：reasoning、主角卡 OOC 警戒、anti_patterns
5. 场景写法补充：`dynamic_context`，仅作风格参考，不能覆盖章纲约束

若当前正文、前置章节或依赖事实存在 `stale` / `previous_chapter_revision_changed`，必须先按报告核对受影响章纲和合同，再运行 `/webnovel-chapter-reload`；不得自动改写下游正文。若履约对账状态为 `needs_reconcile`，必须先预览并记录绑定当前 validation input 的作者裁决，不能把 artifact 自报的 decision 当作授权。

### Step 1：context-agent 生成写作任务书

必须使用 `Agent` 工具调用 `context-agent`，不得由主流程自行整理任务书。
Use the Agent tool to run `webnovel-writer:context-agent`.

Task:
- chapter={chapter_num}
- project_root=${PROJECT_ROOT}
- scripts_dir=${SCRIPTS_DIR}
- storage_path=${PROJECT_ROOT}/.webnovel
- state_file=${PROJECT_ROOT}/.webnovel/state.json（projection/read-model，仅兼容读取）
- 先 research，再按 本章硬性约束 → CBN/CPNs/CEN → 本章禁区 → 风格指引 → dynamic_context 补充参考 的顺序输出五段写作任务书。
- 上下文不足时返回 blocker。

产物：一份写作任务书，能独立支撑 Step 2 起草。

调用后主流程必须记录 `SubagentRun` 汇总（仅供最终报告使用）：

```json
{
  "name": "context-agent",
  "user_label": "整理写作依据",
  "status": "completed | partial | failed | skipped",
  "problems": [],
  "auto_handled": [],
  "needs_user_action": false,
  "duration_ms": 0,
  "outputs": []
}
```

上下文不足、legacy fallback、伏笔数据缺失、任务书不完整或耗时异常，必须写入 `problems` / `auto_handled`，不得在最终报告中静默。

### Step 2：起草正文

只根据任务书起草。不加载 core-constraints/anti-ai-guide（已内化到任务书）。只输出纯正文，无占位符。有结构化节点时围绕 CBN→CPNs→CEN 展开。中文思维写作。

### Step 3：审查

先登记本轮正文输入，保留返回的 validation_input，传给 reviewer 与 data-agent：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" chapter-reload \
  --chapter {chapter_num} --source generated_draft --format json
```

正文已由作者手改或已有 accepted commit 时，不使用 generated_draft 绕过重载确认，改运行 `/webnovel-chapter-reload {chapter_num}`。

必须使用 `Agent` 工具调用 `reviewer`，不得由主流程伪造审查 JSON。

Use the Agent tool to run `webnovel-writer:reviewer`.

Task:
- chapter={chapter_num}
- chapter_file=${CHAPTER_FILE}
- project_root=${PROJECT_ROOT}
- scripts_dir=${SCRIPTS_DIR}
- validation_input={本轮 chapter-reload 返回的原始对象}；顶层 source 原样回传，读取前后核对正文 SHA-256。
- 只返回严格的 reviewer schema JSON，不写任何文件。
- 不评分、不口头总结。

reviewer 只返回 JSON；主流程负责用 `Write` 把返回的 JSON 写入 `${PROJECT_ROOT}/.webnovel/tmp/review_results.json`（reviewer 不持 Write，是这份 artifact 的非写入方）。随后必须运行 review-pipeline；review-pipeline 会把同一路径覆盖为标准 review_result artifact（含 `blocking_count`），供 precommit gate 与后续提交命令使用。

调用后主流程必须记录 `SubagentRun` 汇总（仅供最终报告使用）：

```json
{
  "name": "reviewer",
  "user_label": "写作检查",
  "status": "completed | partial | failed | skipped",
  "problems": [],
  "auto_handled": [],
  "needs_user_action": false,
  "duration_ms": 0,
  "outputs": []
}
```

reviewer 跳过、失败、输出不完整、`--minimal` 写 no-review artifact、blocking issue、维度跳过或耗时异常，必须写入 `problems` / `auto_handled`，不得在最终报告中静默。

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" review-pipeline \
  --chapter {chapter_num} \
  --review-results "${PROJECT_ROOT}/.webnovel/tmp/review_results.json" \
  --metrics-out "${PROJECT_ROOT}/.webnovel/tmp/review_metrics.json" \
  --report-file "审查报告/第{chapter_num}章审查报告.md" \
  --save-metrics
```

同一正文版本只审查一次。`blocking=true` 问题修复后必须重载并审查修改后的版本，不能用旧报告证明新稿通过；无法修复时用 `AskUserQuestion` 让用户裁决（手动修复 / 调整规划 / 放弃），不能直接接受阻断 artifact。非 blocking issue 交给 Step 4 处理，若正文变化仍需重新审查。`--fast` 只检查 setting/timeline/continuity。

`--minimal` 不调用 reviewer 与 `review-pipeline`，但必须**覆盖写入**本章新的 no-review `review_results.json`（禁止复用旧 artifact），使 Step 5 提交链有有效 `--review-result`（成功标准“审查已落库”对 `--minimal` 的豁免仍成立）：

```bash
python -X utf8 -c "import json,os; from pathlib import Path; root=Path(os.environ['PROJECT_ROOT']); ch=int('{chapter_num}'); source=json.loads((root/'.webnovel'/'state.json').read_text(encoding='utf-8'))['progress']['chapter_revisions'][str(ch)]['validation_input']; p=root/'.webnovel'/'tmp'/'review_results.json'; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps({'source':source,'chapter':ch,'issues':[],'issues_count':0,'blocking_count':0,'has_blocking':False,'summary':'minimal mode: reviewer skipped by user-selected --minimal flow','review_skipped':True,'review_mode':'minimal'},ensure_ascii=False,indent=2),encoding='utf-8')"
```

### Step 4：润色

`references/polish-guide.md` 区段读：先 `Grep` 匹配 `^#{1,3} ` 定位锚点行号，再 `Read` 的 offset/limit 取段——主路径取 `## 2. 执行顺序（必须按序）`；Anti-AI 终检单独区段取 `## 2A. Anti-AI 检测细则` 与 `## Phase 1 增补：Anti-AI 规范（7层，原版）`（词库段），不全文读。`references/writing/typesetting.md`、`references/style-adapter.md` 短文件，全文读。

顺序：修复非 blocking issue → 风格适配 → 排版 → Anti-AI 终检。

只改表达不改事实。`anti_ai_force_check=fail` 时不进 Step 5。`--minimal` 仅排版。任何正文变化后，先重新执行 Step 3 的 chapter-reload 登记；默认/fast 重新调用 reviewer，minimal 重新生成绑定新输入的 no-review artifact。最终审查后不得继续修改正文。

### Step 5：提交

#### 5.1 Data Agent 提取事实

必须使用 `Agent` 工具调用 `data-agent`，产出 fulfillment_result / disambiguation_result / extraction_result 三份 JSON，并复用 Step 3 的 review_results。

Use the Agent tool to run `webnovel-writer:data-agent`.

Task:
- chapter={chapter_num}
- chapter_file=${CHAPTER_FILE}
- project_root=${PROJECT_ROOT}
- scripts_dir=${SCRIPTS_DIR}
- output_dir=${PROJECT_ROOT}/.webnovel/tmp
- validation_input={最终正文版本 chapter-reload 返回的原始对象}；三份 artifact 顶层 source 必须匹配。
- 按你自己的 schema（见 data-agent 输出格式段）生成 fulfillment_result.json、disambiguation_result.json、extraction_result.json 三份 artifact。
- 你是这三份 artifact 的唯一写入者；不直接写 state/index/summaries/memory/vectors/projection。

artifact 字段 schema 由 data-agent 自身定义、runtime validator 校验；主流程只检查文件存在与 schema，不重写、不补写、不口头替代。

调用后主流程必须记录 `SubagentRun` 汇总（仅供最终报告使用）：

```json
{
  "name": "data-agent",
  "user_label": "保存本章故事事实",
  "status": "completed | partial | failed | skipped",
  "problems": [],
  "auto_handled": [],
  "needs_user_action": false,
  "duration_ms": 0,
  "outputs": []
}
```

三份 artifact 写入状态、schema 不合格、pending 消歧、长时间无进展或输出不完整，必须写入 `problems`；自动重跑或降级处理必须写入 `auto_handled`。

#### 5.2 提交前校验与 CHAPTER_COMMIT

先校验并登记当前正文对应的四份 artifacts，再跑 precommit gate：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" chapter-reload \
  --chapter {chapter_num} --validate --format json
```


```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" \
  write-gate --chapter {chapter_num} --stage precommit --format json
```

precommit 通过后，运行提交前只读 `git diff` 变更面校验（写入所有权 sanity check，只读、不 stage、不提交）：

```bash
if git -C "${PROJECT_ROOT}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git -C "${PROJECT_ROOT}" diff --name-status -- .
  git -C "${PROJECT_ROOT}" diff --check -- .
fi
```

变更面不得出现插件目录、其他书项目、其他章节正文或不属于本章流程的手写状态文件；`git diff` 只覆盖 git 可见文件，SQLite / `.webnovel/` 内部语义由 5.3 postcommit 与 runtime 只读查询验证。若项目根不是 git worktree，记录“跳过 git diff 校验”，不得因此跳过 precommit gate。本步只读，禁止在此执行 `git add`/`git commit`。

校验通过后运行 chapter-commit：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" chapter-commit \
  --chapter {chapter_num} \
  --review-result "${PROJECT_ROOT}/.webnovel/tmp/review_results.json" \
  --fulfillment-result "${PROJECT_ROOT}/.webnovel/tmp/fulfillment_result.json" \
  --disambiguation-result "${PROJECT_ROOT}/.webnovel/tmp/disambiguation_result.json" \
  --extraction-result "${PROJECT_ROOT}/.webnovel/tmp/extraction_result.json"
```

自动判定：blocking_count>0 或 missed_nodes 非空 或 pending 非空 → rejected，否则 accepted。

#### 5.3 验证投影

projection_status 五项（state/index/summary/memory/vector）全部 done 或 skipped。

`chapter-commit` 在投影含 `failed:`/`pending`（含事件镜像失败）时返回非 0，stderr 会给出失败项与补跑命令；此时提交事实已经落盘，按 5.4 只补跑投影。

chapter_status 由 projection writer 自动推进：accepted→committed，rejected→rejected。

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" \
  write-gate --chapter {chapter_num} --stage postcommit --format json
```

#### 5.4 失败隔离

commit 未生成→重跑 5.2。projection 失败→只补跑 projection，不回退 Step 1-4。

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" \
  projections retry --chapter {chapter_num} --format json
```

注意区分两种非 0 退出：

- `chapter-commit` 返回 1 且 `.story-system/commits/chapter_{NNN}.commit.json` **已存在** → 提交事实有效，只是投影没跑完（stderr 会列出失败项）。按上面补跑 projection，不要重跑 review / 重写正文。
- `chapter-commit` 返回 1 且 commit 文件**不存在** → 提交本身被拒（如 `chapter_artifacts_stale`、`revision_confirmation_required`），回到 5.2 按其提示处理。

### Step 6：Git 备份

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" backup \
  --chapter {chapter_num} \
  --chapter-title "{title}"
```

备份必须以解析后的 `PROJECT_ROOT` 为准，禁止从工作区父目录执行裸全量 Git add，避免把书项目仓库作为父仓库的嵌入仓库/submodule 加入。

## 作者友好过程提示与恢复契约

开始写章前先用作者语言说明本次目标、主要阶段和是否需要守在旁边，不承诺固定耗时。过程提示只说当前在做什么和会产生什么，不直接输出原始 JSON、traceback 或长命令日志；技术详情写入 `.webnovel/logs/run_last.log`：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" run-log \
  --event write-start \
  --payload-json "{\"chapter\": {chapter_num}, \"mode\": \"{mode}\"}" \
  --format text
```

写章过程节点（最多 6 个）：

1. 检查项目环境：确认项目、占位符和本章要求可用。
2. 整理写作依据：读取章纲、最近剧情和未回收伏笔。
3. 起草正文：根据写作任务书生成本章正文。
4. 写作检查：审查阻断问题和高收益修改建议。
5. 保存本章故事事实：提取本章目标完成情况、歧义和新事实。
6. 提交备份：把本章事实入账、更新故事资料并备份。

重复执行同一章时，先读取可信断点：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" run-ledger write-resume \
  --chapter {chapter_num} \
  --mode "{mode}" \
  --format json
```

`run-ledger write-resume` 只给续跑建议，不自动覆盖文件。正文 hash 改变、旧正文没有可信 hash、或前置章修订未处理时，停止自动起草和旧结果复用，转到 `/webnovel-chapter-reload {chapter_num}`；不得覆盖作者手改。作者如明确要求重新起草，也要先备份并重新登记输入，不能沿用旧 accepted 状态跳过审查。这里的恢复选择是：沿用当前正文 / 重新起草 / 只查看状态；正文被手动改过或章纲更新晚于正文时，必须停在卡点并请求确认。

恢复契约关键词：可信断点；正文被手动改过；章纲更新晚于正文；本章已 accepted；沿用当前正文 / 重新起草 / 只查看状态；不得覆盖作者手改。

每个关键步骤完成后记录 `run-ledger record-write-step`，至少记录 step、status、输入/输出文件路径、problems、auto_handled 和 duration_ms，供下一次续跑和最终报告使用。

少打扰确认策略：默认继续推进；只有创作方向、事实一致性、文件覆盖风险或 blocking issue 无法定点处理时才问。需要用户裁决时给 2-3 个有限选项，并说明每个选项影响。

卡住时必须说明卡点、已完成内容和恢复建议：例如“正文和审查报告已保留，保存本章故事事实失败；重新运行 `/webnovel-write {chapter_num}` 会从 data-agent 继续”。不可恢复故障才在最终报告提示 `.webnovel/logs/run_last.log`；平时只保留日志，不打扰作者。

收尾必须调用作者报告 helper，优先以 helper 输出组织最终回复：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" user-report \
  --stage write \
  --chapter {chapter_num} \
  --format text
```

## 充分性闸门

1. 正文文件存在且非空
2. 审查已落库（`--minimal` 除外）
3. blocking=true 必须在 Step 3 定点修复或经用户裁决
4. anti_ai_force_check=pass（`--minimal` 除外）
5. accepted CHAPTER_COMMIT，projection 五项 done/skipped
6. chapter_status=committed（projection 自动推进）
7. `write-gate` 的 prewrite / precommit / postcommit 均通过

## 失败恢复

审查缺失→重跑 Step 3。摘要/状态/记忆缺失→重跑 Step 5。润色失真→回 Step 4 修复后重跑 Step 5。

## 作者友好最终报告契约

最终回复必须面向作者，不输出原始 JSON、traceback 或长命令日志。使用固定三段式，并以一句总状态开头：

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

必须汇报：
- 正文文件路径。
- 审查报告路径。
- `.webnovel/tmp/review_results.json`。
- `.webnovel/tmp/fulfillment_result.json`。
- `.webnovel/tmp/disambiguation_result.json`。
- `.webnovel/tmp/extraction_result.json`。
- `.story-system/commits/chapter_{NNN}.commit.json`。
- state / index / summary / memory / vector 更新状态。
- 备份状态。
- 是否可以继续写下一章。

状态规则：
- `chapter-commit rejected`、任一 `write-gate` failed、projection failed 时，最终状态不得写“已完成”。
- `--fast` 和 `--minimal` 的跳过项必须说明；`--minimal` 跳过审查时归入“已自动处理”或“建议确认”，不得假装已完成完整审查。
- projection retry 发生时必须说明已自动处理和最终结果。

异常分类：
- 已自动处理：projection retry 成功、RAG 临时降级但不影响结果、旧 no-review artifact 被本章新 artifact 覆盖。
- 建议确认：新增角色名 / 设定名、低置信歧义但不阻断、非阻断审查建议。
- 必须处理：blocking issue 未裁决、data artifacts 缺失或 schema 不完整、commit rejected、projection failed。

下一步建议必须使用任务化语言 + 可复制命令，例如：

```text
- 接下来可以写下一章：
  /webnovel-write {next_chapter}
```

不写 token 统计；如需排查故障，只给日志路径或建议运行 `/webnovel-doctor`。
