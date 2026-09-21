# Webnovel Writer（插件源目录）

长篇网文创作插件的 Claude Code 安装包。完整介绍、安装方式与文档导航见仓库根目录的 [README](../README.md)。

> 本文件面向「拿到插件包」的使用者与维护者，列出包内组件与最小验证方式。

## 包内组件

| 类型 | 数量 | 位置 |
|------|------|------|
| Skills（斜杠命令） | 17 | `skills/<name>/SKILL.md` |
| Agents（子代理） | 4 | `agents/*.md` |
| Python 工具 | 统一入口 | `scripts/webnovel.py` |
| 题材/写法数据 | 9 CSV | `references/csv/*.csv` |
| 题材模板 | 按题材 | `templates/genres/*.md` |
| Dashboard 前端 | 预打包 | `dashboard/frontend/dist/`（随包发布，无需本地构建） |

### 17 个 Skill

| 命令 | 用途 |
|------|------|
| `/webnovel-init` | 深度初始化项目骨架、设定集、总纲 |
| `/webnovel-outline-revise` | 以已发布章节为冻结线，确认式修改总纲；推翻型诉求只阻断提示 |
| `/webnovel-plan` | 只生成卷级节拍表、时间线和纯卷纲，并写回新增设定 |
| `/webnovel-volume-revise` | 先确认影响范围，再引导式修改卷纲并刷新 revision |
| `/webnovel-volume-reload` | 人工编辑卷纲后重算 revision，标记下游章纲 stale |
| `/webnovel-chapter-plan` | 基于已完成卷纲生成独立章纲，并刷新章级 Story System 合同 |
| `/webnovel-chapter-revise` | 先确认影响范围，再引导式修改章纲并刷新章级合同与 revision |
| `/webnovel-chapter-reload` | 人工修改正文后重载 revision，重新审查/提取并确认提交 |
| `/webnovel-chapter-discard` | 抛弃章节正文：未提交草稿归档后删除，已提交章回退到上一章版本点 |
| `/webnovel-write` | 一条龙写章：上下文 → 起草 → 审查 → 润色 → 提交 → 备份 |
| `/webnovel-review` | 章节事实一致性与逻辑审查：setting / timeline / continuity / character / logic 五维 |
| `/webnovel-query` | 查询设定、角色、伏笔、运行时信息（只读） |
| `/webnovel-learn` | 把有效写法沉淀进项目长期记忆 |
| `/webnovel-style-learn` | 学习文风：从 正文/ 学现状画像、从 文风/ 学目标画像，产出可复用文风档案 |
| `/webnovel-dashboard` | 启动只读可视化面板 |
| `/webnovel-doctor` | 阶段感知项目体检、版本证据、stale 和投影健康检查 |
| `/webnovel-audit` | 范围级回扫：Strand 三线配比、角色/关系/称谓漂移、文体漂移（只读，只出报告） |

Reviewer 不评分、不评价文笔、不建议情节改动；爽点、节奏、追读力数据用于规划与上下文辅助，不代表完整编辑质量审查。

### 4 个 Agent

| Agent | 职责 | 被谁调用 |
|-------|------|---------|
| `context-agent` | 写前 research，输出写作任务书 | `/webnovel-write` Step 1 |
| `reviewer` | 逐维度事实审查 | `/webnovel-write` Step 3、`/webnovel-review` |
| `data-agent` | 从正文提取事实，生成 commit artifacts | `/webnovel-write` Step 5 |
| `deconstruction-agent` | 参考书拆解，提炼可迁移写法 | `/webnovel-init` Step 1.5 |

## 依赖与安装

通过 Claude Code Marketplace 安装（推荐）：

```bash
claude plugin marketplace add bamboo-jzy/webnovel-writer --scope user
claude plugin install webnovel-writer@webnovel-writer-marketplace --scope user
```

Python 依赖：

```bash
python -m pip install -r scripts/requirements.txt
```

RAG 检索需在书项目根目录配置 `.env`（缺失时自动退回 BM25 关键词检索）。详见 [RAG 与配置](../docs/guides/rag-and-config.md)。

## 最小验证

```bash
# 路径 / 项目根 / Story System 健康预检
python -X utf8 "<CLAUDE_PLUGIN_ROOT>/scripts/webnovel.py" --project-root "<PROJECT_ROOT>" preflight

# 提示词与数据链测试
python -m pytest -q --no-cov
```

> clean-room 安装验证建议用 `git archive` 或干净克隆，避免把本地 `node_modules/`、`__pycache__/`、`.coverage` 等开发产物带进 `--plugin-dir` 测试。

## 协议

[GPL-3.0](LICENSE)。完整文档导航见根目录 [README](../README.md) 与 [docs/](../docs/README.md)。
