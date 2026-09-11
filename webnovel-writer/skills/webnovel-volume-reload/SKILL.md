---
name: webnovel-volume-reload
description: 人工编辑卷纲后重新计算内容 revision，并安全标记依赖旧卷纲的章纲为 stale。
allowed-tools: Read Bash
argument-hint: "[卷号]"
---

# 卷纲重载

主 agent 职责：在作者手动编辑卷纲文件后，检查三份卷级产物并调用统一 CLI 重载 revision。只登记卷纲状态和下游失效，不修改卷纲、独立章纲、章级合同或正文。

## 用法

```text
/webnovel-volume-reload 1
```

## 硬规则

1. 只处理作者已经编辑完成的卷纲，不替作者修改任何卷级文件。
2. 重载前检查 `大纲/第N卷-节拍表.md`、`大纲/第N卷-时间线.md`、`大纲/第N卷-详细大纲.md` 均存在且非空；缺失或为空立即阻断。
3. revision 基于三份文件的文件名和内容 SHA-256，不使用 mtime 作为唯一依据。
4. revision 变化时，只将依赖旧 `source_volume_revision` 的 `chapters_planned` 条目标记为 `stale`；不删除、不自动覆盖作者已有独立章纲。
5. 不自动刷新章级 Story System 合同；下游必须运行 `/webnovel-chapter-plan`。
6. 重载失败或状态写入失败时，不报告为完成；保留已有状态和备份证据。

## 执行流程

先解析并确认项目根目录：

```bash
export WORKSPACE_ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
export SCRIPTS_DIR="${CLAUDE_PLUGIN_ROOT}/scripts"
export PROJECT_ROOT="$(python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${WORKSPACE_ROOT}" where)"
```

读取目标卷的三份文件和 `.webnovel/state.json`，向作者说明当前卷纲已由人工编辑，重载可能使依赖章节过期。需要预览时先运行：

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

重载成功后报告当前 revision、上一 revision 和 stale 章节范围，并明确提示：

```text
/webnovel-chapter-plan {volume_id} {affected_range}
```

只有重新生成并校验章纲、刷新章级合同后，才可运行：

```text
/webnovel-write {chapter_num}
```

不输出原始 JSON、traceback 或长日志；故障排查只提示 `.webnovel/logs/run_last.log` 或 `/webnovel-doctor`。
