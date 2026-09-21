#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chapter-commit CLI 的退出码契约。

背景（docs/operations/index-integrity-assessment-2026-09-17.md）：
commit 先落盘、投影后补，所以"投影失败"不等于"提交失败"。旧行为一律 exit=0，
只看返回码的下游会把 index 只读之类的软失败当成成功；这里把它固化成非 0。
"""

import json
import sys
from pathlib import Path

import pytest

from data_modules.projections import retry_projection


def _ensure_scripts_on_path() -> Path:
    scripts_dir = Path(__file__).resolve().parents[2]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    return scripts_dir


SCRIPTS_DIR = _ensure_scripts_on_path()

import chapter_commit  # noqa: E402


def _write_artifacts(root: Path) -> list[str]:
    artifacts = root / ".webnovel" / "tmp"
    artifacts.mkdir(parents=True, exist_ok=True)
    args: list[str] = []
    for key, name in (
        ("--review-result", "review_results.json"),
        ("--fulfillment-result", "fulfillment_result.json"),
        ("--disambiguation-result", "disambiguation_result.json"),
        ("--extraction-result", "extraction_result.json"),
    ):
        path = artifacts / name
        path.write_text("{}", encoding="utf-8")
        args.extend([key, str(path)])
    return args


class _FakeService:
    """只回放投影状态，用来单独固化 CLI 的退出码策略。"""

    def __init__(self, root, projection_status):
        self.root = Path(root)
        self.projection_status = projection_status

    def build_commit(self, **kwargs):
        return {
            "meta": {"chapter": int(kwargs["chapter"]), "status": "accepted"},
            "projection_status": dict(self.projection_status),
        }

    def persist_commit(self, payload, *, expected_previous="", allow_fact_revision=False, revision_reason=""):
        path = self.root / ".story-system" / "commits" / f"chapter_{int(payload['meta']['chapter']):03d}.commit.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def apply_projections(self, payload):
        return payload


def _install_fake_service(monkeypatch, projection_status):
    monkeypatch.setattr(
        chapter_commit,
        "ChapterCommitService",
        lambda root: _FakeService(root, projection_status),
    )


def _run_cli(root: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "chapter_commit.py",
            "--project-root",
            str(root),
            "--chapter",
            "1",
            *_write_artifacts(root),
        ],
    )
    chapter_commit.main()


def test_chapter_commit_cli_exits_zero_when_projections_complete(tmp_path, monkeypatch):
    _install_fake_service(
        monkeypatch,
        {"state": "done", "index": "done", "summary": "done", "memory": "done", "vector": "skipped"},
    )

    _run_cli(tmp_path, monkeypatch)


def test_chapter_commit_cli_exits_nonzero_when_projection_failed(tmp_path, monkeypatch, capsys):
    _install_fake_service(
        monkeypatch,
        {
            "state": "done",
            "index": "failed:attempt to write a readonly database",
            "summary": "done",
            "memory": "done",
            "vector": "skipped",
        },
    )

    with pytest.raises(SystemExit) as exc:
        _run_cli(tmp_path, monkeypatch)

    assert int(exc.value.code) == 1
    captured = capsys.readouterr()
    assert "failed:attempt to write a readonly database" in captured.err
    assert "projections retry --chapter 1" in captured.err
    # 提交事实已经落盘，不能被这个退出码掩盖
    assert (tmp_path / ".story-system" / "commits" / "chapter_001.commit.json").is_file()


def test_chapter_commit_cli_exits_nonzero_when_projection_pending(tmp_path, monkeypatch):
    _install_fake_service(
        monkeypatch,
        {"state": "done", "index": "pending", "summary": "pending", "memory": "pending", "vector": "pending"},
    )

    with pytest.raises(SystemExit) as exc:
        _run_cli(tmp_path, monkeypatch)

    assert int(exc.value.code) == 1


def test_chapter_commit_cli_accepts_skipped_rejected_commit(tmp_path, monkeypatch):
    """rejected commit 的 index=skipped 是正常状态，不能因此报失败。"""
    _install_fake_service(monkeypatch, {"state": "done", "index": "skipped"})

    _run_cli(tmp_path, monkeypatch)


def test_chapter_commit_cli_flags_event_mirror_failure(tmp_path, monkeypatch, capsys):
    """事件镜像失败记在 provenance 里，同样算"读模型没跑完"。"""
    _install_fake_service(monkeypatch, {"state": "done", "index": "done"})

    class _MirrorFailingService(_FakeService):
        def persist_commit(self, payload, *, expected_previous="", allow_fact_revision=False, revision_reason=""):
            payload.setdefault("provenance", {})["event_mirror_error"] = "file is not a database"
            return super().persist_commit(
                payload,
                expected_previous=expected_previous,
                allow_fact_revision=allow_fact_revision,
                revision_reason=revision_reason,
            )

    monkeypatch.setattr(
        chapter_commit, "ChapterCommitService", lambda root: _MirrorFailingService(root, {"state": "done", "index": "done"})
    )

    with pytest.raises(SystemExit) as exc:
        _run_cli(tmp_path, monkeypatch)

    assert int(exc.value.code) == 1
    assert "event_mirror=file is not a database" in capsys.readouterr().err


def test_index_failure_is_recorded_on_commit_when_reprojecting(tmp_path):
    """真实链路：index.db 打不开时投影失败留痕在 commit 里，且 commit 依然在盘上。"""
    from .test_chapter_reloading import accepted_commit

    accepted_commit(tmp_path, chapter=1)
    db_path = tmp_path / ".webnovel" / "index.db"
    db_path.unlink()
    db_path.mkdir(parents=True)

    report = retry_projection(tmp_path, chapter=1)

    assert report["ok"] is False
    assert str(report["projection_status"]["index"]).startswith("failed:")
    on_disk = json.loads(
        (tmp_path / ".story-system" / "commits" / "chapter_001.commit.json").read_text(encoding="utf-8")
    )
    assert on_disk["meta"]["status"] == "accepted"
    assert str(on_disk["projection_status"]["index"]).startswith("failed:")
