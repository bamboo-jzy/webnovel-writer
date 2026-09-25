# 项目结构与运维

## 目录层级

## 运维口径

- `.story-system/`：主链真源
- accepted `CHAPTER_COMMIT`：唯一写后事实入口
- `.webnovel/state.json`、`index.db`、`summaries/`、`memory_scratchpad.json`：投影/read-model
- `references/genre-profiles.md`：fallback-only
- `preflight` 与 dashboard 的 `story_runtime` / `story-runtime/health` 是第一观察点

系统涉及 4 层目录，使用前需要了解它们的区别：

| 层级 | 说明 | 示例 |
|------|------|------|
| `WORKSPACE_ROOT` | Claude Code 工作区根目录 | `D:\wk\novels` |
| `.claude/` | 工作区级配置与项目指针 | `D:\wk\novels\.claude\` |
| `PROJECT_ROOT` | 某本书的项目根目录（由 `/webnovel-init` 创建） | `D:\wk\novels\凡人资本论` |
| `CLAUDE_PLUGIN_ROOT` | 插件缓存目录（不在项目内，由 Marketplace 安装管理） | 自动管理 |

### 工作区目录

**单书工作区规约：一个工作区只放一本书。** 合法布局只有两种：

```text
# 布局 1（推荐）：书目录就是工作区根
workspace-root/
├── .claude/
│   └── settings.json
├── .webnovel/state.json      # 这本书的项目根
├── 正文/
└── 大纲/

# 布局 2：工作区下恰好一个书目录
workspace-root/
├── .claude/
│   └── settings.json
└── 凡人资本论/                # PROJECT_ROOT（唯一子书）
    └── .webnovel/state.json
```

定位规则：给定工作区根时，依次尝试 ①该目录本身就是书项目 ②工作区内的唯一子书；
工作区里出现 **≥2 本书** 属于违规布局，命令会直接报错并列出书单，
不会误报成"这里不是项目"：

```text
本插件按「一个工作区一本书」使用，但当前工作区里检测到多本书，无法判定该用哪一本。
工作区: D:\wk\novels
检测到 2 本书：
  - 凡人资本论  (D:\wk\novels\凡人资本论)
  - 剑走偏锋  (D:\wk\novels\剑走偏锋)
```

`/webnovel-init` 默认把新书建在 `<workspace>/<书名>/`。若该工作区已有其它书，
init 会打印单书规约警告（不阻断），提醒你把各本书分到各自的工作区。

### 书项目目录（PROJECT_ROOT）

```text
project-root/
├── .webnovel/            # 运行时数据
│   ├── state.json        # 项目状态
│   ├── index.db          # SQLite 索引（实体/关系/章节数据）
│   ├── vectors.db        # 向量索引
│   ├── projection_log.jsonl # 投影执行日志
│   ├── summaries/        # 章节摘要
│   ├── backups/          # 自动备份
│   └── archive/          # 归档
├── .story-system/        # Story System 数据
│   ├── MASTER_SETTING.json
│   ├── chapters/
│   ├── volumes/
│   ├── reviews/
│   ├── commits/
│   └── events/
├── 正文/                  # 正文章节
├── 大纲/                  # 总纲与卷纲
├── 设定集/                # 世界观、角色、力量体系
├── 文风/                  # 文风参考文本 + 文风档案.md（/webnovel-style-learn）
└── 审查报告/              # 审查输出
```

### 插件目录

插件安装在 Claude 插件缓存目录，不在书项目内。运行时通过 `CLAUDE_PLUGIN_ROOT` 引用：

```text
${CLAUDE_PLUGIN_ROOT}/
├── skills/       # 17 个 Skill 命令定义
├── agents/       # 4 个 Agent 定义
├── scripts/      # Python 脚本与数据模块
├── hooks/        # Claude Code 会话钩子
├── references/   # 参考文档（题材画像、追读力分类法等）
├── templates/    # 初始化模板
├── genres/       # 精调题材配置
└── dashboard/    # 可视化面板前端
```

### 工作区指针与用户级 registry（兜底）

单书规约下，这两种记录**只是兜底**，不构成"多书切换"能力：

- 工作区指针 `<workspace>/.claude/.webnovel-current-project`：由 `/webnovel-init` 写入，
  内容是一行绝对路径。
- 用户级 registry：`${CLAUDE_HOME:-~/.claude}/webnovel-writer/workspaces.json`，
  用于工作区指针不可用（全局安装的 skills、子代理/hook 在空上下文中调用）时的兜底。

两者都只记录"该工作区当前解析到哪本书"。指针失效不会造成误导：解析失败会说明具体原因
（书不在、还是工作区里有多本）。只有在指针丢失等异常情况下，才需要用 `webnovel use "<书目录>"`
手工重绑——这是应急命令，日常写作不需要。

## 常用运维命令

### 环境预检

```bash
python -X utf8 "${CLAUDE_PLUGIN_ROOT}/scripts/webnovel.py" --project-root "${WORKSPACE_ROOT}" preflight
python -X utf8 "${CLAUDE_PLUGIN_ROOT}/scripts/webnovel.py" --project-root "${WORKSPACE_ROOT}" project-status --format summary
python -X utf8 "${CLAUDE_PLUGIN_ROOT}/scripts/webnovel.py" --project-root "${WORKSPACE_ROOT}" doctor --format text
```

`preflight` 是快速检查，`project-status` 给短状态和下一步，`doctor` 是阶段感知体检。

检查项：插件脚本路径 / 项目根是否可解析 / Skill 目录是否存在 / 阶段应有文件 / JSON / SQLite / RAG 配置 / Python 依赖 / Dashboard 产物。

若 `story_runtime.mainline_ready=false`，说明当前项目仍在 legacy fallback 或 commit 主链不完整。

### 写章关卡

```bash
python -X utf8 "${CLAUDE_PLUGIN_ROOT}/scripts/webnovel.py" --project-root "${PROJECT_ROOT}" write-gate --chapter 12 --stage prewrite --format text
python -X utf8 "${CLAUDE_PLUGIN_ROOT}/scripts/webnovel.py" --project-root "${PROJECT_ROOT}" write-gate --chapter 12 --stage precommit --format text
python -X utf8 "${CLAUDE_PLUGIN_ROOT}/scripts/webnovel.py" --project-root "${PROJECT_ROOT}" write-gate --chapter 12 --stage postcommit --format text
```

- `prewrite`：检查项目阶段、runtime contract、占位符和写前必要文件。
- `precommit`：检查正文和 review / fulfillment / disambiguation / extraction 四类提交产物。
- `postcommit`：检查 commit 和 projection 状态。

### 索引重建

```bash
python "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" index process-chapter --chapter 1
python "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" index stats
```

`index process-chapter` 是旧入口，不经过 commit / projection，schema 校验失败也返回 0，**不能用来重建索引**。
重建索引的正确手段是下面的 `projections replay`（从 commit 重算）或 `projections retry`（单章）。

### 索引对账（doctor）

`doctor` 除检查 `index.db` 是否存在、表是否可读外，还会与 commit / 事件文件对账（2026-09-17 新增）：

| check | 判据 | 级别 |
|---|---|---|
| `index.commit_sync` | accepted 且索引投影为 `done` 的章节，`chapters` 表必须有对应行；`index.db` 不可读时同样报错 | **blocker** |
| `index.orphan_rows` | `chapters` 行存在但没有 accepted commit（如 rejected commit 未清理旧行） | warning |
| `index.chapter_metadata` | `title` 为空或 `word_count<=0`（多为正文文件名不补零 / 正文缺失） | warning |
| `index.story_events_sync` | 有事件文件的 accepted 章在 `story_events` 表里没有行 | warning |

漏章、整库被重建、只读/损坏的库都属 `index.commit_sync`。修复顺序：确认 `index.db` 是否被误删或未随项目同步；文件已损坏无法读取时先把它移出项目（改名即可，别直接删除），再 `projections replay`。命令由 doctor 的 `repair` 字段按实际章节号生成，可直接复制。

### 健康报告

```bash
python "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" status -- --focus all
python "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" status -- --focus urgency
```

`status` 保留宏观创作健康报告语义；需要机器可读短状态时使用 `project-status`。

### 向量重建

```bash
python "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" rag index-chapter --chapter 1
python "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" rag stats
```

### 投影补跑

```bash
python -X utf8 "${CLAUDE_PLUGIN_ROOT}/scripts/webnovel.py" --project-root "${PROJECT_ROOT}" projections retry --chapter 12 --format text
python -X utf8 "${CLAUDE_PLUGIN_ROOT}/scripts/webnovel.py" --project-root "${PROJECT_ROOT}" projections replay --from-chapter 1 --to-chapter 12 --format text
```

投影补跑只从已有 `.story-system/commits/*.commit.json` 读取事实，并重新生成 `.webnovel/state.json`、`index.db`、`summaries/`、`memory_scratchpad.json`、`vectors.db` 等 read-model。每次执行会追加 `.webnovel/projection_log.jsonl`（撤回信息记在同一行的 `retractions` 字段，不混进 `writers`）。

两条边界（2026-09-17 明确）：

- **会重建 `index.db` 的 `story_events` 镜像**（此前只有 `chapter-commit` 写它，所以镜像一旦写失败就补不回来）。镜像重建是幂等的（`event_id` 唯一 + `INSERT OR IGNORE`）。
- **不产生 commit 侧副作用**：不改写 `.story-system/events/*.events.json`、正文和 commit。事件 JSON 属于提交事实，归 `chapter-commit` 写。
- `index.db` 已损坏时，`retry/replay` 会报 `file is not a database`；先把损坏的库移出项目（改名），再 replay 即可整库重建。

#### 撤回重放（2026-09-17 新增）

投影写入器并非全部可重入：`state_changes` 是纯追加表、`relationships` 只 upsert 最新章、`story_events` 镜像是 `INSERT OR IGNORE`、向量分块 ID 由内容哈希决定。所以"上一版事实"留下的行撤不掉——这正是 `persist_commit` 默认拒绝改写已 accepted 事实的理由。现在有两条显式通道：

```bash
# 1) 作者确认要改写已 accepted 的事实（先撤回该章读模型，再整章重建）
python -X utf8 "${CLAUDE_PLUGIN_ROOT}/scripts/webnovel.py" --project-root "${PROJECT_ROOT}" chapter-commit \
  --chapter 12 --expected-previous "<identity>" \
  --allow-fact-revision --revision-reason "作者为什么改写" \
  --review-result ... --fulfillment-result ... --disambiguation-result ... --extraction-result ...

# 2) 投影半途写坏、被旧行挡住重建时，强制撤回后重放
python -X utf8 "${CLAUDE_PLUGIN_ROOT}/scripts/webnovel.py" --project-root "${PROJECT_ROOT}" projections retry --chapter 12 --retract
```

撤回范围：`index.db` 的 chapters / scenes / appearances / state_changes / relationships、`vectors.db` 的 vectors / bm25_index / doc_stats、`story_events` 镜像。不碰 `entities` / `aliases`（跨章累积），也不碰正文、commit、事件 JSON，因此可安全重复执行。

默认仍然拒绝事实改写：不带 `--allow-fact-revision` 时 `revision_projection_unsafe` 的报错会带上当前 commit identity 与开关名，便于作者复制后决定。改写后的 commit 会一直带 `provenance.retract_required`，所以它后续每次 `projections retry` 都是"先撤回再重建"。

日志读写使用跨进程文件锁，追加完整 JSONL 行并 flush/fsync。坏 JSON、截断行或非法结构会显式报告 `projection_log.corrupt`，不得跳过坏行、回退到旧的成功记录；doctor、postcommit、retry 和续跑会阻断。先保留损坏文件并核对可信备份，再恢复或人工修复；不要删除日志来制造“已完成”状态。

`index.db` 的连接使用 30s busy timeout（不再用默认 5s）。多 agent / 多终端并行写同一本书时，短暂锁竞争会自动等待而不是立刻失败；若锁被长期持有，命令约 40s 后仍会失败并进入 `projections retry` 路径。

### 作者友好报告与恢复

主 Skill 的最终报告统一使用四种总状态：已完成、部分完成、需要你处理、未完成。报告只给作者需要知道的结论、产物、问题和下一步建议；内部 JSON、traceback 和长命令日志不直接展示。

异常分三类处理：

- **已自动处理**：幂等、可重试、不碰作者内容的问题，例如 projection retry 成功、缺失 runtime contract 后重新生成。流程默认继续，但最终报告必须说明处理过什么。
- **需要确认**：会影响创作方向、事实取舍、是否覆盖文件或断点续跑边界的问题，例如正文被手动改过、章纲更新晚于正文、本章已 accepted 后再次写章。系统应给 2-3 个有限选项。
- **必须处理**：blocking 审查问题、关键产物缺失、commit 被拒、投影补跑仍失败等。系统停在安全位置，报告说明已完成内容、卡点和恢复建议。

`/webnovel-write` 会记录写章断点，用于重跑时判断可信完成项：

```bash
python -X utf8 "${CLAUDE_PLUGIN_ROOT}/scripts/webnovel.py" --project-root "${PROJECT_ROOT}" run-ledger write-resume --chapter 12 --format text
```

断点建议只负责判断和提示，不自动覆盖文件。凡是涉及作者手改正文、旧正文是否沿用、accepted commit 是否重做，都必须先询问。

`.webnovel/run_ledger.json` 的读改写全程持锁并通过原子替换落盘。文件不存在才初始化；JSON 或结构损坏时，doctor 报告 `run_ledger.corrupt`，`write-resume` 返回 `blocked`，不会清空账本重跑。锁依赖不可用或写入失败同样不能算成功。

不可恢复故障会提示查看：

```text
.webnovel/logs/run_last.log
```

该日志用于保留最近一次运行的脱敏技术细节，便于排查。写入日志时会遮蔽常见敏感字段和值，包括 `api_key`、`secret`、`token`、`authorization`、`password`、`passwd`、`credential` 以及形如 `KEY=value` 的内联密钥片段。日志仍可能包含文件路径和错误上下文，提交 issue 前建议再人工扫一眼。

### 测试

在仓库根目录运行：

```bash
python -X utf8 -m pytest --no-cov
pwsh webnovel-writer/scripts/run_tests.ps1 -Mode smoke
pwsh webnovel-writer/scripts/run_tests.ps1 -Mode full
python -X utf8 webnovel-writer/scripts/run_behavior_evals.py --format text
python -X utf8 webnovel-writer/scripts/validate_plugin_package.py --format text
```

CI 覆盖 Ubuntu 与 Windows 的 Python 3.12/3.13/3.14（**最低支持版本为 3.12**）；Windows 脚本执行 UTF-8 与临时目录预检。行为评测与插件包校验在独立的 `quality` job 上运行（Ubuntu + 3.12），不依赖测试矩阵的成败。完整 pytest 包含中文带空格路径、多进程账本/日志写入、原子替换失败、真实 accepted commit→projection→backup/resume 和损坏阻断 fixture，不调用真实 LLM 或网络 API。

`run_behavior_evals.py` 是快速行为契约检查，不等同于端到端测试；`validate_plugin_package.py` 按 plugin-dev 思路检查 manifest、Skill / Agent frontmatter、hooks wrapper、README 版本和路径可移植性。

### Hook 开关

插件级 hook 默认很轻：

- `SessionStart`：只运行 `project-status --format summary`，不写文件、不启动服务。
- `PreToolUse`：对直接写主链 / read-model 文件和绕过 runtime 的危险命令做兜底阻断。

需要临时关闭时设置环境变量：

```bash
WEBNOVEL_DISABLE_SESSION_STATUS_HOOK=1
WEBNOVEL_DISABLE_RUNTIME_GUARD_HOOK=1
```

## Story System 运维

### 健康检查

```bash
python -X utf8 "${CLAUDE_PLUGIN_ROOT}/scripts/webnovel.py" --project-root "${PROJECT_ROOT}" story-events --health
```

返回字段：`sqlite_rows` / `event_files` / `ok`

重点关注：

- `.story-system/commits/chapter_XXX.commit.json` 是否存在且为 accepted
- `projection_status` 是否全部为 `done` / `skipped`
- `.story-system/events/` 是否可读
- `index.db` 中 `story_events` 表是否可查
- `override_contracts` 是否能统计 `amend_proposal`

### 备份

本地快照和 Git 备份的故事范围统一为：

```text
正文/
大纲/
设定集/
文风/
.story-system/
.webnovel/state.json
.webnovel/index.db
.webnovel/vectors.db
.webnovel/project_memory.json
.webnovel/memory_scratchpad.json
.webnovel/projection_log.jsonl
.webnovel/style_profile.json
.webnovel/summaries/
```

本地 `snapshot_chNNNN_*` 带 `snapshot/v1` manifest，每个文件记录相对路径、大小和 SHA-256。快照不包含 `.env` / secrets、cache、tmp、`.git`、插件目录、备份目录、符号链接文件或 `.pyc`。

Git 和本地备份成功后统一写入 `.webnovel/backup_receipts.json`（`backup-receipt/v1`）：Git 记录 `chNNNN` tag 与对应 commit；snapshot 记录路径与 manifest SHA-256。续跑会验证 tag/manifest、当前正文和章节提交的内容，而不是看到目录就算完成；tag 或 receipt 写入失败必须报告失败。无 receipt 的旧项目仍可验证现有 tag 或带 manifest 的 snapshot，损坏 receipt 不会被当作缺失后静默回退。

receipt 是本地可重建的运行证据，不作为故事事实、不递归打包进自身备份。回退后仍重新验证当前内容；不要用旧 receipt 替代正文或 commit 的核对。

### 恢复到历史版本

恢复使用 Git 原生命令，插件只负责建立版本点，不再提供恢复子命令：

```bash
git log --oneline ch0030..HEAD              # 回退点之后有哪些提交
git diff --stat ch0030 HEAD                 # 会改哪些文件
git switch -c rewrite-from-ch0030 ch0030    # 工作树整体回到 ch0030 状态，另开分支，历史不动
```

用 `git switch -c <分支> <tag>` 而不是 `git checkout <tag> -- 正文 大纲 设定集 .story-system`：带 pathspec 的 `git checkout` 在任一目录不存在时整体报错、什么都不恢复（实测），也不会删除回退点之后新增的文件；`git switch` 让工作树状态与 tag 完全一致，原分支与历史完整保留，放弃时 `git switch main` 即可。

约束：

- 动手前 `git status --short` 必须为空；有未提交改动时 `git switch` 会拒绝执行，先提交或撤销
- 回退点之后仍存在 `chNNNN` tag，但 `chNNNN` 的语义是"该章最新已备份状态"，不是不可变历史点。重写同一章号时 `backup` 先归档旧点（`chNNNN-prev-<时间戳>`）再把 tag 前移到新提交，因此不会失败、也不需要手工删除 tag；要取回某个被前移的状态用 `git switch -c <分支> chNNNN-prev-<时间戳>`
- `chNNNN-prev-<时间戳>` 只增不改，是真正不可变的历史点；`backup --list` 把它们列在对应章节版本点下方
- 回退后重新运行 doctor 的 Story System / projection health 检查；正文与 `.webnovel/state.json` 不一致时不得继续续写

### 抛弃刚写完的一章

`/webnovel-chapter-discard` 按章的状态自动选路，两条路都**只允许处理最后一章**（`downstream_chapters_exist` 直接阻断）：

| 章的状态 | 路径 | 结果 |
|---|---|---|
| 尚未 accepted（只有正文、草稿项或 rejected commit） | `--draft` | 正文、本章 artifacts、审查报告先整批归档到 `.webnovel/discarded/chapter_NNN_<时间戳>/`（保持项目内相对路径，可原样复制回去），再删除；`state.json` 的章级条目清理并重算 `current_chapter` / `total_words`；`大纲/` 不动 |
| 已 accepted | `--rollback` | 原地版本点回退：正文与本章 commit 先归档，再把工作树与索引恢复成第 N-1 章完成时的内容，并在**当前分支**追加一次抛弃提交；不新建分支、不删除任何提交 |

```bash
python -X utf8 "${CLAUDE_PLUGIN_ROOT}/scripts/webnovel.py" --project-root "${PROJECT_ROOT}" \
  chapter-discard --chapter 10 --dry-run --format json      # 先预览分类与阻塞项
python -X utf8 "${CLAUDE_PLUGIN_ROOT}/scripts/webnovel.py" --project-root "${PROJECT_ROOT}" \
  chapter-discard --chapter 10 --draft --reason "写崩了" --format json
python -X utf8 "${CLAUDE_PLUGIN_ROOT}/scripts/webnovel.py" --project-root "${PROJECT_ROOT}" \
  chapter-discard --chapter 10 --rollback --reason "整章作废" --format json
```

回退这一路的手工等价命令如下，**章号要往前退一位**，且**不新建分支**：

```bash
cd "${PROJECT_ROOT}"
git status --short                        # 必须为空
git log --oneline -3                      # 确认最新提交是 "Chapter N"
git show ch0010:"正文/第10章-*.md" > /tmp/ch10.bak.md   # 想留一份就先导出
git read-tree -u --reset ch0009           # 抛弃第 10 章：回到 ch0009，不是 ch0010
git commit -m "Discard chapter 10: restore to ch0009"   # 在当前分支追加一次提交
```

`git read-tree -u --reset` 只改工作树与索引（未跟踪文件保留），不移动分支指针、不新建分支、不删除提交：被抛弃的提交会成为新提交的父提交，`ch0010` tag 也仍指向它。插件走的是同一条路，只是把归档、提交和核对都代劳了。

### 方向透传裁决记录

四层链路（总纲 → 卷纲 → 章纲 → 正文）的方向检查由 `handoff` 子命令承载。裁决记录写在 `.story-system/handoffs/<decision_id>.json`，**只增不改**，不改任何创作文件：

```bash
python -X utf8 "${CLAUDE_PLUGIN_ROOT}/scripts/webnovel.py" --project-root "${PROJECT_ROOT}" \
  handoff --node master_to_volume --target 1 --check --format json
python -X utf8 "${CLAUDE_PLUGIN_ROOT}/scripts/webnovel.py" --project-root "${PROJECT_ROOT}" \
  handoff --node master_to_volume --target 1 --record --decision align_downstream \
  --reason "以总纲为准修正卷纲章节范围" --impact 1 --expected-input "{input_token}" --format json
```

| 项 | 说明 |
|---|---|
| 节点 | `master_to_volume`（总纲→卷纲）/ `volume_to_chapter`（卷纲→章纲）/ `chapter_to_body`（章纲→正文） |
| 可改单元 | 每层只有「最后一个单位」；其余是既定事实 |
| 裁决值 | `align_downstream` / `align_upstream` / `accepted_deviation`（章级旧值 `outline_to_body`、`body_to_outline` 自动归一） |
| `input_token` | 绑定当时的边界快照与上游来源；上游一改即失效，须重新 `--dry-run` 预览 |
| 错误码 | `handoff_target_locked`（目标不在可改单元内）/ `handoff_confirmation_required`（token 不匹配） |

与 `.story-system/reconciliations/` 的分工：`reconciliations` 是**章内**履约对账（哪个 CBN/CPN 节点没写到位），由 `chapter-reload --reconcile` 写入；`handoffs` 是**层间**方向裁决（章纲与正文整体是否同向，或卷纲与总纲、章纲与卷纲）。两者互补，不互相替代。

章-正节点还有代码级末端约束：`chapter-reload` 的重载与裁决入口在目标章不是最后一章时以 `handoff_target_locked` 阻断，`--backup-only` 豁免；尚无正文的章属首次写作，不受该边界约束。定向透视可跑 `handoff --node chapter_to_body`（不给 `--target`）看当前可改单元与既定事实清单。

`chNNNN` 的语义是"第 N 章**完成后**已备份的状态"（`backup --chapter N` 在 `/webnovel-write` Step 6 执行），所以抛弃第 N 章要回到 `ch{N-1}`。回到 `chNNNN` 只会把这一章原样留着——这是最容易踩的一步。

原地回退之后工作树逐项回到第 N-1 章完成时：

| 内容 | 结果 |
|---|---|
| `正文/第N章*.md` | 删除 |
| `.story-system/commits/chapter_00N.commit.json` | 删除 |
| `.webnovel/state.json`（含 `chapter_revisions`） | 回到第 N-1 章 |
| `.webnovel/index.db`（chapters / scenes / appearances / state_changes / relationships） | 回到第 N-1 章 |
| `.webnovel/summaries/`、`projection_log.jsonl` | 回到第 N-1 章 |
| 被抛弃的提交、`chNNNN` tag、当前分支 | **全部保留**：提交仍是新提交的父提交，分支名不变 |
| `.webnovel/discarded/chapter_NNN_<时间戳>/` | 新增归档副本（未跟踪，不随回退消失） |

`大纲/`、`设定集/` 也在版本点内：如果写这一章时顺手改过章纲，那些改动同样会被撤掉。想看这次回退究竟丢掉了什么：

```bash
git diff --stat HEAD ch0010              # 被抛弃的改动清单
git show ch0010:"正文/第10章-*.md"        # 取回被抛弃的正文
```

不在版本点内、回退后会残留的本地状态（都不影响续写，但要知道它们旧了）：`.webnovel/run_ledger.json`、`.webnovel/backup_receipts.json`、`.webnovel/logs/`、`.webnovel/reports/`、`.webnovel/tmp/`。实测正文文件消失后 `run-ledger write-resume --chapter N` 回到 `resume_from=draft`、六个步骤全是 `run`，不会误报"本章已完成"。断点判定以文件签名为准，不依赖账本里的完成标记。

接着按正常流程重写即可（`/webnovel-write N`）。重写完成后再 `backup --chapter N`，旧 `chNNNN` 自动归档为 `chNNNN-prev-<时间戳>`、tag 前移到新提交；被抛弃的正文可用 `git show chNNNN-prev-<时间戳>:"正文/第N章*.md"` 取回。

两个边界：

- **第 1 章**没有 `ch0000`。`chapter-discard --rollback` 会自动改为回退到仓库初始提交（`git rev-list --max-parents=0 HEAD`）；手工做时用 `git log --oneline` 找到第一个提交，再 `git read-tree -u --reset <sha> && git commit -m "Discard chapter 1: restore to <sha 短号>"`。初始提交不唯一时会阻断，转人工。
- **这一章还没有版本点**（写章中途失败、没跑到 Step 6，只留下 commit 和索引行）。先补一个：`backup --chapter N`，再把上面流程走一遍。不要手工删文件了事——`projections retry --chapter N --retract` 靠 commit json 定位章号，commit json 一删，`index.db` / `vectors.db` / `story_events` 里那一章的派生行就再没有干净的清理入口。

无 Git 环境下列出的 `snapshot_chNNNN_*` 是离线副本（带 `snapshot/v1` manifest 与 SHA-256，只保留最近 10 份），没有配套恢复命令，需要手工复制文件；这个模式下恢复能力由作者自行保证。
