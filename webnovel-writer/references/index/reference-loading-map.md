# Reference Loading Map

> 本文件记录当前 `skills/*/SKILL.md` 的实际 reference 消费关系。
> 口径：只登记 skill 明确要求直接读取的 md/template，以及明确调用 `reference_search.py` 或 `story-system` 间接消费的 CSV。
> 不登记普通项目数据读取，例如 `.webnovel/state.json`、`设定集/*.md`、`大纲/*.md`、`index.db`。
> **「Reference」列永远是外部参考文件，不登记 SKILL.md 自身。**
>
> 覆盖度：**17/17 skill**（2026-09-21 复核，新增 `webnovel-chapter-discard`）。维护方式：改任一 `SKILL.md` 的 reference 表后，必须回写本文件；取证脚本见 `.workbuddy/tmp/audit_reference_usage.py`。

---

## 直接 Read 的 md/template

> 「读取方式」：**区段** = 先 `Grep` 匹配 `^#{1,4} ` 定位真实标题锚点行号，再 `Read` offset/limit 取段（七个靶心大文件，锚点见末列）；**全文** = 短文件整体读。锚点用文件里的真实标题原文（含中文顿号「、」），不是计划简写。靶心文件与锚点出处见 `docs/architecture/phase0-slimming-and-read-audit-2026-06-06.md` §A.1。

| Skill | 阶段 | 触发 | Reference | 读取方式 | 区段锚点（区段读时匹配此真实标题） |
|-------|------|------|-----------|---------|-----------|
| webnovel-init | Step 1 | always | `skills/webnovel-init/references/system-data-flow.md` | 全文 | — |
| webnovel-init | Step 1 | always | `skills/webnovel-init/references/genre-tropes.md` | 区段 | 当前题材段 |
| webnovel-init | 全流程采集 | 按需（逐项收集时） | `skills/webnovel-init/references/init-collection-schema.md` | 区段 | 首个 `##` 之前的收集对象段；字段表读 `## 字段与采集步骤对应` |
| webnovel-init | Step 1 | 选定题材后 | `templates/genres/{题材}.md`（37 个题材模板） | 全文 | — |
| webnovel-init | 卖点/题材采集 | always | `references/genre-profiles.md` | 区段 | 当前 genre 的单个 `### 2.x`；按需加 `## 一、Profile 字段说明` |
| webnovel-init | Step 2 | 用户人物扁平 | `skills/webnovel-init/references/worldbuilding/character-design.md` | 全文 | — |
| webnovel-init | Step 4 | always | `skills/webnovel-init/references/worldbuilding/faction-systems.md` | 区段 | 当前世界观所需小节 |
| webnovel-init | Step 4 | 涉及修仙/玄幻/高武/异能 | `skills/webnovel-init/references/worldbuilding/power-systems.md` | 区段 | 力量体系对应小节 |
| webnovel-init | Step 4 | always | `skills/webnovel-init/references/worldbuilding/world-rules.md` | 全文 | — |
| webnovel-init | Step 5 | always | `skills/webnovel-init/references/creativity/creativity-constraints.md` | 区段 | 采集读 `## 一、创意包 Schema (Idea Package)` / `## 六、硬约束驱动创意 (Hard Constraints)` / `## 八、评分系统 (Scoring System)`；评分展示读 `### 8.1 五维评分` |
| webnovel-init | Step 5 | always | `skills/webnovel-init/references/creativity/selling-points.md` | 区段 | `## 9. 核心卖点定位模板` 骨架；按需补 `### 1.3 核心卖点黄金公式` / `## 7. 实战检查清单` |
| webnovel-init | Step 5 | 复合题材 | `skills/webnovel-init/references/creativity/creative-combination.md` | 区段 | 当前混搭轴对应小节 |
| webnovel-init | Step 5 | 卡顿 | `skills/webnovel-init/references/creativity/inspiration-collection.md` | 区段 | 所需采集小节 |
| webnovel-init | Step 5 | 题材映射命中 | `skills/webnovel-init/references/creativity/anti-trope-*.md` | 区段 | 对应反套路项 |
| webnovel-init | Step 6 | always | `skills/webnovel-init/references/worldbuilding/setting-consistency.md` | 区段 | 一致性校验小节 |
| webnovel-plan | Step 4 | always | `templates/output/大纲-卷节拍表.md` | 全文 | — |
| webnovel-plan | Step 5 | always | `templates/output/大纲-卷时间线.md` | 全文 | — |
| webnovel-plan | Step 6 | always | `references/genre-profiles.md` | 区段 | 当前 genre 的单个 `### 2.x`；按需加 `## 一、Profile 字段说明` |
| webnovel-plan | Step 6 | always | `references/shared/strand-weave-pattern.md` | 全文 | — |
| webnovel-plan | Step 6 | 需要爽点设计 | `references/shared/cool-points-guide.md` | 区段 | 所需爽点维度段；题材适配取 `## 九、题材适配` |
| webnovel-plan | Step 6 | 需要冲突设计 | `skills/webnovel-plan/references/outlining/conflict-design.md` | 区段 | 对应冲突类型小节 |
| webnovel-plan | Step 6 | 特定题材节奏 | `skills/webnovel-plan/references/outlining/genre-volume-pacing.md` | 全文 | — |
| webnovel-chapter-plan | Step 3 | always | `skills/webnovel-plan/references/outlining/chapter-planning.md` | 区段 | `## 10. 结构化节点规范（CBN/CPNs/CEN）`；需模板时加 `## 7. 章节规划模板` |
| webnovel-chapter-plan | Step 3 | always | `references/outlining/plot-signal-vs-spoiler.md` | 全文 | — |
| webnovel-chapter-plan | Step 3 | 需要爽点/追读力（按需） | `references/reading-power-taxonomy.md` / `references/shared/cool-points-guide.md` | 区段 | 按需取钩子、爽点和即时满足段 |
| webnovel-chapter-plan | Step 3 | 需要冲突设计（按需） | `skills/webnovel-plan/references/outlining/conflict-design.md` | 区段 | 对应冲突类型小节 |
| webnovel-chapter-plan | Step 3 | 特定题材节奏（按需） | `skills/webnovel-plan/references/outlining/genre-volume-pacing.md` | 全文 | — |
| webnovel-write | Step 4 | always | `skills/webnovel-write/references/polish-guide.md` | 区段 | 主路径 `## 2. 执行顺序（必须按序）`；Anti-AI 终检 `## 2A. Anti-AI 检测细则` / `## Phase 1 增补：Anti-AI 规范（7层，原版）` |
| webnovel-write | Step 4 | always | `skills/webnovel-write/references/writing/typesetting.md` | 全文 | — |
| webnovel-write | Step 4 | always | `skills/webnovel-write/references/style-adapter.md` | 全文 | — |
| webnovel-review | Step 3 | always | `references/shared/core-constraints.md` | 全文 | — |
| webnovel-review | Step 3 | always | `references/review-schema.md` | 全文 | — |
| webnovel-review | Step 8 | blocking issue 需用户裁决 | `references/review/blocking-override-guidelines.md` | 全文 | — |
| webnovel-query | 查询识别后 | 所有查询 | `skills/webnovel-query/references/system-data-flow.md` | 区段 | 按查询类型取数据源优先级小节 |
| webnovel-query | 查询识别后 | 伏笔分析 | `skills/webnovel-query/references/advanced/foreshadowing.md` | 全文 | — |
| webnovel-query | 查询识别后 | 节奏分析 | `references/shared/strand-weave-pattern.md` | 全文 | — |
| webnovel-query | 查询识别后 | 格式查询 | `skills/webnovel-query/references/tag-specification.md` | 全文 | — |

> 七个靶心大文件（`references/genre-profiles.md`、`skills/webnovel-init/references/creativity/selling-points.md`、`references/reading-power-taxonomy.md`、`skills/webnovel-plan/references/outlining/chapter-planning.md`、`skills/webnovel-init/references/creativity/creativity-constraints.md`、`skills/webnovel-write/references/polish-guide.md`、`references/shared/cool-points-guide.md`）一律区段读，避免 init/plan/write 每跑全量吞入；短文件维持全文读。

> **review 的边界**：`review` 只加载上表三个文件。它**不加载** `cool-points-guide.md` / `strand-weave-pattern.md`——review SKILL.md 明确不评分、不产出节奏爽点评分，这两个文件属于 `plan` / `chapter-plan` / `query` 的消费面（2026-09-17 修正：本表此前误登了这两条）。

## CSV 检索：直接调用 `reference_search.py`

| Skill | 阶段 | 触发 | 实际调用 |
|-------|------|------|----------|
| webnovel-init | 角色/书名/势力设定 | 用户开始设定命名 | `--skill init --table 命名规则 --query "{命名对象} {题材}" --genre {题材}` |
| webnovel-plan | 卷级规划 | 需要爽点/冲突设计 | `--skill plan --table 爽点与节奏 --query "{卷级核心冲突}" --genre "${GENRE}"` |
| webnovel-plan | 卷级规划 | 需要桥段模板 | `--skill plan --table 桥段套路 --query "{卷级核心冲突}" --genre "${GENRE}"` |
| webnovel-plan | 卷级规划 | 新增角色出现 | `--skill plan --table 命名规则 --query "角色命名" --genre {题材}` |
| webnovel-write | Step 2 | 新角色首次出场 | `--skill write --table 命名规则 --query "角色命名" --genre {题材}` |
| webnovel-write | Step 2 | 战斗/对峙场景 | `--skill write --table 场景写法 --query "战斗描写" --genre {题材}` |
| webnovel-write | Step 2 | 多角色对话 | `--skill write --table 写作技法 --query "对话声线 口吻区分" --genre {题材}` |
| webnovel-write | Step 2 | 情感/心理描写 | `--skill write --table 写作技法 --query "情感描写 心理" --genre {题材}` |
| webnovel-write | Step 2 | 高频桥段 | `--skill write --table 场景写法 --query "{桥段类型}" --genre {题材}` |

> `webnovel-write` 的 SKILL.md 只给通用调用式 `--table {表名}` + 触发映射（`write/SKILL.md:34-40`），上表 5 行是把该映射展开后的具体形态。
> `webnovel-plan` 只调用**三个**表：`爽点与节奏` / `桥段套路` / `命名规则`（`plan/SKILL.md:59-61`）。**`场景写法` 不是 plan 的检索表**（2026-09-17 修正：本表此前误登了一条 `场景写法 --query "卷级结构 叙事功能"`）。

## CSV 检索：`story-system` 间接消费

| 入口 Skill | 阶段 | 触发 | 间接消费 |
|------------|------|------|----------|
| webnovel-init | Story System 初始化 | init 完成后 `story-system "${GENRE}" --genre "${GENRE}" --persist --format json` | `题材与调性推理.csv` 路由；按路由的 `推荐基础检索表`/`推荐动态检索表` 检索基础表和动态表；`裁决规则.csv` 注入风格/节奏/毒点裁决 |
| webnovel-plan | runtime 合同刷新 | 规划直接落到具体章节时 `--persist --emit-runtime-contracts --chapter {chapter_num}` | 同上，并由 `RuntimeContractBuilder` 生成 volume/chapter/review 合同 |
| webnovel-chapter-plan | Step 5 | 刷新章节级 Story System 合同 | 同上 |
| webnovel-chapter-revise | Step 4（合同刷新） | 章纲修改后按新 `CHAPTER_GOAL` 重刷合同 | 同上；`--chapter {chapter_num} --persist --emit-runtime-contracts --format both` |
| webnovel-write | 准备阶段 | 起草前 `--persist --emit-runtime-contracts --chapter {chapter_num}` | 同上；`chapter_{NNN}.json` 的 `chapter_focus` 仅作 CSV 参考，章节目标仍以章纲为准 |
| webnovel-review | Step 1 | 目标章缺 runtime 合同时补齐 | 同上；review 优先依据 `.story-system/reviews/chapter_{NNN}.review.json` 与 latest accepted commit |

`StorySystemEngine` 的真实数据流：

| 步骤 | 数据源 | 说明 |
|------|--------|------|
| `_route()` | `题材与调性推理.csv` | 根据 query、显式 genre、题材别名和 canonical genre 选路由 |
| `_collect_tables()` | 路由行推荐的基础/动态表 | 内部以 `skill="write"` 调 `reference_search.search()`，因此推荐表中的知识行需要匹配 write 可见性 |
| `_load_reasoning()` | `裁决规则.csv` | 按 canonical genre 读取风格优先级、节奏默认策略、毒点权重、冲突裁决 |
| `_apply_reasoning()` | 基础/动态检索结果 + 裁决规则 | 给结果加优先级，决定注入合同的排序 |
| `_rank_anti_patterns()` | 路由毒点 + 推荐表毒点 + 裁决反模式 | 合并并排序 `anti_patterns.json` |

## 无独立 reference 的 Skill

以下 skill 只读写项目数据（`大纲/*.md`、`设定集/*.md`、`.webnovel/state.json`、`.story-system/*` 等），**不加载任何外部 md/template/CSV**：

| Skill | 说明 |
|-------|------|
| webnovel-outline-revise | 只改 `大纲/总纲.md` 与确认后的设定集增量；冻结线直接扫 `.story-system/commits/chapter_*.commit.json` |
| webnovel-volume-revise | 只改三份卷级产物；不加载 reference |
| webnovel-volume-reload | 只校验并重算三份卷级产物的 revision |
| webnovel-chapter-revise | 只改 `大纲/第N章-*.md`；合同刷新走 `story-system`（见上一节） |
| webnovel-chapter-reload | 只重载人工正文并重新校验 artifacts；合同由 CLI 侧处理 |
| webnovel-chapter-discard | 只跑 `chapter-discard` 的预览/草稿删除/版本点回退；归档与 `git switch` 都在 CLI 内完成，不加载独立 reference |
| webnovel-dashboard | 只读面板启动流程，不加载独立 reference；核心校验接口是 `/api/story-runtime/health` 与 `/api/preflight` |
| webnovel-learn | 只读 state 后追加 `.webnovel/project_memory.json` |
| webnovel-doctor | 只跑只读体检与索引对账，不加载独立 reference |
| webnovel-audit | 只跑只读范围回扫，不加载独立 reference；节奏类阈值由 `scripts/scope_audit.py` 侧解析 `references/genre-profiles.md`，无需 SKILL.md 侧 Read |
| webnovel-style-learn | 只跑 `style-profile build/show/diff`，不加载独立 reference；阈值常量（长句 40 字、said tag 30%）在 `scripts/data_modules/style_metrics.py` 内声明，不在 SKILL.md 侧 Read |

## 当前非直接调用项

以下文件当前存在，但没有被当前 `SKILL.md` 明确要求直接加载；除非后续 skill 增加触发条件，否则不计入 direct loading map：

| 文件 | 现状 |
|------|------|
| `skills/webnovel-write/references/style-variants.md` | 未在当前 write 流程中直接加载 |
| `skills/webnovel-write/references/writing/combat-scenes.md` | 已 stub 化，正文迁至 CSV `场景写法` |
| `skills/webnovel-write/references/writing/dialogue-writing.md` | 已 stub 化，正文迁至 CSV `写作技法` |
| `skills/webnovel-write/references/writing/emotion-psychology.md` | 已 stub 化，正文迁至 CSV `写作技法` |
| `skills/webnovel-write/references/writing/scene-description.md` | 已 stub 化，正文迁至 CSV `写作技法` |
| `skills/webnovel-write/references/writing/desire-description.md` | 保守保留原文；CSV 尚未覆盖欲念描写细节，当前未被 SKILL.md 直接加载 |
| `skills/webnovel-write/references/writing/genre-hook-payoff-library.md` | 保守保留原文；CSV 仅覆盖部分钩子/兑现条目，当前未被 SKILL.md 直接加载 |
| `skills/webnovel-review/references/common-mistakes.md` | 未在当前 review 流程中直接加载 |
| `skills/webnovel-review/references/pacing-control.md` | 未在当前 review 流程中直接加载 |
