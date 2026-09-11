# 系统架构与模块设计

## 核心理念

### 真源划分

- 写前真源：`.story-system/MASTER_SETTING.json`、`volumes/`、`chapters/`、`reviews/`
- 写后真源：accepted `CHAPTER_COMMIT`
- `.webnovel/state.json`、`index.db`、`summaries/`、`memory_scratchpad.json`：只作为投影 / read-model
- `references/genre-profiles.md`：fallback-only

### 防幻觉三定律

| 定律 | 说明 | 执行方式 |
|------|------|----------|
| **大纲即法律** | 遵循大纲，不擅自发挥 | Context Agent 强制加载章节大纲 |
| **设定即物理** | 遵守设定，不自相矛盾 | Reviewer Agent 内置一致性审查 |
| **发明需识别** | 新实体必须入库管理 | Data Agent 自动提取并消歧 |

### Strand Weave 节奏系统

| Strand | 含义 | 理想占比 | 说明 |
|--------|------|----------|------|
| **Quest** | 主线剧情 | 60% | 推动核心冲突 |
| **Fire** | 感情线 | 20% | 人物关系发展 |
| **Constellation** | 世界观扩展 | 20% | 背景/势力/设定 |

节奏红线：

- Quest 连续不超过 5 章
- Fire 断档不超过 10 章
- Constellation 断档不超过 15 章

## 总体架构图

```text
┌─────────────────────────────────────────────────────────────┐
│                      Claude Code                           │
├─────────────────────────────────────────────────────────────┤
│  Skills (12个):                                             │
│    init / plan / chapter-plan / write / review / query      │
│    learn / dashboard / doctor / volume-revise / volume-reload│
│    chapter-reload                                          │
├─────────────────────────────────────────────────────────────┤
│  Agents (3个):                                             │
│    Context Agent / Data Agent / Reviewer (含六维审查)        │
├─────────────────────────────────────────────────────────────┤
│  Data Layer:                                               │
│    state.json / index.db (SQLite) / vectors.db             │
├─────────────────────────────────────────────────────────────┤
│  Story System:                                             │
│    .story-system/ (合同·提交·事件)                           │
└─────────────────────────────────────────────────────────────┘
```

## 规划与写作主链

```text
总纲
  ↓
/webnovel-plan
  ↓
卷纲、卷节拍表、卷时间线
  ↓
/webnovel-chapter-plan
  ↓
独立章纲与章级 Story System 合同
  ↓
/webnovel-write
```

`/webnovel-plan` 只负责卷级规划和设定/总纲同步；`/webnovel-volume-revise` 用确认式流程定向修改已有卷纲，`/webnovel-volume-reload` 负责人工编辑后的内容 revision 重载。revision 变化会把依赖旧卷纲的章纲登记为 `stale`，但不会覆盖作者文件；`/webnovel-chapter-plan` 在卷级产物齐全后按批次生成或刷新独立章纲与章节合同。卷纲完成本身不代表章节已经具备写作条件。

卷纲 revision 以节拍表、时间线、详细大纲三份文件的名称与内容 SHA-256 为依据。写前门禁会比较章节记录的 `source_volume_revision` 与当前 revision；不一致时必须先重载卷纲并重新规划章纲。

正文人工修改由 `/webnovel-chapter-reload` 管理：正文内容 SHA-256 形成 `content_revision`，状态同时记录 `draft_revision`、`validated_revision` 和 `committed_revision`。重载会备份正文、state、章级合同、旧 commit 与临时 artifacts，并将后续章节标记为 `previous_chapter_revision_changed`；reviewer、data-agent 和 precommit 必须携带同一 `validation_input`。旧 accepted commit 归档到 `commits/history/`，事实变化且现有增量投影无法安全撤销时以 `revision_projection_unsafe` 阻断，而不是覆盖旧事实。

## Agent 分工

### Context Agent（读）

- 文件：`agents/context-agent.md`
- 职责：在写作前构建"创作任务书"，提供本章上下文、约束和追读力策略。

### Data Agent（写）

- 文件：`agents/data-agent.md`
- 职责：从正文提取 `accepted_events / state_deltas / entity_deltas / summary_text` 等 commit artifacts，交给 `chapter-commit` 驱动 projection writers 更新 `state.json`、`index.db`、摘要与长期记忆。

### Reviewer（审）

- 文件：`agents/reviewer.md`
- 职责：章节质量审查，内部包含以下六个审查维度：

| 审查维度 | 检查重点 |
|----------|----------|
| High-point Checker | 爽点密度与质量 |
| Consistency Checker | 设定一致性（战力/地点/时间线） |
| Pacing Checker | Strand 比例与断档 |
| OOC Checker | 人物行为是否偏离人设 |
| Continuity Checker | 场景与叙事连贯性 |
| Reader-pull Checker | 钩子强度、期待管理、追读力 |

## Story System（合同驱动体系）

Story System 以 `.story-system/` 为独立运行面，由以下几部分组成：

- **合同种子**：`MASTER_SETTING.json` + 章节合同 + 反模式配置
- **合同优先运行时**：卷合同 (`volumes/`) + 审查合同 (`reviews/`) + 写前校验
- **章节提交链**：`commits/chapter_XXX.commit.json` + state/index/summary/memory 投影
- **事件审计链**：`events/chapter_XXX.events.json` + 修订提案 + 覆写账本

当前默认即 contract-first + commit-first：`.story-system/` 为主链真源，旧的 `.webnovel/*` 降级为投影 / read-model，`preflight` 与 dashboard 暴露 runtime health。

核心链路：

```text
story-system --persist
    -> 写入合同种子（MASTER_SETTING.json 等）
story-system --emit-runtime-contracts --chapter N
    -> 生成运行时合同 + 写前校验
chapter-reload --chapter N --validate
    -> 校验当前正文与四份 artifacts 的同版输入
chapter-commit --chapter N
    -> 提交 accepted commit + 执行各投影写入
story-events --chapter N / --health
    -> 事件审计与健康检查
preflight / dashboard
    -> story runtime health / fallback 状态 / latest commit 状态
```

事件审计链不另起第二套投影循环，事件路由仅负责声明式激活 writer，
实际执行入口仍是 `ChapterCommitService.apply_projections()`。

详细设计见：`docs/archive/architecture/story-system-phase5.md`
