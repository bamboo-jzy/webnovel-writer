#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .chapter_commit_service import ChapterCommitService
from .projection_log import latest_projection_run


SCHEMA_VERSION = "webnovel-projections/v1"
DEFAULT_PROJECTION_STATUS = {
    "state": "pending",
    "index": "pending",
    "summary": "pending",
    "memory": "pending",
    "vector": "pending",
}


def _commit_path(project_root: Path, chapter: int) -> Path:
    return project_root / ".story-system" / "commits" / f"chapter_{chapter:03d}.commit.json"


def _read_commit(path: Path) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        return {}, "missing_commit"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {}, f"invalid_json:{exc}"
    except OSError as exc:
        return {}, f"read_error:{exc}"
    if not isinstance(payload, dict):
        return {}, "commit_not_object"
    payload.setdefault("projection_status", dict(DEFAULT_PROJECTION_STATUS))
    for key, value in DEFAULT_PROJECTION_STATUS.items():
        payload["projection_status"].setdefault(key, value)
    return payload, ""


def _projection_failed(payload: dict[str, Any]) -> bool:
    projection_status = payload.get("projection_status") or {}
    if not isinstance(projection_status, dict):
        return True
    return any(str(value).startswith("failed:") or str(value) == "pending" for value in projection_status.values())


def retry_projection(
    project_root: str | Path,
    *,
    chapter: int,
    force_retract: bool = False,
) -> dict[str, Any]:
    root = Path(project_root)
    path = _commit_path(root, chapter)
    payload, error = _read_commit(path)
    if error:
        return {
            "schema_version": SCHEMA_VERSION,
            "action": "retry",
            "ok": False,
            "project_root": str(root),
            "chapter": chapter,
            "error": error,
            "commit_path": str(path),
            "projection_status": {},
            "latest_projection_run": None,
        }

    try:
        latest_projection_run(root, chapter=chapter)
        service = ChapterCommitService(root)
        # 撤回必须发生在镜像重建之前（镜像自身也是被撤回的对象）：
        # - commit 带 provenance.retract_required（事实被 --allow-fact-revision 改写）
        # - force_retract（--retract，用于修复半途写坏、被旧行挡住重建的投影）
        retract_results = service.retract_chapter_read_models(payload, force=force_retract)
        # 先重建 story_events 镜像（只写 sqlite，不产生 commit 侧副作用），再跑投影写入器：
        # 这一步让"镜像写失败"变成可修复 —— 旧实现只有 chapter-commit 会写镜像。
        mirror_results = service.rebuild_event_mirror(payload)
        projected = service.apply_projection_writers(
            payload,
            mirror_results=mirror_results,
            retract_results=retract_results,
        )
        latest_run = latest_projection_run(root, chapter=chapter)
    except (OSError, ValueError) as exc:
        return {"schema_version": SCHEMA_VERSION, "action": "retry", "ok": False, "chapter": chapter, "error": str(exc), "commit_path": str(path), "projection_status": {}, "latest_projection_run": None}
    mirror_error = str((projected.get("provenance") or {}).get("event_mirror_error") or "")
    retract_error = _first_retract_error(retract_results)
    return {
        "schema_version": SCHEMA_VERSION,
        "action": "retry",
        "ok": not _projection_failed(projected) and not mirror_error and not retract_error,
        "project_root": str(root),
        "chapter": chapter,
        "error": mirror_error or retract_error,
        "commit_path": str(path),
        "projection_status": dict(projected.get("projection_status") or {}),
        "retractions": retract_results,
        "latest_projection_run": latest_run,
    }


def _first_retract_error(retract_results: dict[str, Any] | None) -> str:
    for name, result in (retract_results or {}).items():
        if isinstance(result, dict) and str(result.get("status") or "") == "failed":
            return f"retract_failed:{name}:{result.get('error') or ''}"
    return ""


def replay_projections(
    project_root: str | Path,
    *,
    start_chapter: int,
    end_chapter: int,
    force_retract: bool = False,
) -> dict[str, Any]:
    root = Path(project_root)
    if start_chapter <= 0 or end_chapter <= 0 or start_chapter > end_chapter:
        return {
            "schema_version": SCHEMA_VERSION,
            "action": "replay",
            "ok": False,
            "project_root": str(root),
            "start_chapter": start_chapter,
            "end_chapter": end_chapter,
            "error": "invalid_chapter_range",
            "results": [],
        }
    results = [
        retry_projection(root, chapter=chapter, force_retract=force_retract)
        for chapter in range(start_chapter, end_chapter + 1)
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "action": "replay",
        "ok": all(item.get("ok") for item in results),
        "project_root": str(root),
        "start_chapter": start_chapter,
        "end_chapter": end_chapter,
        "error": "",
        "results": results,
    }


def format_projection_report(report: dict[str, Any], output_format: str = "json") -> str:
    if output_format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2)
    status = "OK" if report.get("ok") else "ERROR"
    if report.get("action") == "retry":
        lines = [
            f"{status} projections retry",
            f"chapter: {report.get('chapter')}",
            f"commit_path: {report.get('commit_path')}",
            f"projection_status: {report.get('projection_status')}",
            f"error: {report.get('error') or ''}",
        ]
        if report.get("retractions"):
            lines.append(f"retractions: {report.get('retractions')}")
        return "\n".join(lines)
    lines = [
        f"{status} projections replay",
        f"range: {report.get('start_chapter')}-{report.get('end_chapter')}",
        f"error: {report.get('error') or ''}",
    ]
    for item in report.get("results") or []:
        lines.append(f"- chapter {item.get('chapter')}: {'OK' if item.get('ok') else 'ERROR'} {item.get('projection_status') or item.get('error')}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Retry or replay webnovel projections from existing commits")
    parser.add_argument("--project-root", required=True)
    sub = parser.add_subparsers(dest="action", required=True)

    retry = sub.add_parser("retry")
    retry.add_argument("--chapter", type=int, required=True)
    retry.add_argument(
        "--retract",
        action="store_true",
        help="先撤回该章已有的派生行（index 行/向量分块/story_events 镜像）再重放",
    )
    retry.add_argument("--format", choices=["json", "text"], default="json")

    replay = sub.add_parser("replay")
    replay.add_argument("--from-chapter", type=int, required=True)
    replay.add_argument("--to-chapter", type=int, required=True)
    replay.add_argument(
        "--retract",
        action="store_true",
        help="每章重放前先撤回该章的派生行（修复半途写坏的投影）",
    )
    replay.add_argument("--format", choices=["json", "text"], default="json")

    args = parser.parse_args()
    if args.action == "retry":
        report = retry_projection(args.project_root, chapter=args.chapter, force_retract=args.retract)
        print(format_projection_report(report, args.format))
        raise SystemExit(0 if report.get("ok") else 1)
    report = replay_projections(
        args.project_root,
        start_chapter=args.from_chapter,
        end_chapter=args.to_chapter,
        force_retract=args.retract,
    )
    print(format_projection_report(report, args.format))
    raise SystemExit(0 if report.get("ok") else 1)


if __name__ == "__main__":
    main()
