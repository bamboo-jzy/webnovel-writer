# 移除老项目升级/迁移能力（2026-09-17）

> 状态：**已执行完成**。决策依据：作者的正式项目为新建、不接 v5 老数据；判据沿用当日既定的「生产调用方为 0 的能力即砍」。

## 1. 决策

`migrate`（`state.json` → `index.db`）是给 v5 时期老项目升级到 v6 架构用的入口。作者不需要该能力，因此整体移除，而非补 skill 引导。

被移除的能力边界：

| 项 | 说明 |
|---|---|
| 迁移对象 | `state.json` 的 `entities_v3` / `alias_index` / `state_changes` / `structured_relationships` → `index.db` 对应表 |
| 迁移收尾 | 迁移成功后精简 `state.json`，写 `_migrated_to_sqlite` + `_migration_timestamp` |
| 入口 | `webnovel.py migrate [-- --dry-run/--backup/--no-backup/--quiet]` |

## 2. 改动清单

### 删除

| 文件 | 规模 |
|---|---|
| `webnovel-writer/scripts/data_modules/migrate_state_to_sqlite.py` | 368 行 |
| `webnovel-writer/scripts/data_modules/tests/test_migrate_state_to_sqlite.py` | 260 行 |

两者均为**孤立**：全仓核对后，该模块只被 `webnovel.py` 的 CLI 转发与自身测试引用，无其他生产调用方。

### 修改

| 文件 | 改动 |
|---|---|
| `scripts/data_modules/webnovel.py` | `PASSTHROUGH_TOOLS` 去掉 `"migrate"`；删 `p_migrate` parser；删分发分支 `if tool == "migrate"` |
| `scripts/data_modules/tests/test_coverage_boost.py` | 删 `test_webnovel_passthrough_migrate` |
| `scripts/data_modules/tests/test_prompt_integrity.py` | `REGISTERED_CLI_SUBCOMMANDS` 去掉 `"migrate"`（该测试校验 prompt 中出现的子命令必须在注册表内） |
| `docs/guides/commands.md` | 子命令表删 `migrate` 行 |
| `skills/webnovel-query/references/system-data-flow.md` | 删脚本表行、迁移 example 块、errors 中「让 state.json 持续膨胀」一条 |
| `references/index/skill-gap-assessment-2026-09-17.md` | A2 条目标为已撤销；`webnovel-repair` / `webnovel-backup` 的能力清单去掉 `migrate` |

## 3. 明确保留的部分（附判定理由）

逐项核对后，以下 legacy 相关代码**不属"迁移入口"**，且删改有实质风险，故保留：

| 位置 | 实际语义 | 保留理由 |
|---|---|---|
| `data_modules/state_manager.py:316-321` | 保存时强制 pop 四个膨胀字段并写 `_migrated_to_sqlite` | 运行时字段清理保证，不是升级入口；新建项目的 state.json 本就不含这些字段，不触发 |
| `chapter_paths.py:118-120` | 「无标题章节名」`第0001章.md` | **当前功能**：`_build_chapter_filename` 在没有章节标题时也生成这种名字，删掉会直接读不到自己写的文件 |
| `chapter_outline_loader.py:115` | `legacy_volume`（整卷大纲内按章切段） | 当前支持的一种大纲布局，非老项目专属 |
| `data_modules/context_manager.py:378-406` | legacy genre profile fallback | 题材档案降级读取，与版本迁移无关 |

## 4. 已知后果

- **老项目数据**：若真有 v5 项目带着 `entities_v3` 被打开，`state_manager` 保存时会静默 pop 这些字段，而**不再有代码把它们搬进 `index.db`** → 数据丢失。对新建项目无影响。
- **无其他链路依赖**：`init_project.py` 不调用迁移；写章、审查、修订、备份链路均不涉及该模块。

## 5. 验证

宿主存在 `SAFE_DELETE_BULK_CONFIRM_REQUIRED` 批量删除守卫（单次 tool call 内删除超 50 次即抛 `SystemExit(1)`），整批跑 `data_modules/tests` 会报 `INTERNALERROR`，故按文件分批执行。

| 范围 | 结果 |
|---|---|
| `scripts/tests`（分 4 批） | **80 passed**，与改动前一致 |
| `test_prompt_integrity.py` | **129 passed** — 直接覆盖「CLI 注册表 ↔ prompt 引用」一致性 |
| `test_coverage_boost.py` + `test_state_manager_extra.py` | **67 passed** |
| `test_webnovel_unified_cli.py` + `test_data_modules.py` | **60 passed** — 直接覆盖统一 CLI |
| CLI 冒烟 | `--help` 中 `migrate` 出现 **0 次**；执行 `migrate` → `invalid choice: 'migrate'`，`exit=2` |

上述 `INTERNALERROR` 与本改动无关（守卫触发于任意 pytest 临时文件清理）。

## 6. 未处理项

- `docs/superpowers/plans/2026-06-10-audit-fix-plan.md:101-102` 仍引用已删文件。该计划已于 2026-06-11 随 v7 收敛截断（Task 8-24 作废），属历史记录，不改。
- `docs/archive/superpowers/plans/2026-04-15-story-system-final-convergence.md:1860` 的子命令清单含 `migrate`，属归档文档，不改。
- `CHANGELOG.md` 未加条目。本轮与同日「移除快照恢复」累计两批行为变更，发布前需先定版本号（`plugin.json` 当前为 `6.2.1`）。
