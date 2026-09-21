---
name: webnovel-chapter-reload
description: 人工修改正文后备份并重载内容版本，重新审查、提取事实和校验，确认后安全提交。
allowed-tools: Read Write Grep Bash Agent AskUserQuestion
argument-hint: "[章号]"
---

# 正文重载与校验

## 边界

- 不修改、不覆盖人工正文、章纲或合同。作者要修改正文时先停下，由作者处理后重新重载。
- 正文必须唯一、非空、可读；多个候选文件不得任选其一。
- SHA-256 基于正文内容，不依赖 mtime。`draft_revision`、`validated_revision`、`committed_revision` 分别表示初稿、最近校验和最近提交；没有历史 hash 的旧章视为未验证，不能反推手改前原文。
- 备份保存的是当前人工稿；旧正文只有已存在的历史备份才能恢复。
- 重载成功不等于校验通过，校验通过不等于提交成功。
- 四份 artifacts 必须来自同一个 `validation_input`。不得给旧结果补盖当前 hash 或 validation_id；任何输入变化均需重载并重跑 reviewer/data-agent。
- 已 accepted 章的旧事实不能直接覆盖。只有重新提取的完整 extraction（除 source 外）与旧版完全相同、旧投影完成且作者确认时，才支持保留历史后复用投影。出现 `revision_projection_unsafe` 必须停下默认流程、请作者裁决：默认拒绝是**刻意的**（增量投影撤不掉旧事实），唯一合法通道是作者显式授权 `--allow-fact-revision`，由系统先撤回该章派生读模型（index 行 / 向量分块 / `story_events` 镜像）再整章重建；不得通过普通 retry/replay、改状态或删除旧 commit 绕过。

## 1. 预览并确认

```bash
export WORKSPACE_ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
export SCRIPTS_DIR="${CLAUDE_PLUGIN_ROOT:?}/scripts"
export PROJECT_ROOT="$(python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${WORKSPACE_ROOT}" where)"

python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" chapter-reload \
  --chapter {chapter_num} --dry-run --format json
```

向作者展示正文路径、当前/上一 revision、previous_commit、下游 stale 章节和上述投影限制。作者确认后才执行重载与校验。预览失败立即停止；合同缺失或规划过期先修复，不从正文摘要猜测本章要求。

## 2. 备份并登记输入

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" chapter-reload \
  --chapter {chapter_num} --source manual_reload --format json
```

CLI 自动备份正文、state、合同、旧 commit 和四份临时 artifacts，返回本次 `validation_input`。如只需独立备份可用 `--backup-only`，它不改变登记或校验状态。保存返回的 `chapter_file` 为 CHAPTER_FILE，并向下面两个 agent 原样传入 `validation_input`；重复重载相同输入可恢复原批次，不能据此复用未绑定的旧结果。

## 3. 重新审查与提取

先读取当前正文、真实章纲、四份 Story System 合同，以及该章之前的 accepted commits。历史章的聚合 state/index/记忆可能含旧章及后续事实，只能作辅助线索，不能当作当时的前置状态。

必须使用 `Agent` 工具调用 `webnovel-writer:reviewer`，提供 chapter、chapter_file、project_root、scripts_dir、validation_input，要求完整五维审查、不跳过。reviewer 只返回 JSON，主流程用 `Write` 原样保存至 `.webnovel/tmp/review_results.json`，不得代写通过结论。

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" review-pipeline \
  --chapter {chapter_num} \
  --review-results "${PROJECT_ROOT}/.webnovel/tmp/review_results.json" \
  --report-file "审查报告/第{chapter_num}章审查报告.md"
```

有 blocking issue 就停止并报告。作者修正文后重新重载、重新审查；不把修改前审查沿用到修改后正文。

随后必须使用 `Agent` 工具调用 `webnovel-writer:data-agent`，传入相同的 chapter、chapter_file、project_root、scripts_dir、validation_input。data-agent 是 fulfillment_result.json、disambiguation_result.json、extraction_result.json 的唯一写入者，主流程不得补写或改写。三份文件均写入 `.webnovel/tmp/` 并携带顶层 source。

## 4. 校验和提交前门禁

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" chapter-reload \
  --chapter {chapter_num} --validate --format json
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" \
  write-gate --chapter {chapter_num} --stage precommit --format json
```

任一失败均停止；不得登记已通过。`--validate` 只做版本、schema 和阻断项校验，不会替代 agent 的语义审查。若后续正文、合同或 artifacts 被修改，提交入口仍会拒绝。

## 4A. 履约对账与作者裁决

如果 `fulfillment_result` 含 `missed`、`partial`、`contradicted` 或 `not_applicable` 节点，先预览当前对账输入：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" chapter-reload \
  --chapter {chapter_num} --reconcile --dry-run --format json
```

作者只能在核对当前正文、章纲、合同、影响章节和 revision evidence 后，明确记录 `accepted_deviation`、`outline_to_body` 或 `body_to_outline`。记录裁决时必须传入预览返回的 `input_token`：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" chapter-reload \
  --chapter {chapter_num} --reconcile \
  --decision accepted_deviation --reason "{作者理由}" \
  --impact {affected_chapters} --expected-input "{input_token}" --format json
```

这一步只记录独立裁决，不修改正文或章纲；输入、artifact 或 revision 变化后 token 立即失效，必须重新预览。未完成裁决时保持 `needs_reconcile` / `blocked`，不能进入 accepted commit。

## 5. 作者确认后提交

展示新旧 extraction 差异、下游影响和当前校验结论，使用 `AskUserQuestion` 确认提交或仅保留校验结果。不得因用户同意重载就推断同意提交。
没有旧 commit 时省略 `--expected-previous`；有旧 commit 时使用预览返回、经作者确认的 previous_commit identity，不能在冲突后静默替换为新 identity。

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" chapter-commit \
  --chapter {chapter_num} --expected-previous "{previous_commit}" \
  --review-result "${PROJECT_ROOT}/.webnovel/tmp/review_results.json" \
  --fulfillment-result "${PROJECT_ROOT}/.webnovel/tmp/fulfillment_result.json" \
  --disambiguation-result "${PROJECT_ROOT}/.webnovel/tmp/disambiguation_result.json" \
  --extraction-result "${PROJECT_ROOT}/.webnovel/tmp/extraction_result.json"
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" \
  write-gate --chapter {chapter_num} --stage postcommit --format json
```

旧 commit 归档到 `.story-system/commits/history/chapter_NNN/revision_<identity>.commit.json`，保留旧投影状态；同版投影重试不产生新业务 revision。

### 作者确认要改写已 accepted 的事实

事实有变、或上一轮投影未全绿时，提交会以 `revision_projection_unsafe` 被拒（错误信息会带上当前 commit identity，可直接复制）。这是默认安全行为，不是故障。作者看过新旧 extraction 差异并**明确要求改写**后，用同一个 identity 重跑提交并显式授权：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" chapter-commit \
  --chapter {chapter_num} --expected-previous "{previous_commit}" \
  --allow-fact-revision --revision-reason "{作者为什么改写}" \
  --review-result "${PROJECT_ROOT}/.webnovel/tmp/review_results.json" \
  --fulfillment-result "${PROJECT_ROOT}/.webnovel/tmp/fulfillment_result.json" \
  --disambiguation-result "${PROJECT_ROOT}/.webnovel/tmp/disambiguation_result.json" \
  --extraction-result "${PROJECT_ROOT}/.webnovel/tmp/extraction_result.json"
```

这次提交先撤回该章派生读模型（`index.db` 的 chapters / scenes / appearances / state_changes / relationships、`vectors.db` 分块、`story_events` 镜像）再整章重建，旧 commit 进 history，`provenance.retract_required` / `retract_reason` 留痕。撤回只动读模型，不碰正文、commit 和事件 JSON。重建仍需通过 5.3 的 postcommit 五项检查；失败时补跑 `projections retry --chapter {chapter_num}`（该 commit 会一直带 `retract_required`，所以每次重跑都是"先撤回再重建"，可安全重复）。

投影半途写坏、被旧行挡住重建时，补跑入口是 `projections retry --chapter {chapter_num} --retract`（`--retract` 强制撤回该章派生行再重放）。

## 收尾

报告正文路径、备份路径、校验状态、是否实际提交、history 和尚未处理的下游章节，不输出 traceback 或长日志。下游 `previous_chapter_revision_changed` 必须按章顺序核对章纲/合同；已有正文先运行本 Skill，不能自动改写。下游无正文时须先核对并重新登记章纲合同。校验完成不能自动消除下游 stale。
