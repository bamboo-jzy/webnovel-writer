# Skills 缺口评估（2026-09-17，第五次修订）

> 口径：以 `webnovel-writer/skills/` 下 **15 个实际存在的 skill** 为基线（**当前口径 16**，见附录 A.5），对照 `scripts/data_modules/webnovel.py` 已注册子命令、`agents/`、`references/`、`docs/`、`hooks/`、`README.md` 逐项核对。
> **本次修订**：① 已处置项全部移出正文，收进「附录 A：已处置登记」；② 对剩余项逐条重新取证（含 `git`/`grep` 实测），修正 2 处事实错误；③ 新增本轮复核才发现的缺口 N1/N2；④ **E1/E2/E3/E5/E6 已落地**（第 1 批一致性修复），同样移入附录 A；⑤ **2026-09-18 追加**：应作者要求对 B1 做整链取证（真书 `本小区禁止抬头`，2 章已 commit），新增 **N3–N6 四个数据层缺口**与「附录 B：B1 供数断点实测」——结论是 B1 缺的**不是报告脚本，而是供数**：三个子域（伏笔/漂移/Strand）里有三个数据容器**从建表起就没被写入过**；⑥ **2026-09-18 批一落地**：`webnovel-audit` skill + `scope-audit` CLI 已交付 S1/S4/S5 三个检查器（附录 A.3），skill 口径由 14 升为 15；同时修掉两个「让只读检查器偷偷写盘」的缺陷（A.4）。⑦ **2026-09-21 清理落地**：上批留下的 N7（`style_sampler` 死代码）与 N8（S5 未复用档案基线）同批关闭（附录 A.6），A 组「已有能力没有 skill 入口」仅剩 A4；顺带把 dashboard 只读浏览白名单补上 `文风/`。
> 结论：**生产主链（init → plan → chapter-plan → write → review → 修订/重载）闭环完整；运维侧补上了「索引漏章可发现」，文档层补上了「skill 清单与 reference 映射一致」，规模回扫层补上了「供数已就绪的 S1/S4/S5」。剩余缺口集中在「主链之外的四个环」——交付、运维、商业化、跨章质量。** 其中跨章质量（B1）经 2026-09-18 取证后定性为「**数据层半成品**」：Strand 域数据+阈值俱全（**批一已完成**）；伏笔域数据在但「逾期」语义上不可计算（N4）；漂移域三条子链**全部断在供数**（走不到判定那一步）——故 S2/S3 仍然待供数。

---

## 零、本次复核的净变化

| 变化 | 对缺口清单的影响 |
|------|------------------|
| 快照恢复能力移除（改用 Git 原生） | A1 关闭 |
| legacy 迁移能力移除 | A2 关闭 |
| 单书工作区定稿（`WorkspaceHasMultipleBooksError`） | A3 关闭；`webnovel-project` 建议取消 |
| `backup()` 分层 tag（`chNNNN` 可前移 + `chNNNN-prev-*` 归档） | A1 遗留的「非末章修订点建不出来」待决项**关闭** |
| doctor 新增 4 项索引对账（`index.commit_sync` 为 blocker）+ `repair` 可复制命令 | A4 **大幅收窄**：发现能力已具备，缺的只剩「替作者执行」 |
| `chapter-commit` 退出码改非 0（投影 failed/pending 或镜像失败） | D2 部分缓解：失败不再静默 |
| 事件镜像写失败降级 + `projections retry/replay` 可重建镜像 | 新增 N1（日志只写不读） |
| **skill 数量口径统一为 14**（4 处）、**loading map 补齐到 14/14 并消除 3 处冲突**、**2 个 SKILL.md 结构修复**、**8 处 `python -X utf8` 统一** | E1 / E2 / E3 / E5 / E6 关闭 |
| **B1 批一落地**：新增 `webnovel-audit` skill + `scope-audit` CLI，交付 S1 Strand / S4 漂移 / S5 文体三个检查器（只读） | B1 **大幅收窄**：供数已就绪的三个子域已有判定+入口；剩 S2/S3 待供数（N3–N6 未动） |
| `StatusReporter.__init__` 改为惰性构造 `IndexManager`；`scope_audit` 以 `mode=ro` URI 打 index.db | A.4：修掉「只读消费者构造即写库」 |
| **文风档案落地**：新增 `webnovel-style-learn` skill + `style-profile` CLI（`scripts/style_profile.py`），正文现状画像 / 文风目录目标画像 + 差异 + 注入摘要；`文风/` 进 init 与备份白名单 | 记忆层补上「可复用的文风档案」这一环（附录 A.5）；skill 口径 15 → 16；同批留下的 N7 / N8 **已于 2026-09-21 关闭**（附录 A.6） |
| **N7/N8 清理**：删除 `style_sampler` 模块与其 CLI 入口；S5 偏离基线改优先取文风档案 `observed`（新增 `--baseline auto\|profile\|range`）；dashboard 只读白名单补 `文风/` | A 组仅剩 A4；S5 从「范围自比」变为「跨卷可比」（附录 A.6） |

---

## 一、现状基线

| 分组 | Skill（17 个） |
|------|----------------|
| 生产链（6） | init / plan / chapter-plan / write / review / query |
| 修订重载链（5） | outline-revise / volume-revise / volume-reload / chapter-revise / chapter-reload |
| 弃稿链（1） | **chapter-discard**（2026-09-21 新增：草稿归档删除 / 已提交章版本点回退，只处理最后一章，见 A.7） |
| 辅助（5） | learn / **style-learn**（2026-09-21 新增，见 A.5） / dashboard / doctor / audit |

Agent（4 个）：context-agent、data-agent、reviewer、deconstruction-agent（另有 `agents/evals/`）。
数据层已有但**无 skill 入口**的能力：`index get-hook-type-stats` / `get-pattern-usage-stats` / `get-overdue-debts` / `get-chapter-reading-power`（4 个统计子命令）、`chapter_reading_power` 表——目前只有 `dashboard/app.py` 直读展示。（原列的 `style_sampler`（`webnovel.py style`）已于 2026-09-21 整体删除，见附录 A.6。）

---

## 二、功能缺口（按证据）

### A. 已有能力没有 skill 入口

| # | 缺口 | 证据 | 影响 |
|---|------|------|------|
| A4 | **索引/投影修复无执行入口**（收窄后） | doctor 已能发现漏章/库损坏并给出可复制的 `projections replay` 命令（`doctor.py:369 _replay_repair`、`:447/465/497` 三处挂载），但 doctor SKILL.md 原则 1 明写「不自动修复、不安装依赖」；`skills/` 内对 `projections` 零引用 | 作者拿到 `repair` 字段后仍要自己判断「该不该跑、跑完算不算好」。**发现已闭环，执行未闭环** |

> **N7 / N8 已于 2026-09-21 关闭**，登记见附录 A.6。

### B. 内容生产层完全没有的能力

| # | 缺口 | 说明 | 优先级 |
|---|------|------|--------|
| B1 | **跨章/卷级一致性回扫** | `review` 只做**单章**五维事实审查，且明确「不评分、不评价文笔、不提供节奏/追读力评分」（`skills/webnovel-review/SKILL.md:14`）。原评估称「`index.db` 已有 `chapter_reading_power`/`get-hook-type-stats`/`get-pattern-usage-stats`/`get-overdue-debts`/`style_sampler`」——**2026-09-18 取证修正：这些容器是空表，从未被写入**（`chapter_reading_power` 0 行、`style_sampler` 0 条、`chase_debt` 0 行；见附录 B）。故本缺口实际是**两层**：① 供数层缺（N3–N6 + 三个空容器）；② 判定/呈现层缺（无比较器、无 skill 入口）。**2026-09-18 批一已交付判定/呈现层中「供数已就绪」的三个**：S1 Strand 配比 / S4 角色·关系·称谓漂移 / S5 文体漂移（`webnovel-audit` + `scope-audit`，见附录 A.3）。**仍未闭环**：S2 hook 强度趋势、S3 伏笔逾期（需 N3/N4 供数）；性格漂移、战力膨胀亦待 N5/N6 | **P0（批一已落地，批二待供数）** |
| B2 | **商业包装层缺失** | `init` 只收「书名 + 一句话故事」；`命名规则.csv` 只覆盖角色/地点/势力/功法/道具/书名。平台实际需要**简介（200 字多版本）、标签、卖点文案、卷名/章名目录、黄金三章打磨**——全无 | **P0** |
| B3 | **导出/交付缺失** | 全仓库复核（`--include=*.py --include=*.md` 排除内部 JSON/DB 导出后）**零命中** TXT/EPUB/分章导出、字数统计、平台排版清理 | **P0** |
| B4 | **合规/风控缺失** | 复核 `敏感词 / sensitive`：`scripts/` 内命中全部是 `run_logger.py` 的**密钥脱敏**（`SENSITIVE_KEY_RE`）与测试；**业务侧零覆盖**。中文网文平台审核（敏感词、涉政涉黄涉暴、地图/民族/宗教）是硬需求 | **P0** |
| B5 | **拆书能力被锁死在 init** | `deconstruction-agent` 能力完整（quick/deep 双模、质量门控 confidence/coverage/overlap、`do_not_copy`/`canon_contamination_warnings` 防污染），实测 `skills/` 内**仅 `webnovel-init/SKILL.md:41/80/83/92` 引用**，只能由 Step 1.5 调用 | P1 |
| B6 | **批量连写缺失** | `/webnovel-write` 一次一章（SKILL.md 内 `range` 零命中）；长篇需要「按范围连写 N 章，每章仍走完整闸门 + 失败隔离 + 断点续跑」 | P1 |

### C. 记忆/经验层：只写不治理

| # | 缺口 | 证据 |
|---|------|------|
| C1 | **`learn` 只增不减** | `scripts/project_memory.py:100` 只有 `add = sub.add_parser("add-pattern")`，**无 list / update / delete / merge**（仅精确重复跳过）。规则过时或写错后无法清理，脏规则会持续通过 `context-agent` 的 `author_style_patterns` 进入写作任务书 |
| C2 | **长期记忆冲突无修复流程** | `memory/orchestrator.py:45-91` 能发现同主键 active 冲突并写进状态（`count`/`sample`），但没有任何 skill 承接后续消解 |

### D. 质量保障缺口

| # | 缺口 | 证据 |
|---|------|------|
| D1 | **行为评估只覆盖 2/14** | `skills/*/evals/` 实测仅 **webnovel-review** 与 **webnovel-write** 两个；而风险最高的是「改作者文件 + 刷 revision」的 revise/reload 五件套，恰恰没有 eval。agent 侧有 `agents/evals/`（4 个 agent 共用一套） |
| D2 | **doctor 只诊断不治疗** | doctor SKILL.md 原则 1 明写「不自动修复、不安装依赖」。退出码改非 0 后**失败能浮出来了**，但「浮出来之后怎么办」仍要作者自己接 |
| **N1** | **镜像错误日志只写不读**（本次新增） | `.webnovel/logs/event_mirror_errors.log` 只由 `event_log_store._note_mirror_error()` 追加，**全仓库无读者**：doctor 不采样，且 `.webnovel/logs/` 不在 `backup_manager._selected_backup_paths()` 名单（`backup_manager.py:104-117`）→ 历史镜像失败无任何入口可查 |
| **N2** | **漏章信号不进会话启动**（本次新增） | 索引对账（`index.commit_sync`）**只在 `doctor` 里**；`hooks/session_start.py` 调的是 `project-status`，而它只看投影 `failed/pending`（`project_status.py:69`），**不查 `index.db` 与 commit 的对账** → 作者不主动体检时，漏章依然不可见 |

### E. 文档与内部一致性（低成本高收益）

E1 / E2 / E3 / E5 / E6 已处置（见附录 A）。剩余：

| # | 问题 | 证据 |
|---|------|------|
| E4 | **9 个孤儿 reference** | `reference-loading-map.md` 自列「当前非直接调用项」，共 **9 行**：`style-variants.md`、`writing/combat-scenes.md`、`writing/dialogue-writing.md`、`writing/emotion-psychology.md`、`writing/scene-description.md`、`writing/desire-description.md`、`writing/genre-hook-payoff-library.md`、`review/common-mistakes.md`、`review/pacing-control.md`。其中 5 个标注「已 stub 化，正文迁至 CSV」，但**文件仍在**，持续产生维护成本 |
| E7 | **知识库 P1 缺口未消** | `references/index/reference-gap-register.md:86-93` 自报未完成：女频命名规范、言情核心场景（暧昧/误会/重逢/分手/追妻）、悬疑推理技法（线索/公平误导/真相揭露） |
| E8 | **hooks 只有写保护** | `hooks/hooks.json` 只有 SessionStart + PreToolUse(Write/Edit/MultiEdit) + PreToolUse(Bash)。**无 PostToolUse 校验**（如正文写完自动 `placeholder-scan` / 行数 / 文件命名）——而正文文件名不规约正是 `index.chapter_metadata` warning 的主因 |
| E9 | **本地过期检出** | `.claude/worktrees/agent-ae98f3aaaec73e1c0/`（6.6 MB，只含 8 个 skill）。实测 `.gitignore:1` 已忽略 `.claude/`，`git ls-files` **0 条** → **不影响发布仓库**，只是本地目录会污染 grep / AI 检索。定性为「本地噪音」，非仓库缺陷 |

### F. 规模回扫（B1）供数层——2026-09-18 取证新增

> 这四项都不是「没有 skill 入口」，而是**数据本身写不对或写不进去**。它们比 B1 本身更靠前：**任何针对 B1 写的检查器，只要按现有字段做过滤，就会静默漏判**。实测证据见「附录 B」。

| # | 缺口 | 证据 |
|---|------|------|
| **N3** | **伏笔归一化被写入路径绕过** | `normalize_foreshadowing_list()` 存在且能把 9 条全部归一成 `status='未回收'` / `tier='支线'`，但真书 `state.plot_threads.foreshadowing` 实测为 **1–4 条 `未回收`+`支线`，5–9 条 `active`+无 `tier`** —— 即第 1 章走了归一化、**第 2 章没走**。`state_validator.py:36 _PENDING_STATUS_TEXT` 把 `{未回收,待回收,进行中,未解决,pending,active}` 六种写法都当「未回收」容忍 → **不报错，也不统一**。后果：任何按 `status=="未回收"` 过滤的回扫会**漏掉 5/9（55%）** |
| **N4** | **`target_chapter` 全线缺失 → 逾期在语义上不可计算** | `state_validator.py:28 FORESHADOWING_TARGET_KEYS = ["target_chapter"]` 认这个槽位，测试夹具 `mock_demo.py:58-72` 也**逐条都写了 `target_chapter`**，但真实产出链（`agents/data-agent.md:89` 的 `open_loop_created` 载荷）只要求 `content` + 可选 `loop_type/unanswered_question/urgency/planted_chapter/expected_payoff` —— **没有到期章**。实测 9 条全为 `None`。`expected_payoff` 是自由文本（如「锁定动手补漆的人」），不是章号 |
| **N5** | **同一事实两套副本、schema 已分叉、无同步** | 同 9 条伏笔同时存在两处：`state.plot_threads.foreshadowing`（`status` 中文 + `tier` + `planted_chapter`）与 memory 开放环（`status='active'` + `urgency` 数值 + `expected_payoff`）。实测两处**条数与内容一致、词汇表不一致**，且 `plot_threads.active_threads` 恒为 `[]`。无任何机制保证二者收敛 → 回扫要读哪一份、冲突以谁为准，无定义 |
| **N6** | **`review_metrics` 的「分数」是罚分制，测不出漂移** | `review_schema.py:113 _build_dimension_scores` 为 `scores = {category: 100.0 for ...}` 起步，**只对已发现问题扣分** → 0 issue 时 8 个维度（含 `pacing`/`ai_flavor`/`other`）全为 100.0。而 `skills/webnovel-review/SKILL.md:14` 明写「**不提供爽点、节奏或追读力质量评分**」。实测真书 2 章 `overall_score` 均 100.0 → 唯一有消费者的观测数据**恒为直线**，趋势图不可能显示文体/节奏漂移 |

---

## 三、建议新增的 Skill（按优先级）

| 优先级 | Skill | 一句话职责 | 主要复用 |
|--------|-------|-----------|----------|
| **P0** | `webnovel-export` | 交付：分章/合订 TXT、EPUB、字数与章节统计、平台排版清理（缩进/分隔/符号/目录页） | `正文/*.md`、`index.db` |
| ~~**P0**~~ **已落地（批一）** | `webnovel-audit` | 范围级回扫：Strand 占比、hook 强度趋势、伏笔逾期、角色出场/战力曲线、口癖与句长漂移；只输出报告不改正文。**分两批交付**：**批一已交付**「供数已就绪」的 S1/S4/S5（`skills/webnovel-audit/SKILL.md` + `scripts/scope_audit.py`，附录 A.3）；批二待 N3–N6 与三个空容器的供数补齐后再做 S2/S3 | `state.strand_tracker`、`genre-profiles.md` 阈值、`index appearances/state_changes/relationships`、`正文/*.md`；S2/S3 另需 `chapter_reading_power`（待供数） |
| **P0** | `webnovel-packaging` | 商业包装：书名候选、简介多版本、标签、卷名/章名目录、黄金三章打磨 | `命名规则.csv`、`selling-points.md`、`reading-power-taxonomy.md` |
| **P0** | `webnovel-compliance` | 平台合规扫描：敏感词/风险题材分级报告，只提示不改写，可导出待人工确认清单 | 新建 `csv/合规风险.csv` |
| **P0** | `webnovel-repair` | doctor 的修复半边（白名单）：替作者执行并解释 doctor `repair` 字段给出的命令（投影补跑 / `index.db` 重建 / 合同重刷）、清理占位符、消解记忆冲突、采样 `event_mirror_errors.log`、提示孤儿 reference | `doctor`（含逐条 `repair`）、`projections retry/replay` |
| **P1** | `webnovel-backup` | 运维入口：`backup --list` / `--diff A B` / `--create-branch` 的编排与解释（尤其解释 `chNNNN` 可前移 + `chNNNN-prev-*` 归档语义）、离线快照副本清理 | `backup --list/--diff/--create-branch`、`archive` |
| **P1** | `webnovel-deconstruct` | 拆书独立化：把 init Step 1.5 的能力放出来，写到一半也能拆竞品对标，产出报告 + 可选写入参考库 | `deconstruction-agent` |
| **P1** | `webnovel-write --range` | 连写：`/webnovel-write 45-48`，每章仍走完整闸门，失败隔离 + 断点续跑 | 现有 write 全链 |
| **P2** | learn 扩展 | `project-memory list / remove / merge`，让经验记忆可治理 | `project_memory.py` |
| **P2** | doctor 采样镜像日志 | 把 `event_mirror_errors.log` 纳入 doctor 的一项 warning（可归入 `webnovel-repair`，不单开 skill） | `.webnovel/logs/` |

---

## 四、其它方向的不足

1. **宿主耦合**：所有 skill 硬依赖 `CLAUDE_PLUGIN_ROOT` / `CLAUDE_PROJECT_DIR`，agent frontmatter 用 Claude Code 专有字段。`docs/architecture/multi-agent-adaptation-spec-2026-06-05.md` 已立 spec 但未落地；README 也承认仅兼容 Claude Code，而 v8 主线将走 dsh 形态。**缺一层宿主抽象。**
2. **成本不可见**：multi-agent 用 token 换确定性。`run_ledger` 已记录 `SubagentRun`，但无字符/token 统计与「每章成本」曲线（归档路线图 M2-3 的原始诉求）。
3. **读者数据不回流**：内部指标（review 分、hook 强度、追读力）与真实留存/追读数据没有任何对齐机制，质量闭环最终锚在模型口味而非读者行为（归档路线图 M3-3）。
4. **叙事状态层空白**：归档路线图（`docs/archive/architecture/narrative-intelligence-roadmap-2026-06-10.md`）明确指出「事实状态」已有，「叙事状态（张力/信息差/爽点节奏）」与「文体状态（口癖/声音漂移）」是最大空白。**注意**：该路线图已随 v7 冻结归档、v7 形态不再发布，其条目未被 v6 主链吸收——这意味着 `webnovel-audit` 建议里的「文体漂移」「节奏度量」「角色知识边界（信息差）」在 master 上是**真实未实现**。
5. **平台适配缺失**：37 个题材模板是题材维度，但起点/番茄/七猫/知乎的**篇幅节奏与开篇规则差异**只体现在 `genre-canonical.md` 的 `platform_tag` 映射，无 skill 按平台校验。
6. **测试与 eval 不对齐**：pytest 覆盖面集中在主链代码；skill 提示词层的回归保护偏薄（v6.2.0 已引入 prompt integrity check，但只做静态校验；实测无 eval 的 skill 有 12 个）。

---

## 五、建议执行顺序

```text
第 1 批（补闭环，纯改文本 + 小代码）
  ~~E1/E2/E3/E5/E6~~  文档与 skill 文件一致性修复 —— 已完成（2026-09-17）
  E4              孤儿 reference 二选一：接入或删除（已被 CSV 取代的 5 个 stub 优先删）
  C1              learn 增补 list/remove/merge
  N1              doctor 增加镜像错误日志采样

第 2 批（补 P0 能力）
  B3 export  →  B4 compliance  →  B2 packaging  →  B1 audit
    └ B1 分两批（2026-09-18 定）：批一 S1/S4/S5（供数已就绪，见附录 B §B.4）
                                  批二 S2/S3（依赖 N3–N6 与三个空容器补供数）
  A4 + D2 + N2 合并为 repair
  N3/N4/N5  伏笔字段归一化 + 补 target_chapter + 两份副本收敛（B1 批二的前置）
  N6        review 维度分改为评分制，或明确其「罚分」语义不再当质量分用
  空容器     chapter_reading_power / chase_debt 的供数接线
             （`style_sampler` 无需接线——已于 2026-09-21 删除，见 A.6）

第 3 批（提质量）
  D1 把 evals 补到全部 14 个 skill（优先 revise/reload 五件套）
  E7 女频/言情/悬疑知识库补厚
  E8 增加 PostToolUse 轻校验（正文文件名规约 + placeholder-scan）

第 4 批（方向性）
  宿主抽象层 / 成本可视化 / 读者数据回流 / 平台化校验
```

---

## 附录 A：已处置登记（不再计为缺口）

保留此表仅为**避免重复评估**，每项均已落地并改过文档口径。

**A.1 能力类（原 A 组）**：「原编号」一列刻意沿用旧编号——`docs/operations/` 下 4 份处置文档（`legacy-migration-removal`、`multi-book-workspace-assessment`、`index-integrity-assessment`、`index-integrity-fix-plan`）仍以 `A1`/`A2`/`A3` 交叉引用本文件，编号保持不变，引用继续可解析。

| 原编号 | 原缺口 | 处置 | 落地证据 |
|--------|--------|------|----------|
| A1 | 快照恢复无入口 | **能力移除**，回退改走 Git 原生（`git switch -c rewrite-from-chXXXX chXXXX`） | `rollback()` / `restore_snapshot()` 生产调用方为 0（只被 CLI 与测试调用）；文档入口 `README.md`「版本点与恢复」、`docs/operations/operations.md`「恢复到历史版本」、`docs/guides/commands.md` 同名小节。**连带关闭**：`backup --chapter N` 的 `chNNNN` tag 冲突已修为分层 tag（`chNNNN` 可前移 + `chNNNN-prev-*` 归档），非末章修订点不再建不出来；方案见 `docs/operations/chapter-tag-conflict-fix-2026-09-17.md` |
| A2 | 老项目升级/迁移无入口 | **能力移除**，插件不再承担 v5 → v6 升级 | `data_modules/migrate_state_to_sqlite.py` 及测试已删除，`webnovel.py` 的 `migrate` 子命令同步清理（`grep add_parser` 实测无 `migrate`）；处置记录 `docs/operations/legacy-migration-removal-2026-09-17.md` |
| A3 | 多书管理无入口 | **定稿单书工作区**（一个工作区一本书），不新增「书单/切书」能力 | `WorkspaceHasMultipleBooksError` 歧义诊断 + `init` 单书规约警告 + `use` 降为应急命令；`docs/operations/multi-book-workspace-assessment-2026-09-17.md` §6 |
| — | `webnovel-project`（原 P2 建议） | **建议取消** | 多书机制本身是每个 skill bootstrap 的地基（`--project-root "$WORKSPACE_ROOT" where`），缺的只是「当前书回显 + 歧义诊断」，已由 A3 的处置覆盖 |

### A.2 文档与 skill 文件一致性（原 E 组，2026-09-17 落地）

| 原编号 | 原缺口 | 处置 | 落地证据 |
|--------|--------|------|----------|
| E1 | skill 数量口径不一致（原记为「三处」，实测**四处**） | 全部改为 **14** | `README.md:81` mermaid → `14 个 Skill 命令`；`docs/architecture/overview.md` 架构图重写为 `Skills (14个)` 并补全清单（原图各行列宽 62~64 不一致，本次统一到 63 显示宽度）；**新增两处**：`webnovel-writer/README.md:11`（组件表 `12` → `14`）与 `:18`（`### 12 个 Skill` → `### 14 个 Skill`，表体本就 14 行）、`docs/operations/operations.md:89`（`12 个 Skill 命令定义` → `14 个`）。`docs/guides/commands.md` 复核后本就 14/14，无需改 |
| E2 | reference-loading-map 缺 5 个 skill（覆盖 9/14） | 补齐到 **14/14**，并顺手修掉 3 处与 SKILL.md 不符的登记 | 新增「无独立 reference 的 Skill」8 行（outline-revise / volume-revise / volume-reload / chapter-revise / chapter-reload / dashboard / learn / doctor）；新增 init 的 `init-collection-schema.md` 与 `templates/genres/{题材}.md`（37 个题材模板）两行；`chapter-revise` 补进 `story-system` 间接消费表；`chapter-plan` 的 `story-system` 消费也补进该表。取证工具：`.workbuddy/tmp/audit_reference_usage.py` |
| E3 | loading map 与 SKILL.md 冲突（3 处） | **一律以 SKILL.md 为准**修 map，并把 chapter-plan 的模糊「按需」写实 | ① `review` 4 行 → 3 行（`core-constraints` / `review-schema` / `blocking-override-guidelines`），删掉误登的 `cool-points-guide.md` 与 `strand-weave-pattern.md`（review SKILL.md:14 明确「不提供爽点、节奏或追读力质量评分」），并在 map 中加「review 的边界」注记——**该结论与既有测试互相印证**：`evals/fixtures/behavior/fast.json` 的 `skill_review_contract` 早已把 `审查涉及爽点或钩子` / `审查涉及多线交织` 列入 `forbidden_patterns`（`run_behavior_evals.py:96-100` 对 SKILL.md 做正则搜索），而这两句正是 map 原登记里用的触发词，说明 map 是三方（SKILL.md / eval / map）中最后未对齐的一方；② `plan` 的 CSV 行删掉误登的 `场景写法 --query "卷级结构 叙事功能"`（plan SKILL.md:59-61 只调 `爽点与节奏`/`桥段套路`/`命名规则`）；③ `volume-revise` 原登记的「Reference = 自己的 SKILL.md」是无效登记，删除并归入「无独立 reference」；④ `chapter-plan/SKILL.md:70` 把「按需读取 `reading-power-taxonomy.md`、爽点、冲突和节奏参考」改写为四个显式路径，map 相应登记 |
| E5 | 2 个 SKILL.md 结构缺陷 | 已修 | `chapter-plan/SKILL.md`：删掉 Step 7 之后重复的 `user-report` bash 块（Step 7 已有同一调用），并为裸挂的 6 条加 `## 成功标准` 标题（对齐 dashboard / learn / review 的写法）；`plan/SKILL.md:175` 删掉与后续「最终回复必须明确列出：」重复的「，下一步明确给出」 |
| E6 | 裸 `python` 8 处 / 4 个 skill | 全部改为 `python -X utf8`，复检残留 = 0 | `webnovel-dashboard:34/58/64`、`webnovel-init:184/187`、`webnovel-query:20`、`webnovel-review:32/117`。**连带**：`evals/fixtures/behavior/fast.json` 的 `skill_dashboard_contract` 原断言 `"python -m dashboard.server"` 会因此失效，已改为 `"-m dashboard.server"`——只钉「以模块方式启动」这一语义，不再钉解释器参数（子串匹配，见 `run_behavior_evals.py:80`） |

> E4（孤儿 reference）**未处置**，仍在正文 E 组；原因是「接入还是删除」需作者决定。

### A.3 B1 批一：`webnovel-audit` skill + `scope-audit` CLI（2026-09-18 落地）

批一只做「供数已就绪」的三个检查器（附录 B §B.4 的 S1/S4/S5）。三者都**只读**：不改正文、不改 `index.db`、不改 `state.json`。

| 检查器 | 判什么 | 数据来源 | 关键设计 |
|--------|--------|----------|----------|
| **S1 Strand 配比** | Quest/Fire/Constellation 三线占比、主线最大连续、感情线最大断档 | `state.strand_tracker` × 题材阈值 | 阈值走**题材 profile**（`references/genre-profiles.md`，新增 `genre_profile_loader.py` 零依赖解析）；匹配不到才回落 config 默认值，并在报告里**如实标注来源**。占比结论加**短样本闸**（`STRAND_RATIO_MIN_SAMPLE=10`）——开篇 2 章 100% Quest 不算失衡，样本不足时明确写「不对占比下结论」 |
| **S4 角色·关系·称谓漂移** | 称谓跨章变化、角色掉线、状态取值振荡 | `index` 的 `appearances` / `state_changes` / `relationships` | **只把「角色」的称谓变化报为结论**；地点/物品/组织（如「7 号楼电梯轿厢」→「电梯」）只进报告表格，避免噪音淹没真结论 |
| **S5 文体漂移** | 平均句长、最长句、段落长度、对话占比的跨章偏离；重复片段（口癖候选） | `正文/*.md` 直读 | 偏离判定要求**基线章数足够**；2 章样本只报原始数字不判偏离。口癖表只列**极大重复**片段（≥4 字、≥3 次），并剔除长句内的重叠滑窗——否则一段 30 字的句子会贡献 27 个互相重叠的 4-gram，表格被 `鞋尖朝着` 这类跨词碎片淹没 |

**落地后的两个工程质量修正**（都由真书实测暴露）：

- **`--output` 曾被静默忽略**：`--format markdown --output X` 只把 markdown 打到 stdout、不写 X（`main()` 在 markdown 分支提前 return）。现改为「`--output` 对任何非 json 格式都落文件」，`--format markdown` 不带 `--output` 仍只打印。守护：`test_output_flag_writes_file_for_markdown_format`。
- **「✅ 健康」易被误读**：该行实际只评**连续性约束**（最大连续/最大断档），却和「占比 ➖ 样本不足」并列显示，读者会当成「占比也没问题」。现改标为「连续性约束（最大连续 / 最大断档）」并注明「不含占比」。

**连带修掉的判定层缺陷**（都属 B1「判定/呈现层」）：

1. **伏笔逾期假阴性**（`status_reporter._get_foreshadowing_status`）：原用「距埋设章数 `elapsed`」比绝对阈值（50/100 章），**完全不看 `target_chapter`** → 只要缺目标回收章，全部被判「🟢 正常」。现改为以 `remaining = target - current` 为主判据，缺目标章时返回 `NO_TARGET`，全缺时输出「⚠️ 无法判断：N 条伏笔均缺少目标回收章，因此不能得出『伏笔进度正常』的结论」——**绝不写「✅ 所有伏笔进度正常」**。
2. **Strand 阈值硬编码**：原只用 `config` 通用默认值（quest 5 / fire 10），与题材 profile（规则怪谈 quest 4 / fire 15）**两个方向都可能不同**，对所有题材统一误报。现优先读题材 profile。

**入口**：`webnovel.py scope-audit --project-root ... [--checks S1,S4,S5] [--from-chapter N] [--to-chapter N] [--format markdown|text] [--output PATH]`；skill 见 `skills/webnovel-audit/SKILL.md`。

**Skill 口径 14 → 15**：与 E1 相同的那 4 处（`README.md:81`、`docs/architecture/overview.md`、`webnovel-writer/README.md:11/:18`、`docs/operations/operations.md:89`）同步改为 15，`docs/guides/commands.md` 增 `webnovel-audit` 章节，`references/index/reference-loading-map.md` 把 audit 登记进「无独立 reference」表，`evals/fixtures/behavior/fast.json` 增 `skill_audit_contract` 用例（行为评测 23/23 PASS）。**不要**引用 `references/genre-profiles.md` 这种相对 skill 目录的路径——prompt integrity 会判定文件不存在，须写 `../../references/...`。

### A.4 只读消费者「构造即写库」（2026-09-18 修）

**症状**：`ScopeAuditor` 明明以只读方式打开 `index.db`，跑完 S1 后库文件的**文件头字节仍被改写**（`change_counter` 6 → 58）。

**根因（两层）**：

1. `IndexManager.__init__` 会调 `_init_db()`（十几条 `CREATE TABLE IF NOT EXISTS` + `commit()`），是个**有写副作用**的构造器；而 `StatusReporter.__init__` 直接构造它 → **任何纯读取方（doctor / status / scope-audit）只要拿到 reporter，就会写库**。修法：`_index_manager` 改**惰性 property**，只在真正读实体/关系索引时才建连。
2. 仅 `sqlite3.connect(path)` + `PRAGMA query_only=ON` **不够**——连接建立/首次读取时 SQLite 仍会改写文件头。修法：`scope_audit._connect_index()` 改用 `path.resolve().as_uri() + "?mode=ro"`（`uri=True`）。

**守护**：`test_audit_does_not_modify_chapter_files_or_index`（正文 + `index.db` 字节级不变，含 S1 路径）、`test_constructing_reporter_does_not_write_index_db`（构造 reporter + 跑 Strand 分析后字节不变）。

> **诚实边界**：这两个测试**只覆盖字节面**。`IndexManager` 仍是有写副作用的构造器（`get_*` 系方法走 `_get_conn()` 时库可能被 `_init_db` 建表），所以「哪些消费方算只读」这件事**没有静态保证**，只有「谁先碰 `_index_manager`」这一条动态约定。

### A.5 文风档案：`webnovel-style-learn` skill + `style-profile` CLI（2026-09-21 落地）

**动机**：文风此前只有三个互不相通的入口——`/webnovel-learn` 存单条手写经验（`project_memory.json.patterns`）、`设定集/风格契约.md` 靠手写、`scope_audit` S5 只做「本次范围内的自基线偏离」。**没有任何一处回答「这本书的既定文风是什么、要往哪改成什么样子」**，于是每次润色都从零判断。

**交付**：

| 组件 | 位置 | 职责 |
|------|------|------|
| `webnovel-style-learn` | `skills/webnovel-style-learn/SKILL.md` | 编排 `build` / `show` / `diff`，源 A/B 口径约束，归纳段维护规则 |
| `style_profile.py` | `scripts/style_profile.py` | 统计 + 差异 + 落盘（`build` 只重构数字段，归纳段原样保留） |
| `style_metrics.py` | `scripts/data_modules/style_metrics.py` | 文体计量原语，**S5 与文风档案共用一份**（切句/CJK 计数/极大重复过滤三处历史坑只修一次） |

**产物**：`.webnovel/style_profile.json`（机读 + `injection_digest` ≤1500 字）+ `文风/文风档案.md`（人读，脚本段与归纳段以 `<!-- STYLE-PROFILE:... -->` 分隔）。

**四条口径约束（写死在实现里）**：

1. 源 A（`正文/`）默认只取 `.story-system/commits` 里 `status=accepted` 的章；未提交/被拒稿需 `--source all` 显式越权，且该选择会写进档案的「口径提示」。
2. 现状（`observed`）与目标（`target`）**分开存放、分开注入**，冲突以目标为准；现状永不自动升级为标准。
3. 源 B（`文风/`）只存统计特征 + 每文件 ≤120 字机械样本，不复制参考书正文。
4. 样本不足 3 章时不给「正常」结论；无目标画像时显式输出「无目标画像」。

**消费接线**：`memory_contract_adapter._load_style_profile_digest()` 作为 `load_context` 第 10 段（截断 1500 字，损坏/缺失一律静默跳过，**不阻断写作**），`agents/context-agent.md` 的任务书第 4 段消费。

**连带改动**：`init_project.py` 建 `文风/`；`backup_manager._selected_backup_paths` 与 `_allowed_snapshot_path` 收 `文风/` 与 `.webnovel/style_profile.json`（实测 `git ls-files` 可见）；skill 口径 15 → 16（`README.md` mermaid、`webnovel-writer/README.md` 组件表与清单、`docs/architecture/overview.md` 架构图、`docs/operations/operations.md`、`docs/guides/commands.md`、`reference-loading-map.md` 覆盖度 16/16 与登记行）。

**已知代价与残留**：① `文风/` 进版本点意味着参考文本会随 Git 提交，体积/版权风险由作者控制（文档已写明「不要放整本书」）；② 源 B 的「模仿」效果依赖作者往目录里放什么，本 skill 不做质量判断；③ ~~新增 N7（`style_sampler` 死代码未清）与 N8（S5 未复用档案基线，跨卷不可比）~~ → **两项已于 2026-09-21 关闭，见 A.6**。

**守护测试**：`test_memory_contract_adapter.py::TestLoadContextAuthorStyle`（摘要注入/缺失省略/损坏不崩/截断）、`test_scope_audit.py`（抽模块后 S5 输出逐字段不变）、`test_style_profile.py`（24 项：accepted 口径、抽样上限、目标缺失不冒充、红线判定、归纳段保留、**重复 build 不膨胀**、CLI 往返）、`test_prompt_integrity.py`（新 skill 的 frontmatter、reference 可解析、`style-profile` 已注册）、`evals/fixtures/behavior/fast.json` 的 `skill_style_learn_contract`。

> **过程中的两个真实缺陷**（均已修 + 有回归测试）：① 说明文字里出现标记字样时，`partition(BEGIN)` 会从引用处切开，把旧脚本段当人工内容重拼一次——档案随每次 `build` 膨胀（改为 `rfind(BEGIN)` + 其后首个 `END`）；② 「去标题」逻辑按行首字符判断，遇到标记前的空行就停住，导致旧说明块累积（改为「只跳一层 h1 + 其后连续 `>`/空行」）。

### A.6 N7 / N8：死代码清理 + S5 复用档案基线（2026-09-21 落地）

| 原编号 | 原缺口 | 处置 | 落地证据 |
|--------|--------|------|----------|
| **N7** | `style_sampler` 是死代码（0 条数据、0 生产调用），与文风档案构成两套并行实现 | **整体删除**，不保留 deprecated 别名——留一个已废弃入口只会继续吸引后来者接线 | 删除 `scripts/data_modules/style_sampler.py` 与其测试 `test_style_sampler_cli.py`；`data_modules/__init__.py` 去掉 `StyleSampler`/`StyleSample`/`SceneType` 的 `__all__` 与惰性导出；`webnovel.py` 去掉 `style` 子命令三处注册（`PASSTHROUGH_TOOLS` / `add_parser` / dispatch）；`test_data_modules.py` 去掉 `TestStyleSampler`；`skills/webnovel-query/references/system-data-flow.md` 去掉模块表行；`test_coverage_boost.py::test_webnovel_passthrough_style` 改为反向护栏「`webnovel style` 必须 invalid choice（退出码 2）」 |
| **N8** | S5 用「本次范围内章节的中位数」自基线，范围一变基线就变，**跨卷不可比**；档案已产出持久基线却无人读 | S5 新增**基线解析**：`--baseline auto`（默认，档案可用就用档案）/ `profile`（强制要求档案，拿不到就**不下偏离结论**并报 `profile_baseline_unavailable`）/ `range`（旧口径可主动取回）。payload 新增 `baseline_mode` 与 `baseline`（`source` / `avg_sentence` / `dialogue_paragraph_ratio` / `profile.{chapter_count,source_mode,generated_at,last_chapter}`），markdown 报告新增基线来源行 | `scope_audit.py`：新增 `_load_profile_baseline()`（缺失/损坏/异族 schema/样本不足/非数字中位数一律回落且**从不抛异常**）、`_max_int()` 容错；档案常量 `PROFILE_JSON_NAME` / `PROFILE_SCHEMA_VERSION` 上提到 `data_modules/style_metrics.py` 单一来源，`style_profile.py` 与 `memory_contract_adapter.py` 改为引用它 |

**为什么默认复用档案**：`--baseline range` 下「第 80 章句长是否漂移」取决于这次传了什么范围，而不是这本书的既定文风；只审 1 章时基线就等于该章自己，**永远判不出漂移**（旧行为是样本不足直接不下结论）。档案基线另有两条如实披露的代价：结论**依赖档案时效**（档案最新样本早于审计上界时报告会提示重跑 `build`）、档案若是 `--source all` 越权口径也会被点名。

**测试环境补齐**：本机管理版解释器缺 `fastapi/httpx/watchdog`，3 个 dashboard 测试此前一直跳过。本次在 `.../python/envs/dash`（`--system-site-packages`）装了这三个包，dashboard 测试首次可跑——顺带把 `文风/` 加入只读浏览白名单（`dashboard/app.py` 抽 `doc_dirs` 单一常量，前端徽章同步），并补了 2 条白名单守护测试（`文风/` 可读、白名单外目录 403）。

**守护测试**：`test_scope_audit.py` 新增 8 条（档案优先于自比、`range` 可主动取回、无档案回落并写明、`profile` 模式拒绝静默回落、样本不足拒绝、损坏/异族 schema 忽略、陈旧与越权口径提示、**消费 `build_profile` 真产出的集成口径**）；`test_coverage_boost.py` 的 `style` 反向护栏；`test_dashboard_security.py` 的 2 条白名单测试。

---

### A.7 新增 `webnovel-chapter-discard`（2026-09-21，非缺口推导）

「整章不要了」此前只能让作者自己敲 `git switch -c rewrite-from-chXXXX chXXXX`，且没有任何前置检查——脏工作区、中间章、缺版本点都会在执行一半时才暴露。本次把它变成一等能力，同时**明确拒绝**给已提交章做外科手术式删除。

| 决策 | 理由 |
|---|---|
| 只允许抛弃**最后一章** | 中间章会让后续章失去前置章，必须连带回退或重写，超出该能力范围 → `downstream_chapters_exist` 阻断 |
| 草稿走归档删除，不走 git | 未提交章没有版本点；删除前整批归档到 `.webnovel/discarded/chapter_NNN_<时间戳>/`（保持项目内相对路径，可原样复制回去），再删正文 / 本章 artifacts / 审查报告 / 非 accepted commit，并清理章级 state |
| 已提交章走版本点回退 | `state.json` 的 `plot_threads` / `strand_tracker` / `protagonist_state` / `world_settings` 是累计字段，`index.db` 的 `entities.first_appearance` / `last_appearance` 也不随 `retract()` 回退（`index_projection_writer.py` 已知取舍）→ 只删正文与投影行会留下「读模型说没写过、状态记得写过」的书 |
| 第 1 章回退到仓库初始提交 | 没有 `ch0000`；`git rev-list --max-parents=0 HEAD` 唯一时用它，分支名 `rewrite-from-start`；不唯一则阻断转人工 |
| 回退不删除提交 | `git switch -c` 只移动 HEAD，原分支仍指向被抛弃的提交，`git show <原分支>:正文/第N章-*.md` 可取回 |

**落地证据**：`scripts/data_modules/chapter_discard.py`；`webnovel.py` 新增 `chapter-discard` 子命令（`--dry-run` / `--draft` / `--rollback`）；`skills/webnovel-chapter-discard/SKILL.md`；`scripts/data_modules/tests/test_chapter_discard.py`（22 条）；`evals/fixtures/behavior/fast.json` 的 `skill_chapter_discard_contract`；6 处 skill 文档同步（两个 README / overview / operations / commands / reference-loading-map，16 → 17）。

**测试环境注记**：本机文件系统虚拟化会让「仓库树内 `git switch`」吞掉落盘，故 git 行为先在**工作区外**（`%TEMP%`）端到端验证（草稿删除 / 已提交拒绝 / 中间章拒绝 / 版本点回退 / 第 1 章回退初始提交 / 脏工作树拒绝 六组，全绿），再落 pytest。

> **修订（v6.6.0，2026-09-22）**：上表两条已不反映当前实现——`git switch -c rewrite-from-*` 与 `rewrite-from-start` 分支方案已在 v6.6.0 撤销，改为**原地回退**（`git read-tree -u --reset <版本点>` + 当前分支追加「抛弃第 N 章」提交，不新建分支）；第 1 章仍回仓库初始提交，但不再另开分支。本附录保留 2026-09-21 当时的决策记录，**以 `skills/webnovel-chapter-discard/SKILL.md` 为准**。

### A.8 卷纲修改的既定事实锁定（2026-09-24，非缺口推导）

新规则：**卷纲修改不得改动既定事实**——卷纲只演进「尚无章纲覆盖」的部分，已被已存在章纲落实的内容不得修改；正文经由章纲与卷纲关联，章纲锁定即意味着正文同样不动。

| 决策 | 理由 |
|---|---|
| 锁定区以**已存在章纲**为基准（非 accepted commit） | 章纲存在即代表卷级决策已落地到章级；即使正文未写，再改卷纲对应内容也会让二者失配 |
| 锁定区 ⊇ 总纲「冻结线」 | `outline-revise` 冻结线 = 最新 accepted commit；已 accepted 的章必然已有章纲，反向不成立 → 卷纲可改区比总纲可修订区更窄 |
| `chapter-plan` 删掉「除非用户明确要求覆盖」口子 | 该口子让锁定区可从下游绕过——卷纲改不动的内容，却能靠重跑 chapter-plan 变相改写 |
| 跨章卷级要素划为**敏感区**（需逐项声明影响）而非直接锁定 | 卷末高潮、整卷倒计时、伏笔弧线无法按章号切分，一刀切锁定会让卷纲完全不可改 |
| `volume-reload` 输出 `protected_chapters` / `open_chapters` / 作用域声明 | 可验证口径：报告必须自证「本次只写入卷级文件、锁定章未改」，并有逐字节 hash 测试兜底 |

**落地证据**：`volume_planning.py` 的 `_chapter_fact_state()` / `describe_volume_scope()` 与 `format_volume_reload_report` 分流；`webnovel-volume-revise` / `webnovel-volume-reload` / `webnovel-chapter-plan` / `webnovel-write` 四个 SKILL.md；`test_volume_planning.py`（5→8）、`test_prompt_integrity.py`（146→150）；`fast.json` 的 `skill_chapter_plan_contract` 与 `skill_volume_revise_contract`。

### A.9 四层方向透传（2026-09-24，非缺口推导）

新规则：**总纲 → 卷纲 → 章纲 → 正文是一条单向链**，任一层变化时都要检查与紧邻上下层是否同向；不同向时由作者裁决改哪一侧，裁决逐级上溯，总纲是上溯终点。A.8 的「锁定区」只是本模型在总-卷节点的近似实现。

| 决策 | 理由 |
|---|---|
| 统一规律：**每层只有「最后一个单位」可改** | 卷/章/正文三个节点的边界同构，用一条规律描述比三套独立规则更易守；`master_to_volume` 限最后一卷、`volume_to_chapter` 限最后一章、`chapter_to_body` 限最后一章正文 |
| 裁决三值跨节点通用（`align_downstream` / `align_upstream` / `accepted_deviation`），不新增 4 个节点专用值 | 与章-正节点既有的 `outline_to_body` / `body_to_outline` 语义一一对应，旧值保留为别名；新增 7 值全链会让每层多一套枚举 |
| **机器只给候选清单，不给硬判定** | 总纲与卷纲是散文，无法做语义等价比对；`check_handoff` 只输出「锚点差集」（总纲卷表 vs `volumes_planned`、章纲关键实体 vs 上游文本、`fulfillment_result` 偏离），散文层一律列为「须裁决项」 |
| 上溯时 **target 单位要换算** | `volume_to_chapter` 的 target 是章号，上溯到 `master_to_volume` 时须换算成所属卷号；`next_steps` 负责给出正确命令 |
| 章-正末端约束落在**代码**而非仅提示词 | `body_edit_boundary()` + `reload_chapter_body` / `reconcile_chapter_body` 的 `handoff_target_locked` 阻断；`--backup-only` 豁免，尚无正文的章属首次写作不受约束 |
| 裁决记录写 `.story-system/handoffs/`，与 `reconciliations/` 分工 | `reconciliations` = 章内履约对账（哪个 CBN/CPN 没写到位）；`handoffs` = 层间方向（章纲与正文整体是否同向，卷纲与总纲、章纲与卷纲） |
| 统一话术源：`references/handoff/direction-handoff.md` | 七个 skill 原本各自表述边界（volume-revise 说「锁定区」、outline-revise 说「冻结线」），统一到一份文档并加守护测试，避免再次漂移 |

**落地证据**：新增 `direction_handoff.py`（`HANDOFF_NODES` / `HANDOFF_DECISIONS` / `describe_handoff_scope` / `check_handoff` / `record_handoff` / `body_edit_boundary`）；`webnovel.py` 注册 `handoff` 子命令；`chapter_reloading.py` 两处边界阻断；新增 `references/handoff/direction-handoff.md`；七个 SKILL.md（`plan` / `chapter-plan` / `outline-revise` / `volume-revise` / `volume-reload` / `chapter-revise` / `chapter-reload`）；新增 `test_direction_handoff.py`（24 条）、`test_prompt_integrity.py`（150→162）；`fast.json` 五个契约增补。

---

## 附录 B：B1 供数断点实测（2026-09-18）

真书 `D:\projects\webnovel-live\本小区禁止抬头`，`latest_accepted_chapter=2`、投影五项全 `done`、`doctor` blocking=0。**所有数字均为该状态下实测**，不是推断。

### B.1 三个容器从建表起就没被写入过

| 容器 | 行数 | 写入口 | 写入口的调用方 |
|------|-----:|--------|----------------|
| `chapter_reading_power` | **0** | `save-chapter-reading-power`（`index_manager.py:1387`） | **0**（除自身 parser+handler 外全仓无调用；`skills/` 内零引用） |
| `chase_debt` / `debt_events` / `override_contracts` | **0 / 0 / 0** | `create-debt` / `accrue-interest` / `pay-debt` / `create-override-contract` / `fulfill-override` | **0**（同上，`skills/` 内零引用） |
| ~~`style_sampler` 存储~~ | **0 条** | ~~`style extract` / `style select`~~ | 该模块已于 2026-09-21 整体删除（N7/A.6），本行保留为当时的实测记录 |

连带后果（实测）：

- `index get-hook-type-stats` → `{"data": {}}`；`index get-pattern-usage-stats` → `{"data": {}}`；`index get-overdue-debts --current-chapter 3` → `{"data": []}`；`style stats` → `{"total": 0, "by_type": {}, "avg_score": 0}`。
- 故「伏笔逾期」「hook 强度趋势」「口癖漂移」三条**不是缺检查器，是缺数据源**。
- 债务侧**设计是全的**：`genre-profiles.md` 已定义 `debt_multiplier` / `payback_window_default`，`index get-overdue-debts` 也有实现 —— 但**没有任何链路往里写一条数据**。定性为「**设计完整、实现为 0**」。

> 反例（唯一的例外）：`review_metrics` **有**写入方 —— `review_pipeline.py:215` 会落库，实测 2 行。它是当前唯一能被消费的观测数据源，但受 N6 限制（恒 100 分）。

### B.2 三个「差一步」——数据够，只缺比较器

| 域 | 现状 | 差什么 |
|----|------|--------|
| **Strand** | `state.strand_tracker` 实测完整：`{"last_quest_chapter":2,"last_fire_chapter":0,"last_constellation_chapter":0,"current_dominant":"quest","chapters_since_switch":2,"history":[{"chapter":1,...},{"chapter":2,...}]}` | **只差比较器**：拿 `genre-profiles.md` 阈值比一下即可，数据与阈值**都已就绪** |
| **伏笔清单** | 9 条，`urgency` 分层良好（90/80/70/65/60/60/60/55/30），memory 侧可查（`memory-contract get-open-loops` 正常返回） | 缺 `target_chapter`（N4）→ **只能排序，不能判逾期** |
| **review 趋势** | `quality_trend_report.py` 存在且**能跑**（实测生成 `.webnovel/reports/quality-trend.md`） | ① `webnovel.py` **未注册入口**（`grep quality scripts/data_modules/webnovel.py` 为空）→ 只能直接跑脚本；② `skills/` 内零引用；③ 受 N6 限制输出恒为「均分 100.0 / 整体稳定」 |

**Strand 阈值对照（悬疑/推理 `mystery`，实测当前合规）**：`strand_quest_max: 8`、`strand_fire_gap_max: 20`、`stagnation_threshold: 3`、`transition_max_consecutive: 2`、`hook_config.chapter_end_required: true`、`micropayoff_config.min_per_chapter: 1`、`coolpoint_config.density_per_chapter: low`。→ 当前 Quest 连续 2 章（< 8）、Fire 断档 2 章（< 20），**都在阈值内**；这个判定**今天就能算**。

### B.3 漂移域三条子链，全部断在供数

| 子链 | 应有数据 | 实测 | 口径是否已有 |
|------|----------|------|--------------|
| 文体/句长/口癖漂移 | ~~`style_sampler`~~ + `chapter_reading_power.coolpoint_patterns` | 0 条 / 0 行 | **已另行闭环**：S5 直接统计 `正文/*.md`（不依赖任何表），`style_sampler` 已于 2026-09-21 删除（A.6） |
| 钩子强度/爽点分布 | `chapter_reading_power.hook_type` / `hook_strength` | 0 行 | **有**：`reading-power-taxonomy.md` 有完整钩子分类 + **每题材偏好强度**（悬疑 → 共情/恐惧 `medium`、真相vs安全 `strong`） |
| 微兑现密度 | `chapter_reading_power.micropayoffs` | 0 行 | **有**：`micropayoff_config.min_per_chapter` |

**根因**：`agents/reviewer.md` 内 `hook` / `reading_power` / `追读` / `微兑现` **零命中** —— 即产出链上**没有任何角色负责生成这些字段**。`agents/data-agent.md:89` 只把 `urgency`（0-100 标度）写进伏笔事件，**不是 hook 类型**。

### B.4 建议拆分：`webnovel-audit` 的五个检查器

| 检查器 | 内容 | 供数是否就绪 | 归属批次 |
|--------|------|--------------|----------|
| **S1 Strand 配比** | `strand_tracker` × `genre-profiles` 阈值 → 主线连续超限 / 感情线断档超限 | ✅ **就绪** | **批一** |
| **S4 角色·关系·称谓漂移** | `appearances.mentions`（称谓数组）+ `state_changes`（字段级 old→new）+ `relationships`（带 chapter）→ 出场断档、状态跳变、称谓漂移 | ✅ **就绪**（实测 `elevator_man`：ch1 `["中年男人","那人"]` → ch2 `["那个穿深色夹克的男人","那人"]`，即该类信号的原料；**N2 孙万山称谓问题正属此列**） | **批一** |
| **S5 文体漂移** | 句长分布 / 段长 / 对话占比 / 高频 N-gram，**直接从 `正文/*.md` 统计** | ✅ **就绪，且不依赖任何表** | **批一**（最省力的一刀） |
| **S2 伏笔回收节奏** | 未回收清单 × 年龄 × 分级 → 逾期预警 | ❌ 需先补 N3（归一化）、N4（`target_chapter`） | 批二 |
| **S3 钩子强度 / 爽点 / 微兑现分布** | `chapter_reading_power` × `reading-power-taxonomy` × `genre-profiles` | ❌ 需先补供数：让产出链生成 `hook_type`/`hook_strength`/`micropayoff` | 批二 |

**要点**：批一三个检查器**不需要新增任何数据结构**，只需读已有字段 + 读正文；批二才依赖 N3–N6 的修复。这也意味着 **B1 的正确切法是「先做能做的三个」，而不是等供数补全再做**。
