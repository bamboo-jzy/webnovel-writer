#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from chapter_outline_loader import (
        chapter_outline_revision,
        chapter_planned_source_revision,
        find_chapter_outline_file,
        volume_num_for_chapter_from_state,
        volume_planning_revision,
    )
    from chapter_paths import find_chapter_file, volume_num_for_chapter
except ImportError:  # pragma: no cover
    from scripts.chapter_outline_loader import (
        chapter_outline_revision,
        chapter_planned_source_revision,
        find_chapter_outline_file,
        volume_num_for_chapter_from_state,
        volume_planning_revision,
    )
    from scripts.chapter_paths import find_chapter_file, volume_num_for_chapter

from .projection_log import degraded_projection_from_run, read_projection_runs, projection_status_from_run
from .chapter_reloading import body_evidence, chapter_revision_evidence, upstream_body_blockers


PHASE_NO_PROJECT = "no_project"
PHASE_UNKNOWN = "unknown"
PHASE_INIT_SCAFFOLDED = "init_scaffolded"
PHASE_INIT_READY = "init_ready"
PHASE_PLAN_IN_PROGRESS = "plan_in_progress"
PHASE_CHAPTER_CONTRACT_READY = "chapter_contract_ready"
PHASE_DRAFT_IN_PROGRESS = "draft_in_progress"
PHASE_READY_TO_COMMIT = "ready_to_commit"
PHASE_CHAPTER_COMMITTED = "chapter_committed"
PHASE_PROJECTION_FAILED = "projection_failed"

PHASES = (
    PHASE_NO_PROJECT,
    PHASE_UNKNOWN,
    PHASE_INIT_SCAFFOLDED,
    PHASE_INIT_READY,
    PHASE_PLAN_IN_PROGRESS,
    PHASE_CHAPTER_CONTRACT_READY,
    PHASE_DRAFT_IN_PROGRESS,
    PHASE_READY_TO_COMMIT,
    PHASE_CHAPTER_COMMITTED,
    PHASE_PROJECTION_FAILED,
)

INIT_REQUIRED_DIRS = (
    ".webnovel",
    ".webnovel/backups",
    ".webnovel/archive",
    ".webnovel/summaries",
    "设定集",
    "大纲",
    "正文",
    "审查报告",
)

INIT_REQUIRED_FILES = (
    ".webnovel/state.json",
    "设定集/世界观.md",
    "设定集/力量体系.md",
    "设定集/主角卡.md",
    "设定集/反派设计.md",
    "大纲/总纲.md",
    ".env.example",
)

COMMIT_ARTIFACT_FILES = (
    ".webnovel/tmp/review_results.json",
    ".webnovel/tmp/fulfillment_result.json",
    ".webnovel/tmp/disambiguation_result.json",
    ".webnovel/tmp/extraction_result.json",
)

_CHAPTER_FILE_RE = re.compile(r"chapter_(\d{3,4})")


@dataclass(frozen=True)
class ChapterCommitInfo:
    chapter: int
    status: str
    path: str
    projection_status: dict[str, str] = field(default_factory=dict)
    projection_source: str = "commit"
    # 降级跳过（如未配 embedding 时的 vector）→ writer: reason。
    # 与 projection_status 的 `skipped` 分开存，因为作者需要知道
    # "这一项不是不需要，而是没条件做"。
    projection_degraded: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter": self.chapter,
            "status": self.status,
            "path": self.path,
            "projection_status": dict(self.projection_status),
            "projection_source": self.projection_source,
            "projection_degraded": dict(self.projection_degraded),
        }


@dataclass(frozen=True)
class ProjectPhaseSnapshot:
    project_root: str
    phase: str
    target_chapter: int
    latest_accepted_chapter: int
    target_volume: int = 0
    latest_commit: ChapterCommitInfo | None = None
    state_current_chapter: int = 0
    missing_init_files: tuple[str, ...] = ()
    missing_init_dirs: tuple[str, ...] = ()
    missing_contract_files: tuple[str, ...] = ()
    missing_commit_artifacts: tuple[str, ...] = ()
    draft_file: str = ""
    blocking: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    chapter_outline_file: str = ""
    chapter_outline_source: str = "missing"
    chapter_outline_revision: str = ""
    volume_planning_revision: str = ""
    volume_plan_stale: bool = False
    chapter_contract_stale: bool = False
    body_revision_stale: bool = False
    body_revision_uncommitted: bool = False
    body_evidence: dict[str, Any] = field(default_factory=dict)
    revision_evidence: dict[str, Any] = field(default_factory=dict)
    dependency_impacts: dict[str, Any] = field(default_factory=dict)
    dependency_impact_records: dict[str, Any] = field(default_factory=dict)
    upstream_body_stale: tuple[int, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_root": self.project_root,
            "phase": self.phase,
            "target_chapter": self.target_chapter,
            "latest_accepted_chapter": self.latest_accepted_chapter,
            "target_volume": self.target_volume,
            "latest_commit": self.latest_commit.to_dict() if self.latest_commit else None,
            "state_current_chapter": self.state_current_chapter,
            "missing_init_files": list(self.missing_init_files),
            "missing_init_dirs": list(self.missing_init_dirs),
            "missing_contract_files": list(self.missing_contract_files),
            "missing_commit_artifacts": list(self.missing_commit_artifacts),
            "draft_file": self.draft_file,
            "blocking": list(self.blocking),
            "warnings": list(self.warnings),
            "chapter_outline_file": self.chapter_outline_file,
            "chapter_outline_source": self.chapter_outline_source,
            "chapter_outline_revision": self.chapter_outline_revision,
            "volume_planning_revision": self.volume_planning_revision,
            "volume_plan_stale": self.volume_plan_stale,
            "chapter_contract_stale": self.chapter_contract_stale,
            "body_revision_stale": self.body_revision_stale,
            "body_revision_uncommitted": self.body_revision_uncommitted,
            "body_evidence": self.body_evidence,
            "revision_evidence": self.revision_evidence,
            "dependency_impacts": self.dependency_impacts,
            "dependency_impact_records": self.dependency_impact_records,
            "upstream_body_stale": list(self.upstream_body_stale),
        }


def _read_json_object(path: Path) -> tuple[dict[str, Any], str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, "missing"
    except json.JSONDecodeError as exc:
        return {}, f"invalid_json:{exc}"
    except OSError as exc:
        return {}, f"read_error:{exc}"
    if not isinstance(payload, dict):
        return {}, "not_object"
    return payload, ""


def _chapter_from_path(path: Path) -> int:
    match = _CHAPTER_FILE_RE.search(path.name)
    if not match:
        return 0
    try:
        return int(match.group(1))
    except ValueError:
        return 0


def _state_current_chapter(project_root: Path) -> tuple[int, str]:
    state_path = project_root / ".webnovel" / "state.json"
    state, error = _read_json_object(state_path)
    if error:
        return 0, error
    progress = state.get("progress") if isinstance(state, dict) else {}
    if not isinstance(progress, dict):
        return 0, ""
    try:
        return max(0, int(progress.get("current_chapter") or 0)), ""
    except (TypeError, ValueError):
        return 0, ""


def scan_commits(project_root: Path) -> list[ChapterCommitInfo]:
    """列出书项目内全部章节 commit，并带上每章最新的投影状态。

    投影状态优先取 `projection_log.jsonl` 的**最后一次** run（按章节分组、只读日志一次），
    否则回落到 commit 自带的 `projection_status`。

    单遍读取：旧实现按章调用 `latest_projection_run`，每章都会重新加锁并读取整份日志
    （O(章节数 × 日志长度)）；这里改为一次读取后按章分组，语义等价（组内取最后一次）。
    日志损坏/不可读时，全部章节统一标记为 `failed:projection_log_unreadable`，与旧行为一致。
    """
    commits_dir = project_root / ".story-system" / "commits"
    if not commits_dir.is_dir():
        return []

    runs_by_chapter: dict[int, dict[str, str]] = {}
    degraded_by_chapter: dict[int, dict[str, str]] = {}
    log_error = ""
    try:
        for run in read_projection_runs(project_root):
            chapter = run.get("chapter")
            if not isinstance(chapter, int) or isinstance(chapter, bool) or chapter <= 0:
                continue
            statuses = projection_status_from_run(run)
            if statuses:
                runs_by_chapter[chapter] = statuses
            # 无条件覆盖：组内取最后一次 run，降级已恢复时也要跟着清空。
            degraded_by_chapter[chapter] = degraded_projection_from_run(run)
    except (OSError, ValueError) as exc:
        log_error = str(exc)

    commits: list[ChapterCommitInfo] = []
    for path in sorted(commits_dir.glob("chapter_*.commit.json")):
        chapter = _chapter_from_path(path)
        if chapter <= 0:
            continue
        payload, error = _read_json_object(path)
        meta = payload.get("meta") if isinstance(payload, dict) else {}
        if error:
            status = "invalid"
        elif isinstance(meta, dict):
            status = str(meta.get("status") or "missing").strip() or "missing"
        else:
            status = "missing"
        raw_projection = payload.get("projection_status") if isinstance(payload, dict) else {}
        projection_status = {
            str(key): str(value)
            for key, value in (raw_projection or {}).items()
            if isinstance(raw_projection, dict)
        }
        projection_source = "commit"
        if log_error:
            projection_status = {"state": "failed:projection_log_unreadable"}
            projection_source = "projection_log"
        elif chapter in runs_by_chapter:
            projection_status = runs_by_chapter[chapter]
            projection_source = "projection_log"
        projection_degraded = {} if log_error else dict(degraded_by_chapter.get(chapter) or {})
        commits.append(
            ChapterCommitInfo(
                chapter=chapter,
                status=status,
                path=str(path),
                projection_status=projection_status,
                projection_source=projection_source,
                projection_degraded=projection_degraded,
            )
        )
    return commits


def _latest_story_system_chapter(project_root: Path) -> int:
    story_root = project_root / ".story-system"
    if not story_root.is_dir():
        return 0
    chapters: list[int] = []
    for pattern in (
        "chapters/chapter_*.json",
        "reviews/chapter_*.review.json",
        "commits/chapter_*.commit.json",
    ):
        chapters.extend(_chapter_from_path(path) for path in story_root.glob(pattern))
    return max(chapters or [0])


def _latest_draft_chapter(project_root: Path) -> int:
    chapters_dir = project_root / "正文"
    if not chapters_dir.is_dir():
        return 0
    chapters: list[int] = []
    for path in chapters_dir.rglob("第*章*.md"):
        match = re.search(r"第0*(\d+)章", path.name)
        if not match:
            continue
        try:
            chapters.append(int(match.group(1)))
        except ValueError:
            continue
    return max(chapters or [0])


def _target_chapter(
    project_root: Path,
    chapter: int | None,
    *,
    latest_accepted_chapter: int,
) -> int:
    if chapter is not None:
        try:
            return max(0, int(chapter))
        except (TypeError, ValueError):
            return 0
    latest_runtime = max(
        _latest_story_system_chapter(project_root),
        _latest_draft_chapter(project_root),
    )
    if latest_runtime > latest_accepted_chapter:
        return latest_runtime
    return latest_accepted_chapter + 1 if latest_accepted_chapter >= 0 else 0


def _volume_num(project_root: Path, chapter: int) -> int:
    if chapter <= 0:
        return 1
    try:
        return volume_num_for_chapter_from_state(project_root, chapter) or volume_num_for_chapter(chapter)
    except Exception:
        return volume_num_for_chapter(chapter)


def contract_files_for_chapter(project_root: Path, chapter: int) -> dict[str, Path]:
    volume = _volume_num(project_root, chapter)
    story_root = project_root / ".story-system"
    return {
        "master": story_root / "MASTER_SETTING.json",
        "volume": story_root / "volumes" / f"volume_{volume:03d}.json",
        "chapter": story_root / "chapters" / f"chapter_{chapter:03d}.json",
        "review": story_root / "reviews" / f"chapter_{chapter:03d}.review.json",
    }


def _chapter_outline_evidence(project_root: Path, chapter: int) -> tuple[str, str, str]:
    if chapter <= 0:
        return "", "missing", ""
    try:
        path, source = find_chapter_outline_file(project_root, chapter)
    except (OSError, UnicodeError):
        return "", "missing", ""
    if path is None or not path.is_file():
        return "", "missing", ""
    try:
        if not path.read_text(encoding="utf-8").strip():
            return "", "missing", ""
    except (OSError, UnicodeError):
        return "", "missing", ""
    return str(path), source, chapter_outline_revision(project_root, chapter)


def _volume_artifacts_exist(project_root: Path, volume: int) -> bool:
    outline_dir = project_root / "大纲"
    return all(
        (outline_dir / pattern.format(volume=volume)).is_file()
        and bool((outline_dir / pattern.format(volume=volume)).read_text(encoding="utf-8").strip())
        for pattern in (
            "第{volume}卷-节拍表.md",
            "第{volume}卷-时间线.md",
            "第{volume}卷-详细大纲.md",
        )
    )


def _contracts_stale(project_root: Path, chapter: int, outline_file: str) -> bool:
    if not outline_file or chapter <= 0:
        return False
    try:
        state, _ = _read_json_object(project_root / ".webnovel" / "state.json")
        entry = state.get("progress", {}).get("chapter_revisions", {}).get(str(chapter), {})
        planned = next(
            (
                item
                for item in state.get("progress", {}).get("chapters_planned", [])
                if isinstance(item, dict) and item.get("chapter") == chapter
            ),
            {},
        )
        recorded_outline = str(planned.get("chapter_outline_revision") or entry.get("chapter_outline_revision") or "")
        recorded_contract = str(
            planned.get("contract_revision")
            or entry.get("contract_revision")
            or entry.get("validation_input", {}).get("contract_revision")
            or ""
        )
        current_outline = chapter_outline_revision(project_root, chapter)
        if recorded_outline and current_outline != recorded_outline:
            return True
        if recorded_contract:
            from .chapter_reloading import contract_revision
            return contract_revision(project_root, chapter) != recorded_contract
        return False
    except (OSError, ValueError, TypeError, AttributeError):
        return True


def missing_contract_files(project_root: Path, chapter: int) -> tuple[str, ...]:
    if chapter <= 0:
        return tuple(str(path.relative_to(project_root)) for path in contract_files_for_chapter(project_root, 1).values())
    missing: list[str] = []
    for path in contract_files_for_chapter(project_root, chapter).values():
        if not path.is_file():
            missing.append(str(path.relative_to(project_root)))
    return tuple(missing)


def missing_commit_artifacts(project_root: Path) -> tuple[str, ...]:
    missing: list[str] = []
    for rel in COMMIT_ARTIFACT_FILES:
        if not (project_root / rel).is_file():
            missing.append(rel)
    return tuple(missing)


def missing_init_dirs(project_root: Path) -> tuple[str, ...]:
    return tuple(rel for rel in INIT_REQUIRED_DIRS if not (project_root / rel).is_dir())


def missing_init_files(project_root: Path) -> tuple[str, ...]:
    return tuple(rel for rel in INIT_REQUIRED_FILES if not (project_root / rel).is_file())


def has_projection_blocker(commit: ChapterCommitInfo | None) -> bool:
    if commit is None:
        return False
    return any(
        str(value).startswith("failed:") or str(value) == "pending"
        for value in commit.projection_status.values()
    )


def resolve_project_phase(project_root: str | Path | None, chapter: int | None = None) -> ProjectPhaseSnapshot:
    if project_root is None:
        return ProjectPhaseSnapshot(
            project_root="",
            phase=PHASE_NO_PROJECT,
            target_chapter=0,
            latest_accepted_chapter=0,
            blocking=("project_root_missing",),
        )

    root = Path(project_root)
    state_path = root / ".webnovel" / "state.json"
    if not state_path.is_file():
        return ProjectPhaseSnapshot(
            project_root=str(root),
            phase=PHASE_NO_PROJECT,
            target_chapter=0,
            latest_accepted_chapter=0,
            blocking=("missing .webnovel/state.json",),
        )

    state_chapter, state_error = _state_current_chapter(root)
    state, state_shape_error = _read_json_object(state_path)
    if state_shape_error and not state_error:
        state_error = state_shape_error
    commits = scan_commits(root)
    latest_commit = max(commits, key=lambda item: item.chapter) if commits else None
    accepted = [item.chapter for item in commits if item.status == "accepted"]
    latest_accepted = max(accepted or [0])
    target = _target_chapter(root, chapter, latest_accepted_chapter=latest_accepted)

    init_dirs_missing = missing_init_dirs(root)
    init_files_missing = missing_init_files(root)
    contract_missing = missing_contract_files(root, target)
    artifacts_missing = missing_commit_artifacts(root)
    draft_path = find_chapter_file(root, target) if target > 0 else None
    draft_file = str(draft_path) if draft_path else ""
    chapter_outline_file, chapter_outline_source, outline_revision = _chapter_outline_evidence(root, target)
    target_volume = _volume_num(root, target) if target > 0 else 0
    volume_revision = volume_planning_revision(root, target_volume) if target_volume else ""
    volume_artifacts_exist = _volume_artifacts_exist(root, target_volume) if target_volume else False
    source_volume_revision = chapter_planned_source_revision(root, target)
    volume_plan_stale = bool(
        volume_revision
        and source_volume_revision
        and volume_revision != source_volume_revision
    )
    chapter_contract_stale = (
        _contracts_stale(root, target, chapter_outline_file) if not contract_missing else False
    ) or volume_plan_stale
    if chapter_outline_source == "split" and chapter_outline_file:
        planned_row = next(
            (
                row for row in state.get("progress", {}).get("chapters_planned", [])
                if isinstance(row, dict) and row.get("chapter") == target
            ),
            {},
        )
        entry = state.get("progress", {}).get("chapter_revisions", {}).get(str(target), {})
        if not (
            planned_row.get("chapter_outline_revision")
            or planned_row.get("contract_revision")
            or entry.get("chapter_outline_revision")
            or entry.get("contract_revision")
            or (entry.get("validation_input") or {}).get("contract_revision")
        ):
            chapter_contract_stale = True

    body = body_evidence(root, target) if target > 0 else {}
    revision_evidence = chapter_revision_evidence(root, target) if target > 0 else {}
    try:
        upstream_stale = tuple(upstream_body_blockers(root, target)) if target > 0 else ()
    except (OSError, ValueError, TypeError, AttributeError):
        upstream_stale = ()
    warnings: list[str] = []
    blocking: list[str] = []
    if body.get("body_revision_stale"):
        blocking.append("chapter_body_stale")
    if upstream_stale:
        blocking.append("previous_chapter_revision_changed")
    if state_error:
        blocking.append(f"state_json_{state_error}")
    if state_chapter > latest_accepted:
        warnings.append("state_projection_ahead_of_latest_accepted_commit")
    if volume_artifacts_exist and chapter_outline_source == "missing":
        warnings.append("chapter_outline_missing_after_volume_plan")
    if chapter_contract_stale:
        warnings.append("chapter_contract_stale_after_outline_change")
    if volume_plan_stale:
        warnings.append("chapter_plan_stale_after_volume_change")
    if latest_commit and latest_commit.projection_degraded:
        # 降级跳过本身不阻断写作（skipped 是合法终态），但必须让作者看见：
        # 否则"向量检索一直是空的"会一直无人知晓。
        for writer, reason in sorted(latest_commit.projection_degraded.items()):
            warnings.append(f"projection_degraded_{writer}_{reason}")

    if has_projection_blocker(latest_commit):
        phase = PHASE_PROJECTION_FAILED
        latest_statuses = [str(value) for value in (latest_commit.projection_status or {}).values()]
        blocking.append(
            "latest_commit_projection_incomplete"
            if any(value == "pending" for value in latest_statuses)
            else "latest_commit_projection_failed"
        )
    elif init_dirs_missing or init_files_missing:
        phase = PHASE_INIT_SCAFFOLDED
        blocking.extend([f"missing_init_dir:{rel}" for rel in init_dirs_missing])
        blocking.extend([f"missing_init_file:{rel}" for rel in init_files_missing])
    elif body.get("body_revision_stale") or body.get("body_revision_uncommitted") or upstream_stale or body.get("dependency_stale"):
        phase = PHASE_DRAFT_IN_PROGRESS
    elif latest_commit and latest_commit.chapter >= target and latest_commit.status in {"accepted", "rejected"}:
        phase = PHASE_CHAPTER_COMMITTED
    elif volume_plan_stale or chapter_contract_stale:
        phase = PHASE_PLAN_IN_PROGRESS
    elif draft_file and not artifacts_missing:
        phase = PHASE_READY_TO_COMMIT
    elif draft_file:
        phase = PHASE_DRAFT_IN_PROGRESS
    elif not contract_missing and not chapter_contract_stale and (
        chapter_outline_source != "missing" or not volume_artifacts_exist
    ):
        phase = PHASE_CHAPTER_CONTRACT_READY
    elif volume_artifacts_exist or (root / ".story-system" / "MASTER_SETTING.json").is_file() or any(
        (root / "大纲").glob("第*卷*大纲.md")
    ):
        phase = PHASE_PLAN_IN_PROGRESS
    else:
        phase = PHASE_INIT_READY

    return ProjectPhaseSnapshot(
        project_root=str(root),
        phase=phase,
        target_chapter=target,
        latest_accepted_chapter=latest_accepted,
        target_volume=target_volume,
        latest_commit=latest_commit,
        state_current_chapter=state_chapter,
        missing_init_files=init_files_missing,
        missing_init_dirs=init_dirs_missing,
        missing_contract_files=contract_missing,
        missing_commit_artifacts=artifacts_missing,
        draft_file=draft_file,
        blocking=tuple(blocking),
        warnings=tuple(warnings),
        chapter_outline_file=chapter_outline_file,
        chapter_outline_source=chapter_outline_source,
        chapter_outline_revision=outline_revision,
        volume_planning_revision=volume_revision,
        volume_plan_stale=volume_plan_stale,
        chapter_contract_stale=chapter_contract_stale,
        body_revision_stale=bool(body.get("body_revision_stale")),
        body_revision_uncommitted=bool(body.get("body_revision_uncommitted")),
        body_evidence=body,
        revision_evidence=revision_evidence,
        dependency_impacts=(revision_evidence.get("dependency_impacts") or {}),
        dependency_impact_records=(revision_evidence.get("dependency_impact_records") or {}),
        upstream_body_stale=upstream_stale,
    )
