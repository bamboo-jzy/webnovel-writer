#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from runtime_compat import enable_windows_utf8_stdio

from data_modules.chapter_commit_service import ChapterCommitService


def _read_json(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _projection_failures(payload: dict) -> tuple[list[str], list[str]]:
    """返回 (失败项, 未完成项)。

    rejected commit 的 `skipped` 属正常，不算失败；只有 `failed:` / `pending` 才算。
    事件镜像（story_events）单独记在 provenance 里，同样算"读模型没跑完"。
    """
    statuses = payload.get("projection_status")
    failed: list[str] = []
    pending: list[str] = []
    if isinstance(statuses, dict):
        failed = [
            f"{name}={status}"
            for name, status in sorted(statuses.items())
            if str(status).startswith("failed:")
        ]
        pending = [
            f"{name}={status}"
            for name, status in sorted(statuses.items())
            if str(status) == "pending"
        ]
    mirror_error = str((payload.get("provenance") or {}).get("event_mirror_error") or "")
    if mirror_error:
        failed.append(f"event_mirror={mirror_error}")
    return failed, pending


def main() -> None:
    parser = argparse.ArgumentParser(description="Chapter commit CLI")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--chapter", type=int, required=True)
    parser.add_argument("--review-result", required=True)
    parser.add_argument("--fulfillment-result", required=True)
    parser.add_argument("--disambiguation-result", required=True)
    parser.add_argument("--extraction-result", required=True)
    parser.add_argument("--expected-previous", default="", help="作者确认修订的上一 commit identity")
    parser.add_argument(
        "--allow-fact-revision",
        action="store_true",
        help=(
            "显式授权改写已 accepted 的事实：先撤回该章派生读模型（index 行/向量分块/"
            "story_events 镜像）再整章重建。默认拒绝此类修订。"
        ),
    )
    parser.add_argument("--revision-reason", default="", help="改写事实的原因（随 commit 留痕）")
    args = parser.parse_args()

    service = ChapterCommitService(Path(args.project_root))
    payload = service.build_commit(
        chapter=args.chapter,
        review_result=_read_json(args.review_result),
        fulfillment_result=_read_json(args.fulfillment_result),
        disambiguation_result=_read_json(args.disambiguation_result),
        extraction_result=_read_json(args.extraction_result),
    )
    try:
        service.persist_commit(
            payload,
            expected_previous=args.expected_previous,
            allow_fact_revision=args.allow_fact_revision,
            revision_reason=args.revision_reason,
        )
    except ValueError as exc:
        # 提交被拒：commit 文件未落盘，重跑即可，不需要回退正文。
        print(f"❌ 第 {args.chapter} 章提交被拒：{exc}", file=sys.stderr)
        if str(exc).startswith("revision_projection_unsafe"):
            print(
                "  上面给出的 identity 即当前版本；确认要改写事实时，加上",
                file=sys.stderr,
            )
            print(
                '  --allow-fact-revision --revision-reason "<为什么改写>" 再跑一次（会整章重建读模型）。',
                file=sys.stderr,
            )
        raise SystemExit(1)
    payload = service.apply_projections(payload)
    print(json.dumps(payload, ensure_ascii=False))

    failed, pending = _projection_failures(payload)
    if failed or pending:
        chapter = int((payload.get("meta") or {}).get("chapter") or args.chapter)
        print("", file=sys.stderr)
        print(
            f"⚠️ 第 {chapter} 章 commit 已落盘，但读模型投影没有跑完（正文与 commit 不受影响）：",
            file=sys.stderr,
        )
        for item in failed:
            print(f"  failed : {item}", file=sys.stderr)
        for item in pending:
            print(f"  pending: {item}", file=sys.stderr)
        print(
            "修复后只补跑投影，不要回退正文或重跑 review：",
            file=sys.stderr,
        )
        print(
            f'  python -X utf8 "<SCRIPTS_DIR>/webnovel.py" --project-root "{args.project_root}" '
            f"projections retry --chapter {chapter} --format json",
            file=sys.stderr,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    if sys.platform == "win32":
        enable_windows_utf8_stdio()
    main()
