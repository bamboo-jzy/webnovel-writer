from __future__ import annotations

import json
import multiprocessing
import os
import subprocess
from pathlib import Path

import pytest

import backup_manager
from backup_manager import GitBackupManager
from data_modules.chapter_commit_service import ChapterCommitService
from data_modules.chapter_reloading import artifact_paths, validate_chapter_body
from data_modules.doctor import build_doctor_report
from data_modules.projection_log import (
    append_projection_run, projection_log_path,
    read_projection_runs,
)
from data_modules.projections import retry_projection
from data_modules.run_ledger import build_write_resume_plan, load_ledger, record_write_step
from data_modules.write_gates import run_write_gate

from .test_chapter_reloading import accepted_commit, prepare_inputs


def _concurrent_writer(root: str, step: str, barrier) -> None:
    for chapter in range(1, 7):
        barrier.wait(timeout=30)
        record_write_step(root, chapter=chapter, step=step, status="completed")
        append_projection_run(
            root, {"meta": {"chapter": chapter, "status": "accepted"}},
            {"state": {"status": "done", "step": step, "note": "中文记录"}},
        )


def test_concurrent_ledger_and_log_writes_preserve_all_records(tmp_path: Path) -> None:
    root = tmp_path / "中文书名 with spaces"
    root.mkdir()
    context = multiprocessing.get_context("spawn")
    steps = ("draft", "review", "data", "commit")
    barrier = context.Barrier(len(steps))
    processes = [
        context.Process(target=_concurrent_writer, args=(str(root), step, barrier))
        for step in steps
    ]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=60)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=10)

    ledger = load_ledger(root)
    assert len(ledger["write"]) == 6
    for run in ledger["write"].values():
        assert set(run["steps"]) == set(steps)
        assert all(entry["status"] == "completed" for entry in run["steps"].values())
    records = read_projection_runs(root)
    assert len(records) == len({record["run_id"] for record in records}) == 24
    assert {(record["chapter"], record["writers"]["state"]["step"]) for record in records} == {
        (chapter, step) for chapter in range(1, 7) for step in steps
    }


def test_deterministic_accepted_commit_fixture_reaches_projection(tmp_path: Path) -> None:
    payload = accepted_commit(tmp_path, chapter=1)
    commit_path = tmp_path / ".story-system" / "commits" / "chapter_001.commit.json"
    assert payload["meta"]["status"] == "accepted"
    assert commit_path.is_file()
    assert projection_log_path(tmp_path).is_file()
    assert run_write_gate(tmp_path, chapter=1, stage="postcommit")["ok"] is True


def test_deterministic_corrupt_projection_fixture_blocks_health_and_gate(tmp_path: Path) -> None:
    payload = accepted_commit(tmp_path, chapter=1)
    path = projection_log_path(tmp_path)
    path.write_text(path.read_text(encoding="utf-8") + '{"chapter":1', encoding="utf-8")
    before = path.read_bytes()
    doctor = build_doctor_report(tmp_path, chapter=1)
    gate = run_write_gate(tmp_path, chapter=1, stage="postcommit")
    assert doctor["ok"] is False
    assert any(item["id"] == "projection_log.corrupt" for item in doctor["checks"])
    assert gate["ok"] is False
    assert build_write_resume_plan(tmp_path, chapter=1)["blocked"] is True
    assert retry_projection(tmp_path, chapter=1)["ok"] is False
    with pytest.raises(ValueError, match="projection log"):
        ChapterCommitService(tmp_path).apply_projection_writers(payload)
    assert path.read_bytes() == before


def test_deterministic_corrupt_ledger_fixture_blocks_resume(tmp_path: Path) -> None:
    accepted_commit(tmp_path)
    ledger = tmp_path / ".webnovel" / "run_ledger.json"
    ledger.write_text(json.dumps({"schema_version": "webnovel-run-ledger/v1", "write": []}), encoding="utf-8")
    plan = build_write_resume_plan(tmp_path, chapter=1)
    assert plan["blocked"] is True
    assert plan["needs_user_confirmation"][0]["code"] == "run_ledger_corrupt"
    doctor = build_doctor_report(tmp_path, chapter=1)
    assert any(item["id"] == "run_ledger.corrupt" for item in doctor["checks"])
    assert doctor["ok"] is False


def test_projection_log_write_failure_is_reported_and_retryable(tmp_path, monkeypatch):
    payload = accepted_commit(tmp_path)
    before = projection_log_path(tmp_path).read_bytes()

    def fail_append(*args, **kwargs):
        raise OSError("projection log disk full")

    with monkeypatch.context() as patch:
        patch.setattr("data_modules.projection_log.append_projection_run", fail_append)
        with pytest.raises(OSError, match="disk full"):
            ChapterCommitService(tmp_path).apply_projection_writers(payload)
        report = retry_projection(tmp_path, chapter=1)
        assert report["ok"] is False
        assert "disk full" in report["error"]
    assert projection_log_path(tmp_path).read_bytes() == before
    assert retry_projection(tmp_path, chapter=1)["ok"] is True
    assert len(read_projection_runs(tmp_path)) == 2


@pytest.mark.parametrize("kind", ["git", "snapshot"])
@pytest.mark.parametrize("legacy", [False, True])
def test_backup_receipt_resume_and_manual_edit(tmp_path, monkeypatch, kind, legacy):
    root = tmp_path / "中文书 with spaces"
    root.mkdir()
    accepted_commit(root)
    if kind == "snapshot":
        monkeypatch.setattr(backup_manager, "is_git_available", lambda: False)
    else:
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
        monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        for role in ("AUTHOR", "COMMITTER"):
            monkeypatch.setenv(f"GIT_{role}_NAME", "Fixture Author")
            monkeypatch.setenv(f"GIT_{role}_EMAIL", "fixture@example.invalid")
        subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
    manager = GitBackupManager(str(root), auto_init=False)
    assert manager.backup(1)
    if legacy:
        (root / ".webnovel/backup_receipts.json").unlink()
    receipt = manager.verified_backup(1)
    assert receipt["type"] == kind
    assert bool(receipt.get("legacy")) is legacy
    actions = {item["step"]: item["action"] for item in build_write_resume_plan(root, chapter=1)["steps"]}
    assert actions["backup"] == "skip"
    if kind == "git":
        assert not list((root / ".webnovel/backups").glob("snapshot_ch*"))
        assert manager.backup(1), "unchanged Git backup must keep the current tag"
    (root / "正文/第0001章.md").write_text("人工修改正文，不得复用旧备份或旧审查。", encoding="utf-8")
    assert not manager.verified_backup(1)
    actions = {item["step"]: item["action"] for item in build_write_resume_plan(root, chapter=1)["steps"]}
    assert actions["backup"] == "retry"
    assert actions["review"] == "run"


def test_missing_artifact_fixture_blocks_commit_without_losing_body(tmp_path):
    prepare_inputs(tmp_path)
    body = tmp_path / "正文/第0001章.md"
    before = body.read_bytes()
    artifact_paths(tmp_path)["extraction_result"].unlink()
    assert validate_chapter_body(tmp_path, 1)["ok"] is False
    assert run_write_gate(tmp_path, chapter=1, stage="precommit")["ok"] is False
    assert not (tmp_path / ".story-system/commits/chapter_001.commit.json").exists()
    assert body.read_bytes() == before
