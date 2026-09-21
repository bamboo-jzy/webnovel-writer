#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

from .config import DataModulesConfig
from .project_phase import (
    INIT_REQUIRED_DIRS,
    INIT_REQUIRED_FILES,
    PHASE_CHAPTER_CONTRACT_READY,
    PHASE_DRAFT_IN_PROGRESS,
    PHASE_INIT_READY,
    PHASE_READY_TO_COMMIT,
    PHASE_CHAPTER_COMMITTED,
    PHASE_INIT_SCAFFOLDED,
    PHASE_NO_PROJECT,
    ProjectPhaseSnapshot,
    contract_files_for_chapter,
    resolve_project_phase,
    scan_commits,
)
from .projection_log import (
    latest_projection_run,
    projection_log_path,
    projection_run_failed,
    projection_run_pending,
)
from .run_ledger import LedgerCorruptionError, ledger_path, load_ledger
from .story_runtime_health import build_story_runtime_health


SCHEMA_VERSION = "webnovel-doctor/v1"
CHECK_OK = "ok"
CHECK_WARNING = "warning"
CHECK_ERROR = "error"
CHECK_SKIPPED = "skipped"

CONTRACT_REQUIRED_PHASES = {
    PHASE_CHAPTER_CONTRACT_READY,
    PHASE_DRAFT_IN_PROGRESS,
    PHASE_READY_TO_COMMIT,
    PHASE_CHAPTER_COMMITTED,
    "projection_failed",
}


def _contract_checks_required(snapshot: ProjectPhaseSnapshot) -> bool:
    if snapshot.phase in CONTRACT_REQUIRED_PHASES:
        return True
    if snapshot.target_chapter <= 0 or not snapshot.project_root:
        return False
    chapter_contract = contract_files_for_chapter(
        Path(snapshot.project_root), snapshot.target_chapter
    )["chapter"]
    return chapter_contract.is_file()


def _check(
    check_id: str,
    *,
    status: str,
    severity: str,
    message: str,
    path: str = "",
    expected: str = "",
    actual: str = "",
    impact: str = "",
    repair: str = "",
) -> dict[str, Any]:
    return {
        "id": check_id,
        "status": status,
        "severity": severity,
        "message": message,
        "path": path,
        "expected": expected,
        "actual": actual,
        "impact": impact,
        "repair": repair,
    }


def _rel(project_root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(project_root))
    except ValueError:
        return str(path)


def _read_json(path: Path) -> tuple[dict[str, Any], str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, "missing"
    except json.JSONDecodeError as exc:
        return {}, f"invalid json: {exc}"
    except OSError as exc:
        return {}, f"read error: {exc}"
    if not isinstance(payload, dict):
        return {}, "json root is not object"
    return payload, ""


def _expected_profile(snapshot: ProjectPhaseSnapshot) -> dict[str, Any]:
    expected_files = list(INIT_REQUIRED_FILES)
    expected_dirs = list(INIT_REQUIRED_DIRS)
    if _contract_checks_required(snapshot):
        expected_files.extend(snapshot.missing_contract_files)
    if snapshot.target_chapter > 0 and _contract_checks_required(snapshot):
        expected_files.extend(
            str(path.relative_to(Path(snapshot.project_root)))
            for path in contract_files_for_chapter(Path(snapshot.project_root), snapshot.target_chapter).values()
        )
    return {
        "phase": snapshot.phase,
        "target_chapter": snapshot.target_chapter,
        "files": sorted(set(expected_files)),
        "dirs": sorted(set(expected_dirs)),
    }


def _preflight_checks(preflight_report: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not preflight_report:
        return []
    checks: list[dict[str, Any]] = []
    for item in preflight_report.get("checks") or []:
        if not isinstance(item, dict):
            continue
        ok = bool(item.get("ok"))
        name = str(item.get("name") or "unknown")
        checks.append(
            _check(
                f"preflight.{name}",
                status=CHECK_OK if ok else CHECK_ERROR,
                severity="info" if ok else "blocker",
                message=f"preflight {name} {'ok' if ok else 'failed'}",
                path=str(item.get("path") or ""),
                actual=str(item.get("error") or ""),
                impact="" if ok else "统一 CLI 或项目解析可能不可用。",
                repair="" if ok else "先修复 preflight 输出的路径或 project_root 问题。",
            )
        )
    return checks


def _file_checks(project_root: Path, snapshot: ProjectPhaseSnapshot) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for rel in INIT_REQUIRED_DIRS:
        path = project_root / rel
        exists = path.is_dir()
        checks.append(
            _check(
                f"file.dir.{rel}",
                status=CHECK_OK if exists else CHECK_ERROR,
                severity="info" if exists else "blocker",
                message=f"required directory {rel}",
                path=str(path),
                expected="directory exists",
                actual="exists" if exists else "missing",
                impact="" if exists else "项目骨架不完整，后续写作/备份/报告可能写入失败。",
                repair="" if exists else "重新运行 /webnovel-init，或手动创建该目录后再运行 doctor。",
            )
        )
    for rel in INIT_REQUIRED_FILES:
        path = project_root / rel
        exists = path.is_file()
        checks.append(
            _check(
                f"file.required.{rel}",
                status=CHECK_OK if exists else CHECK_ERROR,
                severity="info" if exists else "blocker",
                message=f"required file {rel}",
                path=str(path),
                expected="file exists",
                actual="exists" if exists else "missing",
                impact="" if exists else "项目初始化产物缺失，当前阶段判断和后续流程会不可靠。",
                repair="" if exists else "使用 /webnovel-init 补齐项目骨架，或按 init_project.py 模板补齐文件。",
            )
        )

    if snapshot.body_revision_stale or snapshot.body_revision_uncommitted or snapshot.upstream_body_stale or snapshot.body_evidence.get("dependency_stale"):
        affected = snapshot.upstream_body_stale[0] if snapshot.upstream_body_stale else snapshot.target_chapter
        checks.append(_check("chapter.body_revision_stale", status=CHECK_ERROR, severity="blocker", message="正文版本与可信提交链不一致", expected="validated and committed content revision", actual=str(snapshot.body_evidence), impact="旧审查和投影不能证明当前正文有效。", repair=f"运行 /webnovel-chapter-reload {affected}，校验成功后再确认提交。"))

    if snapshot.upstream_body_stale:
        for upstream in snapshot.upstream_body_stale:
            reasons = snapshot.dependency_impacts.get(str(upstream), [])
            records = getattr(snapshot, "dependency_impact_records", {}).get(str(upstream), [])
            actual = "; ".join(str(reason) for reason in reasons) or "dependency impact not yet classified"
            if records:
                actual += " | structured_evidence=" + json.dumps(records, ensure_ascii=False, sort_keys=True)
            checks.append(
                _check(
                    f"chapter.dependency_impact.{upstream}",
                    status=CHECK_ERROR,
                    severity="blocker",
                    message=f"chapter {snapshot.target_chapter} depends on revised chapter {upstream}",
                    expected="downstream review completed after upstream revision",
                    actual=actual,
                    impact="后续章节可能继续使用旧的角色、物件、关系、地点、时间线或开放问题事实。",
                    repair=f"检查第 {snapshot.target_chapter} 章并运行 /webnovel-chapter-reload {snapshot.target_chapter}；不要自动覆盖作者正文。",
                )
            )

    if snapshot.volume_plan_stale:
        volume = snapshot.target_volume or 1
        checks.append(
            _check(
                "outline.volume_plan_stale",
                status=CHECK_ERROR,
                severity="blocker",
                message=f"volume {volume} plan revision is stale for chapter {snapshot.target_chapter}",
                path=str(project_root / "大纲"),
                expected="chapter source_volume_revision matches current volume revision",
                actual=snapshot.volume_planning_revision,
                impact="当前章节依赖旧卷纲，继续写作会使用过期的卷级决策。",
                repair=f"运行 /webnovel-volume-reload {volume}，再运行 /webnovel-chapter-plan {volume} {snapshot.target_chapter}。",
            )
        )

    if _contract_checks_required(snapshot) and snapshot.target_chapter > 0:
        for name, path in contract_files_for_chapter(project_root, snapshot.target_chapter).items():
            exists = path.is_file()
            checks.append(
                _check(
                    f"file.contract.{name}",
                    status=CHECK_OK if exists else CHECK_ERROR,
                    severity="info" if exists else "blocker",
                    message=f"story contract {name}",
                    path=str(path),
                    expected="file exists",
                    actual="exists" if exists else "missing",
                    impact="" if exists else "写章上下文缺少主链合同，容易用旧 state 或旧大纲写偏。",
                    repair="" if exists else "运行 webnovel.py story-system ... --persist --emit-runtime-contracts --chapter N。",
                )
            )
    return checks


def _json_checks(project_root: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    json_files = [
        project_root / ".webnovel" / "state.json",
        project_root / ".story-system" / "MASTER_SETTING.json",
    ]
    for path in json_files:
        if not path.exists():
            checks.append(
                _check(
                    f"json.{_rel(project_root, path)}",
                    status=CHECK_SKIPPED,
                    severity="info",
                    message=f"{_rel(project_root, path)} not present",
                    path=str(path),
                    expected="valid JSON object when present",
                    actual="missing",
                )
            )
            continue
        payload, error = _read_json(path)
        checks.append(
            _check(
                f"json.{_rel(project_root, path)}",
                status=CHECK_OK if not error else CHECK_ERROR,
                severity="info" if not error else "blocker",
                message=f"{_rel(project_root, path)} json parse",
                path=str(path),
                expected="valid JSON object",
                actual="ok" if not error else error,
                impact="" if not error else "JSON 无法读取会导致 CLI、dashboard 或状态推导失败。",
                repair="" if not error else "用 UTF-8 修复 JSON 格式；必要时从 git/backup 恢复。",
            )
        )
        if path.name == "state.json" and not error:
            for key in ("project_info", "progress"):
                checks.append(
                    _check(
                        f"json.state.{key}",
                        status=CHECK_OK if isinstance(payload.get(key), dict) else CHECK_WARNING,
                        severity="info" if isinstance(payload.get(key), dict) else "warning",
                        message=f"state.json contains {key}",
                        path=str(path),
                        expected="object field",
                        actual=type(payload.get(key)).__name__,
                        impact="" if isinstance(payload.get(key), dict) else "旧项目或手改 state 可能缺少运行时字段。",
                        repair="" if isinstance(payload.get(key), dict) else "运行 webnovel.py init 到同一目录可增量补齐 schema。",
                    )
                )
    return checks


def _sqlite_table_count(path: Path, table: str) -> tuple[bool, int, str]:
    if not path.is_file():
        return False, 0, "missing"
    try:
        with sqlite3.connect(str(path)) as conn:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if not row:
                return False, 0, "table_missing"
            count_row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            return True, int(count_row[0] or 0) if count_row else 0, ""
    except sqlite3.Error as exc:
        return False, 0, str(exc)


def _sqlite_checks(project_root: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    cfg = DataModulesConfig.from_project_root(project_root)
    for db_path, table, check_id, impact in (
        (cfg.index_db, "chapters", "sqlite.index_db.chapters", "查询、关系图谱和 dashboard 章节统计会降级。"),
        (cfg.vector_db, "vectors", "sqlite.vector_db.vectors", "RAG 向量召回不可用，会退化为关键词或空召回。"),
    ):
        exists = db_path.is_file()
        table_ok, count, error = _sqlite_table_count(db_path, table)
        if not exists:
            checks.append(
                _check(
                    check_id,
                    status=CHECK_WARNING,
                    severity="warning",
                    message=f"{db_path.name} missing",
                    path=str(db_path),
                    expected=f"sqlite db with {table} table",
                    actual="missing",
                    impact=impact,
                    repair="运行对应 index/rag 命令生成数据库；init 刚结束时可暂时忽略。",
                )
            )
            continue
        checks.append(
            _check(
                check_id,
                status=CHECK_OK if table_ok else CHECK_WARNING,
                severity="info" if table_ok else "warning",
                message=f"{db_path.name}.{table}",
                path=str(db_path),
                expected=f"{table} table readable",
                actual=f"rows={count}" if table_ok else error,
                impact="" if table_ok else impact,
                repair="" if table_ok else "重新运行索引/RAG 构建命令；若 sqlite 损坏，从备份恢复。",
            )
        )
    return checks


def _index_query(db_path: Path, sql: str) -> tuple[list[tuple[Any, ...]], str]:
    """对索引库执行一次只读查询，返回 (rows, error)。"""
    if not db_path.is_file():
        return [], "missing"
    try:
        with sqlite3.connect(str(db_path), timeout=10) as conn:
            return list(conn.execute(sql).fetchall()), ""
    except sqlite3.Error as exc:
        return [], str(exc)


def _replay_repair(project_root: Path, chapters: list[int]) -> str:
    """给作者可直接复制的补跑命令。"""
    if not chapters:
        return ""
    return (
        f'python -X utf8 "<SCRIPTS_DIR>/webnovel.py" --project-root "{project_root}" '
        f"projections replay --from-chapter {min(chapters)} --to-chapter {max(chapters)} --format json"
    )


_EVENTS_FILE_RE = re.compile(r"^chapter_0*(\d+)\.events\.json$")


def _chapters_with_events(project_root: Path) -> set[int]:
    """`.story-system/events/` 中确实含事件的章节号。"""
    events_dir = project_root / ".story-system" / "events"
    if not events_dir.is_dir():
        return set()
    chapters: set[int] = set()
    for path in events_dir.glob("chapter_*.events.json"):
        match = _EVENTS_FILE_RE.match(path.name)
        if not match:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, list) and payload:
            chapters.add(int(match.group(1)))
    return chapters


def _index_reconciliation_checks(project_root: Path) -> list[dict[str, Any]]:
    """索引库 ↔ commit / 事件文件对账。

    背景（见 docs/operations/index-integrity-assessment-2026-09-17.md）：
    `index.db` 是 commit 的投影产物，此前的检查只看文件存在 / 表可读 / 行数，
    删库、删中间一章都不会被发现；这里补上"accepted commit 必须有索引行"的对账。
    只读，不做任何自动修复。
    """
    cfg = DataModulesConfig.from_project_root(project_root)
    db_path = cfg.index_db
    commits = scan_commits(project_root)
    accepted = {info.chapter for info in commits if info.status == "accepted"}
    if not accepted:
        return []

    index_done = sorted(
        info.chapter
        for info in commits
        if info.status == "accepted"
        and str(info.projection_status.get("index") or "").strip() == "done"
    )

    checks: list[dict[str, Any]] = []
    rows, rows_error = _index_query(db_path, "SELECT chapter, title, word_count FROM chapters")
    chapter_rows: dict[int, dict[str, Any]] = {}
    for raw_chapter, title, word_count in rows:
        try:
            chapter = int(raw_chapter)
            count = int(word_count or 0)
        except (TypeError, ValueError):
            continue
        chapter_rows[chapter] = {"title": str(title or ""), "word_count": count}

    if index_done:
        missing = sorted(set(index_done) - set(chapter_rows))
        if rows_error:
            checks.append(
                _check(
                    "index.commit_sync",
                    status=CHECK_ERROR,
                    severity="blocker",
                    message=f"index.db 无法对账：{len(index_done)} 章已 accepted 且索引投影为 done，但章节行读不出来",
                    path=str(db_path),
                    expected=f"chapters rows for accepted chapters {index_done[:20]}",
                    actual=f"index.db {rows_error}",
                    impact="章节查询、实体关系读模型与 dashboard 章节统计会缺章甚至整库为空，此前不会有任何提示。",
                    repair=(
                        "确认 index.db 是否被误删或未随项目同步；若文件已损坏无法读取，先把它移出项目"
                        "（例如改名为 index.db.broken，不要直接删除），再执行："
                        + _replay_repair(project_root, index_done)
                    ),
                )
            )
        elif missing:
            checks.append(
                _check(
                    "index.commit_sync",
                    status=CHECK_ERROR,
                    severity="blocker",
                    message=f"索引缺少 {len(missing)} 章：{missing}",
                    path=str(db_path),
                    expected=f"chapters 行覆盖全部 accepted commit（共 {len(index_done)} 章）",
                    actual=f"missing={missing} rows={len(chapter_rows)}",
                    impact="漏掉的章在查询、实体关系与 dashboard 统计里消失；若不修，后续章节会在此基础上继续写。",
                    repair=(
                        "按 commit 重放这几章的投影（幂等，可重复执行）："
                        + _replay_repair(project_root, missing)
                    ),
                )
            )
        else:
            checks.append(
                _check(
                    "index.commit_sync",
                    status=CHECK_OK,
                    severity="info",
                    message="index rows cover every accepted commit",
                    path=str(db_path),
                    expected=f"chapters rows for {len(index_done)} accepted chapters",
                    actual=f"rows={len(chapter_rows)}",
                )
            )

    if not rows_error:
        orphan = sorted(set(chapter_rows) - accepted)
        if orphan:
            checks.append(
                _check(
                    "index.orphan_rows",
                    status=CHECK_WARNING,
                    severity="warning",
                    message=f"索引里有 {len(orphan)} 章没有对应 accepted commit（旧投影残留）：{orphan}",
                    path=str(db_path),
                    expected="chapters 行只对应 accepted commit",
                    actual=f"orphan={orphan}",
                    impact="rejected 的 commit 不会清理旧索引行；残留行会让章节列表出现不该存在的章。",
                    repair=(
                        "确认这几章确实不该在索引里后，按 commit 重放覆盖："
                        + _replay_repair(project_root, orphan)
                    ),
                )
            )

        degraded = sorted(
            chapter
            for chapter in accepted
            if chapter in chapter_rows
            and (not chapter_rows[chapter]["title"] or chapter_rows[chapter]["word_count"] <= 0)
        )
        if degraded:
            checks.append(
                _check(
                    "index.chapter_metadata",
                    status=CHECK_WARNING,
                    severity="warning",
                    message=f"{len(degraded)} 章的索引元数据退化（标题为空或字数为 0）：{degraded}",
                    path=str(db_path),
                    expected="title 非空且 word_count > 0",
                    actual=f"degraded={degraded}",
                    impact="章节列表、status 的章节元数据与 index stats 会出现空标题 / 零字数。",
                    repair=(
                        "确认正文文件名为「第NNNN章-标题.md」（只认第{num:03d} / 第{num:04d} 两种补零写法），"
                        "文件在位后重跑该章投影刷新元数据："
                        + _replay_repair(project_root, degraded)
                    ),
                )
            )

    event_chapters = _chapters_with_events(project_root)
    if event_chapters:
        rows, events_error = _index_query(db_path, "SELECT DISTINCT chapter FROM story_events")
        if events_error and "no such table" not in events_error:
            mirror_chapters: set[int] | None = None
        else:
            mirror_chapters = set()
            for (raw_chapter,) in rows:
                try:
                    mirror_chapters.add(int(raw_chapter))
                except (TypeError, ValueError):
                    continue
        if mirror_chapters is not None:
            expected = sorted(event_chapters & accepted)
            unmirrored = sorted(chapter for chapter in expected if chapter not in mirror_chapters)
            if unmirrored:
                checks.append(
                    _check(
                        "index.story_events_sync",
                        status=CHECK_WARNING,
                        severity="warning",
                        message=f"{len(unmirrored)} 章有事件文件但 story_events 表没有对应行：{unmirrored}",
                        path=str(db_path),
                        expected="accepted 章的事件文件与 story_events 镜像一致",
                        actual=f"unmirrored={unmirrored}",
                        impact="事件链查询（story-events）会丢这些章；这些章的章节行可能仍在，所以不会被 index.commit_sync 发现。",
                        repair=(
                            "补跑投影会同时重建事件镜像："
                            + _replay_repair(project_root, unmirrored)
                        ),
                    )
                )

    return checks


def _rag_checks(project_root: Path) -> list[dict[str, Any]]:
    cfg = DataModulesConfig.from_project_root(project_root)
    checks: list[dict[str, Any]] = []
    for key, present, base_url, model in (
        ("embed", bool(str(cfg.embed_api_key or "").strip()), cfg.embed_base_url, cfg.embed_model),
        ("rerank", bool(str(cfg.rerank_api_key or "").strip()), cfg.rerank_base_url, cfg.rerank_model),
    ):
        checks.append(
            _check(
                f"rag.{key}.api_key",
                status=CHECK_OK if present else CHECK_WARNING,
                severity="info" if present else "warning",
                message=f"{key} api key configured",
                expected="api key present in env or .env",
                actual=f"present; model={model}; base_url={base_url}" if present else f"missing; model={model}; base_url={base_url}",
                impact="" if present else "RAG 相关调用会不可用或降级。",
                repair="" if present else "复制 .env.example 为 .env，并填写对应 API key；不要把真实 key 提交到仓库。",
            )
        )
    return checks


def _ledger_checks(project_root: Path) -> list[dict[str, Any]]:
    path = ledger_path(project_root)
    if not path.is_file():
        return [_check(
            "run_ledger.present",
            status=CHECK_SKIPPED,
            severity="info",
            message="run ledger not present",
            path=str(path),
            expected="valid run ledger after write starts",
            actual="missing",
        )]
    try:
        load_ledger(project_root)
    except LedgerCorruptionError as exc:
        return [_check(
            "run_ledger.corrupt",
            status=CHECK_ERROR,
            severity="blocker",
            message="run ledger is damaged",
            path=str(path),
            expected="valid webnovel-run-ledger/v1 JSON object",
            actual=str(exc),
            impact="不能可信地恢复写章断点，禁止把损坏账本当作新运行继续写入。",
            repair="从 .bak、Git 或快照恢复 run_ledger.json，再运行 doctor。",
        )]
    return [_check(
        "run_ledger.present",
        status=CHECK_OK,
        severity="info",
        message="run ledger is valid",
        path=str(path),
        expected="valid webnovel-run-ledger/v1 JSON object",
        actual="ok",
    )]


def _projection_log_checks(project_root: Path, snapshot: ProjectPhaseSnapshot) -> list[dict[str, Any]]:
    log_path = projection_log_path(project_root)
    latest_commit = snapshot.latest_commit
    try:
        latest = latest_projection_run(project_root, chapter=latest_commit.chapter if latest_commit else None)
    except (OSError, ValueError) as exc:
        return [_check(
            "projection_log.corrupt", status=CHECK_ERROR, severity="blocker",
            message="projection log is damaged or unreadable", path=str(log_path), actual=str(exc),
            impact="不能可信地判断最新投影状态，禁止回退到旧记录。",
            repair="保留损坏文件，从可信备份恢复或人工修复后再运行 doctor。",
        )]
    if latest_commit is None:
        return [
            _check(
                "projection_log.present",
                status=CHECK_SKIPPED,
                severity="info",
                message="no commit yet; projection log not required",
                path=str(log_path),
                expected="projection log after first commit",
                actual="no commit",
            )
        ]
    if not log_path.is_file():
        return [
            _check(
                "projection_log.present",
                status=CHECK_WARNING,
                severity="warning",
                message="projection log missing for project with commits",
                path=str(log_path),
                expected="projection_log.jsonl exists after projection run",
                actual="missing",
                impact="无法从独立执行日志定位历史 projection run；仍可兼容读取 commit 内 projection_status。",
                repair="后续 chapter-commit 会自动双写 projection_log；旧项目可暂时忽略。",
            )
        ]
    latest = None
    try:
        latest = latest_projection_run(project_root, chapter=latest_commit.chapter)
    except Exception as exc:
        return [
            _check(
                "projection_log.corrupt",
                status=CHECK_ERROR,
                severity="blocker",
                message="projection log is damaged",
                path=str(log_path),
                expected="every non-empty line is a JSON object",
                actual=str(exc),
                impact="不能可信地判断最新 projection 状态，禁止把旧记录当作完成证据。",
                repair="修复或从备份恢复 projection_log.jsonl，再运行 doctor 和 projection retry/replay。",
            )
        ]
    if not latest:
        return [
            _check(
                "projection_log.latest_run",
                status=CHECK_WARNING,
                severity="warning",
                message="projection log has no run for latest commit",
                path=str(log_path),
                expected=f"run for chapter {latest_commit.chapter}",
                actual="missing",
                impact="最新 commit 的投影执行历史不可见。",
                repair="后续 projection retry/replay 可补齐；当前仍兼容 commit 内 projection_status。",
            )
        ]
    failed = projection_run_failed(latest)
    pending = projection_run_pending(latest)
    status_ok = not failed and not pending
    return [
        _check(
            "projection_log.latest_run",
            status=CHECK_OK if status_ok else CHECK_ERROR,
            severity="info" if status_ok else "blocker",
            message="latest projection log run",
            path=str(log_path),
            expected="latest run status done/skipped",
            actual=f"chapter={latest.get('chapter')} status={latest.get('status')}",
            impact="read-model 投影失败或未完成，需要补跑。" if not status_ok else "",
            repair="查看 projection_log.jsonl 的 writers 字段，修复后补跑 projection retry/replay。" if not status_ok else "",
        )
    ]


# 项目最低支持版本；与 scripts/requirements.txt、README 徽章保持一致。
_MIN_PYTHON = (3, 12)


def _python_checks() -> list[dict[str, Any]]:
    version_ok = sys.version_info >= _MIN_PYTHON
    minimum = f"{_MIN_PYTHON[0]}.{_MIN_PYTHON[1]}"
    checks = [
        _check(
            "python.version",
            status=CHECK_OK if version_ok else CHECK_ERROR,
            severity="info" if version_ok else "blocker",
            message="python version",
            expected=f">= {minimum}",
            actual=platform.python_version(),
            impact="" if version_ok else f"运行时依赖 Python {minimum}+ 语法和库行为。",
            repair="" if version_ok else f"切换到 Python {minimum} 或更高版本。",
        )
    ]
    for module_name in ("aiohttp", "filelock", "pydantic"):
        found = importlib.util.find_spec(module_name) is not None
        checks.append(
            _check(
                f"python.import.{module_name}",
                status=CHECK_OK if found else CHECK_ERROR,
                severity="info" if found else "blocker",
                message=f"import {module_name}",
                expected="module importable",
                actual="present" if found else "missing",
                impact="" if found else "核心数据模块可能无法运行。",
                repair="" if found else "运行 python -m pip install -r scripts/requirements.txt。",
            )
        )
    for module_name in ("fastapi", "uvicorn", "watchdog"):
        found = importlib.util.find_spec(module_name) is not None
        checks.append(
            _check(
                f"python.import.dashboard.{module_name}",
                status=CHECK_OK if found else CHECK_WARNING,
                severity="info" if found else "warning",
                message=f"import {module_name}",
                expected="module importable for dashboard",
                actual="present" if found else "missing",
                impact="" if found else "Dashboard 服务端可能无法启动。",
                repair="" if found else "运行 python -m pip install -r dashboard/requirements.txt。",
            )
        )
    return checks


def _dashboard_checks(plugin_root: Path | None = None) -> list[dict[str, Any]]:
    if plugin_root is None:
        plugin_root = Path(__file__).resolve().parents[2]
    dashboard_root = plugin_root / "dashboard"
    dist = dashboard_root / "frontend" / "dist"
    package_json = dashboard_root / "frontend" / "package.json"
    requirements = dashboard_root / "requirements.txt"
    checks: list[dict[str, Any]] = []
    for check_id, path, expected in (
        ("dashboard.root", dashboard_root, "directory exists"),
        ("dashboard.frontend.dist", dist, "built frontend dist exists"),
        ("dashboard.frontend.package_json", package_json, "package.json exists"),
        ("dashboard.requirements", requirements, "requirements.txt exists"),
    ):
        exists = path.is_dir() if expected.startswith("directory") or path == dist else path.is_file()
        checks.append(
            _check(
                check_id,
                status=CHECK_OK if exists else CHECK_WARNING,
                severity="info" if exists else "warning",
                message=check_id,
                path=str(path),
                expected=expected,
                actual="exists" if exists else "missing",
                impact="" if exists else "Dashboard 可能无法打开或发布包缺少前端产物。",
                repair="" if exists else "按 dashboard 文档安装/构建前端，或检查发布包是否遗漏 dist。",
            )
        )
    return checks


def build_doctor_report(
    project_root: str | Path | None,
    *,
    chapter: int | None = None,
    deep: bool = False,
    preflight_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = resolve_project_phase(project_root, chapter=chapter)
    checks: list[dict[str, Any]] = []
    checks.extend(_preflight_checks(preflight_report))

    if snapshot.phase == PHASE_NO_PROJECT or not snapshot.project_root:
        checks.append(
            _check(
                "project.root",
                status=CHECK_ERROR,
                severity="blocker",
                message="project root not resolved",
                path=str(project_root or ""),
                expected=".webnovel/state.json",
                actual="missing",
                impact="无法判断项目状态，也不能安全运行写作链路。",
                repair="先运行 /webnovel-init，或运行 webnovel.py use <project_root> 绑定已有项目。",
            )
        )
    else:
        root = Path(snapshot.project_root)
        checks.extend(_file_checks(root, snapshot))
        checks.extend(_json_checks(root))
        try:
            runtime_health = build_story_runtime_health(root, chapter=chapter)
        except Exception as exc:
            runtime_health = {"error": str(exc)}
            checks.append(
                _check(
                    "story_runtime.health",
                    status=CHECK_WARNING,
                    severity="warning",
                    message="story runtime health failed",
                    actual=str(exc),
                    impact="Story System 主链健康摘要不可用。",
                    repair="检查 .story-system 合同与 commit JSON 是否可读。",
                )
            )
        else:
            checks.append(
                _check(
                    "story_runtime.health",
                    status=CHECK_OK if runtime_health.get("mainline_ready") else CHECK_WARNING,
                    severity="info" if runtime_health.get("mainline_ready") else "warning",
                    message="story runtime health",
                    expected="mainline_ready true when writing stage",
                    actual=json.dumps(runtime_health, ensure_ascii=False),
                    impact="" if runtime_health.get("mainline_ready") else "当前章节可能会使用 fallback source。",
                    repair="" if runtime_health.get("mainline_ready") else "补齐 Story System 合同和 accepted commit 后再写。",
                )
            )
        checks.extend(_sqlite_checks(root))
        checks.extend(_index_reconciliation_checks(root))
        checks.extend(_ledger_checks(root))
        checks.extend(_projection_log_checks(root, snapshot))
        checks.extend(_rag_checks(root))

    checks.extend(_python_checks())
    if deep:
        checks.extend(_dashboard_checks())

    blocking = [item for item in checks if item["severity"] == "blocker" and item["status"] == CHECK_ERROR]
    warnings = [item for item in checks if item["status"] == CHECK_WARNING]
    recommended_actions = [item["repair"] for item in checks if item.get("repair")]
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": not blocking,
        "project_root": snapshot.project_root,
        "mode": "deep" if deep else "standard",
        "phase": snapshot.phase,
        "expected_profile": _expected_profile(snapshot),
        "blocking_count": len(blocking),
        "warning_count": len(warnings),
        "checks": checks,
        "recommended_actions": list(dict.fromkeys(recommended_actions)),
    }


def format_doctor_report(report: dict[str, Any], output_format: str = "text") -> str:
    if output_format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2)
    status = "OK" if report.get("ok") else "ERROR"
    lines = [
        f"{status} webnovel-doctor",
        f"project_root: {report.get('project_root') or '(未解析)'}",
        f"phase: {report.get('phase')}",
        f"blocking: {report.get('blocking_count')} warnings: {report.get('warning_count')}",
    ]
    for item in report.get("checks") or []:
        if item.get("status") == CHECK_OK:
            continue
        lines.append(f"{str(item.get('status')).upper()} {item.get('id')}: {item.get('message')}")
        if item.get("path"):
            lines.append(f"  path: {item.get('path')}")
        if item.get("actual"):
            lines.append(f"  actual: {item.get('actual')}")
        if item.get("impact"):
            lines.append(f"  impact: {item.get('impact')}")
        if item.get("repair"):
            lines.append(f"  repair: {item.get('repair')}")
    actions = report.get("recommended_actions") or []
    if actions:
        lines.append("recommended_actions:")
        lines.extend(f"- {action}" for action in actions[:8])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run read-only webnovel project doctor")
    parser.add_argument("--project-root", default="", help="书项目根目录")
    parser.add_argument("--chapter", type=int, default=None, help="目标章节号")
    parser.add_argument("--deep", action="store_true", help="包含 dashboard 等较深检查")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args()

    report = build_doctor_report(args.project_root or None, chapter=args.chapter, deep=args.deep)
    print(format_doctor_report(report, args.format))
    raise SystemExit(0 if report.get("ok") else 1)


if __name__ == "__main__":
    main()
