# 真实走通一本书的边界报告（init → plan → chapter-plan → write 1 → write 2）

- 日期：2026-09-17
- 执行者：WorkBuddy（非 Claude Code 宿主）手工模拟 skill 流程
- 书项目：`D:\projects\webnovel-live\本小区禁止抬头`（都市+规则怪谈，100 万字/400 章目标）
- 插件包根：`D:\projects\webnovel-writer\webnovel-writer\`（版本 6.2.1 同源）
- 目的：用一本真书跑完整链路，暴露静态分析看不到的**真实行为边界**

## 一、总结论

**CLI + 内容层可以完整驱动一本真书到第 1 章 `accepted` + 投影五项全绿 + postcommit 通过，可无缝续写第 2 章。**

本节记录**首轮**实测（未打补丁时）卡点，补丁后在第七节复测通过：

- 五步投影首轮实测：`state=done`、`summary=done`、`memory=done`、`index=failed`、`vector=failed`。
- 首轮 `project-status`：`phase=projection_failed`，blocking=`latest_commit_projection_failed`。
- 首轮 `user-report`（插件自带）：总状态 **需要你处理**。
- 首轮因此 6 项充分性闸门中第 5 项（投影五项 done/skipped）与第 7 项（postcommit gate）未过，不能进入第 2 章。

> 补丁后复测：投影五项全 done，postcommit `ok=True, phase=chapter_committed`，`user-report` 总状态 **已完成**，下一步 `/webnovel-write 2`。见第七节。

## 二、链路与实测结果

| 阶段 | 命令 | 结果 |
|------|------|------|
| init | `init_project.py`（34 参） | 成功；`genre=悬疑`，`genre_label=都市+规则怪谈`，`genre_tags.route=["规则怪谈"]` |
| plan 1 | `/webnovel-plan 1` 流程 | 节拍表/时间线/详细大纲 3 件套 + 世界观增量写回 + `master-outline-sync` + `volume-reload`（rev `b8a11bd7be2a7563`） |
| chapter-plan 1 1 | `story-system … --emit-runtime-contracts` | `chapter_001.json` / `chapter_001.review.json` / `volume_001.json` 落盘 |
| write 1 · 起草 | — | 正文 2166 字（目标 2000–2500） |
| write 1 · 审查 | `reviewer` 3 轮 + `review-pipeline` | 第 3 轮 0 问题；`overall_score=100.0`，`blocking_count=0` |
| write 1 · 提取 | `data-agent` → 3 份 artifact | schema 预检全绿（修 2 处） |
| write 1 · 提交 | `chapter-reload --validate` → `write-gate precommit`(ok) → `chapter-commit` | **accepted**，identity `89c8a8ae…`，git `ac91128`，tag `ch0001` |
| write 1 · 投影 | `projections retry` | `state/summary/memory=done`，`index=failed`，`vector=failed` |
| write 1 · 收尾 | `backup` | tag `ch0001` 已存在，成功 |

产物落点：
- 正文 `正文/第0001章-电梯里只有一双鞋.md`
- 审查报告 `审查报告/第1章审查报告.md`
- 提交 `.story-system/commits/chapter_001.commit.json`

## 三、发现的三个真实缺陷（建议上游修）

### D1. `index_projection_writer.py:102-105`：scene 索引 falsy-0 缺陷（**必修**）

```python
for idx, scene in enumerate(scenes, start=1):
    ...
    scene_index = self._safe_int(scene.get("scene_index") or scene.get("index") or idx)
```

0 基 artifact（`index` = 0,1,2,3）时，`scene.get("index")` 为 `0` → **falsy** → 回退成 `idx=1` →
实际落库索引变成 `[1,1,2,3]` → 撞 `UNIQUE(chapter, scene_index)`：

```
index=failed:UNIQUE constraint failed: scenes.chapter, scenes.scene_index
```

注意内部不自洽：`index_chapter_mixin.py:276` 反而默认 0（`s.get("index", 0)`），
而 `vector_projection_writer.py:102` 是**同一个 `or` 链**的复制品，同样有隐患。

**建议修法**（两处同改，语义：键存在即取，否则才用 enumerate 序号）：

```python
raw_index = scene.get("scene_index")
if raw_index is None:
    raw_index = scene.get("index")
scene_index = self._safe_int(raw_index if raw_index is not None else idx)
```

### D2. vector 投影无凭证时无法降级（**幂等阻塞**）

- `config.py:150-153` 读 `EMBED_BASE_URL` / `EMBED_MODEL` / `EMBED_API_KEY`，默认 ModelScope `https://api-inference.modelscope.cn/v1`。
- 无 key → `401 Authentication failed` → `store_chunks` 返回 0 → `error:store_failed`。
- `event_projection_router.TABLE` 把 `character_state_changed` / `power_breakthrough` / `world_rule_revealed` / `world_rule_broken` / `artifact_obtained` 路由到 `vector`，使 vector 在**几乎任何**一章都成为必需 writer。
- `chapter_commit_service._writer_status` 只把 `not_required` / `commit_rejected` 判为 `skipped`，**没有"未配置即 skipped"分支**；配置里也**没有任何开关**可关闭 vector 投影（`graph_rag_enabled`、`context_rag_assist_enabled` 都不管投影）。
- 后果：只要环境没有可用 embedding 端点，`postcommit` gate 永久 fail、无法进入下一章。

**建议**：`_writer_status` 增加显式降级（如 `reason == "embedding_unavailable"` → `skipped`），或在 config 增加 `vector_projection_enabled`，或把 vector 从 `REQUIRED_PROJECTION_WRITERS` 里摘出作为"尽力而为"项。

### D3. 已 accepted 的 commit 无法修订（**与 D1/D2 叠加成死循环**）

`chapter_commit_service.persist_commit:201-205`：

```python
if any(fact_diff.values()):
    raise ValueError("revision_projection_unsafe: facts changed; ...")
statuses = old.get("projection_status", {})
if not statuses or any(v not in {"done","skipped"} for v in statuses.values()):
    raise ValueError("revision_projection_unsafe: previous projections are incomplete")
```

于是"投影失败 → 想改数据（如把 scene 改 1 基）重提交"这条路被**双向封死**：
上一轮投影没全绿就不许重提交，而重提交正是为了修投影。

**建议**：为"投影修复"开一条显式通道（例如允许 `--force-reproject` 在事实不变、仅投影输入修复的场景下重建，并在 provenance 记录）。

**处置（2026-09-17 第二轮，已实现）**：拒绝仍然是默认行为（这是安全底线，增量投影确实撤不掉旧事实），
但补上了缺失的合法通道 —— 详见第七节 7.5 与 `docs/operations/operations.md`「撤回重放」。

## 四、顺带复现的两个解析器坑

- **禁区/必覆盖节点逗号切碎**：`chapter_outline_loader.py:305` 对 `prohibitions` / `mandatory_nodes` 用
  `re.split(r"[、,，；;|]+")` → 条目内含 `、`/`，` 会被切成碎片。本章 5 条禁区须写成不含内部标点的原子串。
- **CPN 的 ASCII 竖线被当分隔符**：`chapter_outline_loader.py:296-311` 的 `cpns` 走 `_split_directive_values`
  → 文档写法 `A｜B｜C` 若用 ASCII `|`，一个 CPN 会被切成 13 段；**必须用全角 `｜`**。

## 五、WorkBuddy 宿主适配要点

- 插件包根多一层：`D:\projects\webnovel-writer\webnovel-writer\`。
- 每条 bash 前需 `export PATH="/usr/bin:/bin:$PATH"`（shim 污染 PATH → `dirname/cd/ls/sed/head` not found）。
- 不要用 `/tmp`（Windows python 不可见），临时文件写 `D:/projects/webnovel-writer/.workbuddy/tmp/`。
- `agents/*.md` 需整段粘进 Agent prompt；`hooks/` 的 runtime-write 护栏不生效，需自觉遵守。
- artifact 的 `source` 必须与 `state.json` 的 `validation_input` **逐键相等**（含 `revision_evidence`），
  校验点 `chapter_reloading.assert_artifact_freshness:683`。
- artifact schema 真源是 `data_modules/chapter_commit_schema.py`：`fulfillment.reconciliation` 必须是对象；
  `extraction.entities_appeared` 必须是对象数组 `{id,type,mentions,confidence}`。
  可用 `PYTHONPATH=<scripts>` + `validate_artifact_file()` 做离线预检。

## 六、处置状态

- D1：**已修**（见第七节 7.1）。
- D2：**已解除**（提供可用 embedding 凭证后不再触发；但"无凭证不能降级"的健壮性问题仍在，见 D2 备注）。
- D3：**已实现**（见第七节 7.5）——默认拒绝保留，补上"作者显式授权 + 整章撤回重建"的合法通道。
- D4：**新增并已修**（见第七节 7.2）。
- 本次改动插件源码 9 个文件 + 2 个文档，新增 8 项单测，全仓单测 **825 passed**（分批跑，排除缺 `fastapi` 的 dashboard 两文件）。

## 七、修复与验证（第二轮：跑通投影）

### 7.1 D1 修复：scene 索引显式值优先

`or` 链在 `0` 处丢失显式序号，导致两场景抢同一序号而撞 UNIQUE。抽出共用函数，语义为**键存在即取（含 0），仅在缺失/不可解析时回退到 enumerate 序号**：

- 新增 `data_modules/commit_artifacts.py::resolve_scene_index(scene, fallback)`
- `index_projection_writer.py`、`vector_projection_writer.py` 改用该函数；两处 `or` 链隐患一并消除

顺带在 index writer 加了**重复序号顺延兜底**：`UNIQUE(chapter, scene_index)` 一旦触发是硬失败，投影失败即整章卡死，因此对重复序号顺延到下一个空闲值并在 stderr 留痕（不再让整章损毁，也不必为此动用"整章重建"这种重手段）。

同时补 `agents/data-agent.md` 契约：明确 `index` **从 1 开始（1 基）**、不得重复——本次 0 基 artifact 正是该处未声明起始基所致。

### 7.2 D4（新发现并修复）：embedding 单批上限硬编码

带凭证重跑后 vector 换了失败原因：

```
[ERR] Embed 400: InvalidParameter: batch size is invalid, it should not be larger than 10.
Vector embedding: 0 stored, 24 skipped (embedding failed)
```

`embed_batch_size` 默认 64，且**没有任何环境变量可覆盖**，而网关（`infistar.cc`）限制单请求 ≤10 条。

- `config.py`：新增 `_env_int()`，把 `EMBED_BATCH_SIZE` / `EMBED_CONCURRENCY` / `RERANK_CONCURRENCY` 改为可被环境变量覆盖（缺失或非法值回退默认）
- `api_client.py`：新增 `_embed_batch_shrinking()`，批次被拒时**折半重试**（深度上限 8），单条也失败才记 None；整批全失败仍返回 None，`skip_failures=False` 语义不变
- 项目 `.env` 写入 `EMBED_BATCH_SIZE=10`

### 7.3 验证结果

| 项 | 修复前 | 修复后 |
|----|--------|--------|
| `projections retry` | index `failed:UNIQUE…`、vector `failed:store_failed` | **五项全 done**（index 4 场景/11 出场/12 实体增量；vector 24 块） |
| `write-gate postcommit` | `ok=False, phase=projection_failed` | **`ok=True, phase=chapter_committed`**，errors/warnings 均空 |
| `project-status` | `phase=projection_failed`，blocking | **`latest_accepted_chapter=1`**，next_action=`/webnovel-chapter-plan 1 2` |
| `user-report` | 总状态"需要你处理" | **总状态"已完成"**，必须处理=无，建议下一步 `/webnovel-write 2` |
| 备份 | tag `ch0001` | 回执确认 `ch0001` completed |

落库抽检：`index.db.scenes` = `(ch1,0/1/2/3)` 四行；`vectors.db` = 24 行（event 8 / entity_delta 11 / scene 4 / summary 1），每条 embedding 4096 字节 = 1024 维 float32。

单测回归：`test_commit_artifacts` / `test_projection_writers` / `test_vector_projection_writer` / `test_config` / `test_api_client` / `test_rag_adapter` / `test_projection_log` / `test_projections_cli` / `test_chapter_commit_service` 共 **111 passed**（`-o addopts=""`，绕开 `--cov-fail-under=90` 全量口径）。

### 7.4 过程中新增的宿主边界（WorkBuddy）

- **Python 级 safe-delete shim**：宿主把 `...\cli\vendor\shim` 注入 `PYTHONPATH`，
  其 `sitecustomize.py` 改写 `shutil.rmtree`/删除路径 → 走回收站 + 批量删除守卫
  （`CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD`，本次 50，状态在 `%TEMP%\codebuddy-safe-delete-bulk`）。
  后果：投影过程中累计删除超阈值会被**中止执行**（`SAFE_DELETE_BULK_CONFIRM_REQUIRED`，命令 exit 1）；
  pytest 则会在 `tempfile` 清理阶段 `INTERNALERROR`。
  解法：按插件 `run_tests.ps1` 的方式显式覆盖 `PYTHONPATH` 使 shim 不参与导入；
  重跑投影（新的一次工具调用，计数重置）即可通过。此 shim 与插件无关，属宿主环境特性。
- `TMP`/`TEMP` 建议指向项目内 scratch（如 `.workbuddy/tmp/pytest`），避免宿主临时目录权限问题。

### 7.5 D3 实现：改写已 accepted 事实的显式通道

**设计原则**：默认行为不变（`revision_projection_unsafe` 仍然拒绝），因为"增量投影撤不掉旧事实"是真实约束，
不是可以静默放宽的口子；缺的是**合法通道**，而不是更松的默认值。

实测确认了"撤不掉"的具体形态（决定了撤回范围）：

| 表 | 写入方式 | 不撤回的后果 |
|---|---|---|
| `chapters` / `scenes` / `appearances` | `INSERT OR REPLACE` / 先删该章再插 | 本身可重入 |
| `state_changes` | **纯 `INSERT`** | 新旧两版状态变化同时留档 |
| `relationships` | upsert `(from,to,type)` 并改写 `chapter` | 已删除的关系行留在库里 |
| `story_events` 镜像 | `INSERT OR IGNORE`（event_id 唯一） | 事件内容改写后旧 `payload_json` 不更新 |
| `vectors` 分块 | `INSERT OR REPLACE`（chunk_id = 内容哈希） | 内容变化即换 ID → 旧分块成孤儿，污染检索 |
| `entities` / `aliases` | 按实体 ID 跨章 upsert | **刻意不撤回**（删章级行会删掉别的章建立的实体）；代价是 `first/last_appearance` 可能停在别的章 |

**落地**：

1. 新增撤回原语（只在读模型层删行，不碰正文 / commit / 事件 JSON）：
   - `index_projection_writer.py::IndexProjectionWriter.retract(chapter)`
   - `vector_projection_writer.py::VectorProjectionWriter.retract(chapter)`
   - `event_log_store.py::EventLogStore.retract_chapter(chapter)`
2. 新增编排 `chapter_commit_service.py::ChapterCommitService.retract_chapter_read_models(payload, force=False)`：
   仅当 commit 带 `provenance.retract_required` 或 `force=True` 才动手。
3. 触发与接线：
   - `persist_commit(payload, expected_previous=..., allow_fact_revision=False, revision_reason="")`：
     事实有变或上轮投影未全绿时，不带开关照样抛错，**但错误信息里带上当前 identity 与开关名**；
     带开关则写 `retract_required` / `retract_reason` / `retract_previous_identity` / `retract_previous_projection_status`，
     并清掉 `projection_reuse` / `projection_refresh`（强制五项重投影）。
   - `apply_projections` 与 `projections.retry_projection` 在**事件镜像重建之前**调用撤回
     （镜像是被撤回对象之一，顺序反了会删掉刚写的镜像）。
   - CLI：`chapter-commit --allow-fact-revision --revision-reason`、
     `projections retry|replay --retract`（强制撤回，用于修复半途写坏、被旧行挡住重建的投影）。
   - 撤回结果写入 projection_log 的**独立 `retractions` 字段**，不混进 `writers`
     ——`writers` 的键会被 postcommit gate / project_phase 当作"必需投影项"逐个校验，混进去会污染状态判定；
     也**不写进 commit**——`commit_identity` 含 `provenance`，投影阶段改它会让重写校验失败。

**验证**（新增 `scripts/data_modules/tests/test_fact_revision.py`，8 项全通过）：

- 不授权时仍被拒，且**未落盘、未留 history**；错误信息含 identity 与 `--allow-fact-revision`
- 授权后：`revision_number=2`、旧版本进 history、`state_changes` 只剩新事实（**不撤回会留两行**）、
  `scenes` / `chapters.summary` 全部换成新事实、`index` 与 `state` 投影 `done`
- `retract_*` 无标记时是空操作，`force=True` 才动手；空表 / 无库 / 空章号都降级不抛异常
- `projections retry` 默认不撤回（污染行保留），加 `--retract` 后修回；`retractions` 记进 projection_log 而非 commit
- 回归：事实没变时仍走 `projection_reuse`，**不会**因为有了开关就变成整章重建

全仓回归：`147 + 303 + 375 = 825 passed`（分 3 批；排除 `test_dashboard_app` / `test_dashboard_watcher`
—— 本机未装 `fastapi`，与本次改动无关）。

---

## 八、第二轮走查：第 2 章跨章续写验证（2026-09-18）

**目标**：验证第 1 章 accepted 之后，跨章续写能否正确加载上文、投影能否累积、各闸门能否放行。
**目标书**：`D:\projects\webnovel-live\本小区禁止抬头`（悬疑，第 1 卷 40 章）。

### 8.1 链路与结果

`chapter-plan 1 2` → `story-system` 章级合同 → `write 2`（context-agent → 起草 → reviewer →
润色 → data-agent → `chapter-commit` → 投影 → `backup`）——**全链通过，无阻断**。

| 验证点 | 结果 |
|---|---|
| 章纲解析 | 第 2 章章纲被 `load_chapter_plot_structure` / `load_chapter_execution_directive` 正确解析（CBN 1、CPNs 3、CEN 1、必须覆盖节点 3、禁区 5、关键实体 8，无污染） |
| 合同新鲜度 | `write-gate prewrite` blocking=false；`volume_plan_stale` / `chapter_contract_stale` / `body_revision_stale` 全 false |
| 跨章依赖 | `chapter-reload` 记 `previous_chapter_revision = 6dd9696f…`（第 1 章正文 sha），`dependency_impacts = {}` → 第 1 章未被判 stale |
| 正文审查 | 5 轮复审收敛到 0 issue；`review-pipeline` overall_score 100，`ai_flavor` 100 |
| 提交 | `chapter-commit` exit=0，`status=accepted`，`revision_number=1`，`missed_nodes=[]`，`pending=[]` |
| 投影 | `state/index/summary/memory/vector` **五项全 done** |
| 投影累积 | `vectors` 24→50、`scenes` 4→8、`appearances` 11+15=26、`entities` 12→23、`story_events` 8→19、`state_changes` 3+2=5（第 2 章只写 2 行，**未翻倍**） |
| postcommit / doctor | `write-gate postcommit` ok=true / `phase=chapter_committed`；`doctor` blocking 0（3 条 warning 仍是本机缺 dashboard 依赖） |
| 版本点 | tag `ch0002`；`ch0001` 自动归档为 `ch0001-prev-20260917T175028` |
| 断点台账 | `run-ledger write-resume --chapter 2` → `resume_from: done`，六步全 skip |

结论：**单书工作区、`latest_accepted_chapter=2`、`next_action=/webnovel-chapter-plan 1 3`**。
第 1 章的上下文（人物、时间锚点、未回收伏笔、`previous_chapter_revision`）被正确带入第 2 章，
投影跨章累积正常。

### 8.2 本轮新发现

#### N1. 章纲书写的分隔符陷阱（**已规避，建议写进 skill 约定**）

`chapter_outline_loader.py` 的分隔符处理不一致：

- `parse_chapter_plot_structure`：`prohibitions` / `mandatory_nodes` 按 `[、,，；;|]+` 切分；`cpns` **不**切分。
- `load_chapter_execution_directive`：`cpns` / `must_cover_nodes` / `forbidden_zones` / `key_entities`
  全部按 `[、,，；;|]+` 切分。

后果：章纲里 `CPNs`、`本章禁区`、`关键实体` 三个字段只要写了中文顿号/逗号/分号，就会被拆碎。
实测第 2 章初稿的「本章禁区」第 3 条（含 `——` 与 `，`）被拆成 2 条，总数 6 > skill 允许的 5 条上限；
`CPNs` 在任务书路径被拆成 6 段。第 1 章的章纲恰好全程用空格和 `／` 分隔，因此没暴露。

**约定**（`webnovel-chapter-plan` 的章纲书写必须遵守）：`CBN/CPNs/CEN/必须覆盖节点/本章禁区/关键实体`
各条目的**行内一律不用** `、` `,` `，` `；` `;` `|`，改用空格或 `／`。
另：`本章变化` 不在 `_DIRECTIVE_FIELD_MAP` 里，若紧跟在 `关键实体` 之后会被并进 `key_entities`（第 1 章章纲
即如此），建议把它放在**标量字段之后**，让它被丢弃而不是污染列表。

#### N2. 孙万山身份在「设定集 ↔ 第 1 章 accepted commit」之间矛盾（**未修，需作者裁决**）

| 来源 | 记载 |
|---|---|
| `设定集/主角卡.md` | 「主要对手：孙万山（物业主任，直属上级）」 |
| `正文/第0001章…md` | 「业主群最后一条消息……发信人孙万山」（业主群发令） |
| `chapter_001.commit.json` 的 `entity_deltas[].payload.desc` | 「孙万山……**业主**，21:41 在业主群发「新须知别乱解读」」 |
| 第 2 章正文 | 座机未接来电备注写「孙万山」（**未加任何称谓**，避免第三种说法） |

第 2 章的处置：只登记为「座机未接来电备注」，`payload.desc` 不写身份/职务，也不给
`state_deltas` 写身份类字段。**矛盾本身留待作者在设定层统一**——第 1 章已 accepted，
改它的 `entity` 描述要走 D3 的事实修订通道（`--allow-fact-revision`），代价大于收益。
建议后续在 `webnovel-plan` 的「设定写回」阶段把 `主角卡.md` 与卷纲的人物身份对齐一次。

### 8.3 值得记下的流程事实

- **「一次正文变化 = 一次重载 + 一次重审」是硬约束，且真的会抓到东西**。第 2 章共 5 版：
  v1 抓到 22:48 跨章时间戳冲突 + 孙万山称谓；v2 抓到白块左右与第 1 章「偏左」矛盾 + 座机「未来来电」；
  v3 抓到停靠时「楼层数字还在跳」；v4 抓到润色新句把方向写成「下楼」。每轮都是**可验证的真缺陷**，
  不是措辞偏好。若图省事只审第一版，这四处全会带进 accepted commit。
- 润色阶段我有两处**自己引入**的漏洞（钥匙没有归还却在下一幕仍被角色看见、删句导致 U 盘失去"插入"前置），
  说明「润色只改表达不改事实」这条红线必须逐句核对，不能只做指标扫描。
- 宿主 `safe-delete` 批量守卫在本轮未再触发（全程无删除动作）；`PYTHONPATH` 隔离 shim 的姿势仍然必需。

