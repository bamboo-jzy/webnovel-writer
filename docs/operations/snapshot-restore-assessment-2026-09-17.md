# 快照恢复能力评估（snapshot restore）

评估日期：2026-09-17
评估对象：`webnovel-writer/scripts/backup_manager.py`（`GitBackupManager`，1114 行）、`scripts/tests/test_backup_manager.py`（18 用例）、`docs/operations/operations.md`、`docs/guides/commands.md`、`README.md`
基线版本：v6.2.1

> **处置状态（2026-09-17，本报告出具后当日执行）：恢复能力已按决策整体移除，回退交给 Git 原生命令。**
>
> 依据：`rollback()` / `restore_snapshot()` 的生产调用方为 0（只被 CLI 与测试调用），且作者的实际项目在 Git 管理下，不需要插件自带一套恢复器。
>
> 已删除（`backup_manager.py` 1113 → 790 行）：`rollback`、`preview_rollback`、`restore_snapshot`、`preview_snapshot_restore`、`_stage_snapshot`、`_install_staged_snapshot`、`_restore_snapshot_contents`、`_verify_restored_snapshot`、`_post_restore_health`、`_ensure_restore_destination`、`_working_tree_dirty`、`_git_target_backup_files`、`_remove_tracked_files_absent_at_tag`，以及 CLI `--rollback` / `--restore-snapshot` / `--dry-run`；同步删除 10 个对应用例。
>
> 保留：`backup()`（Git commit + tag）与无 Git 时的 `_local_backup()` 离线副本（`run_behavior_evals.py:354,385` 有真实调用方）、`verified_backup()`、`diff` / `list_backups` / `create_branch`。
>
> 因此下文 **P0-1、P0-2、P1-1、P1-2、P1-3、P2-1、P2-5 随代码整体消失**（缺陷所在实现已不存在，不需再修）；**P1-4（skills 无入口）不再是缺口**，恢复改由 `README.md`、`docs/operations/operations.md`、`docs/guides/commands.md` 记录 git 原生命令，`--list` / `--diff` / `--create-branch` 也一并补了文档。
>
> 仍然有效：第 1 节的 manifest / 校验 / 排除设计（`_local_backup` 与 `verified_backup` 仍在用）。**新增待决项见第 8 节**：`backup()` 遇已存在且指向其他提交的 `chNNNN` tag 直接返回 False，"历史 tag 已指向其他提交"；移除恢复后 tag 成为唯一版本点，该缺陷已升为 **P0**（章纲修订 100% 硬阻断，详见 §8.2/§8.3）。
>
> **该 P0 已于当日修复并实测通过**：采用分层 tag 命名（`chNNNN` 作为可前移的章节版本点，旧点自动归档为 `chNNNN-prev-<时间戳>`）。方案选型、改动清单与实测记录见 `docs/operations/chapter-tag-conflict-fix-2026-09-17.md`。**下文 §8.5 保留当时的候选对比（未采纳的 A/B/C 仅作背景），§8.6 / §9.3 / §9.4 中"硬停""手工 `git tag -d`"的描述已不适用于当前实现**，对应位置已就地标注。

---

## 0. 结论

**设计骨架是这类系统里少见的严谨，但当前状态下"恢复"这条路在两个真实场景里跑不通或没有安全网。**

- 设计层面：manifest + 全量 SHA-256、路径白名单、staging 两阶段提交、安装失败回滚、symlink/junction 双向防护、SQLite backup API 复制 `.db`、dirty 门禁、receipt 可验证——按"数据安全契约"的标准已经超出多数同类项目。
- 实现层面有 **2 个 P0**：
  1. **dirty 门禁被自身运行产物永久触发** → Git 项目的 `--rollback` 与 `--restore-snapshot` **100% 被拒**。
  2. **保留策略会删掉本次创建的安全快照** → 无 Git 模式写到第 10 章后，恢复会在**没有安全网**的情况下执行，且仍然返回 `ok: true`。
- 入口层面：**14 个 skill 零引用**恢复能力，`--rollback` / `--list` / `--diff` / `--create-branch` 在命令文档里完全没有。README 大段承诺的恢复契约属于"有源无门"。
- 测试层面：18 个用例全绿，但上述两个 P0 **都不在覆盖范围内**（测试都在"干净仓库 + 手工改已跟踪文件"的理想前提下构造 dirty）。

---

## 1. 能力清单（做得好的部分）

| 能力 | 位置 | 评价 |
|---|---|---|
| 快照 manifest `snapshot/v1`：相对路径 + size + SHA-256 | `backup_manager.py:212-228` | 完整，逐个文件可校验 |
| manifest 自校验：schema、重复路径、越界、size、sha 长度 | `:230-267` | 拒绝 `..`、绝对路径、反斜杠、非白名单前缀 |
| 路径白名单（故事主链 + 指定 read-model） | `:149-165` | 与 `_git_backup_scope:695-708` 一致 |
| 排除 `backups/tmp/__pycache__/.pytest_cache/.git/*.pyc/.env*` | `:112-119` | secrets 不进快照，有测试断言 |
| symlink / junction / reparse point 双向防护（源 + 恢复目标父目录） | `:121-132`、`:167-189` | Windows 场景考虑到位 |
| `.db` 用 `sqlite3.backup()` 而非裸拷贝 | `:199-210` | 避免半写状态 |
| 两阶段：staging → 逐项校验 → 安装 → 再校验 | `:332-375`、`:407-430` | staging 失败不碰当前状态（有测试） |
| 安装失败尝试用安全快照回滚 | `:410-424` | 有测试 |
| 恢复后 runtime health + projection status 检查 | `:305-330`、`:429-430` | health 失败即 `ok=false`，有测试 |
| Git 备份 receipt `backup-receipt/v1`，可验证 tag/commit/正文一致 | `:558-592` | 能识别被移动的 tag（有测试） |
| rollback 前滚式：当前分支建恢复提交，历史不丢 | `:916-923` | 有测试断言分支与提交数 |
| rollback 只碰故事范围，不动无关文件 | `:710-714`、`:855-876` | 有测试 |
| rollback / restore 都不移动历史 tag、不 reset、不强推 | 全文件 | 安全姿态正确 |

测试覆盖（`test_backup_manager.py`，18 通过）：manifest 篡改、越界 manifest、checksum 失败、staging 失败、安装失败回滚、junction 父目录、dirty 拒绝、保留策略上限、receipt 篡改、tag 漂移。**覆盖面明显强于项目里 revise/reload 五件套的 eval 空白。**

---

## 2. P0 缺陷

### P0-1 dirty 门禁被自身运行产物永久触发 → Git 项目恢复/回滚全废

**证据（实测）**：`D:\projects\webnovel-writer\.workbuddy\tmp\probe_backup.py`

```text
== 1) git-mode backup(1) ==
backup ok: True
git status --porcelain: '?? .webnovel/'
_working_tree_dirty(): True

== 2) preview_rollback(1) right after a normal chapter backup ==
{ "ok": false, "dirty": true,
  "error": "当前工作树有未提交修改，拒绝恢复以保护当前状态" }
```

**因果链**：

1. `_working_tree_dirty()` 执行的是**全仓** `git status --porcelain`，无 pathspec（`backup_manager.py:730-732`）。
2. `backup()` 只 `git add` 故事范围（`:789-793` + `_git_backup_paths:716-720`），`.webnovel/` 下的 `backups/`、`logs/`、`archive/`、`backup_receipts.json`、`run_ledger.json` 从不提交。
3. 生成的 `.gitignore` **不含 `.webnovel/backups/` 等运行产物**（`backup_manager.py:456-479`；`init_project.py:642-671` 同样缺）。
4. 因此只要写过一章，仓库恒为 dirty → `preview_rollback:760-761` 与 `preview_snapshot_restore:294-295` 都返回 `ok=false`。

**影响**：README `:206` 与 `operations.md:247-256` 承诺的自动备份/回滚/快照恢复，在初始化过 Git 的项目（即 `/webnovel-init` 的默认路径）里**一次都用不了**。用户唯一能做的是手敲 `git commit -a` 清场——而文档从未提及。

**修复方向**：

- 把 dirty 判定限定到受管范围：`git status --porcelain -- <正文> <大纲> <设定集> .story-system .webnovel/state.json .webnovel/index.db ...`；dirty 语义应是"**受管文件**有未提交修改"。
- 同时补齐 `.gitignore`（忽略 `.webnovel/backups/`、`.webnovel/logs/`、`.webnovel/backup_receipts.json`、`.webnovel/run_ledger.json`、`.webnovel/*.lock`），并把 `_init_git` 与 `init_project` 的两份 `.gitignore` 文本收敛为单一真源（当前两份内容已不一致：init 版有 `*.lock/*.bak`，backup 版没有）。

---

### P0-2 保留策略会删掉本次创建的安全快照 → 恢复在无安全网下执行，仍报成功

**证据（实测）**：`D:\projects\webnovel-writer\.workbuddy\tmp\probe_backup3.py`（无 Git 模式，第 1～10 章各备份一次 = 满 10 份快照，然后恢复第 1 章快照）

```text
备份数量: 10
✅ 本地备份完成: ...\snapshot_ch0000_20260917_093752_212601   ← 安全快照以 ch0000 命名创建
restore ok: True
safety_backup 字段: ''                                        ← 安全快照已不存在
backups 目录: 只剩 snapshot_ch0001..ch0010（safety_* 一个都没有）
```

**因果链**：

1. `restore_snapshot` 用 `self._local_backup(0)` 造安全快照（`:382`），随后 glob `snapshot_ch0000_*` 再改名为 `safety_snapshot_*`（`:392-398`）。
2. `_local_backup` 内部先写 receipt、**再裁剪** `snapshot_ch*` 保留最近 10 份（`:673-681`）。
3. 满 10 份时，新造的 `snapshot_ch0000_*` 按名称排序落在 `snapshots[:-10]` 里，**在改名之前就被删掉**。
4. 改名步骤找不到目录 → `safety_backup_path = ""` → 代码不拒绝，继续安装（`:399-409`），报告 `ok: true`。

**影响**：文档承诺"正式恢复会先创建不会被普通保留策略删除的 `safety_snapshot_*`"（`operations.md:254`）与"安装失败会尝试用安全快照回滚"（`:254`）在此状态下失效。若安装中途失败，回滚目标为空路径：先 `_restore_snapshot_contents(Path(""))` 读 manifest 失败，只能报告"快照恢复失败 + 安全快照回滚失败"，**当前工作区已处于半安装状态且无自动还原**。

**可达性**：无 Git 模式（`git` 不在 PATH）下必然可达——第 10 章之后每次恢复都触发。Git 模式下因 P0-1 恢复本就被拒；修掉 P0-1 后同样可达。

**修复方向**：

- 安全快照使用独立命名与前缀直接创建（`safety_snapshot_*`），不参与 `snapshot_ch*` 裁剪；`_local_backup` 增加 `prefix`/`retain` 参数。
- 裁剪时显式跳过 `safety_snapshot_*`。
- **`safety_backup_path == ""` 时必须拒绝恢复**（fail-closed），而不是继续执行并返回 `ok: true`。

---

## 3. P1 缺陷

### P1-1 restore 没有收尾提交，第二次恢复必然被自己的 dirty 门禁拒

`rollback()` 结束时在当前分支创建前滚提交（`:916-923`）；`restore_snapshot()` 结束于校验 + health（`:427-441`），**没有任何提交或收尾**。因此恢复后工作树必然变脏；即使 P0-1 修好（dirty 只看受管范围），受管文件本身也被恢复改动过，第二次恢复/回滚照样被拒。需要补收尾提交（或明确提示 + 开关）。

### P1-2 安全快照写了一条必然悬空的 receipt（`chapter = 0`）

`_local_backup(0)` 会写入 `backup_receipts.json["0"]`（`:625-638`、`:673-674`），紧接目录被改名，receipt 指向已不存在的路径；每次恢复覆盖同一条。实测 `receipts["0"].snapshot` 路径存在性 = `False`，`verified_backup(0)` 静默返回 `{}`（`:558-592` 的宽 `except` + 末尾 `return {}`），而 `operations.md:243` 明确承诺"损坏 receipt 不会被当作缺失后静默回退"。安全快照应走独立 receipt（如 `safety-receipt/v1`）或干脆不写 receipt。

### P1-3 `safety_snapshot_*` 无保留策略、无清理入口、无列举入口

`probe_backup2.py` 显示两次恢复留下两份完整 `safety_snapshot_*`。安全快照是全量副本（含 `index.db` / `vectors.db`），200 万字项目体积可观，且：

- `--list` 只列 Git tag（`:960-997`），**无 Git 时打印"⚠️ 暂无备份"，而 `.webnovel/backups/` 里其实有 10 份**；
- 没有任何 `--list-snapshots` / `--prune`，用户只能手翻目录。

建议：安全快照保留最近 N 份；`--list` 在 Git 不可用时回落到本地快照清单。

### P1-4 零 skill 入口 + 命令行文档缺失

- `skills/` 全部 14 个 skill 只出现 `backup`（创建备份）用法，`restore-snapshot` / `rollback` **零引用**（上一轮 grep 已确认）。
- `docs/guides/commands.md:173-189` 只写了 `--restore-snapshot`；`--rollback` / `--list` / `--diff` / `--create-branch` **完全没有文档**，尽管 `operations.md` 承诺了"备份恢复"能力。
- 最终报告的 `backup_unconfirmed` 问题给出的 `next_action` 是"运行备份命令或重新执行写章收尾步骤"，command 是 `/webnovel-write {chapter}`（`user_report.py:630-642`）——**恢复路径不出现在任何用户可见的建议里**。

结果：用户拿到的是"系统会保证你能回滚"的承诺 + 一条要自己拼的 CLI。这是上一轮 skill 缺口评估里 P1 `webnovel-backup` 的真实动机，本轮已从"缺 skill"升级为"能力本身在 git 场景不可用"。

---

## 4. P2 / 边界

| # | 问题 | 位置 | 说明 |
|---|---|---|---|
| P2-1 | `.webnovel/archive/` 不在备份/恢复范围 | `:97-110` | `archive_manager` 的角色/伏笔/报告归档是状态存储；快照回滚后 `archive/*.json` 与 `index.db`/`state.json` 可能不自洽，`archive --restore-character` 可能失效。至少要在文档注明需复核，或纳入范围 |
| P2-2 | `filelock` 缺失时备份语义破碎 | `security_utils`＋`:542-543` | Git 模式：commit+tag 已建、receipt 写失败 → `backup()` 返回 False，写章流程判"备份未确认"；重试会命中"tag 已存在"再失败，形成不可自愈的报错循环。本地模式：整份快照被删除。应改为"部分成功"语义或把 filelock 变为硬前置（doctor 已有探测） |
| P2-3 | restore 没有并发锁 | 全文 | staging 目录名带 pid+时间戳不会撞名，但两个并发恢复交错安装仍可能损坏状态；`update_state`/`rollback` 路径有 `FileLock`，恢复没有 |
| P2-4 | 大项目多次全量哈希 | `:276-303` → `:400-403` → `:407` → `:427-430` | 一次恢复对语料做约 5 遍 SHA-256（preview、manifest 复读、staging、安装、verify）。功能正确，200 万字 ≈ 数十 MB 文本 + idb/vectors 时可感知 |
| P2-5 | `--restore-snapshot` 只能整仓恢复 | `:377-441` | 不能"只恢复设定集"或按章范围恢复；长篇局部返工场景偏重 |
| P2-6 | `sha256` 只校验长度 64，未校验十六进制 | `:255-256` | 影响极小，属健壮性 |

---

## 5. 测试盲区（为什么 18 个用例全绿却漏掉两个 P0）

| 缺口 | 应该新增的用例 |
|---|---|
| dirty 只在"手工修改已跟踪文件"下测过（`test_rollback_preview_has_no_side_effects_and_dirty_tree_is_rejected`） | Git 项目写入 `.webnovel/backups/`、`run_ledger.json` 等未跟踪运行产物后，dirty 判定与恢复可用性 |
| 保留策略只测了"上限 10 份"（`test_local_backup_copies_manuscript_when_git_unavailable`） | 已有 10 份时恢复 → 断言 `safety_backup` 非空且 `safety_snapshot_*` 存在 |
| 安全快照只测了"存在且是目录" | 断言 `receipts` 中不存在悬空项；`safety_backup == ""` 时恢复必须失败 |
| 无"恢复后再恢复"用例 | 恢复 → 第二次恢复/回滚必须可按文档路径成功 |

---

## 6. 建议修复顺序

1. **P0-1**：dirty 判定限定受管范围 + 两份 `.gitignore` 补齐运行产物（收敛为单一真源）。
2. **P0-2**：安全快照独立命名、不被裁剪；`safety_backup == ""` 时 fail-closed 拒绝恢复。
3. **P1-1**：restore 收尾提交（对齐 `rollback()` 的前滚语义）。
4. **P1-2**：安全快照 receipt 独立化，悬空 receipt 显式报告。
5. **P1-3**：安全快照保留策略 + `--list` 回落本地快照 + 清理入口。
6. **P1-4**：把恢复入口落到 skill（`webnovel-backup`）与 `commands.md`，并在 `user_report` 的 `backup_unconfirmed` 建议里给出恢复命令。
7. 第 5 节 4 个回归用例随 1～4 一起进 `test_backup_manager.py`。
8. P2 项按需处理，其中 P2-1（archive 范围）与 P2-2（filelock 语义）建议在文档中显式声明边界。

---

## 7. 复现方式

```bash
# P0-1：dirty 门禁被运行产物触发
"C:/tool/Python314/python.exe" -X utf8 .workbuddy/tmp/probe_backup.py

# P0-2 + P1-2 + P1-3：安全快照与 receipt
"C:/tool/Python314/python.exe" -X utf8 .workbuddy/tmp/probe_backup3.py

# 现有测试（18 通过，不覆盖上述场景）
python -m pytest webnovel-writer/scripts/tests/test_backup_manager.py -q --no-cov
```

---

## 8. 待决项的影响面：`backup()` 的 tag 冲突（2026-09-17 实测，**当日已修复**）

> **§8.1–§8.4 记录的是修复前行为**（触发矩阵、受影响链路、下游后果、定位），原样保留作根因证据；修复方案与落地记录见 `docs/operations/chapter-tag-conflict-fix-2026-09-17.md`。§8.5 为方案候选对比（当时倾向 C，最终改选 D），§8.6 已就地标注修复后行为。

缺陷本体：`backup_manager.py:584-593`。Step 2 先 `git commit`，Step 3 再检查 `ch{NNNN}` tag；tag 已存在且不等于 `HEAD` 时打印"历史 tag 已指向其他提交"、`return False`（CLI `sys.exit(1)`）。**已提交的内容留在历史里，但没有版本点。**

### 8.1 触发矩阵（实测）

| 前置状态 | `backup --chapter N` | 实测 |
|---|---|---|
| tag `chNNNN` 不存在（新章号） | 成功 | `exit=0`，建 tag |
| tag 存在且 `HEAD == tag`，工作树干净 | 成功（不建新版本点，只重写 receipt） | `✅ Git tag 已存在: ch0001` |
| tag 存在且 `HEAD != tag`，工作树干净 | **失败** | `exit=1`，`❌ 备份失败：历史 tag ch0001 已指向其他提交` |
| tag 存在，工作树有未提交改动 | **失败 + 副作用：把改动提交成 `Chapter N: revise-outline`** | `exit=1`，`HEAD` 前进一格，改动进了历史但无 tag |

**失败是不可自愈的**：失败路径已经让 `HEAD` 越过 tag（要么本来就越过，要么本次提交造成），所以**第 3 行之后的任何重试都必然继续失败**，直到手工 `git tag -d chNNNN`。

### 8.2 受影响的链路（生产调用点只有 3 处）

| 链路 | 调用 | 影响 |
|---|---|---|
| `webnovel-chapter-revise:106-108` | `backup --chapter {被修订章号}` | **100% 硬阻断**。被修订的章必然有 `chNNNN` tag，而 `HEAD` 几乎不可能等于它 → 每次修订非最新章节都在 Step 3 停下。而 SKILL 明文"备份失败则停止，不进行任何编辑"（`:110`）→ **章纲修订功能对已备份章节整体不可用** |
| `webnovel-outline-revise:124-126` | `backup --chapter {最新章号}` | 首次需"工作树干净且 HEAD 正好停在最新章的备份提交"才通过；一旦失败（或做了第二次），失败留下的 orphan commit 使 `HEAD != tag` 永久成立 → **总纲修订后续全部被拒** |
| `webnovel-write:295-301` | `backup --chapter {本次章号}` | 新章正常；**重写/重跑同一章必然失败**（rejected 重写、`/webnovel-write N` 续跑） |

### 8.3 下游后果

- `verified_backup(N)` 对重写章返回 `{}`：`_git_matches_chapter()`（`:408-416`）拿**当前工作树**正文与 tag 提交比对，内容变了就不匹配，receipt 随即失效。第 N 章的版本点**静默地继续指向重写前的正文**。
- `run_ledger._backup_exists()`（`run_ledger.py:239-243`）恒为 False → `write-resume` 每次报 `resume_from=backup`（实测）、backup step `action=retry`、`reason=备份未确认`；重试同样失败，形成死循环。
- `user_report._backup_evidence()`（`user_report.py:469-471`）返回 `(False, .webnovel/backups)` → 最终报告"备份"显示 `unknown`，并挂 `needs_confirmation / backup_unconfirmed`（`:628-642`），`next_action` 指向的补救命令永远不会成功；项目状态被压到 `STATUS_PARTIAL`。
- 与 git 恢复口径的乘积效应：按 `README.md` 的 `git switch -c rewrite-from-chNNNN chNNNN` 回退后重写，**新分支上所有章节都建不出 tag**（实测：`--list` 看不到新版本点，`--diff` / `--create-branch` 只能看到旧线的 tag）。恢复后唯一可用的版本点仍是被 fork 的那一条，且旧线 tag 指向的内容不在当前分支上。
- `create_branch(N)`（`:677-702`）只认 tag：新线上写出的章节无法作为分叉点。
- 附带：`backup_receipts.json` 不在受管范围（`_selected_backup_paths` 名单里没有它），是**跨分支共享的未跟踪状态**；`git switch` 后 receipt 仍来自另一条线，只在 `_git_matches_chapter` 拦下时失效。

### 8.4 定位

- 删除恢复之前，这个缺陷的后果是"安全点建不出来"；删除恢复之后，git tag 成为**唯一**版本点，"建不出版本点"直接等于**失去回退能力**，所以严重度从 P1 升为 **P0**。
- 这是切除恢复时暴露而非引入的缺陷：旧代码路径与 `_working_tree_dirty` 无关，缺陷一直在 `backup()` 内。

### 8.5 修复方向（候选对比，最终采用 D）

| 方案 | 说明 | 代价 |
|---|---|---|
| A. tag 冲突时改用带时间戳的 tag | 如 `ch0031-20260917T1004`，同时放宽 `verified_backup` 的 `receipt["tag"] == chNNNN` 硬校验（`:373-376`） | 触及 receipt schema 与 `--list` / `--diff` 的章号索引；`grep` tag 的旧脚本要同步 |
| B. tag 冲突时重建 tag 指向新提交 | 语义上覆盖历史 tag | 与"不移动历史 tag"的既有承诺冲突，旧版本点丢失 |
| C. 只允许"改前备份"打独立 tag | revise 链路改用 `ch<章号>-pre-revise-<ts>`，写作链路保持 `chNNNN` 单条 | 改动面最小，但两套 tag 语义需要在文档里讲清，且**不覆盖"重写同一章号"**（写章链路的 `chNNNN` 仍冲突） |
| D. skill 层绕过 | revise 前先 `git tag -d`；写进 SKILL 的备份失败分支 | 破坏"不改历史 tag"承诺，且把复杂度推给作者 |

（当时倾向 C；**最终改选 D 的加强版**：`chNNNN` 保持唯一章节版本点语义但**允许前移**，被前移的旧点自动归档为 `chNNNN-prev-<时间戳>`。C 只覆盖 revise 链路，重写同章号仍会冲突；D 覆盖全部触发路径，且 `verified_backup` / `--diff` / `--create-branch` 的 `chNNNN` 语义零改动。落地记录见 `chapter-tag-conflict-fix-2026-09-17.md`。）

### 8.6 精确影响面：改什么会让 `verified_backup(N)` 失效（2026-09-17 实测）

`verified_backup(N)` 是**内容一致性校验**，不是"存在性检查"。它只比对两个文件（`backup_manager.py:398-416`）：

1. `正文/第N章*.md`（`find_chapter_file`）
2. `.story-system/commits/chapter_NNN.commit.json`

| 作者动作 | `verified_backup(N)` | `backup --chapter N` |
|---|---|---|
| 刚写完本章并备份 | ✅ 有效 | ✅（不新增版本点，仅重写 receipt） |
| 改**章纲**（chapter-revise 的目标文件） | ✅ **仍有效** | ~~❌ 硬停（`exit=1`）~~ → 修复后 `exit=0`，`chNNNN` 前移、旧点归档为 `chNNNN-prev-*` |
| 改**卷纲 / 节拍表 / 时间线 / 详细大纲** | ✅ **仍有效** | 不经过本模块，**无影响**（`volume-reload --backup-only` 是 `shutil.copy2` 到 `.webnovel/backups/volume_N_<ts>/`，实测 `ok:true`） |
| 改总纲 / 设定集 / `state.json` | ✅ 仍有效 | — |
| 改**正文** | ❌ 空 `{}`（改后未再备份时） | ~~❌ 硬停~~ → 修复后 `exit=0`，重跑一次即让 `chNNNN` 前移并恢复校验 |
| 改本章 `commit json`（仅写作流程会写） | ❌ 空 `{}`（同上） | 同上 |
| 继续写第 N+1 章（HEAD 前进、树干净） | ✅ **仍有效**（校验比对工作树，与 HEAD 无关） | — |

结论：**"改章纲/卷纲"不会让 `verified_backup(N)` 坏，坏的是流程的硬停；只有改正文（或重写本章）才会让校验变空。**~~变空之后在 tag 存在期间不可修复；唯一修复是 `git tag -d chNNNN` 后重跑 `backup --chapter N`（见 §9.4，实测有效）。~~ **（修复后已不适用）**：改正文后重跑一次 `backup --chapter N` 即可，tag 前移、旧点自动归档，不需要任何手工 git 操作。

### 8.7 复现方式

```bash
"C:/tool/Python314/python.exe" -X utf8 .workbuddy/tmp/probe_tag_conflict.py      # 场景 A/B/C
"C:/tool/Python314/python.exe" -X utf8 .workbuddy/tmp/probe_tag_matrix.py        # 触发矩阵 4 行
"C:/tool/Python314/python.exe" -X utf8 .workbuddy/tmp/probe_verify_matrix.py     # §8.6 失效条件矩阵
"C:/tool/Python314/python.exe" -X utf8 .workbuddy/tmp/probe_body_edit.py         # §9 改正文后果与修复路径
"C:/tool/Python314/python.exe" -X utf8 .workbuddy/tmp/probe_tag_fix.py           # 修复后回归（7 场景，输出 .md）
```

> 前四个复现的是**修复前**的行为，现在跑出来的结果会与本文记录不同（这正是修复生效的证据）；修复后的期望行为以 `probe_tag_fix.py` 与 `tests/test_backup_manager.py` 为准。

---

## 9. 读侧：改正文后 `verified_backup(N)` 变空的后果（2026-09-17 实测）

前提：`verified_backup()` 是**内容一致性校验**。改过第 N 章正文之后、且没有成功建出新版本点之前，它对第 N 章恒返回 `{}`。全仓只有 **2 个生产消费方**（`run_ledger.py:239-242`、`user_report.py:467-478`），所以后果是**可枚举的、且都不是内容安全**。

### 9.1 实测到的三个后果

| 消费方 | 变空后的输出 |
|---|---|
| `run_ledger._backup_exists()` | `False` → `write-resume` 的 backup step 由 `skip / 备份已确认` 变为 **`retry / 备份未确认`**（`resume_from` 随之指向 backup） |
| `user_report._backup_evidence()` | `(False, '.webnovel/backups')` → 报告"备份"项 `status=unknown`、note"未找到可确认的备份记录" |
| `user_report` 追加 issue | 本章 commit `status=accepted` 时挂 `needs_confirmation / backup_unconfirmed`：impact"本章事实已生成，但回滚保障需要再确认"、next_action"运行备份命令或重新执行写章收尾步骤"、command `/webnovel-write {N}` → `_status_from_issues` ⇒ **总状态降为"部分完成"** |

### 9.2 不会发生的事（避免夸大）

- **不阻断写作**：写章充分性闸门 7 条不含备份；`write-gate` 的 prewrite / precommit / postcommit 都不读备份（全 `data_modules` grep 无命中）。第 N+1 章能正常写。
- **不删除、不覆盖任何内容**。
- **改前状态仍可回退**：tag `chNNNN` 还在，指向改前正文，`git switch -c rewind chNNNN` 依然成立。丢的是"**改后**正文的版本点"，不是历史。
- 这不一定是"假警报"：若只手工改正文而没跑过任何备份命令，新正文**确实没有任何版本点**，报告如实反映。

### 9.3 真正实质的风险：新正文可能只存在于工作区

以"写完第 N 章（已备份）→ 手工改正文 → 不跑任何命令"为例，实测：

```
verified_backup(1)      : {}  ← 空
_backup_evidence(1)     : (False, '.webnovel/backups')
git status --porcelain  : ['M 正文/第0001章-a.md']        ← 改动未提交
git show ch0001:正文/…   : 正文 v1                        ← tag 仍是旧正文
当前正文是否已进 git 历史 : 否 —— 只在工作区
```

也就是说：**此时"当前第 N 章正文"既未提交、也无版本点**，只存在于工作区。任何 `git checkout -- .` / `git restore` / 切分支 / `git stash` 都可能把它丢掉，而插件侧报告已经明说"未找到可确认的备份记录"。
（若改完跑过一次 `backup --chapter N`，Step 2 会先提交再失败，新正文至少进了历史，只是没有 tag 索引——见 §8.1 第 4 行。**修复后此中间态消失**：备份直接成功并让 `chNNNN` 前移，不再出现"提交了但没有版本点"。）

**注：本节描述的是修复前的可观测后果，对当前实现已不适用**——改完正文重跑一次 `backup --chapter N` 即可让 `verified_backup(N)` 恢复有效（见 §9.4）。保留此节是为了说明"备份缺失"在报告链路里的真实影响面。

补充：标准善后 `/webnovel-chapter-reload N` **不会**补出版本点。它的备份是本地副本 `.webnovel/backups/chapter_N_<uuid>/`（`chapter_reloading.py:270-286`，含正文），但 `verified_backup` 的 snapshot 分支只 glob `snapshot_ch{NNNN}_*`，认不出 `chapter_N_*` → 校验仍为空。

### 9.4 恢复校验（修复后：由 `backup` 内置，无需手工操作）

修复后不需要任何手工 git 序列。改完正文重跑一次备份即可：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" backup --chapter 1
```

实测（`probe_tag_fix.py` 场景 C，`exit=0`）：`ch0001` 前移到包含改后正文的新提交，改前正文自动保留为 `ch0001-prev-<时间戳>`，`verified_backup(1)` 重新有效、`write-resume` 回到 `skip / 备份已确认`。手工改正文（未提交）与重写本章（已提交但无 tag）两种场景都通。

~~旧做法：`git tag ch0001-pre-revise ch0001` → `git tag -d ch0001` → 重跑 `backup`。~~ **已作废，不要再执行**：它会在中间窗口让该章短暂没有任何版本点，而新实现是"先归档、再前移"，不存在这个窗口。旧实测记录保留在本文档历史版本与 `probe_tag_rename.py` 中。

对下游的影响：`run_ledger._backup_exists` 与前缀报告不再长期挂 `backup_unconfirmed`；`git switch -c rewrite-from-chNNNN chNNNN` 回退后重写，新分支上的章节也能正常建出版本点（`probe_tag_fix.py` 场景 D）。
