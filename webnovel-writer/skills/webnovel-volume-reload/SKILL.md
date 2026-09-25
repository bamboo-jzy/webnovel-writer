---
name: webnovel-volume-reload
description: 人工编辑卷纲后重新计算内容 revision，并安全标记依赖旧卷纲的章纲为 stale；不修改已存在的章纲与正文。
allowed-tools: Read Bash
argument-hint: "[卷号]"
---

# 卷纲重载

主 agent 职责：在作者手动编辑卷纲文件后，检查三份卷级产物并调用统一 CLI 重载 revision。只登记卷纲状态和下游失效，不修改卷纲、独立章纲、章级合同或正文。已被独立章纲覆盖的卷级内容是既定事实——正文经由章纲与卷纲关联，章纲锁定即意味着正文同样不动。

## 用法

```text
/webnovel-volume-reload 1
```

## 硬规则

1. 只处理作者已经编辑完成的卷纲，不替作者修改任何卷级文件。
2. 重载前检查 `大纲/第N卷-节拍表.md`、`大纲/第N卷-时间线.md`、`大纲/第N卷-详细大纲.md` 均存在且非空；缺失或为空立即阻断。
3. revision 基于三份文件的文件名和内容 SHA-256，不使用 mtime 作为唯一依据。
4. revision 变化时，只将依赖旧 `source_volume_revision` 的 `chapters_planned` 条目标记为 `stale`；这是状态层标记，不得借此删除或改写任何已存在的独立章纲与正文。
5. **已完成章节是既定事实**：`protected_chapters` 里的章（已有章纲或正文）本次零动作，章纲演进只能走 `/webnovel-chapter-revise {chapter_num}`；只有 `open_chapters`（尚无章纲）才指向 `/webnovel-chapter-plan`。
6. **只允许重载最后一卷的卷纲**（方向透传「总-卷」节点，依据 `${SKILL_ROOT}/../../references/handoff/direction-handoff.md`）：非最后一卷是既定事实，重载诉求一律阻断并说明；总纲也必须与那些卷的卷纲保持同向。
7. **方向检查必做**：重载后必须确认新卷纲与总纲同向。报出候选偏离时按方向透传的确认话术与作者裁决，用 `handoff --record` 落盘；裁决为「改总纲」时停止本 Skill，改走 `/webnovel-outline-revise`。
8. 不写入 `大纲/第{章}章-*.md`、`正文/**`、`.story-system/commits/**`，不自动刷新章级 Story System 合同。
9. 重载失败或状态写入失败时，不报告为完成；保留已有状态和备份证据。

## 执行流程

先解析并确认项目根目录：

```bash
export WORKSPACE_ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
export SKILL_ROOT="${CLAUDE_PLUGIN_ROOT}/skills/webnovel-volume-reload"
export SCRIPTS_DIR="${CLAUDE_PLUGIN_ROOT}/scripts"
export PROJECT_ROOT="$(python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${WORKSPACE_ROOT}" where)"
```

读取目标卷的三份文件和 `.webnovel/state.json`，向作者说明当前卷纲已由人工编辑，重载可能使依赖章节过期。

**先做卷粒度边界与总-卷方向检查**（依据 `${SKILL_ROOT}/../../references/handoff/direction-handoff.md`）：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" handoff \
  --node master_to_volume --check --target {volume_id} --format json
```

- `target_allowed=false`（该卷不是最后一卷）→ 阻断，不执行重载，并说明该卷是既定事实。
- `candidates` / `manual_items` 非空 → 按确认话术与作者逐项裁决，用
  `handoff --record --node master_to_volume --target {volume_id}` 落盘。
- 裁决为 `align_upstream`（要改总纲）→ **停止本 Skill**，改走 `/webnovel-outline-revise`，
  总纲定稿后再重跑本 Skill。

需要预览时先运行：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" volume-reload \
  --volume {volume_id} --dry-run --format json
```

确认文件完整且预览无错误后执行：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" volume-reload \
  --volume {volume_id} --source manual_reload --format text
```

如果需要在重载前单独保存卷纲和状态，先执行：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" volume-reload \
  --volume {volume_id} --backup-only --format text
```

## 收尾

重载成功后报告当前 revision、上一 revision、锁定区（`protected_chapters`，已有章纲或正文，本次未修改）与可改区（`open_chapters`，尚无章纲），并输出作用域声明。下一步按分流给，**不得把锁定章塞进 `chapter-plan` 的建议范围**：

```text
可改区：/webnovel-chapter-plan {volume_id} {open_range}
锁定区需调整：/webnovel-chapter-revise {chapter_num}
```

报告必须包含方向透传结论：

```text
方向检查（总-卷）
- 可改单元：第 {volume_id} 卷（最后一卷）。
- 既定事实：第 {a}-{b} 卷（本次未修改）。
- 机器候选偏离：{逐条，无则写「无」}。
- 裁决结果：{align_downstream / align_upstream / accepted_deviation}。
```

重载后重跑一次 `handoff --node master_to_volume --check --target {volume_id}` 复核，确认候选偏离未新增。

只有目标章章纲与章级合同均存在且未过期，才可运行：

```text
/webnovel-write {chapter_num}
```

不输出原始 JSON、traceback 或长日志；故障排查只提示 `.webnovel/logs/run_last.log` 或 `/webnovel-doctor`。
