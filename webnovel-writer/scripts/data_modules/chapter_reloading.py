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

from chapter_outline_loader import (
    chapter_outline_revision,
    find_chapter_outline_file,
    volume_num_for_chapter_from_state,
    volume_planning_revision,
)
from security_utils import AtomicWriteError, atomic_write_json

from .artifact_validator import ARTIFACT_SCHEMAS, validate_commit_artifact_files
from .commit_artifacts import canonical_fact_snapshot, fact_snapshot_diff

ARTIFACT_FILES = {
    "review_result": "review_results.json",
    "fulfillment_result": "fulfillment_result.json",
    "disambiguation_result": "disambiguation_result.json",
    "extraction_result": "extraction_result.json",
}
REVISION_KEYS = (
    "volume_plan_revision",
    "chapter_outline_revision",
    "contract_revision",
    "body_content_revision",
    "previous_chapter_revision",
)


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
    if not payload:
        return ""
    identity_payload = dict(payload)
    identity_payload.pop("projection_status", None)
    meta = dict(identity_payload.get("meta") or {})
    evidence = meta.get("revision_evidence")
    if isinstance(evidence, dict):
        evidence = dict(evidence)
        evidence.pop("accepted_commit_identity", None)
        meta["revision_evidence"] = evidence
        identity_payload["meta"] = meta
    return digest_json(identity_payload)


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


def chapter_revision_evidence(root: Path, chapter: int, *, state: dict | None = None) -> dict[str, Any]:
    state = state if state is not None else read_object(root / ".webnovel/state.json", optional=True)
    entry = revision_entry(state, chapter)
    outline_path, outline_source = find_chapter_outline_file(root, chapter)
    volume = volume_num_for_chapter_from_state(root, chapter) or 1
    current_body = ""
    body_error = ""
    try:
        current_body = chapter_body_revision(root, chapter)
    except (OSError, ValueError, UnicodeError) as exc:
        body_error = str(exc)
    current_contract = ""
    contract_error = ""
    try:
        current_contract = contract_revision(root, chapter)
    except (OSError, ValueError, UnicodeError) as exc:
        contract_error = str(exc)
    commit = read_object(commit_path(root, chapter), optional=True)
    previous_body = ""
    if chapter > 1:
        previous = read_object(commit_path(root, chapter - 1), optional=True)
        previous_body = str((previous.get("meta") or {}).get("content_revision") or "")
    evidence = {
        "chapter": chapter,
        "volume": volume,
        "volume_plan_revision": volume_planning_revision(root, volume),
        "chapter_outline_revision": chapter_outline_revision(root, chapter),
        "chapter_outline_file": str(outline_path) if outline_path else "",
        "chapter_outline_source": outline_source,
        "contract_revision": current_contract,
        "body_content_revision": current_body,
        "previous_chapter_revision": previous_body,
        "accepted_commit_identity": commit_identity(commit) if commit.get("meta", {}).get("status") == "accepted" else "",
        "dependency_impacts": entry.get("dependency_impacts", {}),
        "dependency_impact_records": entry.get("dependency_impact_records", {}),
        "recorded": {
            "source_volume_revision": str(next((item.get("source_volume_revision") for item in state.get("progress", {}).get("chapters_planned", []) if isinstance(item, dict) and item.get("chapter") == chapter), "") or ""),
            "chapter_outline_revision": str(entry.get("chapter_outline_revision") or ""),
            "contract_revision": str(entry.get("contract_revision") or entry.get("validation_input", {}).get("contract_revision") or ""),
            "body_content_revision": str(entry.get("content_revision") or ""),
            "accepted_commit_identity": commit_identity(commit) if commit.get("meta", {}).get("status") == "accepted" else "",
        },
    }
    if body_error:
        evidence["body_error"] = body_error
    if contract_error:
        evidence["contract_error"] = contract_error
    return evidence


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


def approved_reconciliation(
    root: Path,
    chapter: int,
    payloads: dict[str, Any],
) -> dict[str, Any] | None:
    state = read_object(root / ".webnovel/state.json", optional=True)
    entry = revision_entry(state, chapter)
    decision_id = entry.get("reconciliation_id", "")
    if not isinstance(decision_id, str) or not re.fullmatch(r"[a-f0-9]{32}", decision_id):
        return None
    record = read_object(
        root / ".story-system/reconciliations" / f"{decision_id}.json",
        optional=True,
    )
    if record.get("decision") != "accepted_deviation" or record.get("chapter") != chapter:
        return None
    expected = entry.get("validation_input")
    if not expected or record.get("validation_input_digest") != digest_json(expected):
        return None
    if record.get("artifact_digest") != digest_json(normalized_artifacts(payloads)):
        return None
    current = chapter_revision_evidence(root, chapter, state=state)
    recorded = record.get("revision_evidence") or {}
    if any(recorded.get(key) != current.get(key) for key in REVISION_KEYS):
        return None
    if not record.get("reason") or not isinstance(record.get("impact"), list):
        return None
    return record


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


def _existing_chapters(root: Path, state: dict) -> set[int]:
    chapters = {
        int(row["chapter"])
        for row in state.get("progress", {}).get("chapters_planned", [])
        if isinstance(row, dict) and row.get("chapter")
    }
    chapters.update(int(key) for key in state.get("progress", {}).get("chapter_revisions", {}))
    for folder, pattern in (
        ("正文", "第*章*.md"),
        ("大纲", "第*章*.md"),
        (".story-system/chapters", "chapter_*.json"),
        (".story-system/commits", "chapter_*.commit.json"),
    ):
        for path in (root / folder).rglob(pattern):
            match = re.search(r"(?:第|chapter_)(\d+)", path.name)
            if match:
                chapters.add(int(match[1]))
    return chapters


def chapter_dependency_impact_records(
    root: Path,
    chapter: int,
    affected: list[int],
    *,
    fact_diff: dict[str, list[dict[str, Any]]] | None = None,
    expected_revision: str = "",
    observed_revision: str = "",
    basis: str = "previous_accepted_commit",
) -> dict[str, list[dict[str, Any]]]:
    category_names = {
        "entity": "实体",
        "state": "角色状态",
        "relationship": "关系",
        "artifact": "物件持有",
        "location": "地点",
        "timeline": "时间线",
        "open_loop": "开放问题",
        "promise": "开放问题",
        "world_rule": "世界规则",
        "event": "已接受事实",
    }
    if fact_diff is None:
        fact_diff = {"added": [], "removed": [], "changed": []}
        try:
            observed = canonical_fact_snapshot(read_object(commit_path(root, chapter), optional=True))
            fact_diff["added"] = [
                {
                    "category": category,
                    "key": key,
                    "before": None,
                    "after": row.get("value"),
                    "source": row.get("source", []),
                }
                for category, rows in observed.get("facts", {}).items()
                for key, row in rows.items()
            ]
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    changed_facts = [
        {**item, "change": change}
        for change in ("added", "removed", "changed")
        for item in fact_diff.get(change, [])
        if isinstance(item, dict)
    ]
    state = read_object(root / ".webnovel/state.json", optional=True)
    prior_records = state.get("progress", {}).get("chapter_revisions", {})
    records: dict[str, list[dict[str, Any]]] = {}
    for later in sorted(set(affected)):
        outline, _ = find_chapter_outline_file(root, later)
        try:
            content = outline.read_text(encoding="utf-8-sig").casefold() if outline else ""
        except (OSError, UnicodeError):
            content = ""
        rows: list[dict[str, Any]] = []
        for item in changed_facts:
            terms = _fact_terms(item)
            matched = sorted(term for term in terms if term in content)
            if not matched:
                continue
            category = str(item.get("category") or "event")
            label = category_names.get(category, category)
            change = str(item.get("change") or "changed")
            rows.append(
                {
                    "source_chapter": chapter,
                    "dependent_chapter": later,
                    "reason": f"{label}事实{change}: " + ", ".join(matched[:8]),
                    "category": category,
                    "fact_key": str(item.get("key") or ""),
                    "before": item.get("before"),
                    "after": item.get("after"),
                    "expected_revision": expected_revision,
                    "observed_revision": observed_revision,
                    "direct": True,
                    "transitive": False,
                    "basis": basis,
                    "matched_terms": matched[:8],
                    "reload_command": f"/webnovel-chapter-reload {later}",
                }
            )
        if not rows:
            inherited = _inherited_impact_records(
                prior_records,
                chapter,
                later,
                changed_facts,
                source_records=records,
            )
            rows.extend(
                {
                    **record,
                    "dependent_chapter": later,
                    "expected_revision": expected_revision,
                    "observed_revision": observed_revision,
                    "basis": basis,
                    "reload_command": f"/webnovel-chapter-reload {later}",
                }
                for record in inherited
            )
        if not rows:
            rows.append(
                {
                    "source_chapter": chapter,
                    "dependent_chapter": later,
                    "reason": f"previous chapter {chapter} body revision changed; downstream review required",
                    "category": "chapter_revision",
                    "fact_key": "",
                    "before": None,
                    "after": None,
                    "expected_revision": expected_revision,
                    "observed_revision": observed_revision,
                    "direct": True,
                    "transitive": False,
                    "basis": basis,
                    "reload_command": f"/webnovel-chapter-reload {later}",
                }
            )
        records[str(later)] = _unique_impact_records(rows)
    return records


def _fact_terms(item: dict[str, Any]) -> set[str]:
    terms = {str(item.get("key") or "").strip().casefold()}
    terms.update(_fact_value_terms(item.get("before")))
    terms.update(_fact_value_terms(item.get("after")))
    return {
        term
        for term in terms
        if term and term not in {"none", "null", "unknown"} and len(term) > 1
    }


def _inherited_impact_records(
    prior_records: dict[str, Any],
    source: int,
    dependent: int,
    changed_facts: list[dict[str, Any]],
    *,
    source_records: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    if dependent <= source:
        return []
    changed_keys = {
        str(item.get("category")) + "|" + str(item.get("key"))
        for item in changed_facts
    }
    inherited: list[dict[str, Any]] = []
    dependent_entry = prior_records.get(str(dependent), {})
    if not isinstance(dependent_entry, dict):
        return inherited
    dependent_records = dependent_entry.get("dependency_impact_records", {})
    if not isinstance(dependent_records, dict):
        return inherited
    for bridge in sorted(int(key) for key in dependent_records if str(key).isdigit()):
        if bridge <= source or bridge >= dependent:
            continue
        bridge_rows = source_records.get(str(bridge), [])
        if not bridge_rows:
            continue
        prior_rows = dependent_records.get(str(bridge), [])
        if not isinstance(prior_rows, list):
            continue
        for item in prior_rows:
            if not isinstance(item, dict):
                continue
            identity = str(item.get("category")) + "|" + str(item.get("fact_key"))
            if changed_keys and identity not in changed_keys:
                continue
            path = list(item.get("propagation_path") or [bridge, dependent])
            if path[0] != source:
                path.insert(0, source)
            if path[-1] != dependent:
                path.append(dependent)
            inherited.append(
                {
                    "source_chapter": source,
                    "reason": f"经第{bridge}章传递的事实影响: {item.get('reason') or '需要重新核对下游承接'}",
                    "category": item.get("category") or "chapter_revision",
                    "fact_key": item.get("fact_key") or "",
                    "before": item.get("before"),
                    "after": item.get("after"),
                    "direct": False,
                    "transitive": True,
                    "propagation_path": path,
                }
            )
    return inherited


def _unique_impact_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = json.dumps(
            [row.get("source_chapter"), row.get("dependent_chapter"), row.get("category"), row.get("fact_key"), row.get("reason")],
            ensure_ascii=False,
            sort_keys=True,
        )
        unique[key] = row
    return sorted(
        unique.values(),
        key=lambda row: (
            int(row.get("source_chapter") or 0),
            int(row.get("dependent_chapter") or 0),
            str(row.get("category") or ""),
            str(row.get("fact_key") or ""),
            str(row.get("reason") or ""),
        ),
    )


def _fact_value_terms(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value.strip().casefold()} if value.strip() else set()
    if isinstance(value, dict):
        terms: set[str] = set()
        for item in value.values():
            terms.update(_fact_value_terms(item))
        return terms
    if isinstance(value, list):
        terms: set[str] = set()
        for item in value:
            terms.update(_fact_value_terms(item))
        return terms
    return set()


def _impact_reason(record: dict[str, Any]) -> str:
    return str(record.get("reason") or "previous chapter body revision changed; downstream review required")


def chapter_dependency_impacts(
    root: Path,
    chapter: int,
    affected: list[int],
    *,
    fact_diff: dict[str, list[dict[str, Any]]] | None = None,
    expected_revision: str = "",
    observed_revision: str = "",
) -> dict[str, list[str]]:
    records = chapter_dependency_impact_records(
        root,
        chapter,
        affected,
        fact_diff=fact_diff,
        expected_revision=expected_revision,
        observed_revision=observed_revision,
    )
    return {chapter_key: [_impact_reason(record) for record in rows] for chapter_key, rows in records.items()}


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
                impact_records = chapter_dependency_impact_records(
                    root,
                    chapter,
                    affected,
                    expected_revision=revision,
                    observed_revision=str(previous),
                    basis="previous_accepted_commit",
                )
                impacts = {
                    key: [_impact_reason(record) for record in rows]
                    for key, rows in impact_records.items()
                }
                for later in affected:
                    dependent = entries.setdefault(str(later), {})
                    dependent.setdefault("stale_dependencies", {})[str(chapter)] = revision
                    dependent.setdefault("dependency_impacts", {})[str(chapter)] = impacts.get(str(later), [])
                    dependent.setdefault("dependency_impact_records", {})[str(chapter)] = impact_records.get(str(later), [])
                    dependent["stale_reason"] = "previous_chapter_revision_changed"
                for row in state["progress"].get("chapters_planned", []):
                    if row.get("chapter") in affected:
                        row.update(status="stale", stale_reason="previous_chapter_revision_changed")
                current_evidence = chapter_revision_evidence(root, chapter, state=state)
                validation_input = entry.get("validation_input", {})
                recorded_input_evidence = validation_input.get("revision_evidence") or {}
                same_input = (
                    validation_input.get("content_revision") == revision
                    and validation_input.get("contract_revision") == inputs
                    and all(
                        recorded_input_evidence.get(key) == current_evidence.get(key)
                        for key in REVISION_KEYS
                    )
                )
                if not same_input:
                    validation_input = {"chapter": chapter, "content_revision": revision, "contract_revision": inputs, "revision_evidence": current_evidence, "validation_id": uuid4().hex}
                    entry.update(content_status="pending_validation", validated_revision="", artifact_digest="")
                evidence = validation_input.get("revision_evidence") or current_evidence
                entry.update(content_revision=revision, contract_revision=inputs, chapter_outline_revision=evidence.get("chapter_outline_revision", ""), revision_evidence=evidence, validation_input=validation_input, reload_source=source, reloaded_at=datetime.now(timezone.utc).isoformat())
                if source == "generated_draft" and not commit:
                    entry.setdefault("draft_revision", revision)
                report.update(validation_input=validation_input, content_status=entry["content_status"], stale_chapters=affected, dependency_impacts=impacts, dependency_impact_records=impact_records)
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
    recorded_evidence = expected.get("revision_evidence") or {}
    if not legacy_baseline and set(REVISION_KEYS) - set(recorded_evidence):
        raise ValueError("chapter_revision_evidence_missing: reload and revalidate before using artifacts")
    if recorded_evidence:
        current_evidence = chapter_revision_evidence(root, chapter, state=state)
        for key in ("volume_plan_revision", "chapter_outline_revision", "contract_revision", "body_content_revision", "previous_chapter_revision"):
            if recorded_evidence.get(key, "") != current_evidence.get(key, ""):
                raise ValueError(f"chapter_revision_stale: {key} changed; reload and revalidate")
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


def reconcile_chapter_body(
    project_root: str | Path, chapter: int, *, decision: str = "", reason: str = "",
    impact: list[int] | None = None, expected_input: str = "", dry_run: bool = False,
) -> dict:
    from .artifact_validator import ERROR_MISSED_OUTLINE_NODE
    from .chapter_commit_schema import RECONCILIATION_DECISIONS, fulfillment_deviation_items

    root = Path(project_root).resolve()
    report = {"ok": False, "action": "reconcile", "chapter": chapter, "dry_run": dry_run}
    try:
        artifacts = validate_commit_artifact_files(**artifact_paths(root))
        if any(item["type"] != ERROR_MISSED_OUTLINE_NODE for item in artifacts["errors"]):
            raise ValueError("reconciliation blocked: repair schema/review/disambiguation first")
        expected = assert_artifact_freshness(root, chapter, artifacts["payloads"])
        if expected.get("legacy_baseline"):
            raise ValueError("reconciliation requires chapter-reload and fresh artifacts")
        normalized = normalized_artifacts(artifacts["payloads"])
        evidence = chapter_revision_evidence(root, chapter)
        binding = {
            "validation_input_digest": digest_json(expected),
            "artifact_digest": digest_json(normalized),
            "revision_evidence": {key: evidence[key] for key in REVISION_KEYS},
        }
        token = digest_json(binding)
        report.update(input_token=token, revision_evidence=evidence,
                      deviations=fulfillment_deviation_items(normalized["fulfillment_result"]))
        if dry_run:
            report["ok"] = True
            return report
        if not expected_input or expected_input != token:
            raise ValueError("reconciliation_confirmation_required: preview and confirm the exact input_token")
        if decision not in RECONCILIATION_DECISIONS or not reason.strip():
            raise ValueError("reconciliation requires an explicit decision and non-empty reason")
        if impact is None or any(type(value) is not int or value <= 0 for value in impact):
            raise ValueError("reconciliation requires an explicit list of affected chapter numbers")
        record = {
            "decision_id": uuid4().hex, "chapter": chapter, "decision": decision,
            "reason": reason.strip(), "impact": sorted(set(impact)),
            "confirmed_at": datetime.now(timezone.utc).isoformat(), **binding,
        }
        with locked_state(root) as state:
            current = {key: read_object(path) for key, path in artifact_paths(root).items()}
            assert_artifact_freshness(root, chapter, current)
            if digest_json(normalized_artifacts(current)) != binding["artifact_digest"]:
                raise ValueError("reconciliation inputs changed during confirmation")
            record_path = root / ".story-system/reconciliations" / f"{record['decision_id']}.json"
            atomic_write_json(record_path, record)
            entry = revision_entry(state, chapter)
            entry.update(reconciliation_id=record["decision_id"], validated_revision="",
                         artifact_digest="", content_status="pending_validation" if decision == "accepted_deviation" else "needs_reconcile")
        report.update(ok=True, decision_id=record["decision_id"], decision=decision,
                      reason=record["reason"], impact=record["impact"],
                      content_status=entry["content_status"])
    except (OSError, ValueError, TypeError, AttributeError, KeyError, AtomicWriteError, filelock.Timeout) as exc:
        report["error"] = str(exc)
    return report


def chapter_artifact_report(root: Path, chapter: int) -> dict:
    from .artifact_validator import ERROR_MISSED_OUTLINE_NODE

    report = validate_commit_artifact_files(**artifact_paths(root))
    if set(report["payloads"]) != set(ARTIFACT_FILES):
        return report
    record = approved_reconciliation(root, chapter, report["payloads"])
    if record:
        waived = [item for item in report["errors"] if item["type"] == ERROR_MISSED_OUTLINE_NODE]
        report["errors"] = [item for item in report["errors"] if item["type"] != ERROR_MISSED_OUTLINE_NODE]
        report["warnings"].extend({**item, "severity": "warning", "decision_id": record["decision_id"]} for item in waived)
        report["reconciliation"] = record
        report["ok"] = not report["errors"]
    return report


def validate_chapter_body(project_root: str | Path, chapter: int, *, dry_run: bool = False) -> dict:
    root = Path(project_root).resolve()
    report = {"ok": False, "action": "validate", "chapter": chapter, "dry_run": dry_run}
    try:
        artifacts = chapter_artifact_report(root, chapter)
        report["artifact_report"] = artifacts
        if not artifacts["ok"]:
            raise ValueError("chapter validation failed: reviewer/data artifacts have blockers")
        expected = assert_artifact_freshness(root, chapter, artifacts["payloads"])
        normalized = normalized_artifacts(artifacts["payloads"])
        previous_commit = read_object(commit_path(root, chapter), optional=True)
        previous_snapshot = canonical_fact_snapshot(previous_commit)
        current_snapshot = canonical_fact_snapshot({"meta": {"chapter": chapter}, "extraction_result": normalized["extraction_result"]})
        fact_diff = fact_snapshot_diff(previous_snapshot, current_snapshot)
        affected = sorted(ch for ch in _existing_chapters(root, read_object(root / ".webnovel/state.json")) if ch > chapter)
        impact_records = chapter_dependency_impact_records(
            root,
            chapter,
            affected,
            fact_diff=fact_diff,
            expected_revision=expected.get("content_revision", ""),
            observed_revision=str((previous_commit.get("meta") or {}).get("content_revision") or ""),
            basis="validated_candidate",
        )
        report.update(
            content_revision=expected["content_revision"],
            content_status="validated_pending_commit",
            fact_diff=fact_diff,
            dependency_impact_records=impact_records,
        )
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
        f"input_token: {report.get('input_token', '')}",
        f"decision_id: {report.get('decision_id', '')}",
        f"decision: {report.get('decision', '')}",
        f"reason: {report.get('reason', '')}",
        f"impact: {report.get('impact', [])}",
        f"error: {report.get('error', '')}",
        "重载/校验不等于提交；正文与旧 accepted commit 均保留。",
    ])
