# 命令详解

## Skill 命令（在 Claude Code 中使用）

### `/webnovel-init`

初始化小说项目，生成目录结构、设定模板和状态文件。

产出：

- `.webnovel/state.json`（运行时状态）
- `设定集/`（世界观、力量体系、主角卡、金手指设计、反派设计等）
- `大纲/总纲.md`、`大纲/爽点规划.md`
- `.env.example`（RAG 配置模板）

### `/webnovel-outline-revise [修改诉求]`

以确认式流程修改总纲。核心约束是**冻结线**：系统取最新 accepted commit 的章节号作为冻结线，冻结线左侧（已发布正文）的事实不可被修订推翻，只有右侧（未写/未提交章节）可修订。

Skill 先计算冻结线、读取冻结线内各章已发布事实，把诉求分为三类处理：

- **纯未来型**（改结局、改未写卷核心冲突）：确认后定向编辑总纲，并标记受影响卷 `volumes_planned` 为 `stale`（`stale_reason: master_outline_changed`），需逐卷运行 `/webnovel-volume-revise`。
- **设定增补型**（补力量体系上层、新增未登场势力）：允许，校验不与已发布事实矛盾后写回。
- **推翻已发布事实型**（"其实某人没死"等否定已写内容）：**只阻断提示**，列出冲突点并给出两条出路——揭示式翻案（登记为伏笔，不生成反转正文）或增补填白；不替作者改写，也不按普通流程写入。

```bash
/webnovel-outline-revise
/webnovel-outline-revise 把结局从悲剧改为大团圆
```

不会修改卷级文件、章纲、正文或 `.story-system/`；整文件重写总纲被禁止，只做定向编辑。

### `/webnovel-plan [卷号]`

只生成卷级规划：卷节拍表、卷时间线和纯卷级详细大纲，并增量写回设定与总纲。它不会生成逐章章纲或章级 Story System 合同。

```bash
/webnovel-plan 1
/webnovel-plan 2
```

### `/webnovel-chapter-plan [卷号] [章节范围]`

读取已完成的卷级规划，按 8-12 章批次生成独立章纲，校验 CBN → CPNs → CEN、相邻章承接、时间锚点与伏笔推进，并刷新每章的 Story System 合同。

```bash
/webnovel-chapter-plan 1
/webnovel-chapter-plan 1 1-10
/webnovel-chapter-plan 1 11-20
```

已有且非空的独立章纲视为作者资产，不会被自动覆盖；失败时只重做当前批次。

### `/webnovel-chapter-revise [章号] [修改诉求]`

以确认式流程修改已有章纲。Skill 会先读取目标章纲、所属卷三份卷级文件、总纲、相邻章纲和状态证据，展示保留项、修改项及对相邻章承接、时间线、伏笔的影响；只有作者确认后才备份并定向编辑。修改后重新校验章纲、刷新章级 Story System 合同，并用 `update-state --chapter-planned` 自动重算章纲 revision 与合同 revision 重登记。

```bash
/webnovel-chapter-revise 15
/webnovel-chapter-revise 15 把反派出场提前到本章中段
```

不会自动覆盖卷级文件或其他章纲；若诉求与卷级规划冲突，会阻断并指向 `/webnovel-volume-revise`。该章已有正文时，章纲修订不改正文；完成后按提示运行 `/webnovel-chapter-reload` 走正文重载链。

### `/webnovel-volume-revise [卷号] [修改诉求]`

以确认式流程修改已有卷纲。Skill 会先读取三份卷级文件、总纲、设定和状态，展示保留项、修改项及影响章节；只有作者确认后才定向编辑。修改前会备份卷纲和状态，修改后校验并刷新 revision，不会自动覆盖独立章纲或章级合同。

```bash
/webnovel-volume-revise 1
/webnovel-volume-revise 1 调整卷末反派身份，但保留前半卷节奏
```

### `/webnovel-volume-reload [卷号]`

人工编辑 `大纲/第N卷-节拍表.md`、`大纲/第N卷-时间线.md` 或 `大纲/第N卷-详细大纲.md` 后，必须显式重载。系统按三份文件内容计算 SHA-256 revision；revision 变化时，将依赖旧 revision 的 `chapters_planned` 标记为 `stale`，保留原有章纲文件不覆盖。

```bash
/webnovel-volume-reload 1
```

受影响章节需重新运行 `/webnovel-chapter-plan`，在章纲和章级合同刷新前，`/webnovel-write` 的写前门禁会阻断过期章节。

### `/webnovel-chapter-reload [章号]`

正文经过作者人工修改后，必须显式重载当前内容 revision。系统按正文字节计算 SHA-256，先预览并备份，再绑定新的 `validation_input`；reviewer 和 data-agent 重新读取当前正文后，使用 `--validate` 检查四份 artifacts 是否与同一版本匹配。重载、校验和提交是三个独立阶段，不会覆盖正文、章纲或合同。

```bash
/webnovel-chapter-reload 12
```

底层 CLI 支持：

```bash
python -X utf8 "${CLAUDE_PLUGIN_ROOT}/scripts/webnovel.py" \
  --project-root "${PROJECT_ROOT}" chapter-reload --chapter 12 --dry-run --format json
python -X utf8 "${CLAUDE_PLUGIN_ROOT}/scripts/webnovel.py" \
  --project-root "${PROJECT_ROOT}" chapter-reload --chapter 12 --backup-only --format json
python -X utf8 "${CLAUDE_PLUGIN_ROOT}/scripts/webnovel.py" \
  --project-root "${PROJECT_ROOT}" chapter-reload --chapter 12 --validate --format json
```

正文 revision 变化会将后续已有规划、合同或正文的依赖标为 `previous_chapter_revision_changed`；系统不会自动改写下游文件。若重新提取的事实与旧 accepted commit 不一致而投影链无法安全撤销旧事实，提交会以 `revision_projection_unsafe` 阻断，并保留旧提交和新校验结果。

履约对账未决时，`chapter-commit` 不会信任 artifact 自报的 `decision` 或 `confirmed_by`。先预览当前输入和偏离节点：

```bash
python -X utf8 "${CLAUDE_PLUGIN_ROOT}/scripts/webnovel.py" --project-root "${PROJECT_ROOT}" chapter-reload \
  --chapter 12 --reconcile --dry-run --format json
```

作者核对正文、章纲、合同、revision evidence 和影响范围后，才可用预览返回的 `input_token` 写入独立裁决：

```bash
python -X utf8 "${CLAUDE_PLUGIN_ROOT}/scripts/webnovel.py" --project-root "${PROJECT_ROOT}" chapter-reload \
  --chapter 12 --reconcile --decision accepted_deviation \
  --reason "作者确认保留当前偏离" --impact 13,14 \
  --expected-input "{input_token}" --format json
```

`outline_to_body` / `body_to_outline` 只表示后续处理方向，不直接放行提交。任何 artifact、正文、章纲或合同变化都会使 token 失效，必须重新预览和校验；未裁决时保持 `needs_reconcile` / `blocked`。

### `/webnovel-write [章号]`（`context-agent` 先 research 并生成写作任务书 → 按任务书起草正文 → 审查 → 润色 → 数据落盘）。

```bash
/webnovel-write 1
/webnovel-write 45
```

### `/webnovel-review [范围]`

对已有章节做章节事实一致性与逻辑审查，覆盖 setting（设定）、timeline（时间线）、continuity（叙事连贯）、character（角色一致性）、logic（逻辑）五维，输出带证据的问题和修复方向。

不评分、不评价文笔、不建议情节改动；不提供爽点、节奏或追读力质量评分。无阻断只表示本次事实审查未发现阻断问题，不代表完整编辑质量达标，下一章仍须通过写前门禁。

```bash
/webnovel-review 1-5
/webnovel-review 45
```

### `/webnovel-query [关键词]`

查询角色、伏笔、节奏、状态等运行时信息。

```bash
/webnovel-query 萧炎
/webnovel-query 伏笔
```

### `/webnovel-learn [内容]`

从当前会话或用户输入中提取可复用写作模式，写入项目记忆。

```bash
/webnovel-learn "本章的危机钩设计很有效，悬念拉满"
```

产出：`.webnovel/project_memory.json`

### `/webnovel-style-learn [build|show|diff]`

学习文风并生成可复用的文风档案：`正文/` 学「现状画像」，`文风/` 学「目标画像」。

```bash
/webnovel-style-learn build
/webnovel-style-learn show
/webnovel-style-learn diff
```

产出：`.webnovel/style_profile.json`（机读，含任务书注入摘要）+ `文风/文风档案.md`（人读，脚本段 + 归纳段）

说明：

- 源 A 默认只统计已提交接收（`accepted`）的章，未提交/被拒的稿不计入；确需用草稿学习才加 `--source all`
- 源 B 是往 `文风/` 放要模仿的参考文本（`.md` / `.txt`）；该目录随版本点提交，不要放整本书
- 现状与目标语义不同：冲突时以目标为准，不得把现状当标准
- 档案的脚本段会被 `build` 覆盖写；标记之外的「归纳段」原样保留

### `/webnovel-dashboard`

启动只读可视化面板，查看项目状态、实体关系、章节与大纲内容。

```bash
/webnovel-dashboard
```

说明：

- 默认只读，不会修改项目文件
- 前端构建产物已随插件发布，无需本地 `npm build`

### 恢复到历史版本

`backup --chapter N` 每章产生一个提交和 `chNNNN` 版本点 tag（指向该章最新已备份状态），恢复用 Git 原生命令，插件不接管回退：

```bash
git log --oneline ch0030..HEAD              # 回退点之后有哪些提交
git diff --stat ch0030 HEAD                 # 会改哪些文件
git switch -c rewrite-from-ch0030 ch0030    # 工作树整体回到第 30 章，另开分支，历史不动
git switch main                             # 放弃这条线就切回原分支
```

说明：

- 用 `git switch -c <分支> <tag>`，不要用 `git checkout <tag> -- 正文 大纲 设定集 .story-system`：后者在任一目录不存在时整体报错、什么都不恢复，也不会删除回退点之后新增的文件
- 动手前 `git status --short` 必须为空，否则 `git switch` 拒绝执行
- 重写已存在的章号时，`backup` 会把 `chNNNN` 前移到新提交，被前移的旧版本点自动保留为 `chNNNN-prev-<时间戳>`（如 `ch0031-prev-20260917T105258`）——历史提交不丢，也不必手工删 tag；要回到某个旧版本点用 `git switch -c <分支> chNNNN-prev-<时间戳>`
- `backup --list` 会分两段显示：章节当前版本点 `chNNNN`，以及它下面缩进的 `↳ 历史点`
- 切回旧章后重新跑一次 `/webnovel-doctor`，确认 Story System 与 projection 一致再续写
- 查点与开分支仍走插件：`backup --list`、`backup --diff 20 40`、`backup --create-branch 50 --branch-name <name>`
- Git 不可用时 `backup` 生成 `.webnovel/backups/snapshot_chNNNN_*` 离线副本（只保留最近 10 份），没有恢复子命令，需手工复制文件


### `/webnovel-doctor [--chapter N] [--deep]`

只读体检当前网文项目，检查阶段应有文件、JSON、SQLite、RAG 配置、Python 依赖与 Dashboard 产物，并给出影响和修复建议。

```bash
/webnovel-doctor
/webnovel-doctor --chapter 12
/webnovel-doctor --deep
```

说明：

- 不写入项目，不安装依赖，不启动服务
- 会先判断当前项目阶段，init 刚结束时不会按终态项目误报

### `/webnovel-audit [--from-chapter N] [--to-chapter N] [--checks s1,s4,s5]`

范围级回扫：单章 `review` 查不到「跨了很多章之后才开始显现」的问题，这个命令补这一段。

```bash
/webnovel-audit
/webnovel-audit --checks s1,s5
/webnovel-audit --from-chapter 41 --to-chapter 80
```

三个检查项：

| 检查项 | 看什么 |
|--------|--------|
| `s1` Strand 配比 | Quest/Fire/Constellation 三线占比、主线最大连续、感情线最大断档 |
| `s4` 漂移 | 角色/地点/物品的**称谓跨章变化**、角色状态变更链、出场断档、状态取值反复 |
| `s5` 文体漂移 | 句长/段长分布、对话段占比、高频 4 字片段（口癖候选） |

说明：

- **只读**：不改正文、不改索引、不改状态；报告落在 `.webnovel/reports/scope-audit.md`
- 阈值优先取题材 profile（`references/genre-profiles.md`），回落默认值时报告会明确标注
- **S5 偏离基线可指定**：`--baseline auto`（默认，有 `.webnovel/style_profile.json` 就用
  文风档案的 observed 中位数当基线，**跨卷可比**）/ `profile`（强制要求档案基线，拿不到就
  不下偏离结论）/ `range`（只用本次范围内的中位数，旧口径）。报告与 json 里的
  `baseline` 字段都会写明这次用的是哪一种、来自多少章、生成于何时
- **样本不足时明确不下结论**：占比结论需 ≥10 章，文体偏离结论需 ≥3 章（或一份可用的文风档案）；
  报告会把这些项列在「本次不下结论的项」里，**不要把空结论当成「一切正常」**
- 伏笔逾期与钩子强度分布两项**尚未实现**（供数未接通，见 `references/index/skill-gap-assessment-2026-09-17.md` 附录 B）

## 统一 CLI（命令行使用）

所有 CLI 命令的入口都是 `webnovel.py`，格式：

```bash
python -X utf8 "<CLAUDE_PLUGIN_ROOT>/scripts/webnovel.py" --project-root "<PROJECT_ROOT>" <子命令> [参数]
```

### 作者友好运行体验

`/webnovel-init`、`/webnovel-plan`、`/webnovel-chapter-plan`、`/webnovel-write` 和 `/webnovel-review` 结束时都会输出统一最终报告。报告不直接输出原始 JSON、traceback 或长命令日志，而是先给一句总状态，再分三段说明：产生的文件与完成情况、过程中遇到的问题与异常耗时、下一步建议。

总状态有四种：

- **已完成**：目标产物和关键校验都通过。
- **部分完成**：主要产物已保留，但存在跳过项、自动处理项或待确认事项。
- **需要你处理**：系统停在安全位置，需要作者裁决创作方向、事实取舍、文件覆盖或 blocking 问题。
- **未完成**：关键产物没有可信生成，需要按报告建议重跑或排查。

长流程执行中只显示少量过程提示，说明当前阶段和会产生什么。自动补跑投影、重新 emit 缺失合同这类幂等操作不会打断作者，但会出现在最终报告里。重复执行同一条主命令时，系统会优先检查可信断点；首版断点续跑重点覆盖 `/webnovel-write`，尽量从失败点继续，而不是重写已可信完成的正文、审查、提交或备份。

## Story System 主链

推荐按以下顺序执行：

1. 生成合同

```bash
python -X utf8 "<CLAUDE_PLUGIN_ROOT>/scripts/webnovel.py" --project-root "<PROJECT_ROOT>" story-system "玄幻退婚流" --chapter 12 --persist --emit-runtime-contracts --format both
```

2. 提交章节

```bash
python -X utf8 "<CLAUDE_PLUGIN_ROOT>/scripts/webnovel.py" --project-root "<PROJECT_ROOT>" chapter-commit \
  --chapter 12 \
  --review-result ".webnovel/tmp/review_results.json" \
  --fulfillment-result ".webnovel/tmp/fulfillment_result.json" \
  --disambiguation-result ".webnovel/tmp/disambiguation_result.json" \
  --extraction-result ".webnovel/tmp/extraction_result.json"
```

3. 检查主链健康

```bash
python -X utf8 "<CLAUDE_PLUGIN_ROOT>/scripts/webnovel.py" --project-root "<PROJECT_ROOT>" preflight --format json
```

其中 `.story-system/` 是主链真源，`.webnovel/*` 是投影/read-model。

### 常用工具子命令

| 子命令 | 说明 |
|--------|------|
| `where` | 打印当前解析出的项目根目录 |
| `preflight` | 校验 CLI 环境、脚本路径和项目根是否可用 |
| `project-status` | 输出机器可读短状态（phase、目标章节、下一步），不占用旧 `status` |
| `doctor` | 阶段感知项目体检（目录、文件、DB、RAG、依赖、Dashboard） |
| `scope-audit` | 范围级回扫（S1 Strand / S4 漂移 / S5 文体），只读，报告落 `.webnovel/reports/scope-audit.md` |
| `write-gate` | 写章自然边界校验（`prewrite` / `precommit` / `postcommit`） |
| `projections` | 从已有 commit 补跑或重放 projection |
| `user-report` | 渲染作者友好的最终报告，可输出 text/json |
| `run-ledger` | 记录写章步骤状态，或生成 `/webnovel-write` 断点续跑建议 |
| `run-log` | 写入脱敏运行日志 `.webnovel/logs/run_last.log` |
| `use <路径>` | 【应急】把工作区绑定到指定书项目；单书工作区通常不需要 |

示例：

```bash
python -X utf8 "<CLAUDE_PLUGIN_ROOT>/scripts/webnovel.py" --project-root "<PROJECT_ROOT>" user-report --stage write --chapter 12 --format text
python -X utf8 "<CLAUDE_PLUGIN_ROOT>/scripts/webnovel.py" --project-root "<PROJECT_ROOT>" run-ledger write-resume --chapter 12 --format text
python -X utf8 "<CLAUDE_PLUGIN_ROOT>/scripts/webnovel.py" --project-root "<PROJECT_ROOT>" run-log --event write_failed --payload-json "{\"chapter\":12,\"reason\":\"projection timeout\"}"
```

### 数据模块子命令

| 子命令 | 说明 |
|--------|------|
| `index` | 索引管理（`process-chapter`、`stats` 等） |
| `state` | 状态管理 |
| `rag` | RAG 向量索引（`index-chapter`、`stats` 等） |
| `entity` | 实体链接 |
| `context` | 上下文管理 |

### 运维子命令

| 子命令 | 说明 |
|--------|------|
| `status` | 宏观创作健康报告（`--focus all` / `--focus urgency`），仍转发到 `status_reporter.py` |
| `update-state` | 手动更新状态 |
| `backup` | 备份管理 |
| `archive` | 归档管理 |
| `extract-context` | 提取章节上下文（`--chapter N --format json`） |

### 长期记忆子命令

| 子命令 | 说明 |
|--------|------|
| `memory stats` | 查看总量、分类统计 |
| `memory query` | 按 category/subject/status 过滤查询 |
| `memory dump` | 导出完整 scratchpad 内容 |
| `memory conflicts` | 查看同主键 active 冲突项 |
| `memory bootstrap` | 从 index.db 与 summaries 回填初始长期记忆 |
| `memory update` | 对指定章节结果执行手动映射写入 |

示例：

```bash
python -X utf8 "<CLAUDE_PLUGIN_ROOT>/scripts/webnovel.py" --project-root "<PROJECT_ROOT>" memory stats
python -X utf8 "<CLAUDE_PLUGIN_ROOT>/scripts/webnovel.py" --project-root "<PROJECT_ROOT>" memory query --category character_state --subject xiaoyan
```

### Story System 子命令

| 子命令 | 说明 |
|--------|------|
| `story-system "<题材>" --persist` | 写入合同种子（`MASTER_SETTING.json` 等） |
| `story-system "<题材>" --emit-runtime-contracts --chapter N` | 生成运行时合同 + 写前校验 |
| `chapter-commit --chapter N` | 提交章节 commit（可附带 review/fulfillment/disambiguation/extraction 结果） |
| `chapter-commit --chapter N --expected-previous <identity> --allow-fact-revision --revision-reason "..."` | 作者确认后**改写已 accepted 的事实**：先撤回该章派生读模型再整章重建。不带 `--allow-fact-revision` 仍拒绝（`revision_projection_unsafe`） |
| `write-gate --chapter N --stage prewrite` | 写前检查项目阶段、Story System 合同和占位符 |
| `write-gate --chapter N --stage precommit` | 提交前检查正文和四类 commit artifacts |
| `write-gate --chapter N --stage postcommit` | 提交后检查 commit 与 projection 状态 |
| `projections retry --chapter N` | 基于已有 commit 补跑单章 projection（同时重建 `story_events` 镜像） |
| `projections retry --chapter N --retract` | 先撤回该章已有派生行（index / 向量分块 / `story_events` 镜像）再重放，用于修复半途写坏、被旧行挡住重建的投影 |
| `projections replay --from-chapter A --to-chapter B [--retract]` | 按章节范围重放 projection；`--retract` 让每章先撤回再重放 |
| `user-report --stage write --chapter N` | 汇总本次写章产物、问题和下一步建议 |
| `run-ledger record-write-step --chapter N` | 记录写章关键步骤的状态、输入输出、问题和耗时 |
| `run-ledger write-resume --chapter N` | 根据可信断点输出续跑建议，不自动覆盖文件 |
| `run-log --event <name>` | 写入脱敏日志，供不可恢复故障排查 |
| `story-events --chapter N` | 查询指定章节事件 |
| `story-events --health` | 事件链健康检查 |
| `memory-contract` | 记忆合同管理 |
| `review-pipeline --chapter N --review-results <file>` | 审查流水线 |

示例：

```bash
python -X utf8 "<CLAUDE_PLUGIN_ROOT>/scripts/webnovel.py" --project-root "<PROJECT_ROOT>" story-system "玄幻退婚流" --persist
python -X utf8 "<CLAUDE_PLUGIN_ROOT>/scripts/webnovel.py" --project-root "<PROJECT_ROOT>" chapter-commit --chapter 12 --review-result .webnovel/tmp/review.json
python -X utf8 "<CLAUDE_PLUGIN_ROOT>/scripts/webnovel.py" --project-root "<PROJECT_ROOT>" story-events --health
```

产物：

- `story-system --persist` → `.story-system/MASTER_SETTING.json`
- `--emit-runtime-contracts` → `volumes/*.json` 与 `reviews/*.review.json`
- `chapter-commit` → `commits/*.commit.json`
- `story-events` → 读取 `events/*.events.json` 或 `index.db.story_events`

退出码：

- `chapter-commit`：commit 已落盘但投影未跑完（任一 `projection_status` 为 `failed:`/`pending`，或事件镜像写入失败）时返回 **1**，并把失败项与 `projections retry --chapter N` 写到 stderr；提交事实本身仍然有效，不要因此回退正文或重跑 review。`rejected` commit 的 `index=skipped` 属正常，返回 0。
- `chapter-commit` 的**提交被拒**（例如 `chapter_artifacts_stale`、`revision_confirmation_required`、`revision_projection_unsafe`）同样是返回 1，但此时 commit 不会落盘 —— 用退出码之外的信息（commit 文件是否存在）区分这两种情况。`revision_projection_unsafe` 的报错自带当前 `commit_identity`：确认要改写已提交的事实，加上 `--expected-previous <identity> --allow-fact-revision` 重跑。
- `projections retry/replay`：`ok=false`（含事件镜像失败）时返回 1。
- `doctor`：存在 blocker（含 `index.commit_sync`）时返回 1。
