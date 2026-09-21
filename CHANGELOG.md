# 更新日志

这里记录每个正式版本对作者和维护者的影响。发布说明优先面向中文网文作者：先说写作体验有什么变化，再补维护者关心的技术细节。

## v6.3.0 - 文风有档案，漏章藏不住，已定稿的事实也能改

发版范围：`v6.2.1..v6.3.0`。

### 给作者看的变化

- **一个工作区一本书**：一个工作区只放一本书，书目录可以直接就是工作区根，也可以是工作区下唯一的书目录。工作区里放了两本书时，命令会明确告诉你"检测到 2 本书、无法判定"，并列出书单和处理方式，不再含糊地说"这里不是项目"。
- 往已有书的工作区里再初始化一本新书时，会收到醒目提示：该工作区已有一本书、建议新书另建工作区；同时告知工作区指针已改绑到新书（以前这一步没有任何提示）。
- 章节改完之后如果重新备份，恢复点能正确定位到当前版本（原先"改正文/改章纲后重跑备份"会直接失败）。
- 恢复到历史版本改用 git 原生命令：`git switch -c rewrite-from-chXXXX chXXXX`。插件不再提供内置回退命令，也不再写"回退到某个章节快照"的文档承诺。
- 老项目升级/迁移入口移除：本系统面向新建项目，不再提供从 v5 老数据结构搬运数据的命令。
- **索引漏章现在会被发现**：`/webnovel-doctor` 会拿索引库和章节提交对账。如果 `.webnovel/index.db` 被清理脚本删过、被覆盖成空库，或某一章在索引里消失（以前**一律报"正常"**），现在会明确报出缺了哪几章，并给出可直接复制的重建命令（`projections replay`）。
- **提交成功但索引没写成功时不再"看起来成功"**：写章提交现在会在索引写入失败（例如库只读）时返回失败，并告诉你是哪一项失败、怎么补跑；正文与提交事实都还在，不需要重写。
- 索引库被别的程序占用（比如同时开了两个终端或 dashboard）时不再几秒就失败：默认等 30 秒，短暂占用会自动等过去。
- 以 `第7章-标题.md` 这种不补零的文件名保存正文时，体检会提示改名（只提示，不阻断写章）。
- **已提交章节的事实也能改了（需作者确认）**：以前某章一旦提交，再改写事实会被安全拦下（提示"增量投影撤不掉旧事实"），只能绕过系统。现在确认后用 `chapter-commit --allow-fact-revision --revision-reason "..."` 即可：系统先撤回该章的派生数据（索引行、向量分块、事件镜像）再整章重建，旧版本自动进历史。**默认仍然拦截**，不会误改；拦截时会直接把当前版本标识和该加的开关一起打给你。
- 投影被旧数据挡住、半途写坏时，补跑入口升级为 `projections retry --chapter N --retract`（先撤回该章派生数据再重放）。
- 同一章里场景序号重复（例如两个场景都写 1）不再让该章索引投影整章失败，系统会顺延序号并在日志里说明。
- embedding 服务限制单请求条数时，向量化会自动缩小批次重试（不再"整章 0 条写入"），单批条数可用 `EMBED_BATCH_SIZE` 调整。
- **新增文风档案**：`/webnovel-style-learn` 把"文风"变成一份可复用的档案——从 `正文/` 学你**已经写成什么样**（只统计已提交接收的章节），从 `文风/` 学你**想写成什么样**（把要模仿的参考文本放进去即可）。档案给出现状、目标、两者差异和硬约束检查（长句占比、said tag 占比），并自动带一份摘要进写章任务书。现状与目标永远分开：现状不会自动变成"标准"，两者冲突时以目标为准。
- `文风/` 目录已随版本点一起备份，参考文本不会丢；建议只放要模仿的片段，不要放整本书。
- **文体体检的参照物变成"这本书既定文风"**：`/webnovel-audit` 的 S5（句长/对话占比漂移）以前是拿"这次扫的那一段"自己当中位数，只扫一卷时基线就是那一卷——同一章换个范围扫描，结论会不一样，读者也没法跨卷比。现在默认改用文风档案里的全书画像当基线，**跨卷可比**；报告会写明这次用的是哪种基线、来自多少章、档案生成于何时，档案比正文旧时会提示重建。想恢复旧口径用 `--baseline range`。
- **删掉了没人用的"风格采样"模块**（`webnovel.py style`）。它的存储一直是 0 条、`skills/` 里零调用，能力已由文风档案承担；留着两套实现只会误导。`webnovel style ...` 现在会直接报参数错误。
- dashboard 的「文档浏览」现在也能看 `文风/`（只读）。

### 是否需要改旧项目

不需要。已有书项目继续使用，无需迁移 `.story-system/` 或 `.webnovel/` 数据。
`文风/` 是新增的可选目录：老项目不建也不影响任何命令，首次跑 `/webnovel-style-learn build` 时会自动创建；`init` 新建的项目会预建该目录。
注意两点行为变化：①一个工作区里放多本书不再被支持，请把每本书放在自己的工作区；②原 `migrate` 子命令已移除。
升级后第一次 `/webnovel-doctor` 若报索引缺章，说明这个漏章**在升级前就已存在**（只是当时没有任何检查能发现），按提示的 `projections replay` 修一次即可。

### 给维护者

- `project_locator.py`：新增 `WorkspaceHasMultipleBooksError`（`FileNotFoundError` 子类，带 `workspace_root`/`books`）、`find_child_project_roots()`、`read_current_project_pointer()`、`detect_workspace_single_book_conflicts()`；`resolve_project_root()` 在解析失败且工作区子书 ≥2 时抛歧义异常；指针读取抽为 `_read_pointer_file()` 复用。
- `webnovel.py`：新增 `_multiple_books_diagnostic()`；`use` 降为应急命令并在执行前回显被替换的原绑定。
- `init_project.py`：新增 `_warn_if_workspace_has_multiple_books()`（非阻断）。
- `backup_manager.py`：章节版本点 tag 改为可前移的分层命名（`chNNNN` 当前点 + `chNNNN-prev-<时间戳>` 归档旧点），修掉 tag 冲突导致的重跑失败；`list_backups` 改为按 `^ch\d{4}$` 解析。
- 移除内置快照恢复（`rollback` / `preview_rollback` / `restore_snapshot` 及辅助代码），备份侧只保留"建立版本点"。
- 移除老项目迁移模块（`data_modules/migrate_state_to_sqlite.py` 及其测试、`webnovel.py` 的 `migrate` 转发）。
- `doctor.py`：新增 `_index_reconciliation_checks()` —— `index.commit_sync`（accepted+index done 却缺行 / 库不可读 → blocker）、`index.orphan_rows`、`index.chapter_metadata`、`index.story_events_sync`（warning）。
- `chapter_commit.py`：投影含 `failed:`/`pending` 或 `provenance.event_mirror_error` 非空 → stderr 摘要 + `SystemExit(1)`；stdout 仍打印 commit JSON；`rejected`（全 skipped）不触发。
- `event_log_store.py`：镜像写入异常降级为 `last_mirror_error` + `.webnovel/logs/event_mirror_errors.log`；新增 `mirror_events_only()`（retry 用，只写 sqlite、不产生 commit 侧副作用）；连接加 `timeout`.
- `chapter_commit_service.py` / `projections.py`：`_sync_event_mirror()` + `rebuild_event_mirror()`，使 `retry/replay` 能重建 `story_events` 镜像（旧实现只有 `chapter-commit` 会写它，镜像失败后无法补救）。
- `project_phase.py`：`_scan_commits` → 公开 `scan_commits()`，日志读取单遍化（去掉逐章 O(N²) 读 `projection_log`）。
- `config.py`：新增 `SQLITE_BUSY_TIMEOUT_SECONDS = 30`；`index_manager` / `event_log_store` 统一使用。**未开 WAL**（`-wal/-shm` 不在备份受管名单）。
- `commit_artifacts.py`：新增 `resolve_scene_index()` —— **键存在即取（含 `0`）**，仅缺失/不可解析才回退 enumerate 序号；`index_projection_writer.py` / `vector_projection_writer.py` 共用，去掉 `A or B or idx` 的 falsy-0 隐患。index writer 另加重复序号顺延兜底（stderr 留痕）。`agents/data-agent.md` 声明 `index` 为 **1 基**、不得重复。
- `config.py` / `api_client.py`：新增 `_env_int()`，`EMBED_BATCH_SIZE` / `EMBED_CONCURRENCY` / `RERANK_CONCURRENCY` 可被环境变量覆盖（缺失/非法回退默认）；`embed_batch` 批次被拒时折半重试（`_embed_batch_shrinking()`，深度 ≤8），`skip_failures=False` 语义不变。
- **改写已 accepted 事实的显式通道**：`persist_commit(..., allow_fact_revision=False, revision_reason="")` 默认行为不变（拒绝时错误信息带当前 `commit_identity` 与开关名）；新增 `ChapterCommitService.retract_chapter_read_models(payload, force=False)`、`IndexProjectionWriter.retract()`（chapters/scenes/appearances/state_changes/relationships）、`VectorProjectionWriter.retract()`（vectors/bm25_index/doc_stats）、`EventLogStore.retract_chapter()`（story_events 镜像）。撤回必须发生在事件镜像重建**之前**（镜像自身也是被撤回对象），所以 `apply_projections` 与 `projections.retry_projection` 都在镜像步骤前调用。撤回结果写 `projection_log` 的独立 `retractions` 字段——**不进 `writers`**（postcommit gate / `project_phase` 会把 `writers` 的键当必需投影项校验），**也不进 commit**（`commit_identity` 含 `provenance`，投影阶段改它会撞"previous projections are incomplete"）。`entities`/`aliases` 刻意不撤回（跨章累积），代价是 `first/last_appearance` 可能停在别的章。
- CLI：`chapter-commit --allow-fact-revision / --revision-reason`；`projections retry|replay --retract`；`chapter_commit.py` 把 `ValueError` 转成作者可读报错 + `SystemExit(1)`（不再吐 traceback）。
- 文档同步：`operations.md` 工作区目录/兜底说明 + 新增「索引对账（doctor）」+「撤回重放」，`guides/commands.md` 退出码小节，`skills/webnovel-write/SKILL.md` 5.3/5.4，`skills/webnovel-doctor/SKILL.md`，`skills/webnovel-chapter-reload/SKILL.md`（事实改写通道），`README.md`，`docs/operations/*-2026-09-17.md`（评估、修复方案与实施记录）。
- **文风档案**：新增 `scripts/style_profile.py`（CLI 子命令 `style-profile build|show|diff`）与 `skills/webnovel-style-learn/SKILL.md`；文体计量原语抽到 `data_modules/style_metrics.py`，`scope_audit` 的 S5 改为 import 该模块（切句 / CJK 计数 / 极大重复过滤三处逻辑只保留一份，S5 输出逐字段不变）。
- `style_profile.py` 口径：源 A（`正文/`）默认只取 `scan_commits()` 里 `status=accepted` 的章，未提交/被拒稿需 `--source all` 显式越权（选择会写进档案「口径提示」）；`observed` / `target` 分开存放、分开注入，冲突以 target 为准；源 B（`文风/`）只存统计特征 + 每文件 ≤120 字机械样本，不复制参考书正文；样本 <3 章不给结论。
- `style_profile.py` 产物：`.webnovel/style_profile.json`（含 `injection_digest` ≤1500 字）+ `文风/文风档案.md`（脚本段以 `<!-- STYLE-PROFILE:BEGIN/END -->` 分隔，**段外归纳段 build 不覆盖**；无标记的旧档整份按人工内容保留）。
- `memory_contract_adapter.py`：新增 `load_context` 第 10 段 `style_profile`（`_load_style_profile_digest()`，截断 1500 字，缺失/损坏静默跳过，**不阻断写作**）；`agents/context-agent.md` 的任务书第 4 段消费并声明「现状 ≠ 目标」。
- `backup_manager.py`：`_selected_backup_paths()` / `_allowed_snapshot_path()` 收 `文风/` 与 `.webnovel/style_profile.json`（实测 `git ls-files` 可见）；`init_project.py` 建 `文风/`。
- 文档同步（skill 口径 15 → 16）：`README.md` mermaid、`webnovel-writer/README.md` 组件表与 skill 清单、`docs/architecture/overview.md` 架构图、`docs/operations/operations.md`、`docs/guides/commands.md`、`references/index/reference-loading-map.md`（覆盖度 16/16 + 登记行）、`references/index/skill-gap-assessment-2026-09-17.md`（附录 A.5 落地登记；同批留下的 N7/N8 见下条 A.6）。
- **N7 死代码清理**：删除 `scripts/data_modules/style_sampler.py` 与其测试 `test_style_sampler_cli.py`；`data_modules/__init__.py` 去掉 `StyleSampler`/`StyleSample`/`SceneType` 的 `__all__` 与 `_LAZY_EXPORTS`；`webnovel.py` 去掉 `style` 子命令三处注册；`test_data_modules.py` 去掉 `TestStyleSampler`；`webnovel-query/references/system-data-flow.md` 去掉模块表行。`test_coverage_boost.py` 的透传测试改为**反向护栏**（断言 `webnovel style` 退出码 2，防止死代码被接回来）。
- **N8：S5 复用文风档案基线**。`scope_audit.py` 新增 `_load_profile_baseline()`（读 `.webnovel/style_profile.json` 的 `observed.summary.scalars.{avg_sentence,dialogue_paragraph_ratio}.median`）与 `--baseline auto|profile|range`（默认 `auto`）；payload 新增 `baseline_mode` 与 `baseline`（含 `source`、两个基线值、`profile.{chapter_count,source_mode,generated_at,last_chapter}`），markdown 报告新增基线来源行。`--baseline profile` 拿不到档案时**不静默回落**，改报 `profile_baseline_unavailable` 且不下偏离结论。档案缺失/损坏/异族 schema/样本不足/中位数非数字一律回落旧口径，**从不抛异常**（审计不能被增强项拖死）。
- `data_modules/style_metrics.py`：新增 `PROFILE_JSON_NAME` / `PROFILE_SCHEMA_VERSION` 两个共享常量，`style_profile.py` 与 `memory_contract_adapter.py` 改为引用（原先三处各写一份 `"style_profile.json"`）。
- `dashboard/app.py`：文档浏览白名单抽为 `doc_dirs = ("正文", "大纲", "设定集", "文风")`（原为三处写死的元组），前端 `FilesPage.jsx` 徽章与已构建产物同步为「正文 / 大纲 / 设定集 / 文风」。
- 测试环境：本机管理版解释器缺 `fastapi/httpx/watchdog`，3 个 dashboard 测试长期跳过；本次在 `.../python/envs/dash`（`--system-site-packages`）补齐，dashboard 测试首次纳入本地回归。

### 验证

- 全量 pytest（含此前跳过的 3 个 dashboard 文件）：**985 passed / 1 failed**。唯一失败是 `test_backup_manager.py::test_backup_after_git_switch_rewrite_keeps_chapters_tagged`，根因是 `scripts/conftest.py:85` 把 `tmp_path` 钉在仓库内 `.tmp/pytest/`，而本机文件系统沙箱在 `git switch` 后不会重建该目录（`FileNotFoundError: ...\.tmp\pytest\...\正文\第0001章.md`）；同一流程在 `.workbuddy/tmp` 下复现为 `backup rc=0 / switch rc=0 / 正文在位`，**非本次改动引入**。
- 覆盖率：本次全量跑出 **88.31%**（12715 statements / 1486 missing），未达 `pytest.ini` 的 `--cov-fail-under=90`。这是**既有口径**——`scripts/run_tests.ps1` 与 `.github/workflows/test-matrix.yml` 都用 `--no-cov` 绕过该闸门，缺口集中在 `rag_adapter`（112）/`user_report`（104）/`webnovel.py`（95）/`run_ledger`（81）等未被本批触及的模块；本次删除的 `style_sampler.py` 原为高覆盖模块，删掉它对整体比率的影响约 −0.04pp，不足以造成 215 行的差距。
- 行为评测 `--suite fast`：**24/24 PASS**（`skill_audit_contract` 新增 `--baseline` 必需项、`skill_style_learn_contract` 仍通过），`validate_plugin_package.py` → OK（0 error / 0 warning）。
- 相关 pytest 通过（`scripts/tests` 80 passed；`test_prompt_integrity` 129 passed；`data_modules` 相关分批通过）。
- D1/D4/D3 修复后新增 `data_modules/tests/test_fact_revision.py`（8 passed），全仓分批 **825 passed**（排除本机缺 `fastapi` 的 dashboard 两文件）。
- 真实书 `本小区禁止抬头` 上跑 `projections retry --chapter 1 --retract`：撤回 19 行 index + 24 分块 + 8 镜像，五项全 done，且 **commit 文件 sha256 与跑之前逐字节相同**（撤回/重放零 commit 副作用）。
- D3 端到端（书项目副本）：改一条 `state_delta` → 不带授权 `exit=1` 且 stderr 给出 identity 与 `--allow-fact-revision` → 带授权 + `--revision-reason` → `revision_number=2`、`retract_required`/`retract_reason`/`retract_previous_identity` 齐备、旧 commit 进 `history/`、`fact_diff` 恰好 1 条 changed、`state_changes` 只剩 3 行（**不撤回会是 6 行**）、postcommit gate `ok=True`。
- 单书规约 6 组场景实测通过（`.workbuddy/tmp/probe_single_book.py`）。
- 章节 tag 冲突 4 组回归场景实测 `exit=0`。
- 索引 16 组场景探针重跑并逐条比对修复前后（`.workbuddy/tmp/probe_index_integrity{,2,3,4}.py`）：漏章从 `doctor ok=True` 变为 blocker；库只读从 exit=0 变为 exit=1；按 doctor 的 `repair` 执行 replay 后对账转 ok。
- 版本元数据同步到 `6.3.0`；`sync_plugin_version.py --check --expected-version 6.3.0`、`validate_release_notes.py --version 6.3.0`、`validate_plugin_package.py` 三项校验全绿，`git diff --check` 无输出。

## v6.2.1 - 修复 Windows 下写章提交偶发的「拒绝访问」

发版范围：`v6.2.0..v6.2.1`。

### 给作者看的变化

- 修复 Windows 上写章提交时偶发的 `WinError 5（拒绝访问）`：`.webnovel/` 下的故事资料文件被 VSCode、杀毒软件或同步盘短暂占用时，系统会自动等待并重试，不再直接失败（#125）。
- 建议 VSCode 用户把 `**/.webnovel/**` 加入 `files.watcherExclude`，项目尽量不放同步盘目录，可进一步降低占用冲突。

### 是否需要改旧项目

不需要。已有书项目继续使用，无需任何迁移。

### 给维护者

- `atomic_write_json` 的 `os.replace` 遇 `PermissionError` 改为指数退避重试（约 2.6 秒窗口），穷尽后如实抛错；全部 JSON 投影共用该写入函数，一并受益。
- 新增 4 个针对性测试，含 Windows 真实句柄占用复现。

### 验证

- 全量 pytest 通过（774 passed）。
- 版本同步、发布说明与插件包校验通过。

## v6.2.0 - 写章结果更清楚，失败后更好恢复

发版范围：`v6.1.0..v6.2.0`。

### 给作者看的变化

- 写章、审查、规划和初始化结束后，最终报告更像写作助手的汇报：会说明已完成、部分完成、需要你处理或未完成。
- `/webnovel-write` 中断后，重复执行同一章会优先检查可信断点，尽量从失败位置继续，减少重写和误覆盖。
- 写章过程减少技术细节打扰；只有创作方向、事实取舍、文件覆盖风险或阻断问题需要裁决时才询问。
- 写作流程的上下文读取更克制，初始化、规划、写章、审查、查询等命令更聚焦，减少无关资料塞满上下文。
- 章节提交前后的中间结果校验更稳，能更早发现缺失的审查、事实提取或故事资料同步结果。
- 文档补充了最终报告读法、恢复边界、日志用途和常见运维入口。

### 是否需要改旧项目

不需要。已有书项目可以继续使用，不需要迁移 `.story-system/` 或 `.webnovel/` 数据。

### 给维护者

- 新增作者术语表、异常目录、审查作者视图、最终报告 helper、写章 run ledger、脱敏 run log。
- 新增 `user-report`、`run-ledger`、`run-log` 统一 CLI 子命令。
- 收紧 commit artifacts、projection writers、write-gate 和 postcommit 的结构化校验。
- 轻量化多个 Skill / Agent 的提示词，补充 reference loading map 和 region-read 规则。
- 增加 prompt integrity、unit tests、behavior eval，覆盖 artifact ownership、最小写章模式、projection retry、blocking review、断点续跑和日志脱敏。
- `Plugin Release` 工作流改为推送到 `master` 后自动发版，并保留手动兜底入口。

### 验证

- 相关 pytest 通过。
- behavior eval 通过。
- `compileall` 通过。
- `git diff --check` 通过。
- 版本同步和插件包校验通过。

## v6.1.0 - 项目体检更稳，出问题更容易定位

- 增加 doctor、project-status、write-gate、projection 重放、hooks、行为评估和插件包校验。
- 强化 Story System 运行时健康检查和 Marketplace 发布校验。

## v6.0.0 - Story System 主链上线，长篇事实更不容易写乱

- 上线合同种子、运行时合同、章节提交、事件审计和投影链路。
- 补齐主链相关集成测试。
