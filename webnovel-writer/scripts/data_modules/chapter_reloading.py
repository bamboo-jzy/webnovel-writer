from __future__ import annotations

import hashlib
import json
import re
import shutil
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import filelock

from chapter_outline_loader import find_chapter_outline_file
from security_utils import AtomicWriteError, atomic_write_json

from .artifact_validator import ARTIFACT_SCHEMAS, validate_commit_artifact_files

ARTIFACT_FILES = {
    "review_result": "review_results.json",
    "fulfillment_result": "fulfillment_result.json",
    "disambiguation_result": "disambiguation_result.json",
    "extraction_result": "extraction_result.json",
}


def read_object(path: Path, *, optional: bool = False) -> dict[str, Any]:
    if optional and not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def digest_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def chapter_candidates(project_root: Path, chapter: int) -> list[Path]:
    return [path for path in (project_root / "正文").rglob("第*章*.md") if re.match(rf"第0*{chapter}章(?:[-_ ].*)?\.md$", path.name) and path.is_file()]


def chapter_body(project_root: Path, chapter: int) -> tuple[Path, bytes]:
    if chapter <= 0:
        raise ValueError("chapter must be positive")
    root = project_root.resolve()
    candidates = chapter_candidates(root, chapter)
    for path in candidates:
        if not path.resolve().is_relative_to(root / "正文"):
            raise ValueError("chapter path escapes 正文 directory")
    if len(candidates) != 1:
        raise ValueError(f"chapter {chapter}: expected one body file, found {len(candidates)}")
    data = candidates[0].read_bytes()
    if not data.decode("utf-8-sig").strip():
        raise ValueError(f"chapter {chapter}: body is empty")
    return candidates[0], data


def chapter_body_revision(project_root: Path, chapter: int) -> str:
    return hashlib.sha256(chapter_body(project_root, chapter)[1]).hexdigest()


def commit_path(root: Path, chapter: int) -> Path:
    return root / ".story-system" / "commits" / f"chapter_{chapter:03d}.commit.json"


def commit_identity(payload: dict) -> str:
    return digest_json({key: value for key, value in payload.items() if key != "projection_status"}) if payload else ""


def revision_entry(state: dict, chapter: int) -> dict:
    return state.get("progress", {}).get("chapter_revisions", {}).get(str(chapter), {})


@contextmanager
def locked_state(root: Path, *, write: bool = True):
    path = root / ".webnovel" / "state.json"
    if not path.is_file():
        raise ValueError("missing .webnovel/state.json")
    with filelock.FileLock(str(path) + ".lock", timeout=10):
        state = read_object(path)
        if not isinstance(state.get("progress", {}), dict):
            raise ValueError("state.progress must be an object")
        yield state
        if write:
            atomic_write_json(path, state, use_lock=False, backup=True)


def contract_revision(root: Path, chapter: int) -> str:
    from .project_phase import contract_files_for_chapter

    paths = list(contract_files_for_chapter(root, chapter).values())
    outline, _ = find_chapter_outline_file(root, chapter)
    if outline:
        paths.append(outline)
    digest = hashlib.sha256()
    for path in paths:
        data = path.read_bytes()
        if not data.strip():
            raise ValueError(f"empty contract input: {path}")
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(b"\0" + data + b"\0")
    for path in sorted((root / ".story-system/commits").glob("chapter_*.commit.json")):
        match = re.match(r"chapter_(\d+)\.commit\.json$", path.name)
        if match and int(match[1]) < chapter:
            digest.update(commit_identity(read_object(path)).encode("ascii"))
    return digest.hexdigest()


def _existing_chapters(root: Path, state: dict) -> set[int]:
    chapters = {int(row["chapter"]) for row in state.get("progress", {}).get("chapters_planned", []) if isinstance(row, dict) and row.get("chapter")}
    chapters.update(int(key) for key in state.get("progress", {}).get("chapter_revisions", {}))
    for folder, pattern in (("正文", "第*章*.md"), ("大纲", "第*章*.md"), (".story-system/chapters", "chapter_*.json"), (".story-system/commits", "chapter_*.commit.json")):
        for path in (root / folder).rglob(pattern):
            match = re.search(r"(?:第|chapter_)(\d+)", path.name)
            if match:
                chapters.add(int(match[1]))
    return chapters


def body_evidence(root: Path, chapter: int, *, state: dict | None = None) -> dict:
    try:
        state = state if state is not None else read_object(root / ".webnovel/state.json")
        entry = revision_entry(state, chapter)
        commit = read_object(commit_path(root, chapter), optional=True)
        committed = str(commit.get("meta", {}).get("content_revision") or "")
        try:
            revision = chapter_body_revision(root, chapter)
        except (OSError, ValueError) as exc:
            if entry.get("content_revision") or committed or chapter_candidates(root, chapter):
                return {"body_revision_stale": True, "content_error": str(exc), "content_revision": "", "committed_revision": committed}
            return {"body_revision_stale": False, "content_revision": "", "committed_revision": "", "dependency_stale": bool(entry.get("stale_dependencies")), "stale_dependencies": entry.get("stale_dependencies", {})}
        baseline = entry.get("content_revision") or committed
        if not baseline:
            return {
                "content_revision": revision,
                "draft_revision": entry.get("draft_revision", ""),
                "validated_revision": entry.get("validated_revision", ""),
                "committed_revision": committed,
                "body_revision_stale": commit.get("meta", {}).get("status") == "accepted",
                "body_revision_uncommitted": commit.get("meta", {}).get("status") == "accepted",
                "content_status": "pending_validation",
                "dependency_stale": bool(entry.get("stale_dependencies")),
                "stale_dependencies": entry.get("stale_dependencies", {}),
            }
        stale = revision != baseline
        uncommitted = bool(commit.get("meta", {}).get("status") == "accepted" and revision != committed)
        return {
            "content_revision": revision,
            "draft_revision": entry.get("draft_revision", ""),
            "validated_revision": entry.get("validated_revision", ""),
            "committed_revision": committed,
            "body_revision_stale": stale,
            "body_revision_uncommitted": uncommitted,
            "content_status": "reload_required" if stale else entry.get("content_status", "committed"),
            "dependency_stale": bool(entry.get("stale_dependencies")),
            "stale_dependencies": entry.get("stale_dependencies", {}),
        }
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        return {"body_revision_stale": True, "content_error": str(exc), "content_revision": ""}


def upstream_body_blockers(root: Path, chapter: int, state: dict | None = None) -> list[int]:
    state = state if state is not None else read_object(root / ".webnovel/state.json")
    blocked = []
    for previous in sorted(_existing_chapters(root, state)):
        if previous >= chapter:
            continue
        info = body_evidence(root, previous, state=state)
        if info.get("body_revision_stale") or info.get("body_revision_uncommitted") or info.get("dependency_stale"):
            blocked.append(previous)
    return blocked


def _backup(root: Path, chapter: int, body_path: Path, data: bytes) -> Path:
    target = root / ".webnovel/backups" / f"chapter_{chapter}_{uuid4().hex}"
    target.mkdir(parents=True)
    from .project_phase import contract_files_for_chapter

    paths = [root / ".webnovel/state.json", commit_path(root, chapter), *contract_files_for_chapter(root, chapter).values(), *artifact_paths(root).values()]
    for path in paths:
        if path.is_file():
            if not path.resolve().is_relative_to(root):
                raise ValueError(f"backup input escapes project: {path}")
            destination = target / path.relative_to(root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
    destination = target / body_path.relative_to(root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return target


def artifact_paths(root: Path) -> dict[str, Path]:
    return {key: root / ".webnovel/tmp" / name for key, name in ARTIFACT_FILES.items()}


def reload_chapter_body(project_root: str | Path, chapter: int, *, source: str = "manual_reload", dry_run: bool = False, backup_only: bool = False) -> dict:
    root = Path(project_root).resolve()
    report = {"ok": False, "chapter": chapter, "action": "backup" if backup_only else "reload", "dry_run": dry_run}
    try:
        state = read_object(root / ".webnovel/state.json")
        path, data = chapter_body(root, chapter)
        revision = hashlib.sha256(data).hexdigest()
        inputs = contract_revision(root, chapter) if not backup_only else ""
        commit = read_object(commit_path(root, chapter), optional=True)
        previous = revision_entry(state, chapter).get("content_revision") or commit.get("meta", {}).get("content_revision", "")
        affected = sorted(ch for ch in _existing_chapters(root, state) if ch > chapter) if revision != previous else []
        report.update(content_revision=revision, previous_revision=previous, changed=revision != previous, stale_chapters=affected, chapter_file=str(path), previous_commit=commit_identity(commit))
        if dry_run:
            report["ok"] = True
            return report
        with locked_state(root, write=not backup_only) as state:
            path, data = chapter_body(root, chapter)
            if hashlib.sha256(data).hexdigest() != revision or (not backup_only and contract_revision(root, chapter) != inputs):
                raise ValueError("inputs changed during reload; retry")
            backup = _backup(root, chapter, path, data)
            report["backup_dir"] = str(backup)
            if not backup_only:
                entries = state.setdefault("progress", {}).setdefault("chapter_revisions", {})
                entry = entries.setdefault(str(chapter), {})
                previous = entry.get("content_revision") or commit.get("meta", {}).get("content_revision", "")
                affected = sorted(ch for ch in _existing_chapters(root, state) if ch > chapter) if revision != previous else []
                for later in affected:
                    dependent = entries.setdefault(str(later), {})
                    dependent.setdefault("stale_dependencies", {})[str(chapter)] = revision
                    dependent["stale_reason"] = "previous_chapter_revision_changed"
                for row in state["progress"].get("chapters_planned", []):
                    if row.get("chapter") in affected:
                        row.update(status="stale", stale_reason="previous_chapter_revision_changed")
                validation_input = entry.get("validation_input", {})
                same_input = validation_input.get("content_revision") == revision and validation_input.get("contract_revision") == inputs
                if not same_input:
                    validation_input = {"chapter": chapter, "content_revision": revision, "contract_revision": inputs, "validation_id": uuid4().hex}
                    entry.update(content_status="pending_validation", validated_revision="", artifact_digest="")
                entry.update(content_revision=revision, validation_input=validation_input, reload_source=source, reloaded_at=datetime.now(timezone.utc).isoformat())
                if source == "generated_draft" and not commit:
                    entry.setdefault("draft_revision", revision)
                report.update(validation_input=validation_input, content_status=entry["content_status"], stale_chapters=affected)
        report["ok"] = True
    except (OSError, ValueError, TypeError, AttributeError, AtomicWriteError, filelock.Timeout) as exc:
        report["error"] = str(exc)
    return report


def normalized_artifacts(payloads: dict) -> dict:
    from .chapter_commit_schema import normalize_accepted_events

    result = {key: model.model_validate(payloads[key]).model_dump() for key, model in ARTIFACT_SCHEMAS.items()}
    source = result["extraction_result"].get("source", {})
    if source.get("chapter"):
        result["extraction_result"]["accepted_events"] = normalize_accepted_events(source["chapter"], result["extraction_result"]["accepted_events"])
    return result


def assert_artifact_freshness(root: Path, chapter: int, payloads: dict, *, require_validated: bool = False) -> dict:
    state = read_object(root / ".webnovel/state.json")
    entry = revision_entry(state, chapter)
    expected = entry.get("validation_input")
    commit = read_object(commit_path(root, chapter), optional=True)
    legacy_baseline = (
        not expected
        and not entry
        and not commit.get("meta", {}).get("content_revision")
        and commit.get("meta", {}).get("status") != "accepted"
    )
    if legacy_baseline:
        current_revision = chapter_body_revision(root, chapter)
        return {
            "chapter": chapter,
            "content_revision": current_revision,
            "contract_revision": contract_revision(root, chapter),
            "validation_id": "",
            "legacy_baseline": True,
        }
    if not expected:
        raise ValueError("chapter_artifacts_stale: run chapter-reload before reviewer/data-agent")
    if chapter_body_revision(root, chapter) != expected["content_revision"] or contract_revision(root, chapter) != expected["contract_revision"]:
        raise ValueError("chapter_body_stale: body or contracts changed; reload and revalidate")
    blockers = upstream_body_blockers(root, chapter, state)
    if blockers:
        raise ValueError(f"previous_chapter_revision_changed: resolve chapters {blockers} first")
    if not legacy_baseline:
        for key in ARTIFACT_FILES:
            if payloads.get(key, {}).get("source") != expected:
                raise ValueError(f"chapter_artifacts_stale: {key} source does not match validation input")
            if payloads[key].get("chapter", chapter) != chapter:
                raise ValueError(f"chapter_artifacts_stale: {key} chapter mismatch")
    if (entry.get("reload_source") != "generated_draft" or commit_path(root, chapter).exists()) and payloads["review_result"].get("review_skipped"):
        raise ValueError("manual reload requires reviewer; no-review artifact is not allowed")
    normalized = normalized_artifacts(payloads)
    for event in normalized["extraction_result"]["accepted_events"]:
        if event["chapter"] != chapter:
            raise ValueError("extraction event chapter mismatch")
    from .project_phase import resolve_project_phase
    phase = resolve_project_phase(root, chapter)
    if phase.missing_contract_files or phase.chapter_contract_stale or phase.volume_plan_stale:
        raise ValueError("chapter inputs stale: refresh outline/contracts before validation")
    if not legacy_baseline and require_validated and (entry.get("validated_revision") != expected["content_revision"] or entry.get("artifact_digest") != digest_json(normalized)):
        raise ValueError("chapter_artifacts_stale: run chapter-reload --validate on these exact artifacts")
    return expected


def validate_chapter_body(project_root: str | Path, chapter: int, *, dry_run: bool = False) -> dict:
    root = Path(project_root).resolve()
    report = {"ok": False, "action": "validate", "chapter": chapter, "dry_run": dry_run}
    try:
        artifacts = validate_commit_artifact_files(**artifact_paths(root))
        report["artifact_report"] = artifacts
        if not artifacts["ok"]:
            raise ValueError("chapter validation failed: reviewer/data artifacts have blockers")
        expected = assert_artifact_freshness(root, chapter, artifacts["payloads"])
        report.update(content_revision=expected["content_revision"], content_status="validated_pending_commit")
        if not dry_run:
            with locked_state(root) as state:
                assert_artifact_freshness(root, chapter, artifacts["payloads"])
                entry = revision_entry(state, chapter)
                entry.update(validated_revision=expected["content_revision"], artifact_digest=digest_json(normalized_artifacts(artifacts["payloads"])), content_status="validated_pending_commit", validated_at=datetime.now(timezone.utc).isoformat())
        report["ok"] = True
    except (OSError, ValueError, TypeError, AttributeError, AtomicWriteError, filelock.Timeout) as exc:
        report["error"] = str(exc)
    return report


def format_chapter_reload_report(report: dict, output_format: str = "text") -> str:
    if output_format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2)
    return "\n".join([
        f"{'OK' if report.get('ok') else 'ERROR'} chapter-reload {report.get('action')}",
        f"chapter: {report.get('chapter')}",
        f"content_revision: {report.get('content_revision', '')}",
        f"status: {report.get('content_status', 'preview' if report.get('dry_run') else '')}",
        f"stale_chapters: {report.get('stale_chapters', [])}",
        f"backup: {report.get('backup_dir', '')}",
        f"error: {report.get('error', '')}",
        "重载/校验不等于提交；正文与旧 accepted commit 均保留。",
    ])
