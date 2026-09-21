# 索引损坏 / 漏章场景评估（2026-09-17）

评估对象：`.webnovel/index.db`（chapters / scenes / appearances / entities / relationships …）的写入链路与可发现性
方法：读代码 + **实测**（4 组探针脚本，共 16 个场景，见文末「复现方式」）
结论状态：**已定论并已处置**（修复实施记录见 `index-integrity-fix-plan-2026-09-17.md` §7）

---

## 1. 一句话结论

索引不是"写正文时顺便更新的计数表"，而是 **commit 的投影产物**：
`正文 → review/fulfillment/disambiguation/extraction → chapter-commit → persist_commit → projection writers（index 是其中之一）`。
所以索引出问题的场景，几乎都能归到三类：**投影没跑成 / 投影跑了但落点坏了 / 投影结果与正文-commit 不再对应**。

最需要知道的三条：

1. **索引整库丢失是静默的**：删掉 `index.db` 后继续写章，下一次 commit 会**自动重建一个空库**，历史章节全部消失，而 `doctor` 报 **ok**（实测 S3）。
2. **索引写失败默认不阻断写作**：`index.db` 只读时 `chapter-commit` **exit=0**，只在 JSON 里留 `"index": "failed:attempt to write a readonly database"`（实测 S5）。
3. **索引坏了/被锁时，commit 反而会硬失败**：异常从 `event_log_store._write_sqlite_mirror` 逃逸，`chapter-commit` **exit=1 + traceback**，但 **commit json 已经落盘且五项投影全 pending** → 留下"有 commit、无索引"的章（实测 U2、V2）。

---

## 2. 写入链路（谁在什么时候写 index）

| 步骤 | 位置 | 对索引的影响 |
|---|---|---|
| `init` | `init_project.py` | **不建 `index.db`**（实测：init 后文件不存在） |
| `chapter-commit` | `chapter_commit.py:38-39` | 先 `persist_commit`（写 commit json），再 `apply_projections` |
| 事件落盘 | `event_log_store.write_events` → `_write_sqlite_mirror` | **写同一个 `index.db`**（`story_events` 表）；任何 sqlite 错误在此**未捕获**，直接终止命令 |
| 投影路由 | `event_projection_router.required_writers` | `accepted` → 必定含 `index`；`rejected` → 只有 `state`，**index 记为 skipped** |
| 索引投影 | `index_projection_writer.apply` | 非 accepted 直接 early-return；写 chapters / scenes / appearances / state_changes / entity_deltas |
| 写失败 | `chapter_commit_service.apply_projection_writers:342-344` | `except Exception` → `projection_status[name] = "failed:<原始异常文本>"`，**继续往下走** |
| 连接 | `index_manager._get_conn` | 裸 `sqlite3.connect`，**无 WAL、无 busy_timeout 设置**（默认等 5s 后 `database is locked`） |

两个"硬失败/软失败"分水岭：
- 落在 **`IndexProjectionWriter.apply` 内部** → 软失败（`failed:`，commit 仍 exit=0）。
- 落在 **事件镜像 / 其它未包 try 的 sqlite 写入** → 硬失败（exit=1、无 JSON 输出、commit 已落盘）。

---

## 3. 场景清单（全部实测）

### A. 索引漏章（chapters 缺行）

| # | 场景 | 触发方式 | 实测表现 | 可否被发现 |
|---|---|---|---|---|
| A1 | **commit 未被 accepted** | 审查 blocking / missed_nodes / pending 非空 | `index=skipped`，该章永不进索引（设计如此） | 只有作者回炉重提 |
| A2 | **`index.db` 被删除** | 误删、清理脚本、换机器只拷了正文 | 下一次 commit **自动重建空库**；历史章全丢（S3：应有 2 章，只剩 `[(2,…)]`） | **doctor 报 ok**（`rows=1`） |
| A3 | **`index.db` 内容损坏** | 磁盘写坏、被非 SQLite 内容覆盖 | `chapter-commit` **exit=1 + traceback**；commit json 已落盘、五项投影全 pending（U2） | doctor 有 `warning: file is not a database` |
| A4 | **`index.db` 被其它进程持锁** | 多 agent/多终端并行；dashboard 长事务 | 同 A3：exit=1，`sqlite3.OperationalError: database is locked`，耗时 7.8s（V2） | 无（只能靠 exit=1 发现） |
| A5 | **`index.db` 只读** | 只读盘、权限变更、被 `attrib +R` | commit **exit=0**，`index="failed:attempt to write a readonly database"`（S5） | 仅 `write-gate --stage postcommit` 报 `ok=False` |
| A6 | **流程中断** | 写完正文但没跑 review/commit | 索引无该章（commit json 也不存在） | 无索引侧提示 |
| A7 | **手工改库/删行** | 直接 SQL、脚本误删 | 该章消失（S6 模拟） | **doctor 报 ok** |

### B. 章在、元数据退化

| # | 场景 | 实测表现 |
|---|---|---|
| B1 | **正文文件名不含补零的"第NNNN章"**（如 `第7章-标题.md`） | `find_chapter_file` 匹配不到 → 索引 `title=''`、`word_count=0`；改为 `第0007章-初入宗门.md` 后同一逻辑得到 `title='初入宗门'`、`word_count=250`（S7） |
| B2 | **正文文件缺失/被移走**（commit 已存在） | 同样 `title=''`、`word_count=0`，且 **exit=0 不报错**（T3） |

> B1/B2 不是"缺章"，但会让 dashboard 章节列表、`status` 的章节元数据、`index_manager stats` 出现空标题/零字数。

### C. 索引残留 / 与 commit 不一致

| # | 场景 | 实测表现 |
|---|---|---|
| C1 | **已 accepted 章想改判 rejected** | 直接被拒：`chapter_artifacts_stale: reload and revalidate before revising an accepted commit`（T1）。必须先 `chapter-reload` 再重提 |
| C2 | **rejected commit 不清理旧索引行** | `IndexProjectionWriter.apply` 对非 accepted 直接 early-return（`index_projection_writer.py:25-26`）→ 旧行保留。S2 实测 `index=skipped`、旧行仍在 |
| C3 | **git 回退（已 backup）** | **一致回退**：`git switch -c rewrite-from-ch0002 ch0002` 后正文 2 章、commit 2 个、`index.chapters` 2 行（U3） |
| C4 | **git 回退（回退点之后未 backup）** | **回退是空操作**：未提交文件被保留，`M .webnovel/index.db` 的本地修改被 carry over → 仍是 3 章，作者以为回退了其实没有（U4） |

> C3 成立的前提是 `backup` 把 `index.db` 收进了版本点（`_selected_backup_paths` 含 `.webnovel/index.db`，实测 tag 内确实有该文件）。
> 注意 `backup_receipts.json` **不在受管名单**，`git status` 里始终是 `??`，不随版本点回退（与既有结论一致）。

### D. 可发现性与修复（关键缺口）

| # | 事实 | 证据 |
|---|---|---|
| D1 | **doctor 不做索引对账**：只检查 `index.db` 文件是否存在、`chapters` 表是否可读、行数是多少，**不比对 commit 数/正文数** | 38 项检查里只有 `sqlite.index_db.chapters`（`rows=N`）；S3/S6/A7 全部 `ok` |
| D2 | 漏章/整库重建**没有任何告警** | S3 `rows=1`（应有 2）、S6 删中段一章 `rows=2`（应有 3），doctor 均 `ok=True` |
| D3 | `projections retry --chapter N` 有效 | S5：恢复可写后 `done`；V2：释放锁后补上 ch2 |
| D4 | `projections replay --from 1 --to N` 可从 commit **整库重建** | V1：删掉损坏库 → replay 1-2 → `ch1/ch2` 全部回位，`ok=true` |
| D5 | 旧入口 `state process-chapter` 不可靠 | schema 校验失败时 **exit=0** + 只输出 error JSON（U1）；不经过 commit/projection，`chapters` 表未变 |
| D6 | 索引的作用面 | `dashboard/app.py:397/447/454` 直读 `chapters`；`index_observability_mixin` 的 COUNT/MAX 供 `stats`；`status_reporter.scan_chapters` **扫正文**、只在补元数据时查索引（索引缺失时降级不崩） |

---

## 4. 最容易踩的三种真实情形

1. **"我改过正文，索引对不对？"** —— 改正文不会动索引（`verified_backup` 会变空、`doctor` 的 `chapter.body_revision_stale` 才会报 ERROR）。重跑 commit 才会刷新索引。
2. **"我删过 `.webnovel/` 里的东西清理空间"** —— 索引整库消失后无法察觉，直到 dashboard 章节列表变短（A2）。
3. **"我回退到两章前重写"** —— 如果回退点之后没跑过 `backup`，`git switch` 不会清理那些文件，回退静默失效（C4）。

---

## 5. 处置建议（**已实施**，2026-09-17）

实施记录见 `index-integrity-fix-plan-2026-09-17.md` §7（含改动文件、实测复现与遗留项）。

| 优先级 | 建议 | 状态 | 落地位置 |
|---|---|---|---|
| P0 | `doctor` 增加 **index ↔ commit 对账**：`accepted` commit 的章号集合 vs `chapters` 行集合，缺行即 ERROR（含修复指引 `projections replay`） | **已实施**（blocker） | `doctor.py:_index_reconciliation_checks` |
| P1 | `chapter-commit` 在 `projection_status` 含 `failed:`/`pending` 时 **返回非 0**（或 stderr 摘要） | **已实施**（返回 1 + stderr 摘要） | `chapter_commit.py:_projection_failures` |
| P1 | `event_log_store._write_sqlite_mirror` 的 sqlite 异常改为**降级 + 留痕**，不让整个 commit 硬失败 | **已实施** | `event_log_store.py:_mirror_errors` |
| P1+ | （实测新发现）`retry/replay` **重建 `story_events` 镜像**，否则降级后的镜像失败永远补不回来 | **已实施**（幂等，不产生 commit 侧副作用） | `chapter_commit_service.py:_sync_event_mirror` / `event_log_store.py:mirror_events_only` |
| P2 | `IndexManager._get_conn` 设 `timeout` + `journal_mode=WAL` | **部分实施**：`timeout=30s` + `PRAGMA busy_timeout` 已加；**WAL 不开**（旁路文件不在版本点名单，见 fix-plan §1 改动 4） | `config.py:SQLITE_BUSY_TIMEOUT_SECONDS` |
| P2 | 正文文件命名规约（补零）在 write 流程入口做校验/自动改名 | **改为只诊断不阻断** | `doctor.py:index.chapter_metadata`（warning，不阻断写章） |

另修：`project_phase._scan_commits` → 公开 `scan_commits()`，并把"按章读整份 projection_log"的单遍化改造做掉（doctor 每次体检不再新增 O(N²) 日志读取）。

---

## 6. 关系与后续

- 与 `skill-gap-assessment-2026-09-17.md` 的 P0 `webnovel-repair`（doctor 的修复半边）**同源**：该 skill 明确包含"DB/索引重建"，本评估给出它必须覆盖的两个具体动作 —— `projections replay`（从 commit 重建）与"删除损坏库后再 replay"。两条命令现已写进 doctor 的 `repair` 字段，可直接复用。
- 与既有的 `chapter-tag-conflict-fix-2026-09-17.md`、`multi-book-workspace-assessment-2026-09-17.md` 无冲突：那些解决的是版本点与工作区定位，本文件解决索引。
- **已处置**：§5 的 P0–P2 已实施（P2 的 WAL 有意不做），实施记录见 `index-integrity-fix-plan-2026-09-17.md` §7。
- **有意留白**：doctor 不做自动修复；`backup_receipts.json` 不在受管范围；`story_events` 之外的读模型（vectors.db）仍无 commit 对账。

---

## 7. 复现方式

```bash
# 场景 A1–A7 / B1 / B2（16 组中的前 8 组）
"C:/tool/Python314/python.exe" -X utf8 .workbuddy/tmp/probe_index_integrity.py
# C1–C4（失败原因、正文缺失、直接写库、git 回退两形态）
"C:/tool/Python314/python.exe" -X utf8 .workbuddy/tmp/probe_index_integrity2.py
# U1–U4（旧入口 schema、损坏库的完整栈与 commit 落盘、干净/脏回退）
"C:/tool/Python314/python.exe" -X utf8 .workbuddy/tmp/probe_index_integrity3.py
# V1/V2（replay 重建、EXCLUSIVE 锁竞争）
"C:/tool/Python314/python.exe" -X utf8 .workbuddy/tmp/probe_index_integrity4.py
```

输出分别存为同名 `.out.md`。探针在 `.workbuddy/tmp/` 下自建临时项目，`WEBNOVEL_CLAUDE_HOME` 指向临时目录，**不污染真实 `~/.claude`**。
