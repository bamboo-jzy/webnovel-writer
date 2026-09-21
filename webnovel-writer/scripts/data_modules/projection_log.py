#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

try:
    from security_utils import FileLock, HAS_FILELOCK
except ImportError:  # pragma: no cover - package import fallback
    from ..security_utils import FileLock, HAS_FILELOCK


SCHEMA_VERSION = "webnovel-projection-log/v1"
PROJECTION_LOG_REL = Path(".webnovel") / "projection_log.jsonl"


class ProjectionLogCorruptionError(ValueError):
    pass


def projection_log_path(project_root: str | Path) -> Path:
    return Path(project_root) / PROJECTION_LOG_REL


def _log_lock(path: Path):
    if not HAS_FILELOCK:
        raise OSError("projection log requires filelock for safe concurrent access")
    return FileLock(str(path.with_suffix(path.suffix + ".lock")), timeout=10)


def commit_hash(commit_payload: dict[str, Any]) -> str:
    raw = json.dumps(commit_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _overall_status(writers: dict[str, dict[str, Any]]) -> str:
    statuses = {str(item.get("status") or "") for item in writers.values()}
    if any(status.startswith("failed") for status in statuses):
        return "failed"
    if statuses and statuses <= {"skipped"}:
        return "skipped"
    if "pending" in statuses:
        return "pending"
    return "done"


def build_projection_run(
    *,
    project_root: str | Path,
    commit_payload: dict[str, Any],
    writer_results: dict[str, dict[str, Any]],
    commit_path: str | Path | None = None,
    retract_results: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    meta = commit_payload.get("meta") if isinstance(commit_payload, dict) else {}
    chapter = int((meta or {}).get("chapter") or 0)
    if commit_path is None and chapter > 0:
        commit_path = Path(project_root) / ".story-system" / "commits" / f"chapter_{chapter:03d}.commit.json"
    writers = {str(name): dict(result) for name, result in writer_results.items()}
    record = {
        "schema_version": SCHEMA_VERSION,
        "run_id": uuid4().hex,
        "created_at": _now_iso(),
        "chapter": chapter,
        "commit_path": str(commit_path or ""),
        "commit_hash": commit_hash(commit_payload),
        "commit_status": str((meta or {}).get("status") or ""),
        "content_revision": str((meta or {}).get("content_revision") or ""),
        "revision_number": (meta or {}).get("revision_number", 0),
        "status": _overall_status(writers),
        "writers": writers,
        "projection_status": dict(commit_payload.get("projection_status") or {}),
    }
    if retract_results:
        # 与 writers 分开存放：writers 的键会被 postcommit gate / project_phase
        # 当作"必需投影项"逐个校验，撤回不是投影项，混进去会污染状态判定。
        record["retractions"] = {
            str(name): dict(result) for name, result in retract_results.items()
        }
    return record


def projection_status_from_run(run: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(run, dict):
        return {}
    writers = run.get("writers")
    if isinstance(writers, dict):
        statuses = {
            str(name): str(result.get("status") or "")
            for name, result in writers.items()
            if isinstance(result, dict) and result.get("status")
        }
        if statuses:
            return statuses
    projection_status = run.get("projection_status")
    if isinstance(projection_status, dict):
        return {str(name): str(status) for name, status in projection_status.items()}
    return {}


def projection_run_failed(run: dict[str, Any] | None) -> bool:
    if not isinstance(run, dict):
        return False
    if str(run.get("status") or "").startswith("failed"):
        return True
    return any(status.startswith("failed") for status in projection_status_from_run(run).values())


# 这些 reason 表示"投影按降级策略跳过"，不同于 `not_required` 的路由判定：
# 它们意味着某一项 read model **本该有内容却没有产出**，作者需要知道。
DEGRADED_PROJECTION_REASONS = {"embedding_unavailable", "vector_projection_disabled"}


def degraded_projection_from_run(run: dict[str, Any] | None) -> dict[str, str]:
    """从 projection run 里抽出"降级跳过"的 writer → 原因。

    只认 writers 条目里带了显式降级 reason 的项；`not_required` 之类的
    常规跳过不计入，否则每章都会刷出无意义的提醒。
    """
    if not isinstance(run, dict):
        return {}
    writers = run.get("writers")
    if not isinstance(writers, dict):
        return {}
    degraded: dict[str, str] = {}
    for name, entry in writers.items():
        if not isinstance(entry, dict):
            continue
        result = entry.get("result")
        reason = ""
        if isinstance(result, dict):
            reason = str(result.get("reason") or "").strip()
        if not reason:
            reason = str(entry.get("degraded_reason") or "").strip()
        if reason in DEGRADED_PROJECTION_REASONS:
            degraded[str(name)] = reason
    return degraded


def projection_run_pending(run: dict[str, Any] | None) -> bool:
    if not isinstance(run, dict):
        return False
    if str(run.get("status") or "") == "pending":
        return True
    return any(status == "pending" for status in projection_status_from_run(run).values())


def _read_records(path: Path) -> tuple[list[dict[str, Any]], str]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [], ""
    except UnicodeDecodeError as exc:
        raise ProjectionLogCorruptionError(f"projection log 损坏: {path}: {exc}") from exc
    records = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError("JSON root is not object")
            if not isinstance(payload.get("chapter"), int) or isinstance(payload["chapter"], bool) or payload["chapter"] < 0:
                raise ValueError("chapter must be a non-negative integer")
            if "schema_version" in payload and payload["schema_version"] != SCHEMA_VERSION:
                raise ValueError("unsupported schema_version")
            for name in ("writers", "projection_status"):
                if name in payload and not isinstance(payload[name], dict):
                    raise ValueError(f"{name} must be object")
            for name, result in payload.get("writers", {}).items():
                if not isinstance(result, dict) or not isinstance(result.get("status"), str) or not result["status"]:
                    raise ValueError(f"writers.{name} must have a non-empty status string")
            for name, status in payload.get("projection_status", {}).items():
                if not isinstance(status, str) or not status:
                    raise ValueError(f"projection_status.{name} must be a non-empty string")
        except (ValueError, TypeError) as exc:
            raise ProjectionLogCorruptionError(f"projection log 损坏: {path}:{line_number}: {exc}") from exc
        records.append(payload)
    return records, text


def append_projection_run(
    project_root: str | Path,
    commit_payload: dict[str, Any],
    writer_results: dict[str, dict[str, Any]],
    *,
    commit_path: str | Path | None = None,
    retract_results: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    record = build_projection_run(
        project_root=project_root,
        commit_payload=commit_payload,
        writer_results=writer_results,
        commit_path=commit_path,
        retract_results=retract_results,
    )
    path = projection_log_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    with _log_lock(path):
        _, text = _read_records(path)
        if text and not text.endswith("\n"):
            line = "\n" + line
        with path.open("a", encoding="utf-8", newline="") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
    return record


def read_projection_runs(project_root: str | Path, *, chapter: int | None = None) -> list[dict[str, Any]]:
    path = projection_log_path(project_root)
    if not path.exists():
        return []
    with _log_lock(path):
        records, _ = _read_records(path)
    if chapter is not None:
        return [record for record in records if record["chapter"] == int(chapter)]
    return records


def latest_projection_run(project_root: str | Path, *, chapter: int | None = None) -> dict[str, Any] | None:
    records = read_projection_runs(project_root, chapter=chapter)
    return records[-1] if records else None
