# 索引损坏 / 漏章 —— 修复方案（2026-09-17）

对应评估：`index-integrity-assessment-2026-09-17.md`（16 组实测证据）
状态：**已确认并已实施**（作者选定 P0 + P1，对账用 blocker，`chapter-commit` 退出码改非 0；实施记录见 §7）
适用前提：一个工作区一本书（已定稿）；恢复历史版本一律用 git 原生命令

---

## 0. 设计原则（三条，决定方案边界）

1. **可发现性优先于自动修复**：本轮**不做**任何"自动重建 / 自动删库 / 自动改名"。漏章的根因往往是作者在项目外的动作（删 `.webnovel/`、清理脚本、换机器只拷正文），插件替作者做决定只会把问题掩盖得更深。方案目标是把"静默"变成"有据可查 + 一条可复制的修复命令"。
2. **不动 commit 的落盘契约**：`persist_commit` 先落盘、投影后补，这是评估里唯一"救了作者"的设计（A3/A4 虽然命令失败，但 commit json 还在，能被 `projections replay` 救回来）。所有改动都必须保住这条。
3. **修复动作必须幂等**：`projections retry / replay` 现在就是从 commit 重算读模型，方案新增的能力也必须保持"重跑一次不会产生第二份事实"。

--- 

## 1. 改动清单

### 改动 1（P0）`doctor` 增加 index ↔ commit 对账

**问题**：`doctor` 现有的 `sqlite.index_db.chapters` 只看"文件在不在 / 表能不能读 / 行数多少"（`doctor.py:316-353`），**不与 commit、正文对账** → 删整库（A2）、删中间一章（A7/S6）、`index.db` 与 commit 不再对应，全部报 `ok=True`。

**改法**

1. `data_modules/project_phase.py`：把 `_scan_commits()`（`project_phase.py:207-249`）**改名公开为 `scan_commits()`**，调用点只此一处（`:444`），全仓无其它引用（已 grep 确认）。
2. `data_modules/doctor.py` 新增 `_index_reconciliation_checks(project_root)`，在 `checks.extend(_sqlite_checks(root))`（`:634`）之后挂上。
   实现要点：**一次 glob 读全部 commit + 一次 `read_projection_runs()` 读全部日志**（不要按章调 `latest_projection_run`，那是 O(N²) 的文件读 + 每次一把 filelock；`_scan_commits` 现在就是这么干的，doctor 不该再叠一层）。
3. 三项检查：

| check id | 判据 | 级别 | repair |
|---|---|---|---|
| `index.commit_sync` | accepted commit（且其 index 投影为 `done`）的章号集 − `chapters` 行集 ≠ ∅ | **blocker / error** | `webnovel.py --project-root "<ROOT>" projections replay --from-chapter A --to-chapter B --format json`；若 `index.db` 已损坏/丢失，先删库再 replay |
| `index.story_events_sync` | `.story-system/events/chapter_NNN.events.json` 存在、事件数 > 0，但 `story_events` 表该章 0 行 | warning | 同上（依赖改动 3 的 retry 重建镜像，见 §2 依赖） |
| `index.chapter_metadata` | `chapters` 行 `title=''` 或 `word_count<=0` | warning | 检查正文文件名是否为 `第NNNN章-标题.md`（`find_chapter_file` 只认 `第{num:03d}`/`第{num:04d}`，B1/B2），确认后 `projections retry --chapter N` 刷新元数据 |
| `index.orphan_rows` | `chapters` 行存在但无任何 commit（残留） | warning | 说明该行是旧投影残留；确认后按上面的 replay 覆盖 |

> `index.commit_sync` 为什么可以判到 accepted：`accepted` 的 commit，其 index 投影只有 `done` / `failed:` 两种；`failed:` 已由 `write-gate --stage postcommit` 覆盖，这里只对 `done` 却缺行的情况报错 —— 这正是"投影写成功了但落点坏了"的唯一外部可见信号（评估 §3-A2）。

**影响面 / 风险**

- `doctor` 的 `ok` / 退出码（`webnovel.py:285-295`、`doctor.py:643-657`）在漏章项目上会从 `True` 变 `False`（`blocker` 才翻 `ok`）。这是**本次修复的目的**，但要接受：作者升级后首次 `doctor` 可能突然报错 —— 缓解方式是把 repair 写成可直接复制的命令，并在 `webnovel-doctor/SKILL.md` 说明"这不是新坏的，是一直没被发现"。
- 已核查测试影响面：`test_doctor.py`（无 commit 的 init-ready fixture，断言不受影响）、`test_reliability_fixture.py:85/104`（本就断言 `ok is False`）、`test_webnovel_unified_cli.py:514-533`（无 commit）。**未发现"有 commit 且断言 doctor ok=True"的用例**。

---

### 改动 2（P1）`chapter-commit` 的投影失败必须可见

**问题**：`index.db` 只读时（A5）`chapter-commit` **exit=0**，失败只写在 commit JSON 的 `projection_status.index="failed:…"` 里（`chapter_commit_service.py:342-344`）。而 `webnovel-write/SKILL.md` 的 5.3/5.4 与状态规则（`:275-293`、`:399-406`）虽然要求"projection 失败不得写已完成"，但**命令返回码是 0**，一旦下游只看返回码就会当成成功。

**改法**：`scripts/chapter_commit.py:38-40`

- 始终打印 commit JSON（不变）；
- 投影汇总后判定：**任一 `projection_status` 以 `failed:` 开头或等于 `pending`** → stderr 打一段固定摘要（哪些 writer 失败、原样异常文本、下一步命令 `projections retry --chapter N`），然后 `raise SystemExit(1)`；
- `rejected` commit（index=skipped 属正常）**不触发**；写章过程中 `state` 之外的 skipped 也不触发。

**影响面 / 风险**

- 这是**契约变化**：`chapter-commit` 从此在"commit 已落盘、但投影没跑完"时返回非 0。已核查 `test_webnovel_unified_cli.py:268-274` 是 mock `_run_script`，不受影响；仓库内没有直接跑 `chapter_commit.py` CLI 并断言 `returncode == 0` 的测试。
- 文档必须同步：`webnovel-write/SKILL.md`（5.3/5.4）、`webnovel-chapter-reload/SKILL.md`（同一命令）、`docs/guides/commands.md`。否则语义会与"重复执行 chapter-commit"混淆。

---

### 改动 3（P1）镜像写失败降级 + `retry/replay` 能重建镜像

**问题**（一对，必须一起做）

- **前半**：`event_log_store._write_sqlite_mirror`（`:109-146`）写 `index.db` 的 `story_events` 表，异常**未捕获** → 库损坏（A3）或被别的进程持锁（A4）时整个 `chapter-commit` 硬失败（`exit=1` + traceback），尽管 commit json 已经落盘、五项投影全 `pending`。
- **后半（关键，本次实测新发现）**：`projections.retry_projection`（`projections.py:52-85`）只调 `apply_projection_writers`，**不会重写 `story_events` 镜像** —— 镜像只在 `apply_projections` 的 accepted 分支里经由 `event_store.write_events()` 写（`chapter_commit_service.py:373-384`）。所以只做前半的话，镜像一旦写失败就**永远补不回来**（评估 §3-D4 的"replay 可重建整库"对 `chapters/scenes/appearances/state_changes/entities` 成立，对 `story_events` **不成立**）。

**改法**

1. `event_log_store.py`：`write_events()` 里把 `self._write_sqlite_mirror(...)` 包进 `try/except (sqlite3.Error, OSError)`，失败时：`self.last_mirror_error = str(exc)`，并 best-effort 追加一行到 `.webnovel/logs/event_mirror_errors.log`（时间戳 + 章节 + 异常 + 修复提示）。**只吞 sqlite/OS 错误**，`ValueError`（事件规范化失败）继续抛 —— 现有 `test_event_log_store.py:86-137` 那 4 个 `pytest.raises(ValueError)` 用例因此不受影响。
2. `chapter_commit_service.apply_projections`：`write_events` 之后检查 `event_store.last_mirror_error`，非空则把它并入投影结果的信道 —— 记进 `payload["projection_status"]["index"]`？**不**。更干净的做法：写进 `provenance["mirror_error"]`，并让 `apply_projection_writers` 的 `writer_results` 里带一条 `{"status":"failed","error":...}` 给 projection_log（这样 `write-gate postcommit` 与 `projections retry` 都能看到它）。**具体落点由实现时定，但必须满足"写进投影日志、能被 postcommit 看到"。**
3. `projections.py:retry_projection`：读取 commit 后，若 `meta.status == "accepted"`，先 `EventLogStore(root).write_events(chapter, extraction_list(payload,"accepted_events"))` 再跑 writers（`INSERT OR IGNORE` + `event_id UNIQUE` → **幂等**，重跑不产生重复事实）。这一步顺带把 `story_events_sync`（改动 1 的第 2 项检查）变成**可自动修复**。

**影响面 / 风险**

- A3/A4 会从"整条命令失败、什么都不确定"变成"commit 成功落盘 + 失败可见 + 一条命令可补"。风险是**降级后失败变得更隐蔽** —— 兜底是改动 1 的 `index.story_events_sync` 检查与改动 2 的非 0 退出码（两者都覆盖了这条）。
- 若 `index.db` 只是 `story_events` 表有问题而 `chapters` 可写，index writer 会报 `done`，此时唯一信号就是改动 1 的新检查。所以**改动 3 必须和改动 1 一起上，不能单独上**。

---

### 改动 4（P2）SQLite 连接：超时与并发

**问题**：`index_manager._get_conn`（`index_manager.py:627-634`）裸 `sqlite3.connect`，无 `timeout`、无 WAL、无 busy_timeout → 并发/长事务下默认约 5s 后 `database is locked`（A4 实测 7.8s 后硬失败）。

**改法**：`sqlite3.connect(str(db), timeout=30)` + `PRAGMA busy_timeout=30000`。（`event_log_store._connect`（`:22-32`）同样处理。）

**风险**：`journal_mode=WAL` 单独讨论 —— WAL 会产生 `-wal/-shm` 旁路文件，而 `backup_manager._selected_backup_paths` 只收 `.webnovel/index.db` 一个文件；虽然 SQLite 在最后一个连接关闭时会自动 checkpoint 并删除 `-wal`，但 Dashboard 常驻连接时会让版本点里的 `index.db` 滞后。**建议本轮只加 timeout，不开 WAL**，把 WAL 留给"确认 dashboard 会规整关闭连接"之后再单独评审。

---

### 改动 5（P2）正文命名规约的入口校验

**问题**：B1/B2 —— `find_chapter_file` 只认 `第{num:03d}章*` / `第{num:04d}章*`，名字不合规或文件被移走时，索引 `title=''`、`word_count=0`，且 `exit=0` 不报错。

**改法（二选一，倾向 a）**

- **a. 只诊断不阻断**：已由改动 1 的 `index.chapter_metadata` 覆盖（warning）。写章流程入口（`webnovel-write` 的 Step 5 之前）**不做**硬校验，避免给作者制造"文件名不合规就不让写"的新阻断。
- b. 在 `chapter_commit`/`write-gate precommit` 加 warning 级提示。

---

## 2. 顺序与依赖

```text
改动 1 (doctor 对账)  ──┬─→ 独立可上（纯只读，零行为变化）
                        └─→ 是改动 3 的兜底（必须先有）

改动 2 (退出码)  ─── 独立可上（契约变化，需同步 4 处文档）

改动 3 (镜像降级 + retry 重建)  ─── 必须与改动 1 同批（否则降级后无信号）

改动 4/5 (P2)  ─── 可放第二批
```

---

## 3. 验证计划

**单测（新增）**

- `test_doctor.py`：新增 4 例 —— ① accepted commit + `chapters` 缺行 → `index.commit_sync` = error 且 `ok is False`；② 正常项目 → 该项 `ok`；③ `chapters` 有残留行 → warning 不翻 `ok`；④ `title=''`/`word_count=0` → warning。
- `test_event_log_store.py`：新增 —— `index.db` 被替换成非 SQLite 内容时 `write_events` **不抛**、`last_mirror_error` 非空、事件 JSON 文件仍落盘。
- `test_projections_cli.py`：新增 —— 删掉 `index.db` 后 `retry_projection` 同时恢复 `chapters` 行**与** `story_events` 行；再跑一次结果不变（幂等）。
- `test_webnovel_unified_cli.py`：新增 —— 投影失败时 `chapter-commit` 退出码为 1；正常时 0。
- `test_project_phase.py` / 引用 `_scan_commits` 的地方：随改名同步。

**实测（复用 + 新增探针）**

- 复用 `.workbuddy/tmp/probe_index_integrity*.py` 的 16 组场景重跑，逐条比对"修复前后"：A2/A7/S6 应出现 `index.commit_sync` error（修复前 `ok=True`）；A5 的 `chapter-commit` 应 `exit=1`（修复前 0）；A3/A4 应"commit 落盘 + 退出非 0"而非 traceback；V1 场景应额外恢复 `story_events` 行。
- 新增探针：对 3 章项目删中间一章的行、删整库、只读库三种形态跑 `doctor --format json` 与 `projections replay` 前后对比。

**回归（按文件分批，宿主有批量删除守卫）**

- `data_modules/tests/test_doctor.py`、`test_reliability_fixture.py`、`test_write_gates.py`、`test_projections_cli.py`、`test_projection_writers.py`、`test_event_log_store.py`、`test_project_phase.py`、`test_chapter_commit_service.py`、`test_chapter_reloading.py`
- `tests/`（scripts 全量 80 例）

---

## 4. 文档同步清单

| 文件 | 改什么 |
|---|---|
| `docs/operations/index-integrity-assessment-2026-09-17.md` | §5 处置建议加"已实施/未实施"状态列，§6 去掉"均未实施" |
| `docs/operations/operations.md` | 新增"索引体检与对账"小节：`doctor` 新检查项、`projections retry/replay` 的适用边界（**包括 `story_events` 修复后也可重建**） |
| `docs/guides/commands.md` | `chapter-commit` 的退出码语义；`doctor` 检查项 |
| `skills/webnovel-write/SKILL.md` | 5.3/5.4：非 0 退出码的处理路径（仍只允许 `projections retry`，不回退 Step 1-4） |
| `skills/webnovel-doctor/SKILL.md` | 新增检查项的解读与"这不是新坏的"说明 |
| `CHANGELOG.md` | 并入现有 `## 未发布（版本号待定）` 小节 |

---

## 5. 明确不做的事（本轮边界）

- 不自动重建索引、不自动删损坏库、不自动改正文文件名。
- 不改 `persist_commit` 先落盘后投影的顺序。
- 不开 `journal_mode=WAL`（见改动 4 的旁路文件问题）。
- 不动 `/webnovel-chapter-reload` 的本地副本命名（`chapter_N_<uuid>`，与本议题无关的既有结论）。
- 不给 doctor 增加自动修复动作（修复半边留给 `skill-gap-assessment` 里的 P0 `webnovel-repair`，本轮只把命令写进 repair 字段）。

---

## 6. 决策记录（2026-09-17）

| 决策点 | 作者选择 |
|---|---|
| 实施范围 | **P0 + P1**（改动 1–3；改动 4 的 timeout 一并落地，WAL 不做；改动 5 降级为"只诊断"） |
| `index.commit_sync` 级别 | **blocker**（doctor `ok=False`、退出码 1） |
| `chapter-commit` 退出码 | **改为非 0**（exit=1 + stderr 摘要） |

---

## 7. 实施记录（2026-09-17）

### 7.1 改动文件

| 文件 | 改动 |
|---|---|
| `scripts/data_modules/project_phase.py` | `_scan_commits` → 公开 **`scan_commits()`**；日志读取单遍化（一次 `read_projection_runs` 后按章分组，取代逐章 `latest_projection_run`）；损坏日志语义不变（全章 `failed:projection_log_unreadable`） |
| `scripts/data_modules/doctor.py` | 新增 `_index_reconciliation_checks()` 及 4 个 check（见 §1 改动 1 表）、`_index_query` / `_replay_repair` / `_chapters_with_events`；挂到 `build_doctor_report` 的 `_sqlite_checks` 之后 |
| `scripts/chapter_commit.py` | `_projection_failures()`：`projection_status` 含 `failed:`/`pending`，或 `provenance.event_mirror_error` 非空 → stderr 摘要 + `SystemExit(1)`；**stdout 的 commit JSON 照旧打印** |
| `scripts/data_modules/event_log_store.py` | `write_events` 走 `_mirror_errors()`：捕获 `sqlite3.Error/OSError` → `last_mirror_error` + 追加 `.webnovel/logs/event_mirror_errors.log`；新增 `mirror_events_only()`（只写 sqlite，不写事件 JSON）；`_connect` 加 `timeout` + `busy_timeout` |
| `scripts/data_modules/chapter_commit_service.py` | `_sync_event_mirror(payload, write_event_file=...)`：主链写"事件文件 + 镜像"，retry 只重建镜像；失败记入 `provenance.event_mirror_error` 并作为 `event_mirror` 条目进 projection run；`apply_projection_writers` 增加 `mirror_results` 入参 |
| `scripts/data_modules/projections.py` | `retry_projection` 先 `rebuild_event_mirror()` 再跑 writers；`ok` 与 `error` 计入镜像失败 |
| `scripts/data_modules/config.py` | `SQLITE_BUSY_TIMEOUT_SECONDS = 30` |
| `scripts/data_modules/index_manager.py` | `_get_conn` 使用该 timeout + `PRAGMA busy_timeout` |

### 7.2 新增/调整测试

- `test_doctor.py`：+6 例（健康项目 ok；缺行 → blocker；库不可读 → blocker；残留行 warning；元数据退化 warning；事件镜像缺行 warning）
- `test_event_log_store.py`：+3 例（库损坏降级并写错误日志；`mirror_events_only` 不建事件文件且幂等；非法事件降级为错误）
- `test_projections_cli.py`：+1 例（retry 重建 `story_events` 镜像，跑两次仍 1 行）
- `test_chapter_commit_cli.py`：**新文件 6 例**（退出码 0/1、pending、`rejected` 全 skipped 不算失败、事件镜像失败计入、真实链路失败留痕）
- 既有 `test_projections_cli.test_retry_projection_does_not_rewrite_commit_side_effects` 一度被破坏：**retry 不得改事件 JSON** 是既有契约 → 改为 `mirror_events_only`（只写 sqlite）后恢复通过

### 7.3 实测（探针重跑，修复前 → 修复后）

| 场景 | 修复前 | 修复后 |
|---|---|---|
| S3 删库后继续写章（自动重建空库） | `doctor ok=True`（`rows=1`） | **`index.commit_sync` error/blocker `missing=[1]`**，doctor exit=1 |
| S6 删掉中间一章的行 | `doctor ok=True` | **`missing=[2]`**，doctor exit=1 |
| S7 不补零文件名 | `title='' word_count=0`，无提示 | `index.chapter_metadata` warning `degraded=[7]`（不阻断） |
| S4 库内容损坏 | 全章丢失，doctor 仅 warning | `index.commit_sync` error（库不可读） |
| S5 库只读 | `chapter-commit exit=0` | **`exit=1`** + stderr 失败项 + 补跑命令；`retry` 后恢复 |
| U2 库损坏 | exit=1 + traceback | exit=1 + **可读摘要**（无 traceback），stdout 仍是 JSON，commit json 落盘 |
| V2 EXCLUSIVE 锁 | exit=1，耗时 7.8s | exit=1，耗时 **43.1s**（30s busy timeout 生效；锁长期持有时仍会失败，随后 retry 可补） |
| V1 删损坏库后 replay 1–2 | ch1/ch2 回位 | 回位，且 `doctor ok=True`、index 检查全 ok |
| S6 修复闭环 | —— | 按 `repair` 执行 `projections replay --from-chapter 2 --to-chapter 3` → `commit_sync` 转 ok，`rows=3` |

### 7.4 回归

`scripts/tests` 80 passed；`test_prompt_integrity` 129 passed；其余分批：doctor 13 / 事件与提交与投影 53+56+38 / reloading+可靠性+写闸门 47 / run_ledger+user_report+统一 CLI 45 / coverage_boost+project_phase 等 93+28，全绿。

### 7.5 遗留（有意）

- **不开 WAL**：`-wal/-shm` 不在 `_selected_backup_paths`，dashboard 常驻连接时会让版本点里的 `index.db` 滞后。
- **`vectors.db` 无对账**：本批只做 index。
- **`index process-chapter` 仍是旧入口**（不经过 commit，schema 失败也返回 0）：文档已标注"不能用来重建索引"，代码未改。
- **并发最坏耗时**：锁被长期持有时 `chapter-commit` 约 40s 才失败（30s × 连接点）。若作者觉得等待过久，调 `SQLITE_BUSY_TIMEOUT_SECONDS` 即可。

---

## 8. 待确认决策点（已确认，见 §6）
