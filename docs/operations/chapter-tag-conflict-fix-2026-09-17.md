# 章号 tag 冲突修复方案与实施记录

日期：2026-09-17
状态：**已实施并通过实测**
缺陷来源：`docs/operations/snapshot-restore-assessment-2026-09-17.md` §8（P0）、§9

---

## 1. 问题

`backup --chapter N` 在「`chNNNN` tag 已存在且 `HEAD` 不等于该 tag」时直接失败：

```
❌ 备份失败：历史 tag ch0001 已指向其他提交     # 旧实现，exit=1
```

失败前 Step 2 已经把改动 `git commit` 进历史，因此产物是**一个没有版本点的提交**；且失败路径必然让 `HEAD` 越过 tag，**重试不会自愈**，只能手工 `git tag -d chNNNN`。

移除恢复能力后 `chNNNN` 成为唯一版本点，后果从"安全点建不出来"升级为"回退能力在修订/重写场景下消失"：

| 链路 | 修复前行为 |
|---|---|
| `webnovel-chapter-revise` Step 3（用被修订章号） | 100% 硬阻断。SKILL 明文"备份失败则停止，不进行任何编辑" → 章纲修订对已备份章整体不可用 |
| `webnovel-outline-revise` Step 3（用最新章号） | 首次失败后留下 orphan commit，`HEAD != tag` 永久成立 → 后续全部被拒 |
| `webnovel-write` 重写/重跑同章号 | 必失败 |
| `verified_backup(N)` | 返回 `{}` → `run_ledger` 恒 `resume_from=backup`、报告挂 `backup_unconfirmed`、项目状态压到 `PARTIAL` |
| 按文档口径 `git switch -c rewrite-from-chNNNN chNNNN` 回退后重写 | 新分支上所有章号都建不出 tag |

## 2. 根因

`chNNNN` 一个名字承担了两种互斥语义：

1. **该章"当前"版本点的索引**（`verified_backup:368-396`、`diff`、`create_branch` 都硬编码 `ch{num:04d}`）
2. **不可变的历史点**（旧实现的注释："create the tag without moving an existing historical tag"）

第一次备份之后，任何"同章再备份"都同时要求"索引更新"和"历史不动"，于是只能用失败来保住第 2 条。**根因不是判断写错了，是命名空间不够用。**

## 3. 方案选型

| 方案 | 做法 | 结论 |
|---|---|---|
| A. 冲突时让 skill 先 `git tag -d` | 把复杂度推给作者与 SKILL | ❌ 作者必须懂 git；`chapter-revise` 的"备份失败则停止"直接变成"先删 tag 再备份"，且删完的旧点没有名字，仍会丢 |
| B. 直接 `git tag -f chNNNN` | 前移，不保留旧点 | ❌ 旧版本点被静默覆盖，等于删历史 |
| C. 修订安全点独立命名（`chNNNN-pre-revise`） | 只解决 revise | ❌ 不覆盖"重写同一章号"，`chNNNN` 仍会冲突 |
| **D. 分层命名：`chNNNN` 前移 + `chNNNN-prev-<ts>` 归档（选定）** | `chNNNN` = 该章最新已备份状态（可前移）；旧点自动另存为不可变 tag | ✅ 语义自洽；所有硬编码 `chNNNN` 的消费方零改动；历史不丢 |

选 D 的关键理由：**`chNNNN` 的现有消费方全部只需要"该章最新版本点"这个语义**（`verified_backup`、`diff`、`create_branch`、文档里的 `git switch -c rewrite-from-chNNNN`），把它们逐个改成"多版本列举"会破坏既有口径；把"不可变"这一半拆到新命名空间，改动面最小且向后兼容。

## 4. 实现清单

`webnovel-writer/scripts/backup_manager.py` 770 → 833 行。

| 改动 | 位置 | 内容 |
|---|---|---|
| 新增常量 | `:87` | `_CHAPTER_TAG_PATTERN = re.compile(r"^ch(\d{4})$")`：只有严格 `chNNNN` 才算章节版本点 |
| 新增方法 | `:464-483` | `_archive_tag(tag_name, commit)`：把旧点另存为 `<tag>-prev-<YYYYmmddTHHMMSS>`；同秒冲突自动追加 `-2`、`-3`；只新增 tag，不动提交；失败返回 `None` |
| 重写 Step 3 | `:612-660` | 三态：① HEAD == tag → 只刷新 receipt（幂等）；② tag 存在且 HEAD 不同 → 先归档、再 `git tag -f` 前移；③ tag 不存在 → 直接创建。归档失败则**不前移**并报失败，保证不出现"旧点已丢、新点没建" |
| 重写 `list_backups` | `:689-725` | 按 `_CHAPTER_TAG_PATTERN` 分流：当前版本点主行显示，历史点以 `↳ 历史点 …` 缩进列在其下；合计"N 个章节版本点，M 个历史点" |
| 修正 docstring | `:9-49` | 补 tag 分层语义；把此前残留的 `git checkout <tag> -- 正文 大纲 设定集` 错误示例改成 `git switch -c <分支> <tag>` |
| 新增 `import re` | `:55` | 供严格解析使用 |

**未改动**（有意为之）：`verified_backup` 的校验逻辑（`:368-396`）保持不变——它校验 `receipt["tag"] == chNNNN` 且 tag 提交与工作树内容一致；前移时 receipt 同步重写，因此校验继续成立，防篡改语义没有被削弱（见 §5 场景 G）。`diff`、`create_branch`、`_local_backup`、保留策略均未改动。

## 5. 验证

### 5.1 单元测试

`webnovel-writer/scripts/tests/test_backup_manager.py` 199 → 303 行，8 → **12 个用例全绿**（`-q --no-cov -p no:cacheprovider`）。

新增 4 个（`:204`、`:234`、`:250`、`:283`）：

| 用例 | 断言 |
|---|---|
| `test_backup_moves_chapter_tag_and_archives_previous_point` | `chNNNN` 前移到新提交、旧提交被 `chNNNN-prev-*` 指向且 `cat-file -e` 可达、receipt 指向新提交 |
| `test_backup_same_commit_is_idempotent_without_archiving` | HEAD 未变时重复备份不产生历史点 |
| `test_backup_after_git_switch_rewrite_keeps_chapters_tagged` | 复现"`git switch` 回退后重写"：第 1、2 章都能建出新版本点，`verified_backup(1)/(2)` 均有效，旧第 2 章版本点被归档 |
| `test_list_backups_separates_current_and_archived_points` | `--list` 不因 `chNNNN-prev-*` 崩溃，输出含"历史点"与合计行 |

既有 `test_git_receipt_rejects_moved_tag`（手工 `git tag -f` 篡改）仍然通过 → 防篡改未被削弱。

### 5.2 端到端实测（`.workbuddy/tmp/probe_tag_fix.py` → `probe_tag_fix.out.md`）

| 场景 | 修复前 | 修复后（实测输出） |
|---|---|---|
| A 首建版本点 | exit 0 | `✅ Git tag 已创建: ch0001`，`verified_backup(1)=True` |
| B 改章纲后重备份同章（chapter-revise） | **exit 1** | `✅ Git tag 已前移: ch0001（旧版本点保留为 ch0001-prev-20260917T105258）` |
| C 重写本章正文后重备份 | **exit 1** | exit 0，前移 + 归档，`tag_commit == HEAD` |
| D `git switch -c rewrite-from-ch0001 ch0001` 后重写第 1、2 章 | **双双 exit 1** | 两次都 exit 0；`verified_backup(1)/(2)` 均 True；`ch0002-prev-*` 保留原主线提交 |
| E 同一章连续重写 3 次 | 全部失败 | 7 个历史点全部 `cat-file -e` 可达，内容分别为 `v1, v1, v2, v3, v4, v5`（与操作序列逐一对应） |
| F `--list` | — | exit 0，主行 + `↳ 历史点` 缩进 + `总计：2 个章节版本点，7 个历史点` |
| G 外部 `git tag -f ch0002 HEAD` | 返回空 | 仍返回空 ✅（防篡改保持） |

复现命令：

```bash
"C:/tool/Python314/python.exe" -X utf8 .workbuddy/tmp/probe_tag_fix.py
"C:/tool/Python314/python.exe" -m pytest webnovel-writer/scripts/tests/test_backup_manager.py -q --no-cov -p no:cacheprovider
```

## 6. 语义与兼容性

**不变的**（作者与 skill 无需改任何命令）：

- `backup --chapter N` 首次备份行为、退出码、receipt 格式（`backup-receipt/v1`）
- `verified_backup(N)`：仍要求 receipt 的 tag 是 `chNNNN` 且该 tag 提交里的正文/commit json 与工作树一致
- `--diff A B`、`--create-branch N`：仍取 `chNNNN`（= 该章最新版本点）
- 文档口径 `git switch -c rewrite-from-chNNNN chNNNN` 不变，且现在**回退后重写不再需要任何手工清理**
- `webnovel-chapter-revise` / `webnovel-outline-revise` 的"备份失败则停止"保持不变——因为备份不再失败

**新增的**：

- `chNNNN-prev-<时间戳>` 只增不改，是真正不可变的历史点；`git switch -c <分支> chNNNN-prev-<时间戳>` 可回到任意被前移的状态
- `--list` 输出分两段，末行给出合计

**需要作者知道的一条**：`chNNNN` 会移动。如果项目已经 push 到远端并在别的机器上 clone 过，远端 tag 不会自动跟着前移；本插件自身不执行 push，跨机器场景需自行 `git push --tags --force`（或改用 `chNNNN-prev-*` 作长期引用）。这是"索引型 tag"的固有代价，也是唯一的行为倒退点。

## 7. 未覆盖 / 后续

1. **`backup_receipts.json` 不在受管范围**（`_selected_backup_paths:98-111` 名单不含它）→ 它跨分支共享，切分支后 receipt 可能来自另一条线；`verified_backup` 因此在跨分支场景会返回空（保守，不产生错误结论）。若要修，需在 receipt 里记录分支 + 提交，并在校验时降级为 tag-only。
2. **`webnovel-chapter-reload` 的本地副本命名**（`chapter_N_<uuid>`）不被 `verified_backup` 的 snapshot 分支识别（只认 `snapshot_chNNNN_*`）。
3. **无 Git 模式的快照只保留最近 10 份**，恢复已整体移除，快照仅作离线副本，保留策略维持原样。
4. `CHANGELOG.md` 未加条目——`validate_release_notes.py` 与 `.github/workflows/plugin-version.yml` 要求覆盖上个 tag 且含当前版本，需先定版本号再走发版流程。
