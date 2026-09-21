#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抛弃章节正文：未提交草稿直接删除，已提交章走版本点回退。

为什么不做「外科手术式删除已提交章」
--------------------------------------
`state.json` 的 `plot_threads` / `strand_tracker` / `protagonist_state` /
`world_settings` 都是**累计字段**，不是按章可逆的；`index.db` 的
`entities.first_appearance` / `last_appearance` 也明确不随 `retract()` 回退
（见 `index_projection_writer.retract()` 的已知取舍）。只删正文 + 投影行，
会得到一本「读模型说没写过、状态却记得写过」的书：检索不到该章的场面，
但主角状态、伏笔账、实体首次出场还在。

所以本模块只提供两条安全路径：

1. `discard_chapter_draft` —— 该章**没有 accepted commit** 时，正文、四份
   临时 artifacts、非 accepted 的 commit、章级 state 条目都还在「本章草稿」
   范畴内，可以直接删除；删除前整批归档到 `.webnovel/discarded/`。
2. `rollback_chapter` —— 该章**已 accepted** 时，用版本点回退
   （`git switch -c rewrite-from-chXXXX chXXXX`）让整棵树回到该章之前。
   回退不删除任何提交：原分支仍指向被抛弃的那次提交，随时可取回。

两条路径都只允许处理**最后一章**（序列中不留空洞）。中间章会让后续章
失去「前置章」，必须连带回退或重写，不属于本模块的能力。

章号 off-by-one（最容易踩的坑）
--------------------------------
`chNNNN` 的语义是「第 N 章**完成后**」（`backup --chapter N` = 写完章 Step 6）。
所以抛弃**第 N 章**要回退到 `ch{N-1}`，不是 `chN`。第 1 章没有 `ch0000`，
回退目标是仓库的初始提交（`git rev-list --max-parents=0 HEAD`）。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from security_utils import AtomicWriteError, atomic_write_json

from .chapter_reloading import (
    ARTIFACT_FILES,
    artifact_paths,
    chapter_candidates,
    commit_identity,
    commit_path,
    locked_state,
    read_object,
    revision_entry,
)

SCHEMA_VERSION = "webnovel-chapter-discard/v1"
DISCARD_DIR_REL = Path(".webnovel") / "discarded"
ROLLBACK_BRANCH_PREFIX = "rewrite-from-ch"
INITIAL_ROLLBACK_BRANCH = "rewrite-from-start"
REVIEW_REPORT_DIR = "审查报告"
STATE_REL = Path(".webnovel") / "state.json"

# 章级 status 词汇（`StateManager.CHAPTER_STATUS_ORDER` + rejected）
WRITTEN_STATUSES = ("chapter_drafted", "chapter_reviewed", "chapter_committed")
REJECTED_STATUS = "chapter_rejected"
COMMITTED_STATUS = "chapter_committed"


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def version_point_tag(chapter: int) -> str:
    """版本点 tag 名。语义是「第 N 章**完成后**」，所以抛弃第 N 章要回退 `ch{N-1}`。"""
    return f"ch{int(chapter):04d}"


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git(root: Path, *args: str) -> tuple[bool, str, str]:
    """跑一条 git 命令，返回 (成功, stdout, stderr)。git 不可用时返回 (False, "", 原因)。"""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, ValueError) as exc:
        return False, "", str(exc)
    return proc.returncode == 0, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def _body_candidates(root: Path, chapter: int) -> list[Path]:
    try:
        return chapter_candidates(root, chapter)
    except OSError:
        return []


def _materialized_chapters(root: Path) -> set[int]:
    """已经落到盘上的章号：有正文文件，或有 commit 文件。"""
    found: set[int] = set()
    for folder, pattern in (("正文", "第*章*.md"), (".story-system/commits", "chapter_*.commit.json")):
        base = root / folder
        if not base.exists():
            continue
        for path in base.rglob(pattern):
            if not path.is_file():
                continue
            match = re.search(r"(?:第|chapter_)(\d+)", path.name)
            if match:
                found.add(int(match.group(1)))
    return found


def _state_chapter_status(state: dict) -> dict[str, str]:
    statuses = state.get("progress", {}).get("chapter_status")
    return statuses if isinstance(statuses, dict) else {}


def _written_chapters(root: Path, state: dict, *, exclude: int = 0) -> set[int]:
    """写作意义上的「已写过」：有正文 / 有 commit / state 里登记为已写或已草拟。

    `exclude` 用来剔除正在被抛弃的章：state 清理发生在正文文件删除之前，
    不排除的话会把「即将被删掉的那章」重新算回 `current_chapter`。
    """
    found = _materialized_chapters(root)
    for key, status in _state_chapter_status(state).items():
        chapter = _safe_int(key)
        if chapter > 0 and status != REJECTED_STATUS:
            found.add(chapter)
    revisions = state.get("progress", {}).get("chapter_revisions")
    if isinstance(revisions, dict):
        for key in revisions:
            chapter = _safe_int(key)
            if chapter > 0:
                found.add(chapter)
    found.discard(_safe_int(exclude))
    return found


def _downstream_chapters(root: Path, state: dict, chapter: int) -> list[int]:
    found = {ch for ch in _materialized_chapters(root) if ch > chapter}
    for key, status in _state_chapter_status(state).items():
        later = _safe_int(key)
        if later > chapter and status != REJECTED_STATUS:
            found.add(later)
    return sorted(found)


def _latest_commit_chapter(root: Path) -> int:
    base = root / ".story-system" / "commits"
    if not base.exists():
        return 0
    best = 0
    for path in base.glob("chapter_*.commit.json"):
        match = re.search(r"chapter_(\d+)", path.name)
        if match:
            best = max(best, int(match.group(1)))
    return best


def _row_chapter(value: Any) -> int:
    if not isinstance(value, dict):
        return 0
    for key in ("chapter", "chapter_num", "target_chapter"):
        chapter = _safe_int(value.get(key))
        if chapter > 0:
            return chapter
    return 0


def _count_words(content: str) -> int:
    text = re.sub(r"```[\s\S]*?```", "", content)
    text = re.sub(r"^#+ .*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"---", "", text)
    return len(text.strip())


def _total_words_after(root: Path, state: dict, *, exclude: int = 0) -> int:
    """按 `StateProjectionWriter._project_total_words` 同一口径重算：只数已提交章。"""
    from chapter_paths import find_chapter_file

    total = 0
    for key, status in _state_chapter_status(state).items():
        if status != COMMITTED_STATUS:
            continue
        chapter = _safe_int(key)
        if chapter <= 0 or chapter == _safe_int(exclude):
            continue
        path = find_chapter_file(root, chapter)
        if path is None:
            continue
        try:
            total += _count_words(path.read_text(encoding="utf-8"))
        except OSError:
            continue
    return total


# ---------------------------------------------------------------------------
# 预览 / 检查
# ---------------------------------------------------------------------------

def _rollback_probe(root: Path, chapter: int) -> dict:
    """回退的 git 侧取证：目标版本点、工作树、原分支。"""
    probe: dict[str, Any] = {
        "git_available": False,
        "target_kind": "",
        "target_tag": "",
        "target_commit": "",
        "branch_name": "",
        "branch_exists": False,
        "command": "",
        "source_branch": "",
        "source_commit": "",
        "dirty_paths": [],
        "recovery_hint": "",
        "version_point_tag": version_point_tag(chapter),
        "version_point_exists": False,
        "blockers": [],
    }
    ok, out, err = _git(root, "rev-parse", "--is-inside-work-tree")
    if not ok or out != "true":
        probe["blockers"].append({
            "code": "git_unavailable",
            "message": f"项目不是可用的 git 工作树（{err or out or 'git 未安装'}）；版本点回退需要 git。",
        })
        return probe
    probe["git_available"] = True

    ok, head, _ = _git(root, "rev-parse", "HEAD")
    probe["source_commit"] = head if ok else ""
    ok, branch, _ = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    probe["source_branch"] = branch if ok and branch != "HEAD" else ""

    if chapter > 1:
        target_tag = version_point_tag(chapter - 1)
        probe["target_kind"] = "version_point"
        probe["target_tag"] = target_tag
        ok, commit, _ = _git(root, "rev-parse", f"{target_tag}^{{commit}}")
        if ok:
            probe["target_commit"] = commit
        else:
            probe["blockers"].append({
                "code": "version_point_missing",
                "message": (
                    f"找不到回退目标版本点 {target_tag}（第 {chapter} 章抛弃后应回到「第 {chapter - 1} 章完成后」）。"
                    "请先补齐该版本点，或改用更早的版本点手工回退。"
                ),
            })
    else:
        probe["target_kind"] = "initial_commit"
        ok, roots, err = _git(root, "rev-list", "--max-parents=0", "HEAD")
        root_commits = [line.strip() for line in roots.splitlines() if line.strip()]
        if not ok or len(root_commits) != 1:
            probe["blockers"].append({
                "code": "initial_commit_not_unique",
                "message": (
                    "第 1 章没有 ch0000 版本点，只能回退到仓库初始提交，"
                    f"但初始提交不是唯一可判定的（找到 {len(root_commits)} 个）。请手工 git 处理。"
                ),
            })
        else:
            probe["target_commit"] = root_commits[0]

    branch_name = (
        f"{ROLLBACK_BRANCH_PREFIX}{chapter - 1:04d}" if chapter > 1 else INITIAL_ROLLBACK_BRANCH
    )
    probe["branch_name"] = branch_name
    ok, _, _ = _git(root, "rev-parse", "--verify", f"refs/heads/{branch_name}")
    probe["branch_exists"] = ok
    if ok:
        probe["blockers"].append({
            "code": "rollback_branch_exists",
            "message": f"分支 {branch_name} 已存在；请先处理它（改名或删除），避免覆盖上一次回退结果。",
        })

    target_ref = probe["target_tag"] or probe["target_commit"]
    if target_ref:
        probe["command"] = f"git switch -c {branch_name} {target_ref}"

    ok, porcelain, _ = _git(root, "-c", "core.quotepath=false", "status", "--porcelain", "--untracked-files=no")
    if not ok:
        probe["blockers"].append({
            "code": "git_status_failed",
            "message": "无法读取 git 工作树状态，拒绝在不明确的工作树上做回退。",
        })
    else:
        probe["dirty_paths"] = [line.strip() for line in porcelain.splitlines() if line.strip()]
        if probe["dirty_paths"]:
            probe["blockers"].append({
                "code": "working_tree_dirty",
                "message": (
                    f"工作树有 {len(probe['dirty_paths'])} 个未提交的受管改动；"
                    "先提交或 stash，否则回退会带走/丢弃它们。"
                ),
            })

    if probe["source_commit"]:
        where = probe["source_branch"] or f"detached HEAD（{probe['source_commit'][:8]}）"
        probe["recovery_hint"] = (
            f"回退不删除提交：{where} 仍指向被抛弃的那次提交，"
            f"需要取回时 git switch {probe['source_branch'] or probe['source_commit'][:8]}。"
        )

    ok, vp_commit, _ = _git(root, "rev-parse", f"{probe['version_point_tag']}^{{commit}}")
    probe["version_point_exists"] = ok
    if ok:
        probe["version_point_commit"] = vp_commit
    return probe


def plan_chapter_discard(project_root: str | Path, chapter: int, *, state: dict | None = None) -> dict:
    """只读取证：该章现在是什么状态、能走哪条路、会动哪些文件。不写任何东西。"""
    root = Path(project_root).resolve()
    chapter = _safe_int(chapter)
    plan: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "chapter": chapter,
        "classification": "absent",
        "chapter_file": "",
        "body_candidates": [],
        "body_revision": "",
        "state_registered": False,
        "commit_status": "",
        "commit_path": "",
        "commit_identity": "",
        "latest_commit_chapter": 0,
        "downstream_chapters": [],
        "archive_dir": str(root / DISCARD_DIR_REL / f"chapter_{chapter:03d}_<时间戳>"),
        "planned_actions": [],
        "blockers": [],
        "warnings": [],
    }
    if chapter <= 0:
        plan["blockers"].append({"code": "invalid_chapter", "message": "章号必须是正整数"})
        plan["draft"] = {"allowed": False, "blockers": []}
        plan["rollback"] = {"allowed": False, "blockers": []}
        return plan

    if state is None:
        state = read_object(root / STATE_REL, optional=True)
        if not isinstance(state, dict):
            state = {}

    candidates = _body_candidates(root, chapter)
    plan["body_candidates"] = [str(path) for path in candidates]
    if len(candidates) == 1:
        plan["chapter_file"] = str(candidates[0])
    commit_file = commit_path(root, chapter)
    commit = read_object(commit_file, optional=True)
    commit_status = str((commit.get("meta") or {}).get("status") or "") if commit else ""
    plan["commit_status"] = commit_status
    if commit:
        plan["commit_path"] = str(commit_file)
        plan["commit_identity"] = commit_identity(commit)

    entry = revision_entry(state, chapter)
    plan["state_registered"] = bool(entry) or str(chapter) in _state_chapter_status(state)

    accepted = commit_status == "accepted"
    if accepted:
        plan["classification"] = "accepted"
    elif candidates or commit or plan["state_registered"]:
        plan["classification"] = "draft"
    else:
        plan["classification"] = "absent"

    if len(candidates) == 1:
        try:
            plan["body_revision"] = hashlib.sha256(candidates[0].read_bytes()).hexdigest()
        except OSError:
            plan["body_revision"] = ""

    plan["latest_commit_chapter"] = _latest_commit_chapter(root)
    plan["downstream_chapters"] = _downstream_chapters(root, state, chapter)

    # ---- 通用阻塞 ----
    if plan["classification"] == "absent":
        plan["blockers"].append({
            "code": "chapter_absent",
            "message": f"第 {chapter} 章没有正文、没有 commit、state 里也没有登记，无需抛弃。",
        })
    if len(candidates) > 1:
        plan["blockers"].append({
            "code": "body_candidates_ambiguous",
            "message": f"第 {chapter} 章在 正文/ 下有 {len(candidates)} 个候选文件，不能任选其一删除。",
        })
    if plan["downstream_chapters"]:
        plan["blockers"].append({
            "code": "downstream_chapters_exist",
            "message": (
                f"第 {chapter} 章之后已有内容：{plan['downstream_chapters']}。"
                "只允许抛弃最后一章；中间章会让后续章失去前置章。"
            ),
        })

    # ---- 草稿删除 ----
    draft_blockers: list[dict[str, str]] = []
    if accepted:
        draft_blockers.append({
            "code": "chapter_committed",
            "message": (
                f"第 {chapter} 章已 accepted（commit {plan['commit_identity'][:12]}），"
                "已提交章不能用文件删除，请走版本点回退。"
            ),
        })
    plan["draft"] = {"allowed": not draft_blockers and not plan["blockers"], "blockers": draft_blockers}

    # ---- 版本点回退 ----
    rollback = _rollback_probe(root, chapter)
    rollback_blockers = list(rollback.pop("blockers", []))
    if not accepted:
        rollback_blockers.insert(0, {
            "code": "chapter_not_committed",
            "message": f"第 {chapter} 章没有 accepted commit，不需要回退版本点，直接用草稿删除。",
        })
    rollback["allowed"] = not rollback_blockers and not plan["blockers"]
    rollback["blockers"] = rollback_blockers
    plan["rollback"] = rollback

    # ---- 计划动作 ----
    if plan["classification"] == "draft":
        plan["planned_actions"] = [
            f"归档 正文 下第 {chapter} 章正文（含四份临时 artifacts、非 accepted commit、审查报告）到 .webnovel/discarded/",
            f"删除 正文/第{chapter}章*.md",
            f"清理 .webnovel/tmp 下属于第 {chapter} 章的 artifacts",
            f"清理 state.json 中第 {chapter} 章的 chapter_revisions / chapter_status / chapter_meta / 裁决记录，并重算 current_chapter 与 total_words",
            f"清理 .webnovel/run_ledger.json 中第 {chapter} 章的断点记录",
            f"保留 大纲/第{chapter}章-*.md 章纲，作者可直接重写",
        ]
    elif plan["classification"] == "accepted":
        plan["planned_actions"] = [
            f"确认回退目标版本点（抛弃第 {chapter} 章 → 回到第 {chapter - 1} 章完成后的状态）",
            f"执行 {rollback.get('command') or 'git switch -c <分支> <版本点>'}",
            "回退会同时还原 正文 / .story-system/commits / state.json / index.db / summaries / projection_log",
            "回退不删除提交：原分支仍指向被抛弃的提交，可取回",
        ]
    return plan


# ---------------------------------------------------------------------------
# 归档
# ---------------------------------------------------------------------------

def _archive_target(root: Path, chapter: int) -> Path:
    base = root / DISCARD_DIR_REL
    stamp = _stamp()
    target = base / f"chapter_{chapter:03d}_{stamp}"
    suffix = 1
    while target.exists():
        target = base / f"chapter_{chapter:03d}_{stamp}_{suffix}"
        suffix += 1
    return target


def _copy_into(root: Path, source: Path, archive: Path) -> str:
    """把文件按项目内相对路径复制进归档目录；返回相对路径（源不在项目内时返回文件名）。"""
    try:
        relative = source.resolve().relative_to(root)
    except ValueError:
        relative = Path(source.name)
    destination = archive / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return relative.as_posix()


def _artifact_chapter(payload: Any) -> int:
    """从 artifact payload 里尽力读出章号；读不出返回 0（视为本章）。"""
    if not isinstance(payload, dict):
        return 0
    for key in ("chapter", "chapter_num"):
        chapter = _safe_int(payload.get(key))
        if chapter > 0:
            return chapter
    source = payload.get("source")
    if isinstance(source, dict):
        chapter = _safe_int(source.get("chapter"))
        if chapter > 0:
            return chapter
    start = _safe_int(payload.get("start_chapter"))
    end = _safe_int(payload.get("end_chapter"))
    if start and start == end:
        return start
    return 0


# ---------------------------------------------------------------------------
# 草稿删除
# ---------------------------------------------------------------------------

def discard_chapter_draft(
    project_root: str | Path,
    chapter: int,
    *,
    dry_run: bool = False,
    reason: str = "",
) -> dict:
    """删除未提交草稿。先整批归档到 `.webnovel/discarded/`，再删文件、清章级 state 条目。"""
    root = Path(project_root).resolve()
    chapter = _safe_int(chapter)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "action": "discard_draft",
        "chapter": chapter,
        "dry_run": bool(dry_run),
        "ok": False,
        "reason": reason,
    }
    plan = plan_chapter_discard(root, chapter)
    report["plan"] = plan
    blockers = list(plan["blockers"]) + list(plan.get("draft", {}).get("blockers") or [])
    if blockers:
        report["blockers"] = blockers
        report["error"] = "blocked"
        return report
    if dry_run:
        report["ok"] = True
        report["status"] = "preview"
        return report

    archived: list[str] = []
    removed: list[str] = []
    kept: list[str] = []
    archive = _archive_target(root, chapter)
    try:
        archive.mkdir(parents=True, exist_ok=False)

        # 1) 正文
        body_file = Path(plan["chapter_file"])
        archived.append(_copy_into(root, body_file, archive))

        # 2) 非 accepted commit（可能是 rejected 或半成品）
        commit_file = commit_path(root, chapter)
        if commit_file.is_file():
            archived.append(_copy_into(root, commit_file, archive))

        # 3) 四份临时 artifacts + review_metrics（只动属于本章或无法归属的）
        tmp_dir = root / ".webnovel" / "tmp"
        artifact_paths_map = {key: path for key, path in artifact_paths(root).items()}
        artifact_paths_map["review_metrics"] = tmp_dir / "review_metrics.json"
        for key, path in artifact_paths_map.items():
            if not path.is_file():
                continue
            payload = read_object(path, optional=True)
            owner = _artifact_chapter(payload) if payload else 0
            if owner not in (0, chapter):
                kept.append(f"{path.relative_to(root).as_posix()} (第 {owner} 章)")
                continue
            archived.append(_copy_into(root, path, archive))

        # 4) 审查报告
        review_dir = root / REVIEW_REPORT_DIR
        if review_dir.exists():
            for path in sorted(review_dir.glob(f"第{chapter}章*")):
                if path.is_file():
                    archived.append(_copy_into(root, path, archive))

        # 5) state.json 章级条目
        progress_before: dict[str, Any] = {}
        progress_after: dict[str, Any] = {}
        with locked_state(root) as state:
            progress = state.setdefault("progress", {})
            revisions = progress.get("chapter_revisions")
            if isinstance(revisions, dict):
                revisions.pop(str(chapter), None)
            statuses = progress.get("chapter_status")
            if isinstance(statuses, dict):
                statuses.pop(str(chapter), None)
            chapter_meta = state.get("chapter_meta")
            if isinstance(chapter_meta, dict):
                chapter_meta.pop(str(chapter), None)
                chapter_meta.pop(chapter, None)
            elif isinstance(chapter_meta, list):
                state["chapter_meta"] = [row for row in chapter_meta if _row_chapter(row) != chapter]

            rows = progress.get("chapters_planned")
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict) and _safe_int(row.get("chapter")) == chapter:
                        row["status"] = "planned"
                        row.pop("stale_reason", None)
                        row.pop("content_status", None)

            checkpoints = state.get("review_checkpoints")
            if isinstance(checkpoints, list):
                state["review_checkpoints"] = [
                    row for row in checkpoints
                    if not (isinstance(row, dict) and _row_chapter(row) == chapter)
                ]
            for key in ("disambiguation_warnings", "disambiguation_pending"):
                rows = state.get(key)
                if isinstance(rows, list):
                    state[key] = [
                        row for row in rows
                        if not (isinstance(row, dict) and _row_chapter(row) == chapter)
                    ]

            progress_before = {
                "current_chapter": _safe_int(progress.get("current_chapter")),
                "total_words": _safe_int(progress.get("total_words")),
            }
            progress["current_chapter"] = max(_written_chapters(root, state, exclude=chapter), default=0)
            progress["total_words"] = _total_words_after(root, state, exclude=chapter)
            progress["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            progress_after = {
                "current_chapter": _safe_int(progress.get("current_chapter")),
                "total_words": _safe_int(progress.get("total_words")),
            }

        # 6) 断点账本（派生缓存，坏了只警告，不阻断抛弃）
        try:
            from .run_ledger import load_ledger, save_ledger

            ledger = load_ledger(root)
            write_runs = ledger.get("write")
            if isinstance(write_runs, dict) and write_runs.pop(f"chapter_{chapter:03d}", None) is not None:
                save_ledger(root, ledger)
                removed.append(".webnovel/run_ledger.json (章断点记录)")
        except Exception as exc:  # noqa: BLE001 - 账本损坏不应阻断抛弃
            report.setdefault("warnings", []).append(f"run_ledger 未清理：{exc}")

        # 7) 物理删除（归档成功后才动手）
        body_file.unlink(missing_ok=True)
        removed.append(plan["chapter_file"])
        if commit_file.is_file():
            commit_file.unlink()
            removed.append(commit_file.relative_to(root).as_posix())
        for entry in list(archived):
            path = root / entry
            if path.is_file() and path != body_file:
                rel = path.relative_to(root).as_posix()
                if rel.startswith((".webnovel/tmp/", f"{REVIEW_REPORT_DIR}/")):
                    path.unlink(missing_ok=True)
                    removed.append(rel)

        manifest = {
            "schema_version": SCHEMA_VERSION,
            "action": "discard_draft",
            "chapter": chapter,
            "reason": reason,
            "discarded_at": _now_iso(),
            "chapter_file": plan["chapter_file"],
            "body_revision": plan["body_revision"],
            "allow_commit": plan["commit_status"] or "none",
            "archived": sorted(set(archived)),
            "removed": sorted(set(removed)),
            "kept": sorted(set(kept)),
            "progress_before": progress_before,
            "progress_after": progress_after,
            "restore_hint": f"归档目录里保持项目内相对路径，可原样复制回项目根恢复；章纲未被触碰。",
        }
        atomic_write_json(archive / "discard.json", manifest, use_lock=False, backup=False)

        report.update(
            ok=True,
            status="discarded",
            archive_dir=str(archive),
            archived=manifest["archived"],
            removed=manifest["removed"],
            kept=manifest["kept"],
            progress_before=progress_before,
            progress_after=progress_after,
        )
    except (OSError, ValueError, TypeError, AttributeError, AtomicWriteError) as exc:
        report["error"] = str(exc)
        report["archive_dir"] = str(archive) if archive.exists() else ""
    return report


# ---------------------------------------------------------------------------
# 版本点回退
# ---------------------------------------------------------------------------

def rollback_chapter(
    project_root: str | Path,
    chapter: int,
    *,
    dry_run: bool = False,
    reason: str = "",
) -> dict:
    """已提交章走版本点回退：`git switch -c <分支> <ch{N-1}>`。不删除任何提交。"""
    root = Path(project_root).resolve()
    chapter = _safe_int(chapter)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "action": "rollback",
        "chapter": chapter,
        "dry_run": bool(dry_run),
        "ok": False,
        "reason": reason,
    }
    plan = plan_chapter_discard(root, chapter)
    report["plan"] = plan
    rollback = plan.get("rollback") or {}
    blockers = list(plan["blockers"]) + list(rollback.get("blockers") or [])
    if blockers:
        report["blockers"] = blockers
        report["error"] = "blocked"
        return report
    if dry_run:
        report.update(ok=True, status="preview", command=rollback.get("command", ""))
        return report

    command = str(rollback.get("command") or "")
    target_ref = rollback.get("target_tag") or rollback.get("target_commit") or ""
    branch_name = str(rollback.get("branch_name") or "")
    if not (command and target_ref and branch_name):
        report["blockers"] = [{"code": "rollback_plan_incomplete", "message": "回退计划不完整，拒绝执行"}]
        report["error"] = "blocked"
        return report

    ok, out, err = _git(root, "switch", "-c", branch_name, target_ref)
    report["command"] = command
    report["stdout"] = out
    if not ok:
        report["error"] = err or out or "git switch failed"
        return report

    ok, head, _ = _git(root, "rev-parse", "HEAD")
    report["head_commit"] = head if ok else ""
    target_commit = str(rollback.get("target_commit") or "")
    verified = bool(target_commit) and report["head_commit"] == target_commit
    body_left = _body_candidates(root, chapter)
    state_after = read_object(root / STATE_REL, optional=True)
    current_after = _safe_int((state_after.get("progress") or {}).get("current_chapter"))
    report.update(
        ok=verified and not body_left,
        status="rolled_back",
        target_ref=target_ref,
        target_commit=target_commit,
        branch=branch_name,
        head_matches_target=verified,
        chapter_body_absent=not body_left,
        current_chapter_after=current_after,
        source_branch=rollback.get("source_branch", ""),
        source_commit=rollback.get("source_commit", ""),
        recovery_hint=rollback.get("recovery_hint", ""),
        next_steps=[
            f"当前在分支 {branch_name}，处于「第 {chapter - 1} 章完成后」的状态。",
            f"要写的下一章仍是第 {chapter} 章；章纲 大纲/第{chapter}章-*.md 未被回退影响。",
            "确认无误后继续写作；被抛弃的正文可用 git show 从原分支取回核对。",
        ],
    )
    if not report["ok"]:
        report["error"] = "回退后核对未通过：正文或 HEAD 与预期不一致，请先 git status 手工确认。"
    return report


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------

def format_chapter_discard_report(report: dict, output_format: str = "text") -> str:
    if output_format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2)
    plan = report.get("plan") or {}
    lines = [
        f"{'OK' if report.get('ok') else 'ERROR'} chapter-discard {report.get('action')}",
        f"chapter: {report.get('chapter')}",
        f"classification: {plan.get('classification', '')}",
        f"chapter_file: {plan.get('chapter_file', '')}",
        f"commit_status: {plan.get('commit_status', '')}",
        f"downstream_chapters: {plan.get('downstream_chapters', [])}",
        f"status: {report.get('status', 'preview' if report.get('dry_run') else '')}",
        f"archive_dir: {report.get('archive_dir', '')}",
        f"removed: {report.get('removed', [])}",
        f"kept: {report.get('kept', [])}",
        f"command: {report.get('command', '')}",
        f"current_chapter_after: {report.get('current_chapter_after', '')}",
        f"recovery_hint: {report.get('recovery_hint', '')}",
        f"error: {report.get('error', '')}",
    ]
    blockers = report.get("blockers") or []
    if blockers:
        lines.append("blockers:")
        lines.extend(f"  - {item.get('code')}: {item.get('message')}" for item in blockers)
    warnings = report.get("warnings") or []
    if warnings:
        lines.append("warnings:")
        lines.extend(f"  - {item}" for item in warnings)
    lines.append("抛弃只动本章；章纲与其它章不受影响。")
    return "\n".join(lines)
