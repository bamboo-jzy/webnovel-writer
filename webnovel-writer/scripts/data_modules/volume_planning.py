#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from chapter_outline_loader import volume_planning_revision
    from security_utils import create_secure_directory
except ImportError:  # pragma: no cover
    from scripts.chapter_outline_loader import volume_planning_revision
    from scripts.security_utils import create_secure_directory


_VOLUME_ARTIFACTS = (
    ("节拍表", "第{volume}卷-节拍表.md"),
    ("时间线", "第{volume}卷-时间线.md"),
    ("详细大纲", "第{volume}卷-详细大纲.md"),
)


def volume_artifact_paths(project_root: Path, volume: int) -> dict[str, Path]:
    outline_dir = project_root / "大纲"
    return {
        label: outline_dir / pattern.format(volume=int(volume))
        for label, pattern in _VOLUME_ARTIFACTS
    }


def _read_state(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("state.json 根节点必须是对象")
    return payload


def _write_state(path: Path, state: dict[str, Any]) -> None:
    temp_path = path.with_name(f".{path.name}.volume-reload.tmp")
    temp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)


def _volume_entry(progress: dict[str, Any], volume: int) -> dict[str, Any] | None:
    planned = progress.get("volumes_planned")
    if not isinstance(planned, list):
        return None
    for item in planned:
        if isinstance(item, dict) and item.get("volume") == int(volume):
            return item
    return None


def _chapter_sources(progress: dict[str, Any], volume: int) -> list[tuple[dict[str, Any], str]]:
    planned = progress.get("chapters_planned")
    if not isinstance(planned, list):
        return []
    result: list[tuple[dict[str, Any], str]] = []
    for item in planned:
        if not isinstance(item, dict) or item.get("volume") != int(volume):
            continue
        source = str(item.get("source_volume_revision") or "").strip()
        result.append((item, source))
    return result


def _range_for_chapters(chapters: list[int]) -> str:
    if not chapters:
        return ""
    return f"{min(chapters)}-{max(chapters)}"


def _backup_volume_artifacts(root: Path, volume: int, paths: dict[str, Path]) -> Path:
    backup_dir = root / ".webnovel" / "backups" / f"volume_{volume}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    create_secure_directory(str(backup_dir))
    for path in paths.values():
        shutil.copy2(path, backup_dir / path.name)
    state_path = root / ".webnovel" / "state.json"
    if state_path.is_file():
        shutil.copy2(state_path, backup_dir / state_path.name)
    return backup_dir


def reload_volume_plan(
    project_root: str | Path,
    volume: int,
    *,
    chapters_range: str = "",
    source: str = "manual_reload",
    dry_run: bool = False,
    backup_only: bool = False,
) -> dict[str, Any]:
    root = Path(project_root)
    volume = int(volume)
    if volume <= 0:
        return {"ok": False, "volume": volume, "errors": ["卷号必须大于 0"]}

    paths = volume_artifact_paths(root, volume)
    missing = [label for label, path in paths.items() if not path.is_file()]
    empty = [label for label, path in paths.items() if path.is_file() and not path.read_text(encoding="utf-8").strip()]
    if missing or empty:
        return {
            "ok": False,
            "volume": volume,
            "revision": "",
            "previous_revision": "",
            "changed": False,
            "missing": missing,
            "empty": empty,
            "errors": ["卷级规划文件缺失或为空"],
        }

    if backup_only:
        try:
            backup_dir = _backup_volume_artifacts(root, volume, paths)
        except OSError as exc:
            return {"ok": False, "volume": volume, "errors": [f"卷纲备份失败: {exc}"]}
        return {
            "ok": True,
            "volume": volume,
            "backup_only": True,
            "backup_dir": str(backup_dir),
            "files": {label: str(path) for label, path in paths.items()},
        }

    revision = volume_planning_revision(root, volume)
    if not revision:
        return {"ok": False, "volume": volume, "errors": ["无法计算卷纲 revision"]}

    state_path = root / ".webnovel" / "state.json"
    try:
        state = _read_state(state_path)
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError) as exc:
        return {"ok": False, "volume": volume, "errors": [f"无法读取 state.json: {exc}"]}

    progress = state.setdefault("progress", {})
    if not isinstance(progress, dict):
        return {"ok": False, "volume": volume, "errors": ["state.json.progress 必须是对象"]}
    planned = progress.setdefault("volumes_planned", [])
    if not isinstance(planned, list):
        return {"ok": False, "volume": volume, "errors": ["state.json.progress.volumes_planned 必须是数组"]}

    entry = _volume_entry(progress, volume)
    chapter_sources = _chapter_sources(progress, volume)
    previous_revision = str((entry or {}).get("planning_revision") or "").strip()
    if not previous_revision:
        known_sources = {source_revision for _, source_revision in chapter_sources if source_revision}
        if len(known_sources) == 1:
            previous_revision = next(iter(known_sources))

    changed = bool(previous_revision and previous_revision != revision)
    stale_chapters: list[int] = []
    if changed:
        for item, source_revision in chapter_sources:
            if not source_revision or source_revision == revision:
                continue
            chapter = int(item.get("chapter") or 0)
            if chapter <= 0:
                continue
            item["status"] = "stale"
            item["stale_reason"] = "volume_plan_changed"
            item["stale_at"] = datetime.now().strftime("%Y-%m-%d")
            stale_chapters.append(chapter)

    now = datetime.now().strftime("%Y-%m-%d")
    if entry is None:
        entry = {
            "volume": volume,
            "chapters_range": chapters_range,
            "planned_at": now,
        }
        planned.append(entry)
    elif chapters_range:
        entry["chapters_range"] = chapters_range
    if not entry.get("planned_at"):
        entry["planned_at"] = now
    if previous_revision and previous_revision != revision:
        entry["previous_revision"] = previous_revision
    entry["planning_revision"] = revision
    entry["updated_at"] = now
    entry["reloaded_at"] = now
    entry["reload_source"] = source
    entry["stale_chapters"] = sorted(stale_chapters)
    progress["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    backup_path = ""
    backup_dir = ""
    if not dry_run:
        try:
            backup_dir = str(_backup_volume_artifacts(root, volume, paths))
            backup_path = str(Path(backup_dir) / state_path.name)
            _write_state(state_path, state)
        except (OSError, ValueError) as exc:
            return {
                "ok": False,
                "volume": volume,
                "revision": revision,
                "previous_revision": previous_revision,
                "changed": changed,
                "stale_chapters": sorted(stale_chapters),
                "errors": [f"卷纲重载状态写入失败: {exc}"],
            }

    return {
        "ok": True,
        "volume": volume,
        "revision": revision,
        "previous_revision": previous_revision,
        "changed": changed,
        "stale_chapters": sorted(stale_chapters),
        "stale_chapters_range": _range_for_chapters(stale_chapters),
        "source": source,
        "dry_run": dry_run,
        "state_file": str(state_path),
        "state_backup": backup_path,
        "backup_dir": backup_dir,
        "files": {label: str(path) for label, path in paths.items()},
    }


def format_volume_reload_report(report: dict[str, Any], output_format: str = "text") -> str:
    if output_format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2)
    if not report.get("ok"):
        errors = "；".join(str(item) for item in report.get("errors") or []) or "未知错误"
        return f"未完成：第 {report.get('volume', 0)} 卷卷纲重载失败。\n- {errors}"
    if report.get("backup_only"):
        return f"已完成：第 {report.get('volume')} 卷卷纲原始文件已备份到 {report.get('backup_dir')}。"
    if report.get("dry_run"):
        state = "预览完成，未写入状态"
    elif report.get("changed"):
        state = "已重载，依赖章节已标记为 stale"
    else:
        state = "已登记，revision 未变化"
    lines = [
        f"已完成：第 {report.get('volume')} 卷卷纲{state}。",
        f"- 当前 revision：{report.get('revision')}",
    ]
    if report.get("previous_revision"):
        lines.append(f"- 上一 revision：{report.get('previous_revision')}")
    stale_range = str(report.get("stale_chapters_range") or "")
    if stale_range:
        lines.append(f"- 需要重新规划的章节：{stale_range}")
        lines.append(f"- 建议运行：/webnovel-chapter-plan {report.get('volume')} {stale_range}")
    return "\n".join(lines)
