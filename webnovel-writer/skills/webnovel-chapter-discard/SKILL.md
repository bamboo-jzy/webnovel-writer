---
name: webnovel-chapter-discard
description: 抛弃一章正文。未提交草稿直接删除并归档可恢复；已提交章原地回退到上一章版本点，不新建分支。只处理最后一章。
allowed-tools: Read Write Grep Bash AskUserQuestion
argument-hint: "[章号]"
---

# 抛弃章节正文

## 边界

- **只允许抛弃最后一章**。中间章会让后续章失去前置章，必须连带回退或重写；发现 `downstream_chapters_exist` 一律停下，先与作者确认是回退到该章之前（放弃后面所有章）还是改走修改路线。
- **未提交草稿**（该章没有 accepted commit）：正文、四份临时 artifacts、非 accepted commit、审查报告都还在「本章草稿」范畴内，可以直接删除；删除前整批归档到 `.webnovel/discarded/chapter_NNN_<时间戳>/`，归档目录保持项目内相对路径，可原样复制回项目根恢复。
- **已提交章**（accepted）**不做外科手术式删除**。`state.json` 的 `plot_threads` / `strand_tracker` / `protagonist_state` / `world_settings` 是累计字段，`index.db` 的 `entities.first_appearance` / `last_appearance` 明确不随撤回回退——只删正文与投影行会留下一本「读模型说没写过、状态却记得写过」的书。已提交章一律走**原地版本点回退**。
- **已提交章的原地回退不新建分支**：先把正文与本章 commit 归档到 `.webnovel/discarded/`，再用 `git read-tree -u --reset {目标版本点}` 把工作树与索引恢复成版本点内容，最后在**当前分支**追加一次 `Discard chapter N: restore to <版本点>` 提交。分支名不变，不需要切回，也不会卡在同名分支上（同一章可以反复抛弃/重写）。
- 原地回退会同时还原 `正文/`、`大纲/`、`设定集/`、`文风/`、`.story-system/`（含 commits）与 `.webnovel/` 状态文件（含 `index.db`、`summary`、`projection_log`）。**未跟踪文件不受影响**（归档目录因此得以保留）。所有受跟踪文件都会回到版本点内容，作者自己额外跟踪的文件也一样。
- 回退**不新建分支、不删除任何提交、不移动版本点 tag**：被抛弃的提交会成为新提交的父提交，仍留在当前分支历史里，tag `ch{N}` 也仍指向它，随时可取回。
- **章号 off-by-one**：`chNNNN` 的语义是「第 N 章**完成后**」。抛弃第 N 章要回退到 `ch{N-1}`，不是 `chN`。第 1 章没有 `ch0000`，回退目标是仓库初始提交。
- 章纲 `大纲/第N章-*.md` 与设定集**不删**：作者通常要用同一份章纲重写这一章。
- 回退要求工作树干净（受管文件没有未提交改动）。有改动时先请作者提交或 stash，不得代替作者决定丢弃。
- 抛弃 ≠ 修改 ≠ 重跑投影。只想改正文内容用 `/webnovel-chapter-reload`；只想重跑投影用 `projections retry --chapter N`；只删正文不要章纲时才用本 Skill。
- 回退的取回路径有三条（都告诉作者）：`git show {被抛弃提交}:正文/第N章-*.md`、tag `ch{N}`、以及 `.webnovel/discarded/chapter_NNN_<时间戳>/` 下的归档副本。


## 0. 准备变量

```bash
export WORKSPACE_ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
export SCRIPTS_DIR="${CLAUDE_PLUGIN_ROOT:?}/scripts"
export PROJECT_ROOT="$(python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${WORKSPACE_ROOT}" where)"
```

## 1. 预览并确认

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" chapter-discard \
  --chapter {chapter_num} --dry-run --format json
```

向作者展示：`plan.classification`（`draft` / `accepted` / `absent`）、正文路径、`plan.downstream_chapters`、`plan.planned_actions`；已提交章还要展示 `plan.rollback.command`、`plan.rollback.commit_message`、`plan.rollback.recovery_hint` 与将被还原的目录清单，并明确说明**本次不会新建分支**。

用 `AskUserQuestion` 确认后再执行，并明确问清「抛弃后本章章纲保留，是要重写这一章吗」。

`plan.blockers` 非空一律停下并原样报告 `code` 与 `message`，不得绕过、不得手工删文件绕开检查：

- `chapter_absent`：没有可抛弃的内容；
- `body_candidates_ambiguous`：`正文/` 下有多个同章候选文件，不任选其一；
- `downstream_chapters_exist`：不是最后一章；
- `chapter_committed`（`draft` 阻塞项）：已提交章不能用 `--draft`；
- `chapter_not_committed`（`rollback` 阻塞项）：草稿不需要回退；
- `version_point_missing`：回退目标版本点不存在，先补齐再回退；
- `version_point_not_ancestor`：版本点不在当前分支历史上，原地恢复会把两段历史混在一起 → 转人工；
- `working_tree_dirty`：先提交或 stash；
- `git_unavailable` / `initial_commit_not_unique`：本次无法安全回退，转人工。

## 2. 未提交草稿：直接删除

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" chapter-discard \
  --chapter {chapter_num} --draft --reason "{作者为什么抛弃}" --format json
```

CLI 会依次：归档正文 / 非 accepted commit / 属于本章的临时 artifacts / 审查报告 → 删除这些文件 → 清理 `state.json` 的 `chapter_revisions`、`chapter_status`、`chapter_meta`、`review_checkpoints`、`disambiguation_*`、`chapters_planned` 章状态（复位为 `planned`）→ 重算 `current_chapter` 与 `total_words` → 清理 `run_ledger.json` 的章断点。

`ok=false` 时报告 `error`，并说明归档目录里已有副本、`removed` 与 `kept` 的差异；`warnings` 里出现 `run_ledger 未清理` 属派生缓存问题，不阻断抛弃，如实告知即可。

## 3. 已提交章：原地版本点回退（不新建分支）

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" chapter-discard \
  --chapter {chapter_num} --rollback --reason "{作者为什么整章作废}" --format json
```

CLI 只在预览的全部检查通过后执行，依次是：归档正文与本章 commit 到 `.webnovel/discarded/chapter_NNN_<时间戳>/` → `git read-tree -u --reset {目标版本点}` → 在当前分支提交一次 `Discard chapter N: restore to <版本点>`（工作树已等于版本点则不产生空提交）。**不新建分支、不移动既有引用、不删除提交。**

回退后核对三项：`tree_matches_target`（新提交的树 == 版本点）、`chapter_body_absent`、`current_chapter_after`。任一项不符会返回 `ok=false`，此时**停下并让作者手工 `git status` 确认**，不要自行追加 git 命令。

回退后建议核对一次项目健康度：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" doctor --format json
```

`doctor.index.commit_sync` 若仍报 blocker，用 `projections replay` 按其给出的命令修复。

## 4. 收尾

报告实际动作、归档目录、`current_chapter` 变化、章纲是否保留、分支名未变（**没有新建分支**）、以及「下一章仍是第 N 章、可直接用 `/webnovel-write` 重写」，不输出 traceback 或长日志。

被抛弃的正文有三条取回路径：`git show {被抛弃提交的短号}:正文/第N章-*.md`、tag `ch{N}`、归档目录。不要为了让工作树干净而手工删除 `正文/` 或 `.story-system/commits/` 里的文件。
