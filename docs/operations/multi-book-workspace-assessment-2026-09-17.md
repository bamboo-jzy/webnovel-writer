# 多书（工作区）能力评估：它到底是什么，有没有使用场景

日期：2026-09-17
评估对象：`webnovel-writer/scripts/project_locator.py` + `webnovel.py use` + `init_project.py` 的指针写入
方法：读代码 + **实测**（探针脚本见文末「复现方式」）
结论状态：**已定稿并落地——采用方案 A「降级为单书工作区」（一个工作区一本书）**，见 §6.1、§6.2

---

## 1. 一句话结论

项目里没有"多书管理"，只有**"当前书定位"**——它是一个**工作区级的 project_root 解析机制**。

- 它**不是可选功能**：每个 skill 的 bootstrap 都写着
  `export PROJECT_ROOT="$(python ... --project-root "${WORKSPACE_ROOT}" where)"`，
  解析链路不通，**全部 14 个 skill 都起不来**。
- 它**没有管理半边**：没有书单、没有各书状态、没有"我现在绑的是哪本"的回显；`use` 只写不读，`--help` 里 `list`/`books`/`projects`/`switch` 命中数均为 **0**（实测）。

所以问题不是"多书管理有没有用"，而是"**你需不需要一个工作区里放多本书**"。答案见表 §4。

---

## 2. 机制构成（三件东西，两层）

| 组成 | 位置 | 作用 | 写入方 |
|---|---|---|---|
| 工作区指针 | `<workspace>/.claude/.webnovel-current-project`（纯文本，一行绝对路径） | 工作区 → 当前书 | `init`（`init_project.py:686-692`）、`use`（`webnovel.py:438`） |
| 用户级 registry | `${CLAUDE_HOME:-~/.claude}/webnovel-writer/workspaces.json` | workspace → `current_project_root` | `update_global_registry_current_project()`（`project_locator.py:191-236`） |
| 目录规约 | 子目录名 `webnovel-project/`、唯一子书兜底 | 无指针时的启发式 | — |

解析顺序（`resolve_project_root`，`project_locator.py:349-427`）：

```
显式 --project-root 给定:
  ① 本身就是书项目（含 .webnovel/state.json）→ 用它
  ② 指针文件（向上找，止于 git root）          → 用它
  ③ 唯一子书（工作区内恰好 1 本）              → 用它
  ④ 用户级 registry（精确匹配 → 前缀匹配）      → 用它
  ⑤ 否则 FileNotFoundError

未给定:
  ① WEBNOVEL_PROJECT_ROOT 环境变量
  ② cwd 向上的指针文件
  ③ 用户级 registry（只有存在 CLAUDE_PROJECT_DIR 时才允许 last_used 兜底）
  ④ cwd / cwd/webnovel-project / 各级父目录扫描
```

注意 registry 只存**每工作区当前一本**（`workspaces[ws].current_project_root`），
没有任何"这个工作区有哪些书"的字段 → **结构上就不可能列出书单**。

---

## 3. 实测证据

### 3.1 无指针 + 两本书 → 直接失败

```
[1] where(工作区)  -> <exit=1>
    未找到有效书项目根目录（需要包含 .webnovel/state.json）: .../novels
    detail: Not a webnovel project root (missing .webnovel/state.json): .../novels
```

诊断里**没有**"工作区存在 2 本书、无法判定、请 use 指定"这类信息（`cmd_where` → `_project_root_diagnostic`，`webnovel.py:144-167`），作者只会得到"这里不是项目"的误导结论。根因是 `_resolve_unique_child_project_root()`（`project_locator.py:286-299`）在 `len(children) != 1` 时**静默返回 None**。

### 3.2 连续 init 两本 → 指针被静默改绑

```
[init 凡人资本论] Default project pointer updated: ...\.claude\.webnovel-current-project
    where(工作区) -> ...\凡人资本论
[init 剑走偏锋] Default project pointer updated: ...\.claude\.webnovel-current-project
    where(工作区) -> ...\剑走偏锋     ← 凡人资本论 已不可达
```

`init` 的副作用是"**把当前书切到刚建的那本**"，只有一行 stdout，没有覆盖确认、没有"原绑定是 X、现在改为 Y"的提示。**若此时直接跑 `/webnovel-write`，会写进刚 init 的那本新书，全程无警告。**

### 3.3 指针失效（改名/换机器/整理目录）→ 两本书时硬失败

```
[指针失效] 剑走偏锋 -> 剑走偏锋_renamed
    where(工作区) -> <exit=1> 未找到有效书项目根目录...
```

指针存**绝对路径**、registry 存**用户目录**，换机器/移动工作区二者同时失效。单本书时还能靠"唯一子书"自愈（实测 `[8]` 通过），**两本书时彻底失败**，且同样没有"指针指向的 X 已不存在"的提示。

### 3.4 能正常工作的三种布局（实测通过）

| 布局 | `where(工作区)` | 依赖的兜底 |
|---|---|---|
| 工作区根**就是**书项目 | ✅ | ① 无 |
| 工作区下**1 本**子书、无指针 | ✅ | ③ 唯一子书 |
| 工作区下 N 本 + 指针有效 | ✅ | ② 指针 |

### 3.5 其余实测事实

- `use <书路径>` 可用（`exit=0`），能写指针 + registry，是**唯一的切书手段**。
- **`skills/` 里零引用 `use`**（grep 无命中）；只有 `doctor` 的自述里写了"当前书项目"。所以切书这个动作，作者只能自己知道、自己敲。
- 跨书不串味：直接指定某一本时互不干扰（`where(书A)` / `where(书B)` 各自正确）。`session_start` 钩子也用工作区根做 `--project-root`（`hooks/session_start.py:33-50`），走同一条解析链，行为一致。
- 指针文件与 registry 都写在**书项目的 git 仓库之外** → 不随项目备份/迁移走。

---

## 4. 使用场景判定

| 场景 | 多书机制是否必需 | 说明 |
|---|---|---|
| **只写一本书**（当前场景） | ❌ 不需要 | 扁平布局（书目录 = 工作区）或"工作区下唯一子书"都能自动解析；指针存在与否不影响 |
| **双开：主更 + 新书试水** | ✅ 必需 | 同一工作区并列两本；**当前实现有静默改绑风险**（§3.2） |
| **老书维护 + 新书连载**（回头改老书章纲/回退） | ✅ 必需 | 但切书只能靠 `use`，无 skill 暴露；`chapter-revise` 的 bootstrap 永远解析到"当前书"，**想改另一本必须先手动 `use`** |
| **系列文 / 同世界观多本** | ⚠️ 不需要 | 每本独立项目即可；共享的 `references/genres` 是插件级、本来就全局共享，与多书机制无关 |
| **换机器 / 移动工作区** | ⚠️ 退化为负担 | 指针与 registry 双双失效（§3.3）；单本自愈、多本硬失败 |
| **被 subagent / hook 在空上下文调用** | ✅ 必需 | registry 是唯一的兜底（且仅同机器有效） |
| **工作室 / 代笔多本并行** | ✅ 必需 | 同上，且更需要"当前绑的是哪本"的回显——当前没有 |

---

## 5. 缺陷清单（全部实测）

| # | 缺陷 | 位置 | 影响 | 状态 |
|---|---|---|---|---|
| M1 | 多书 + 指针失效 → 报"这里不是项目"，不报"有 N 本书歧义" | `project_locator.py:286-299`、`webnovel.py:154-167` | 误导性诊断，作者无法自救 | **已修**（§6.1） |
| M2 | `init` 静默改绑当前书指针 | `init_project.py:686-692` | 建新书后下一次写作会写进新书 | **已修**（警告不再静默） |
| M3 | `use` 只写不读，无回显、无书单 | `project_locator.py:191-236`；`--help` 无 list/books | 无法确认当前绑的是哪本 | 部分修（应急路径回显原绑定）；A 下不再需要书单 |
| M4 | 指针/registry 均存绝对路径、均在仓库外 | 同上 | 换机器/移动目录失效，且不可版本化 | 留白（§6.3） |
| M5 | `use`、`where` 在 `skills/` 零引用 | grep 全 `skills/` 无命中 | 切书动作对作者不可见 | 随 A 消解（不再有"切书"概念） |
| M6 | registry 写入是 best-effort 且不报错 | `project_locator.py:106-115` | 权限/只读盘下静默丢失，后续"空上下文"解析失败无迹可循 | 留白（§6.3） |

---

## 6. 决策与落地（已定稿）

**最终选择：方案 A —— 降级为"单书工作区"，明确"一个工作区一本书"。**

| 方案 | 做法 | 状态 |
|---|---|---|
| **A. 降级为"单书工作区"** | 明确规约：一个工作区只放一本书，书目录即工作区根（或唯一子目录）。`pointer`/`registry` 变为纯兜底，`use` 从文档移除（降为应急命令）。 | ✅ **已采用并落地**（§6.1） |
| B. 保留机制 + 补齐最小管理面 | 补 M1 的歧义诊断、M2 的覆盖警告、M3 的当前书回显；再考虑 `webnovel-project` skill（list / use / 各书状态） | ❌ 未选：等于把"多书"变成被支持的官方能力，与 A 的定位相反；只借用了它对 M1/M2 的诊断思路 |
| C. 彻底移除 | 删指针写读、registry、`use`，解析退化为"自己/唯一子书" | ❌ 未选：双开与空上下文兜底消失，且要动 `test_project_locator.py`，收益低 |

### 6.1 落地清单（2026-09-17）

规约本身：**一个工作区一本书**。合法布局只有两种——①书目录就是工作区根；②工作区下恰好一个书项目子目录。

| 改动 | 位置 | 内容 |
|---|---|---|
| 单书规约写入模块契约 | `project_locator.py` 模块 docstring | 声明两种合法布局、≥2 本视为违规、指针/registry 只是兜底 |
| 多书歧义异常 | `project_locator.py` `WorkspaceHasMultipleBooksError` | `FileNotFoundError` **子类**（既有 `except FileNotFoundError` 行为不变），携带 `workspace_root` 与 `books` 列表，消息含规约与书名 |
| 子书枚举 | `project_locator.py` `find_child_project_roots()` | 公开、稳定排序；`_resolve_unique_child_project_root()` 改为基于它实现 |
| 歧义诊断接入 | `project_locator.py` `resolve_project_root()` | 显式工作区根分支 + cwd 分支在解析失败且子书 ≥2 时抛该异常（**修 M1**：不再报"这里不是项目"） |
| 指针回读 | `project_locator.py` `read_current_project_pointer()` | 指针无效返回 `None`；供 `use` 回显原绑定、供 init 冲突检测 |
| 冲突检测 | `project_locator.py` `detect_workspace_single_book_conflicts()` | 只读：返回 `workspace_root` / `other_books` / `pointer_target` |
| 作者可见的规约提示 | `webnovel.py` `_multiple_books_diagnostic()` | 列书单 + 三条处理方式（移出多余的书 / `--project-root` 指定 / 应急 `use`） |
| init 警告（**修 M2**） | `init_project.py` `_warn_if_workspace_has_multiple_books()` | 工作区已有其它书时打印规约警告 + "指针已改绑到新书"；**不阻断** |
| `use` 降级 | `webnovel.py` | `--help` 标注"【应急】…单书工作区通常不需要"；执行前回显被替换的原绑定（**部分修 M3**） |
| 文档 | `operations.md`「工作区目录」「工作区指针与用户级 registry」、`guides/commands.md`、`skills/webnovel-init/SKILL.md`、`README.md` | 删除"一个工作区可以包含多本书"，改为单书规约 + 两种合法布局 |
| 报告回写 | `references/index/skill-gap-assessment-2026-09-17.md` A3 / P2 | A3 标为已处置；P2「新增 `webnovel-project` skill」**取消** |

### 6.2 实测证据（探针脚本 8 组场景，全部符合预期）

| 场景 | 结果 |
|---|---|
| S1 工作区根即书项目 | `exit=0`，解析到工作区根 |
| S2 工作区下唯一子书、无指针 | `exit=0`，解析到该子书 |
| S3 两本书 + 无指针 | `exit=1`，输出单书规约 + 书单 + 三条处理方式（修复前：`exit=1`"这里不是项目"） |
| S3b 两本书 + cwd 定位 | 同 S3（两条解析路径一致） |
| S4 两本书 + 指针有效 | `exit=0`，指针兜底生效、不报错 |
| S5 两本书 + 指针失效 | `exit=1`，同上歧义诊断（修复前：`exit=1`"这里不是项目"） |
| S6 连续 init 两本 | 第二次 init 打印"⚠ 单书工作区规约…" + 工作区路径 + 已存在的书清单 + "指针已改绑到刚创建的书"（修复前：无任何提示，`exit=0`） |
| S7 `webnovel use` 指向无效目录 | `exit=1`，输出可读诊断（修复前：`UnboundLocalError` traceback） |
| S8 `webnovel use` 改绑到另一本 | `exit=0`，stderr 回显"⚠ 工作区原绑定的书项目: …"（修复前：静默替换） |

### 6.3 未处置（明确留白）

- M4（指针/registry 存绝对路径、在仓库外）：**保留**。单书规约下这两种记录只是兜底，
  换机器/移动工作区后失效的后果退化为"兜底失效"，而单书布局本身能靠"自己 / 唯一子书"自愈。
- M6（registry 写入 best-effort 静默失败）：同上，兜底失效不阻断主链，不额外处理。
- 双开（一个工作区两本书）**不再是被支持的能力**：指针有效时仍能用（兜底逻辑在），
  但没有任何"切换/回显/书单"入口，也不再承诺该布局可用。

与 `skill-gap-assessment-2026-09-17.md` 的关系：原 A3/P2 把这条记作"多书管理无入口，建议补 `webnovel-project` skill"。本评估修正为：**入口缺失只是表层，真正缺的是"当前书回显 + 歧义诊断"**；采用 A 后，`webnovel-project` skill **取消而非新增**（已回写该报告）。

---

## 7. 复现方式

```bash
# 本次评估（9 组解析链路 + 真实 init 两连发）
"C:/tool/Python314/python.exe" -X utf8 .workbuddy/tmp/probe_multi_book.py        # 解析链路 9 组场景
"C:/tool/Python314/python.exe" -X utf8 .workbuddy/tmp/probe_multi_book_init.py   # 真实 init 两连发 + 指针失效

# 落地后的单书规约回归（6 组，见 §6.2）
"C:/tool/Python314/python.exe" -X utf8 .workbuddy/tmp/probe_single_book.py
```

探针用 `CLAUDE_HOME` / `WEBNOVEL_CLAUDE_HOME` 指向临时目录，**不污染真实 `~/.claude`**。
输出另存为同名 `.out.md`。
