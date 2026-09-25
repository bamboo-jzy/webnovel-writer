#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
webnovel 统一入口（面向 skills / agents 的稳定 CLI）

设计目标：
- 只有一个入口命令，避免到处拼 `python -m data_modules.xxx ...` 导致参数位置/引号/路径炸裂。
- 自动解析正确的 book project_root（包含 `.webnovel/state.json` 的目录）。
- 所有写入类命令在解析到 project_root 后，统一前置 `--project-root` 传给具体模块。

典型用法（推荐，不依赖 PYTHONPATH / 不要求 cd）：
  python "<SCRIPTS_DIR>/webnovel.py" preflight
  python "<SCRIPTS_DIR>/webnovel.py" where
  # 单书工作区规约：一个工作区只放一本书，正常不需要下面的 use（仅在指针丢失/异常时应急）
  python "<SCRIPTS_DIR>/webnovel.py" use "<PROJECT_ROOT>"
  python "<SCRIPTS_DIR>/webnovel.py" --project-root "<PROJECT_ROOT>" index stats
  python "<SCRIPTS_DIR>/webnovel.py" --project-root "<PROJECT_ROOT>" state process-chapter --chapter 100 --data @payload.json
  python "<SCRIPTS_DIR>/webnovel.py" --project-root "<PROJECT_ROOT>" extract-context --chapter 100 --format json

也支持（不推荐，容易踩 PYTHONPATH/cd/参数顺序坑）：
  python -m data_modules.webnovel where
"""

from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

from runtime_compat import enable_windows_utf8_stdio, normalize_windows_path
from project_locator import (
    WorkspaceHasMultipleBooksError,
    read_current_project_pointer,
    resolve_project_root,
    update_global_registry_current_project,
    write_current_project_pointer,
)

from .story_runtime_health import build_story_runtime_health


if sys.platform == "win32":
    enable_windows_utf8_stdio(skip_in_pytest=True)


def _scripts_dir() -> Path:
    # data_modules/webnovel.py -> data_modules -> scripts
    return Path(__file__).resolve().parent.parent


def _resolve_root(explicit_project_root: Optional[str]) -> Path:
    # 允许显式传入工作区根目录或书项目根目录
    raw = explicit_project_root
    if raw:
        return resolve_project_root(raw)
    return resolve_project_root()


def _strip_project_root_args(argv: list[str]) -> list[str]:
    """
    下游工具统一由本入口注入 `--project-root`，避免重复传参导致 argparse 报错/歧义。
    """
    out: list[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--project-root":
            i += 2
            continue
        if tok.startswith("--project-root="):
            i += 1
            continue
        out.append(tok)
        i += 1
    return out


PASSTHROUGH_TOOLS = {
    "index",
    "state",
    "rag",
    "entity",
    "context",
    "memory",
    "status",
    "update-state",
    "backup",
    "archive",
    "init",
    "story-system",
    "memory-contract",
    "project-memory",
    "scope-audit",
    "style-profile",
}


def _passthrough_tail(argv: list[str], tool: str) -> list[str]:
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "--project-root":
            i += 2
            continue
        if token.startswith("--project-root="):
            i += 1
            continue
        if token == tool:
            return list(argv[i + 1 :])
        i += 1
    return []


def _run_data_module(module: str, argv: list[str]) -> int:
    """
    Import `data_modules.<module>` and call its main(), while isolating sys.argv.
    """
    mod = importlib.import_module(f"data_modules.{module}")
    main = getattr(mod, "main", None)
    if not callable(main):
        raise RuntimeError(f"data_modules.{module} 缺少可调用的 main()")

    old_argv = sys.argv
    try:
        sys.argv = [f"data_modules.{module}"] + argv
        try:
            main()
            return 0
        except SystemExit as e:
            return int(e.code or 0)
    finally:
        sys.argv = old_argv


def _run_script(script_name: str, argv: list[str]) -> int:
    """
    Run a script under `.claude/scripts/` via a subprocess.

    用途：兼容没有 main() 的脚本。
    """
    script_path = _scripts_dir() / script_name
    if not script_path.is_file():
        raise FileNotFoundError(f"未找到脚本: {script_path}")
    proc = subprocess.run([sys.executable, str(script_path), *argv])
    return int(proc.returncode or 0)


def cmd_where(args: argparse.Namespace) -> int:
    try:
        root = _resolve_root(args.project_root)
    except FileNotFoundError as exc:
        print(_project_root_diagnostic(args.project_root, exc), file=sys.stderr)
        return 1
    print(str(root))
    return 0


def _project_root_diagnostic(
    explicit_project_root: Optional[str], exc: FileNotFoundError
) -> str:
    if isinstance(exc, WorkspaceHasMultipleBooksError):
        return _multiple_books_diagnostic(exc)
    if explicit_project_root:
        return (
            "未找到有效书项目根目录（需要包含 .webnovel/state.json）: "
            f"{explicit_project_root}\n"
            f"detail: {exc}"
        )
    return (
        "当前工作区还没有激活的书项目（未找到 .webnovel/state.json）。\n"
        "请先运行 /webnovel-init 创建项目。\n"
        f"detail: {exc}"
    )


def _multiple_books_diagnostic(exc: WorkspaceHasMultipleBooksError) -> str:
    """多书工作区的诊断：单书规约下说得清“有几本书”，而不是“这里不是项目”。"""
    lines = [
        "本插件按「一个工作区一本书」使用，但当前工作区里检测到多本书，无法判定该用哪一本。",
        f"工作区: {exc.workspace_root}",
        f"检测到 {len(exc.books)} 本书：",
    ]
    lines.extend(f"  - {book.name}  ({book})" for book in exc.books)
    lines.extend(
        [
            "处理方式（任选其一）：",
            "  1) 只保留一本：把其余书目录移出该工作区到各自独立的工作区（推荐）",
            '  2) 指定其中一本：给命令加 --project-root "<书目录>"',
            '  3) 应急绑定：webnovel use "<书目录>" 写入工作区指针（此后该工作区固定解析到它）',
            f"detail: {exc}",
        ]
    )
    return "\n".join(lines)


def _build_preflight_report(explicit_project_root: Optional[str]) -> dict:
    scripts_dir = _scripts_dir().resolve()
    plugin_root = scripts_dir.parent
    skill_root = plugin_root / "skills" / "webnovel-write"
    entry_script = scripts_dir / "webnovel.py"
    extract_script = scripts_dir / "extract_chapter_context.py"

    checks: list[dict[str, object]] = [
        {"name": "scripts_dir", "ok": scripts_dir.is_dir(), "path": str(scripts_dir)},
        {"name": "entry_script", "ok": entry_script.is_file(), "path": str(entry_script)},
        {"name": "extract_context_script", "ok": extract_script.is_file(), "path": str(extract_script)},
        {"name": "skill_root", "ok": skill_root.is_dir(), "path": str(skill_root)},
    ]

    project_root = ""
    project_root_error = ""
    story_runtime: dict = {}
    try:
        resolved_root = _resolve_root(explicit_project_root)
        project_root = str(resolved_root)
        checks.append({"name": "project_root", "ok": True, "path": project_root})
        story_runtime = build_story_runtime_health(resolved_root)
    except FileNotFoundError as exc:
        project_root_error = _project_root_diagnostic(explicit_project_root, exc)
        checks.append(
            {
                "name": "project_root",
                "ok": False,
                "path": explicit_project_root or "",
                "error": project_root_error,
            }
        )
    except Exception as exc:
        project_root_error = str(exc)
        checks.append({"name": "project_root", "ok": False, "path": explicit_project_root or "", "error": project_root_error})

    return {
        "ok": all(bool(item["ok"]) for item in checks),
        "project_root": project_root,
        "scripts_dir": str(scripts_dir),
        "skill_root": str(skill_root),
        "checks": checks,
        "project_root_error": project_root_error,
        "story_runtime": story_runtime,
    }


def cmd_preflight(args: argparse.Namespace) -> int:
    report = _build_preflight_report(args.project_root)
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for item in report["checks"]:
            status = "OK" if item["ok"] else "ERROR"
            path = item.get("path") or ""
            print(f"{status} {item['name']}: {path}")
            if item.get("error"):
                print(f"  detail: {item['error']}")
        story_runtime = report.get("story_runtime") or {}
        if story_runtime:
            print(
                "INFO story_runtime: "
                f"chapter={story_runtime.get('chapter')} "
                f"mainline_ready={story_runtime.get('mainline_ready')} "
                f"latest_commit_status={story_runtime.get('latest_commit_status')}"
            )
    return 0 if report["ok"] else 1


def cmd_project_status(args: argparse.Namespace) -> int:
    from .project_status import build_project_status, format_project_status

    try:
        root: Path | str | None = _resolve_root(args.project_root)
    except FileNotFoundError:
        root = args.project_root or None
    report = build_project_status(root, chapter=args.chapter)
    print(format_project_status(report, args.format))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from .doctor import build_doctor_report, format_doctor_report

    preflight_report = _build_preflight_report(args.project_root)
    root: Path | str | None = preflight_report.get("project_root") or args.project_root or None
    report = build_doctor_report(
        root,
        chapter=args.chapter,
        deep=bool(args.deep),
        preflight_report=preflight_report,
    )
    print(format_doctor_report(report, args.format))
    return 0 if report.get("ok") else 1


def cmd_write_gate(args: argparse.Namespace) -> int:
    from .write_gates import format_gate_report, run_write_gate

    root = _resolve_root(args.project_root)
    report = run_write_gate(root, chapter=args.chapter, stage=args.stage)
    print(format_gate_report(report, args.format))
    return 0 if report.get("ok") else 1


def cmd_projections(args: argparse.Namespace) -> int:
    from .projections import format_projection_report, replay_projections, retry_projection

    root = _resolve_root(args.project_root)
    if args.projection_action == "retry":
        report = retry_projection(root, chapter=args.chapter, force_retract=bool(args.retract))
    else:
        report = replay_projections(
            root,
            start_chapter=args.from_chapter,
            end_chapter=args.to_chapter,
            force_retract=bool(args.retract),
        )
    print(format_projection_report(report, args.format))
    return 0 if report.get("ok") else 1


def cmd_user_report(args: argparse.Namespace) -> int:
    from .user_report import build_user_report, format_user_report

    root = _resolve_root(args.project_root)
    report = build_user_report(
        root,
        stage=args.stage,
        chapter=args.chapter,
        volume=args.volume,
    )
    print(format_user_report(report, args.format))
    return 0


def cmd_chapter_reload(args: argparse.Namespace) -> int:
    from .chapter_reloading import format_chapter_reload_report, reload_chapter_body, validate_chapter_body, reconcile_chapter_body

    root = _resolve_root(args.project_root)
    if args.reconcile:
        report = reconcile_chapter_body(
            root, args.chapter, decision=args.decision, reason=args.reason,
            impact=args.impact, expected_input=args.expected_input, dry_run=args.dry_run,
        )
    elif args.decision or args.reason or args.impact is not None or args.expected_input:
        report = {"ok": False, "action": "reload", "chapter": args.chapter, "error": "decision options require --reconcile"}
    elif args.validate:
        report = validate_chapter_body(root, args.chapter, dry_run=args.dry_run)
    else:
        report = reload_chapter_body(root, args.chapter, source=args.source, dry_run=args.dry_run, backup_only=args.backup_only)
    print(format_chapter_reload_report(report, args.format))
    return 0 if report.get("ok") else 1


def cmd_chapter_discard(args: argparse.Namespace) -> int:
    from .chapter_discard import (
        discard_chapter_draft,
        format_chapter_discard_report,
        plan_chapter_discard,
        rollback_chapter,
    )

    root = _resolve_root(args.project_root)
    if args.draft:
        report = discard_chapter_draft(root, args.chapter, reason=args.reason)
    elif args.rollback:
        report = rollback_chapter(root, args.chapter, reason=args.reason)
    else:
        plan = plan_chapter_discard(root, args.chapter)
        report = {
            "schema_version": plan["schema_version"],
            "action": "preview",
            "chapter": args.chapter,
            "dry_run": True,
            "ok": not plan["blockers"],
            "reason": args.reason,
            "plan": plan,
        }
    print(format_chapter_discard_report(report, args.format))
    return 0 if report.get("ok") else 1


def cmd_volume_reload(args: argparse.Namespace) -> int:
    from .volume_planning import format_volume_reload_report, reload_volume_plan

    root = _resolve_root(args.project_root)
    report = reload_volume_plan(
        root,
        args.volume,
        chapters_range=args.chapters_range,
        source=args.source,
        dry_run=bool(args.dry_run),
        backup_only=bool(args.backup_only),
    )
    print(format_volume_reload_report(report, args.format))
    return 0 if report.get("ok") else 1


def cmd_handoff(args: argparse.Namespace) -> int:
    from .direction_handoff import (
        check_handoff,
        describe_handoff_scope,
        format_handoff_report,
        record_handoff,
    )

    root = _resolve_root(args.project_root)
    if args.record:
        report = record_handoff(
            root,
            args.node,
            target=args.target,
            decision=args.decision,
            reason=args.reason,
            impact=args.impact,
            expected_input=args.expected_input,
            dry_run=bool(args.dry_run),
        )
    elif args.check:
        report = check_handoff(root, args.node, target=args.target)
    else:
        report = describe_handoff_scope(root, args.node, target=args.target)
    print(format_handoff_report(report, args.format))
    return 0 if report.get("ok") else 1


def cmd_run_ledger(args: argparse.Namespace) -> int:
    from .run_ledger import (
        build_write_resume_plan,
        format_resume_plan,
        record_write_step,
    )

    root = _resolve_root(args.project_root)
    if args.ledger_action == "record-write-step":
        try:
            inputs = json.loads(args.inputs_json)
            outputs = json.loads(args.outputs_json)
            problems = json.loads(args.problems_json)
            auto_handled = json.loads(args.auto_handled_json)
        except json.JSONDecodeError as exc:
            print(f"ledger JSON 参数不合法: {exc}", file=sys.stderr)
            return 2
        if not isinstance(inputs, dict) or not isinstance(outputs, dict):
            print("inputs-json / outputs-json 必须是 JSON object", file=sys.stderr)
            return 2
        if not isinstance(problems, list) or not isinstance(auto_handled, list):
            print("problems-json / auto-handled-json 必须是 JSON list", file=sys.stderr)
            return 2
        entry = record_write_step(
            root,
            chapter=args.chapter,
            step=args.step,
            status=args.status,
            mode=args.mode,
            inputs={str(key): str(value) for key, value in inputs.items()},
            outputs={str(key): str(value) for key, value in outputs.items()},
            problems=[str(item) for item in problems],
            auto_handled=[str(item) for item in auto_handled],
            duration_ms=args.duration_ms,
        )
        if args.format == "json":
            print(json.dumps(entry, ensure_ascii=False, indent=2))
        else:
            print(f"{entry['step']}: {entry['status']}")
        return 0
    if args.ledger_action == "write-resume":
        report = build_write_resume_plan(
            root,
            chapter=args.chapter,
            mode=args.mode,
        )
        print(format_resume_plan(report, args.format))
        return 0
    return 2


def cmd_run_log(args: argparse.Namespace) -> int:
    from .run_logger import write_run_log

    try:
        root = _resolve_root(args.project_root)
    except FileNotFoundError:
        root = normalize_windows_path(args.project_root).expanduser()
        try:
            root = root.resolve()
        except Exception:
            root = root
    try:
        payload = json.loads(args.payload_json)
    except json.JSONDecodeError as exc:
        print(f"payload-json 不是合法 JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(payload, dict):
        print("payload-json 必须是 JSON object", file=sys.stderr)
        return 2
    result = write_run_log(root, event=args.event, payload=payload, append=args.append)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["path"])
    return 0


def cmd_use(args: argparse.Namespace) -> int:
    project_root = normalize_windows_path(args.project_root).expanduser()
    try:
        project_root = project_root.resolve()
    except Exception as exc:
        print(f"⚠️ path.resolve() 失败 ({project_root}): {exc}", file=sys.stderr)

    workspace_root: Optional[Path] = None
    if args.workspace_root:
        workspace_root = normalize_windows_path(args.workspace_root).expanduser()
        try:
            workspace_root = workspace_root.resolve()
        except Exception as exc:
            print(f"⚠️ path.resolve() 失败 ({workspace_root}): {exc}", file=sys.stderr)

    # 0) 回显原绑定：单书工作区下一次绑定可能替换掉另一本书（应急路径，需让作者看见）
    if workspace_root is not None:
        try:
            previous = read_current_project_pointer(workspace_root)
        except Exception:
            previous = None
        if previous is not None and previous != project_root:
            print(f"⚠ 工作区原绑定的书项目: {previous}", file=sys.stderr)

    # 1) 写入工作区指针（若工作区内存在 `.claude/`）
    try:
        pointer_file = write_current_project_pointer(project_root, workspace_root=workspace_root)
    except FileNotFoundError as exc:
        # 目标是无效书项目：给作者可读的诊断，而不是 traceback
        print(_project_root_diagnostic(str(project_root), exc), file=sys.stderr)
        return 1
    if pointer_file is not None:
        print(f"workspace pointer: {pointer_file}")
    else:
        print("workspace pointer: (skipped)")

    # 2) 写入用户级 registry（保证全局安装/空上下文可恢复）
    reg_path = update_global_registry_current_project(workspace_root=workspace_root, project_root=project_root)
    if reg_path is not None:
        print(f"global registry: {reg_path}")
    else:
        print("global registry: (skipped)")

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="webnovel unified CLI")
    parser.add_argument("--project-root", help="书项目根目录或工作区根目录（可选，默认自动检测）")

    sub = parser.add_subparsers(dest="tool", required=True)

    p_where = sub.add_parser("where", help="打印解析出的 project_root")
    p_where.set_defaults(func=cmd_where)

    p_preflight = sub.add_parser("preflight", help="校验统一 CLI 运行环境与 project_root")
    p_preflight.add_argument("--format", choices=["text", "json"], default="text", help="输出格式")
    p_preflight.set_defaults(func=cmd_preflight)

    p_project_status = sub.add_parser("project-status", help="输出机器可读的项目短状态")
    p_project_status.add_argument("--chapter", type=int, default=None, help="目标章节号")
    p_project_status.add_argument("--format", choices=["summary", "json"], default="summary", help="输出格式")
    p_project_status.set_defaults(func=cmd_project_status)

    p_doctor = sub.add_parser("doctor", help="阶段感知的只读项目体检")
    p_doctor.add_argument("--chapter", type=int, default=None, help="目标章节号")
    p_doctor.add_argument("--deep", action="store_true", help="包含 dashboard 等较深检查")
    p_doctor.add_argument("--format", choices=["text", "json"], default="text", help="输出格式")
    p_doctor.set_defaults(func=cmd_doctor)

    p_write_gate = sub.add_parser("write-gate", help="写章自然边界校验")
    p_write_gate.add_argument("--chapter", type=int, required=True, help="目标章节号")
    p_write_gate.add_argument("--stage", choices=["prewrite", "precommit", "postcommit"], required=True, help="校验阶段")
    p_write_gate.add_argument("--format", choices=["json", "text"], default="json", help="输出格式")
    p_write_gate.set_defaults(func=cmd_write_gate)

    p_projections = sub.add_parser("projections", help="从已有 commit 补跑或重放 projection")
    projections_sub = p_projections.add_subparsers(dest="projection_action", required=True)
    p_projection_retry = projections_sub.add_parser("retry", help="补跑单章 projection")
    p_projection_retry.add_argument("--chapter", type=int, required=True, help="目标章节号")
    p_projection_retry.add_argument(
        "--retract",
        action="store_true",
        help="先撤回该章已有派生行（index 行/向量分块/story_events 镜像）再重放",
    )
    p_projection_retry.add_argument("--format", choices=["json", "text"], default="json", help="输出格式")
    p_projection_retry.set_defaults(func=cmd_projections)
    p_projection_replay = projections_sub.add_parser("replay", help="按章节范围重放 projection")
    p_projection_replay.add_argument("--from-chapter", type=int, required=True, help="起始章节号")
    p_projection_replay.add_argument("--to-chapter", type=int, required=True, help="结束章节号")
    p_projection_replay.add_argument(
        "--retract",
        action="store_true",
        help="每章重放前先撤回该章派生行（修复半途写坏的投影）",
    )
    p_projection_replay.add_argument("--format", choices=["json", "text"], default="json", help="输出格式")
    p_projection_replay.set_defaults(func=cmd_projections)

    p_user_report = sub.add_parser("user-report", help="渲染作者友好的最终报告")
    p_user_report.add_argument("--stage", choices=["init", "plan", "chapter-plan", "write", "review"], required=True, help="报告阶段")
    p_user_report.add_argument("--chapter", type=int, default=None, help="目标章节号")
    p_user_report.add_argument("--volume", type=int, default=None, help="目标卷号")
    p_user_report.add_argument("--format", choices=["text", "json"], default="text", help="输出格式")
    p_user_report.set_defaults(func=cmd_user_report)

    p_chapter_reload = sub.add_parser("chapter-reload", help="重载人工正文并校验当前版本 artifacts")
    p_chapter_reload.add_argument("--chapter", type=int, required=True)
    p_chapter_reload.add_argument("--source", choices=["manual_reload", "generated_draft"], default="manual_reload")
    p_chapter_reload.add_argument("--dry-run", action="store_true")
    reload_mode = p_chapter_reload.add_mutually_exclusive_group()
    reload_mode.add_argument("--backup-only", action="store_true")
    reload_mode.add_argument("--validate", action="store_true")
    reload_mode.add_argument("--reconcile", action="store_true", help="预览或记录作者裁决，不修改正文或章纲")
    p_chapter_reload.add_argument("--decision", choices=["outline_to_body", "body_to_outline", "accepted_deviation"], default="")
    p_chapter_reload.add_argument("--reason", default="")
    p_chapter_reload.add_argument("--impact", nargs="*", type=int, default=None, help="明确列出受影响章节；空列表也需传此参数")
    p_chapter_reload.add_argument("--expected-input", default="", help="作者确认的预览 input_token")
    p_chapter_reload.add_argument("--format", choices=["json", "text"], default="text")
    p_chapter_reload.set_defaults(func=cmd_chapter_reload)

    p_chapter_discard = sub.add_parser(
        "chapter-discard",
        help="抛弃章节正文：未提交草稿直接删除，已提交章回退到上一章版本点",
    )
    p_chapter_discard.add_argument("--chapter", type=int, required=True, help="目标章节号")
    discard_mode = p_chapter_discard.add_mutually_exclusive_group()
    discard_mode.add_argument("--dry-run", action="store_true", help="只预览，不写入（默认）")
    discard_mode.add_argument("--draft", action="store_true", help="执行：删除未提交草稿（先归档到 .webnovel/discarded/）")
    discard_mode.add_argument("--rollback", action="store_true", help="执行：已提交章原地回退到上一章版本点（不新建分支）")
    p_chapter_discard.add_argument("--reason", default="", help="抛弃理由，记入归档清单")
    p_chapter_discard.add_argument("--format", choices=["json", "text"], default="text", help="输出格式")
    p_chapter_discard.set_defaults(func=cmd_chapter_discard)

    p_volume_reload = sub.add_parser("volume-reload", help="重载卷纲 revision 并标记下游章纲过期")
    p_volume_reload.add_argument("--volume", type=int, required=True, help="目标卷号")
    p_volume_reload.add_argument("--chapters-range", default="", help="章节范围（首次登记时可提供）")
    p_volume_reload.add_argument("--source", default="manual_reload", help="重载来源")
    p_volume_reload.add_argument("--dry-run", action="store_true", help="只检查和预览，不写入状态")
    p_volume_reload.add_argument("--backup-only", action="store_true", help="只备份当前卷纲文件，不更新状态")
    p_volume_reload.add_argument("--format", choices=["json", "text"], default="text", help="输出格式")
    p_volume_reload.set_defaults(func=cmd_volume_reload)

    p_handoff = sub.add_parser(
        "handoff",
        help="四层方向透传：总纲→卷纲→章纲→正文 的边界与一致性裁决",
    )
    p_handoff.add_argument(
        "--node",
        required=True,
        choices=["master_to_volume", "volume_to_chapter", "chapter_to_body"],
        help="方向透传节点",
    )
    p_handoff.add_argument("--target", type=int, default=0, help="目标卷号（总-卷）或章号（卷-章 / 章-正）")
    p_handoff.add_argument("--check", action="store_true", help="采集两侧锚点并输出候选偏离（默认只输出作用域）")
    p_handoff.add_argument("--record", action="store_true", help="预览或记录作者裁决，不修改任何创作文件")
    p_handoff.add_argument(
        "--decision",
        choices=["align_downstream", "align_upstream", "accepted_deviation"],
        default="",
        help="裁决口径：改下游 / 改上游（需上溯）/ 接受偏离",
    )
    p_handoff.add_argument("--reason", default="")
    p_handoff.add_argument("--impact", nargs="*", type=int, default=None, help="明确列出受影响单位；空列表也需传此参数")
    p_handoff.add_argument("--expected-input", default="", help="作者确认的预览 input_token")
    p_handoff.add_argument("--dry-run", action="store_true", help="只预览，不写入裁决记录")
    p_handoff.add_argument("--format", choices=["json", "text"], default="text", help="输出格式")
    p_handoff.set_defaults(func=cmd_handoff)

    p_run_ledger = sub.add_parser("run-ledger", help="记录或查询写章断点续跑状态")
    run_ledger_sub = p_run_ledger.add_subparsers(dest="ledger_action", required=True)
    p_record_write_step = run_ledger_sub.add_parser("record-write-step", help="记录写章步骤状态")
    p_record_write_step.add_argument("--chapter", type=int, required=True, help="目标章节号")
    p_record_write_step.add_argument("--step", choices=["draft", "review", "data", "commit", "projection", "backup"], required=True)
    p_record_write_step.add_argument("--status", required=True)
    p_record_write_step.add_argument("--mode", default="default")
    p_record_write_step.add_argument("--inputs-json", default="{}")
    p_record_write_step.add_argument("--outputs-json", default="{}")
    p_record_write_step.add_argument("--problems-json", default="[]")
    p_record_write_step.add_argument("--auto-handled-json", default="[]")
    p_record_write_step.add_argument("--duration-ms", type=int, default=0)
    p_record_write_step.add_argument("--format", choices=["json", "text"], default="json", help="输出格式")
    p_record_write_step.set_defaults(func=cmd_run_ledger)
    p_write_resume = run_ledger_sub.add_parser("write-resume", help="输出写章断点续跑建议")
    p_write_resume.add_argument("--chapter", type=int, required=True, help="目标章节号")
    p_write_resume.add_argument("--mode", default="default", help="写章模式")
    p_write_resume.add_argument("--format", choices=["json", "text"], default="json", help="输出格式")
    p_write_resume.set_defaults(func=cmd_run_ledger)

    p_run_log = sub.add_parser("run-log", help="写入脱敏运行日志")
    p_run_log.add_argument("--event", required=True, help="事件名")
    p_run_log.add_argument("--payload-json", default="{}", help="要写入日志的 JSON 对象")
    p_run_log.add_argument("--append", action="store_true", help="追加而不是覆盖 run_last.log")
    p_run_log.add_argument("--format", choices=["json", "text"], default="json", help="输出格式")
    p_run_log.set_defaults(func=cmd_run_log)

    p_use = sub.add_parser(
        "use",
        help="【应急】把工作区绑定到指定书项目（写指针/registry）；单书工作区通常不需要",
    )
    p_use.add_argument("project_root", help="书项目根目录（必须包含 .webnovel/state.json）")
    p_use.add_argument("--workspace-root", help="工作区根目录（可选；默认由运行环境推断）")
    p_use.set_defaults(func=cmd_use)

    # Pass-through to data modules
    p_index = sub.add_parser("index", help="转发到 index_manager")
    p_index.add_argument("args", nargs=argparse.REMAINDER)

    p_state = sub.add_parser("state", help="转发到 state_manager")
    p_state.add_argument("args", nargs=argparse.REMAINDER)

    p_rag = sub.add_parser("rag", help="转发到 rag_adapter")
    p_rag.add_argument("args", nargs=argparse.REMAINDER)

    p_entity = sub.add_parser("entity", help="转发到 entity_linker")
    p_entity.add_argument("args", nargs=argparse.REMAINDER)

    p_context = sub.add_parser("context", help="转发到 context_manager")
    p_context.add_argument("args", nargs=argparse.REMAINDER)

    p_memory = sub.add_parser("memory", help="转发到 memory.store")
    p_memory.add_argument("args", nargs=argparse.REMAINDER)

    # Pass-through to scripts
    p_status = sub.add_parser("status", help="转发到 status_reporter.py")
    p_status.add_argument("args", nargs=argparse.REMAINDER)

    p_update_state = sub.add_parser("update-state", help="转发到 update_state.py")
    p_update_state.add_argument("args", nargs=argparse.REMAINDER)

    p_backup = sub.add_parser("backup", help="转发到 backup_manager.py")
    p_backup.add_argument("args", nargs=argparse.REMAINDER)

    p_archive = sub.add_parser("archive", help="转发到 archive_manager.py")
    p_archive.add_argument("args", nargs=argparse.REMAINDER)

    p_init = sub.add_parser("init", help="转发到 init_project.py（初始化项目）")
    p_init.add_argument("args", nargs=argparse.REMAINDER)

    p_extract_context = sub.add_parser("extract-context", help="转发到 extract_chapter_context.py")
    p_extract_context.add_argument("--chapter", type=int, required=True, help="目标章节号")
    p_extract_context.add_argument("--format", choices=["text", "json"], default="text", help="输出格式")

    p_story_system = sub.add_parser("story-system", help="转发到 story_system.py")
    p_story_system.add_argument("args", nargs=argparse.REMAINDER)

    p_story_events = sub.add_parser("story-events", help="转发到 story_events.py")
    p_story_events.add_argument("--chapter", type=int, default=0, help="目标章节号")
    p_story_events.add_argument("--limit", type=int, default=200, help="查询条数")
    p_story_events.add_argument("--health", action="store_true", help="输出事件链健康信息")

    p_commit = sub.add_parser("chapter-commit", help="转发到 chapter_commit.py")
    p_commit.add_argument("--chapter", type=int, required=True, help="目标章节号")
    p_commit.add_argument("--review-result", default="", help="review_result JSON 文件")
    p_commit.add_argument("--fulfillment-result", default="", help="fulfillment_result JSON 文件")
    p_commit.add_argument("--disambiguation-result", default="", help="disambiguation_result JSON 文件")
    p_commit.add_argument("--extraction-result", default="", help="extraction_result JSON 文件")
    p_commit.add_argument("--expected-previous", default="", help="作者确认修订的上一 commit identity")
    p_commit.add_argument(
        "--allow-fact-revision",
        action="store_true",
        help="显式授权改写已 accepted 的事实（先撤回该章派生读模型再整章重建）",
    )
    p_commit.add_argument("--revision-reason", default="", help="改写事实的原因（随 commit 留痕）")

    p_memory_contract = sub.add_parser("memory-contract", help="转发到 memory_cli.py")
    p_memory_contract.add_argument("args", nargs=argparse.REMAINDER)

    p_project_memory = sub.add_parser("project-memory", help="转发到 project_memory.py")
    p_project_memory.add_argument("args", nargs=argparse.REMAINDER)

    p_scope_audit = sub.add_parser("scope-audit", help="转发到 scope_audit.py（范围级回扫，只读）")
    p_scope_audit.add_argument("args", nargs=argparse.REMAINDER)

    p_style_profile = sub.add_parser(
        "style-profile", help="转发到 style_profile.py（文风档案：正文现状 + 文风目录目标）"
    )
    p_style_profile.add_argument("args", nargs=argparse.REMAINDER)

    p_review_pipeline = sub.add_parser("review-pipeline", help="转发到 review_pipeline.py")
    p_review_pipeline.add_argument("--chapter", type=int, required=True, help="目标章节号")
    p_review_pipeline.add_argument("--review-results", required=True, help="reviewer 原始结果 JSON 文件")
    p_review_pipeline.add_argument("--metrics-out", default="", help="metrics 输出文件")
    p_review_pipeline.add_argument("--report-file", default="", help="审查报告路径")
    p_review_pipeline.add_argument("--save-metrics", action="store_true", help="直接写入 index.db")

    p_placeholder_scan = sub.add_parser("placeholder-scan", help="扫描大纲/设定集未补齐占位")
    p_placeholder_scan.add_argument("--format", choices=["json", "text"], default="json", help="输出格式")

    p_master_outline_sync = sub.add_parser("master-outline-sync", help="当前卷规划完成后写回 V+1 最小总纲锚点")
    p_master_outline_sync.add_argument("--volume", type=int, required=True, help="当前已完成规划的卷号")
    p_master_outline_sync.add_argument("--writeback-file", default="", help="显式结构化写回 JSON")
    p_master_outline_sync.add_argument("--format", choices=["json", "text"], default="json", help="输出格式")

    knowledge_parser = sub.add_parser("knowledge", help="时序知识查询")
    knowledge_sub = knowledge_parser.add_subparsers(dest="knowledge_action")

    qs_parser = knowledge_sub.add_parser("query-entity-state", help="查询实体在指定章节的状态")
    qs_parser.add_argument("--entity", required=True, help="实体 ID")
    qs_parser.add_argument("--at-chapter", type=int, required=True, help="目标章节号")

    qr_parser = knowledge_sub.add_parser("query-relationships", help="查询实体在指定章节的关系")
    qr_parser.add_argument("--entity", required=True, help="实体 ID")
    qr_parser.add_argument("--at-chapter", type=int, required=True, help="目标章节号")

    # 兼容：允许 `--project-root` 出现在任意位置（减少 agents/skills 拼命令的出错率）
    from .cli_args import normalize_global_project_root

    argv = normalize_global_project_root(sys.argv[1:])
    args, unknown_args = parser.parse_known_args(argv)

    # where/use 直接执行
    if hasattr(args, "func"):
        if unknown_args:
            parser.error(f"unrecognized arguments: {' '.join(unknown_args)}")
        code = int(args.func(args) or 0)
        raise SystemExit(code)

    tool = args.tool
    if unknown_args and tool not in PASSTHROUGH_TOOLS:
        parser.error(f"unrecognized arguments: {' '.join(unknown_args)}")

    rest = _passthrough_tail(argv, tool) if tool in PASSTHROUGH_TOOLS else list(getattr(args, "args", []) or [])
    # argparse.REMAINDER 可能以 `--` 开头占位，这里去掉
    if rest[:1] == ["--"]:
        rest = rest[1:]
    rest = _strip_project_root_args(rest)

    # init 是创建项目，不应该依赖/注入已存在 project_root
    if tool == "init":
        raise SystemExit(_run_script("init_project.py", rest))

    # 其余工具：统一解析 project_root 后前置给下游
    project_root = _resolve_root(args.project_root)
    forward_args = ["--project-root", str(project_root)]

    if tool == "index":
        raise SystemExit(_run_data_module("index_manager", [*forward_args, *rest]))
    if tool == "state":
        raise SystemExit(_run_data_module("state_manager", [*forward_args, *rest]))
    if tool == "rag":
        raise SystemExit(_run_data_module("rag_adapter", [*forward_args, *rest]))
    if tool == "entity":
        raise SystemExit(_run_data_module("entity_linker", [*forward_args, *rest]))
    if tool == "context":
        raise SystemExit(_run_data_module("context_manager", [*forward_args, *rest]))
    if tool == "memory":
        raise SystemExit(_run_data_module("memory.store", [*forward_args, *rest]))
    if tool == "status":
        raise SystemExit(_run_script("status_reporter.py", [*forward_args, *rest]))
    if tool == "update-state":
        raise SystemExit(_run_script("update_state.py", [*forward_args, *rest]))
    if tool == "backup":
        raise SystemExit(_run_script("backup_manager.py", [*forward_args, *rest]))
    if tool == "scope-audit":
        raise SystemExit(_run_script("scope_audit.py", [*forward_args, *rest]))
    if tool == "style-profile":
        raise SystemExit(_run_script("style_profile.py", [*forward_args, *rest]))
    if tool == "archive":
        raise SystemExit(_run_script("archive_manager.py", [*forward_args, *rest]))
    if tool == "extract-context":
        return_args = [*forward_args, "--chapter", str(args.chapter), "--format", str(args.format)]
        raise SystemExit(_run_script("extract_chapter_context.py", return_args))
    if tool == "story-system":
        raise SystemExit(_run_script("story_system.py", [*forward_args, *rest]))
    if tool == "story-events":
        return_args = [*forward_args, "--limit", str(args.limit)]
        if args.chapter:
            return_args.extend(["--chapter", str(args.chapter)])
        if args.health:
            return_args.append("--health")
        raise SystemExit(_run_script("story_events.py", return_args))
    if tool == "chapter-commit":
        return_args = [*forward_args, "--chapter", str(args.chapter)]
        if args.review_result:
            return_args.extend(["--review-result", str(args.review_result)])
        if args.fulfillment_result:
            return_args.extend(["--fulfillment-result", str(args.fulfillment_result)])
        if args.disambiguation_result:
            return_args.extend(["--disambiguation-result", str(args.disambiguation_result)])
        if args.extraction_result:
            return_args.extend(["--extraction-result", str(args.extraction_result)])
        if args.expected_previous:
            return_args.extend(["--expected-previous", str(args.expected_previous)])
        if args.allow_fact_revision:
            return_args.append("--allow-fact-revision")
            if args.revision_reason:
                return_args.extend(["--revision-reason", str(args.revision_reason)])
        raise SystemExit(_run_script("chapter_commit.py", return_args))
    if tool == "memory-contract":
        raise SystemExit(_run_script("memory_cli.py", [*forward_args, *rest]))
    if tool == "project-memory":
        raise SystemExit(_run_script("project_memory.py", [*forward_args, *rest]))
    if tool == "review-pipeline":
        return_args = [
            *forward_args,
            "--chapter", str(args.chapter),
            "--review-results", str(args.review_results),
        ]
        if args.metrics_out:
            return_args.extend(["--metrics-out", str(args.metrics_out)])
        if args.report_file:
            return_args.extend(["--report-file", str(args.report_file)])
        if args.save_metrics:
            return_args.append("--save-metrics")
        raise SystemExit(_run_script("review_pipeline.py", return_args))
    if tool == "placeholder-scan":
        raise SystemExit(_run_data_module("placeholder_scanner", [*forward_args, "--format", str(args.format)]))
    if tool == "master-outline-sync":
        return_args = [*forward_args, "--volume", str(args.volume), "--format", str(args.format)]
        if args.writeback_file:
            return_args.extend(["--writeback-file", str(args.writeback_file)])
        raise SystemExit(_run_script("update_master_outline.py", return_args))

    if tool == "knowledge":
        from .knowledge_query import KnowledgeQuery
        from .cli_output import print_success
        kq = KnowledgeQuery(project_root)
        if args.knowledge_action == "query-entity-state":
            result = kq.entity_state_at_chapter(args.entity, args.at_chapter)
            print_success(result, message="entity_state_at_chapter")
            raise SystemExit(0)
        elif args.knowledge_action == "query-relationships":
            result = kq.entity_relationships_at_chapter(args.entity, args.at_chapter)
            print_success(result, message="entity_relationships_at_chapter")
            raise SystemExit(0)

    raise SystemExit(2)


if __name__ == "__main__":
    main()
