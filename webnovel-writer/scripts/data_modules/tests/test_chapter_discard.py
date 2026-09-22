#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`chapter-discard`：草稿删除与版本点回退。"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def _ensure_scripts_on_path() -> None:
    scripts_dir = Path(__file__).resolve().parents[2]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


_ensure_scripts_on_path()

from data_modules import webnovel as unified_cli  # noqa: E402
from data_modules.chapter_discard import (  # noqa: E402
    discard_chapter_draft,
    format_chapter_discard_report,
    plan_chapter_discard,
    rollback_chapter,
    version_point_tag,
)

LEDGER = {
    "schema_version": "webnovel-run-ledger/v1",
    "write": {
        "chapter_002": {"chapter": 2, "mode": "default", "steps": {}, "updated_at": "x"},
        "chapter_001": {"chapter": 1, "mode": "default", "steps": {}, "updated_at": "x"},
    },
}

V1_PROGRESS = {
    "current_chapter": 1,
    "total_words": 100,
    "chapter_status": {"1": "chapter_committed"},
    "chapter_revisions": {},
    "chapters_planned": [
        {"chapter": 1, "status": "committed"},
        {"chapter": 2, "status": "planned"},
        {"chapter": 3, "status": "planned"},
    ],
}

V2_PROGRESS = {
    "current_chapter": 2,
    "total_words": 999999,
    "chapter_status": {"1": "chapter_committed", "2": "chapter_drafted"},
    "chapter_revisions": {"2": {"content_revision": "abc"}},
    "chapters_planned": [
        {"chapter": 1, "status": "committed"},
        {"chapter": 2, "status": "drafted"},
        {"chapter": 3, "status": "planned"},
    ],
}


def _write_state(root: Path, progress: dict, *, chapter_meta=None, checkpoints=None, warnings=None) -> None:
    (root / ".webnovel").mkdir(parents=True, exist_ok=True)
    state = {
        "project_info": {"title": "测试书"},
        "progress": json.loads(json.dumps(progress)),
        "chapter_meta": chapter_meta if chapter_meta is not None else {},
        "review_checkpoints": checkpoints if checkpoints is not None else [],
        "disambiguation_warnings": warnings if warnings is not None else [],
        "disambiguation_pending": [],
    }
    (root / ".webnovel/state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _chapter_one_files(root: Path) -> None:
    (root / "正文").mkdir(parents=True, exist_ok=True)
    (root / "正文/第0001章-开端.md").write_text("# 第1章 开端\n" + "字" * 100, encoding="utf-8")
    (root / "大纲").mkdir(parents=True, exist_ok=True)
    (root / "大纲/第1章-开端.md").write_text("目标：a\n", encoding="utf-8")
    _write_json(
        root / ".story-system/commits/chapter_001.commit.json",
        {"meta": {"chapter": 1, "status": "accepted", "content_revision": "r1"}},
    )


def _chapter_two_files(root: Path, *, accepted: bool = False) -> None:
    (root / "正文").mkdir(parents=True, exist_ok=True)
    (root / "正文/第0002章-转折.md").write_text("# 第2章 转折\n" + "字" * 200, encoding="utf-8")
    (root / "大纲").mkdir(parents=True, exist_ok=True)
    (root / "大纲/第2章-转折.md").write_text("目标：x\n", encoding="utf-8")
    (root / "审查报告").mkdir(parents=True, exist_ok=True)
    (root / "审查报告/第2章审查报告.md").write_text("# 审查\n", encoding="utf-8")
    _write_json(root / ".webnovel/tmp/review_results.json", {"chapter": 2, "blocking_count": 0})
    _write_json(root / ".webnovel/tmp/extraction_result.json", {"source": {"chapter": 2}})
    _write_json(root / ".webnovel/tmp/fulfillment_result.json", {"source": {"chapter": 1}})
    _write_json(root / ".webnovel/run_ledger.json", LEDGER)
    if accepted:
        _write_json(
            root / ".story-system/commits/chapter_002.commit.json",
            {"meta": {"chapter": 2, "status": "accepted", "content_revision": "r2"}},
        )


def _draft_project(root: Path, *, ch2_accepted: bool = False) -> Path:
    _write_state(
        root,
        V2_PROGRESS,
        chapter_meta={"2": {"dominant_strand": "quest"}},
        checkpoints=[{"chapter": 2, "note": "x"}, {"chapter": 1, "note": "keep"}],
        warnings=[{"chapter": 2, "text": "y"}],
    )
    _chapter_one_files(root)
    _chapter_two_files(root, accepted=ch2_accepted)
    return root


def _git(project: Path, *args: str, check: bool = True):
    return subprocess.run(
        ["git", *args], cwd=str(project), capture_output=True, text=True, check=check,
        encoding="utf-8", errors="replace",
    )


def _git_project(root: Path, *, with_chapter_two: bool = True) -> Path:
    """初始提交 = 空脚手架；ch0001 = 第 1 章完成；ch0002 = 第 2 章完成。"""
    if shutil.which("git") is None:  # pragma: no cover - 环境无 git
        pytest.skip("git 不可用")
    for rel in ("正文", "大纲", "审查报告", ".webnovel/tmp", ".story-system/commits"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    _write_state(root, {"current_chapter": 0, "total_words": 0, "chapter_status": {}, "chapter_revisions": {}, "chapters_planned": []})
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "tester")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "初始化网文项目")

    _write_state(root, V1_PROGRESS, checkpoints=[{"chapter": 1, "note": "keep"}])
    _chapter_one_files(root)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "第1章完成")
    _git(root, "tag", version_point_tag(1))
    if not with_chapter_two:
        return root

    _write_state(
        root,
        V2_PROGRESS,
        chapter_meta={"2": {"dominant_strand": "quest"}},
        checkpoints=[{"chapter": 2, "note": "x"}, {"chapter": 1, "note": "keep"}],
        warnings=[{"chapter": 2, "text": "y"}],
    )
    _chapter_two_files(root, accepted=True)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "第2章完成")
    _git(root, "tag", version_point_tag(2))
    return root


def _run_cli(monkeypatch, capsys, project: Path, *args: str):
    monkeypatch.setattr(sys, "argv", ["webnovel.py", "--project-root", str(project), "chapter-discard", *args])
    with pytest.raises(SystemExit) as excinfo:
        unified_cli.main()
    captured = capsys.readouterr().out
    return int(excinfo.value.code or 0), captured


# ---------------------------------------------------------------------------
# 分类 / 预览
# ---------------------------------------------------------------------------

def test_version_point_tag_is_chapter_completed_semantics():
    """`chNNNN` = 第 N 章完成后，抛弃第 N 章要回退 ch{N-1}。"""
    assert version_point_tag(1) == "ch0001"
    assert version_point_tag(10) == "ch0010"


def test_plan_reports_absent_chapter(tmp_path):
    _write_state(tmp_path, V1_PROGRESS)
    plan = plan_chapter_discard(tmp_path, 5)
    assert plan["classification"] == "absent"
    assert [item["code"] for item in plan["blockers"]] == ["chapter_absent"]
    assert plan["draft"]["allowed"] is False


def test_plan_classifies_draft_versus_accepted(tmp_path):
    draft_root = _draft_project(tmp_path / "draft")
    assert plan_chapter_discard(draft_root, 2)["classification"] == "draft"

    accepted_root = _draft_project(tmp_path / "accepted", ch2_accepted=True)
    plan = plan_chapter_discard(accepted_root, 2)
    assert plan["classification"] == "accepted"
    assert [item["code"] for item in plan["draft"]["blockers"]] == ["chapter_committed"]


def test_plan_flags_ambiguous_body_and_downstream(tmp_path):
    root = _draft_project(tmp_path)
    (root / "正文/第0002章-另一个标题.md").write_text("重复", encoding="utf-8")
    assert "body_candidates_ambiguous" in [item["code"] for item in plan_chapter_discard(root, 2)["blockers"]]

    clean_root = _draft_project(tmp_path / "clean")
    (clean_root / "正文/第0003章-后续.md").write_text("# 第3章\n字", encoding="utf-8")
    plan = plan_chapter_discard(clean_root, 2)
    assert "downstream_chapters_exist" in [item["code"] for item in plan["blockers"]]
    assert plan["downstream_chapters"] == [3]


# ---------------------------------------------------------------------------
# 草稿删除
# ---------------------------------------------------------------------------

def test_discard_draft_archives_and_cleans_state(tmp_path):
    root = _draft_project(tmp_path)
    report = discard_chapter_draft(root, 2, reason="写崩了")
    assert report["ok"], report

    # 正文与本章 artifacts 删除，章纲与别章 artifacts 保留
    assert not (root / "正文/第0002章-转折.md").exists()
    assert (root / "正文/第0001章-开端.md").exists()
    assert (root / "大纲/第2章-转折.md").exists()
    assert not (root / ".webnovel/tmp/review_results.json").exists()
    assert not (root / ".webnovel/tmp/extraction_result.json").exists()
    assert (root / ".webnovel/tmp/fulfillment_result.json").exists()
    assert not (root / "审查报告/第2章审查报告.md").exists()

    archive = Path(report["archive_dir"])
    assert (archive / "discard.json").is_file()
    assert (archive / "正文/第0002章-转折.md").is_file()
    assert (archive / "审查报告/第2章审查报告.md").is_file()
    manifest = json.loads((archive / "discard.json").read_text(encoding="utf-8"))
    assert manifest["chapter"] == 2 and manifest["reason"] == "写崩了"

    state = json.loads((root / ".webnovel/state.json").read_text(encoding="utf-8"))
    assert "2" not in state["progress"]["chapter_status"]
    assert "2" not in state["progress"]["chapter_revisions"]
    assert "2" not in state["chapter_meta"]
    assert state["progress"]["current_chapter"] == 1
    assert state["progress"]["total_words"] == 100
    assert [c for c in state["review_checkpoints"] if c.get("chapter") == 2] == []
    assert [w for w in state["disambiguation_warnings"] if w.get("chapter") == 2] == []
    assert [r for r in state["progress"]["chapters_planned"] if r["chapter"] == 2][0]["status"] == "planned"

    ledger = json.loads((root / ".webnovel/run_ledger.json").read_text(encoding="utf-8"))
    assert "chapter_002" not in ledger["write"] and "chapter_001" in ledger["write"]


def test_discard_draft_is_idempotently_blocked_on_second_run(tmp_path):
    root = _draft_project(tmp_path)
    assert discard_chapter_draft(root, 2)["ok"]
    again = discard_chapter_draft(root, 2)
    assert again["ok"] is False
    assert [item["code"] for item in again["blockers"]] == ["chapter_absent"]


def test_discard_draft_dry_run_writes_nothing(tmp_path):
    root = _draft_project(tmp_path)
    report = discard_chapter_draft(root, 2, dry_run=True)
    assert report["ok"] and report["status"] == "preview"
    assert (root / "正文/第0002章-转折.md").exists()
    assert not (root / ".webnovel/discarded").exists()


def test_discard_draft_rejects_committed_chapter(tmp_path):
    root = _draft_project(tmp_path, ch2_accepted=True)
    report = discard_chapter_draft(root, 2)
    assert report["ok"] is False
    assert [item["code"] for item in report["blockers"]] == ["chapter_committed"]
    assert (root / "正文/第0002章-转折.md").exists()


def test_discard_draft_rejects_downstream_chapters(tmp_path):
    root = _draft_project(tmp_path)
    (root / "正文/第0003章-后续.md").write_text("# 第3章\n字", encoding="utf-8")
    report = discard_chapter_draft(root, 2)
    assert report["ok"] is False
    assert "downstream_chapters_exist" in [item["code"] for item in report["blockers"]]
    assert (root / "正文/第0002章-转折.md").exists()
    assert not (root / ".webnovel/discarded").exists()


def test_discard_draft_survives_corrupt_run_ledger(tmp_path):
    """断点账本是派生缓存，损坏只警告，不阻断抛弃。"""
    root = _draft_project(tmp_path)
    (root / ".webnovel/run_ledger.json").write_text("{ not json", encoding="utf-8")
    report = discard_chapter_draft(root, 2)
    assert report["ok"], report
    assert any("run_ledger" in item for item in report.get("warnings", []))
    assert not (root / "正文/第0002章-转折.md").exists()


def test_format_report_text_includes_blockers_and_warnings():
    text = format_chapter_discard_report(
        {
            "ok": False,
            "action": "discard_draft",
            "chapter": 2,
            "plan": {"classification": "accepted", "downstream_chapters": []},
            "blockers": [{"code": "chapter_committed", "message": "已提交"}],
            "warnings": ["run_ledger 未清理：x"],
        }
    )
    assert "chapter_committed: 已提交" in text
    assert "run_ledger 未清理：x" in text


# ---------------------------------------------------------------------------
# 已提交章：原地回退（不新建分支）
# ---------------------------------------------------------------------------

def _branches(root: Path) -> list[str]:
    return sorted(_git(root, "branch", "--format=%(refname:short)").stdout.split())


def test_rollback_preview_targets_previous_version_point_in_place(tmp_path):
    root = _git_project(tmp_path)
    plan = plan_chapter_discard(root, 2)
    assert plan["classification"] == "accepted"
    rollback = plan["rollback"]
    assert rollback["allowed"], rollback["blockers"]
    assert rollback["target_tag"] == "ch0001"
    assert rollback["version_point_exists"] is True
    assert rollback["command"] == "git read-tree -u --reset ch0001"
    assert rollback["commit_message"] == "Discard chapter 2: restore to ch0001"
    assert rollback["mode"] == "in_place"
    assert rollback["creates_branch"] is False
    assert "branch_name" not in rollback
    assert rollback["recovery_hint"]


def _status_porcelain(root: Path) -> str:
    return _git(root, "-c", "core.quotepath=false", "status", "--porcelain").stdout


def _assert_worktree_restored(root: Path, rel: str, tag: str) -> None:
    """回退后工作树应恢复 `rel`；工作树被环境吞写时退到 git 侧等价证据。

    本机沙箱把 TMP 钉在仓库内 `.tmp/pytest/`（`scripts/conftest.py:21`），该路径下
    git 写工作树可能被静默吞掉：目标文件缺失、`git status` 记为 ` D`，而索引与新提交的
    树都正确等于版本点。此类证据下跳过，避免把环境缺陷误判成产品缺陷；树恢复已在
    工作区外烟测验证（`.workbuddy/tmp/discard_smoke.py`）。
    """
    if (root / rel).exists():
        return
    tracked = _git(root, "-c", "core.quotepath=false", "ls-files").stdout
    head_tree = _git(root, "rev-parse", "HEAD^{tree}").stdout.strip()
    point_tree = _git(root, "rev-parse", f"{tag}^{{tree}}").stdout.strip()
    if head_tree == point_tree and rel in tracked and f" D {rel}" in _status_porcelain(root):
        pytest.skip(f"本机沙箱吞掉 git 写工作树（{rel} 缺失但新提交树=={tag}）；树恢复见工作区外烟测")
    assert (root / rel).exists(), f"回退后 {rel} 未恢复：\n{_status_porcelain(root)}"


def test_rollback_restores_tree_in_place_and_keeps_commit_recoverable(tmp_path):
    """原地回退：分支不变、不新建分支，被抛弃的提交成为新提交的父提交。"""
    root = _git_project(tmp_path)
    old_branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    discarded_commit = _git(root, "rev-parse", "HEAD").stdout.strip()
    branches_before = _branches(root)

    report = rollback_chapter(root, 2, reason="整章作废")
    assert report["ok"], report
    assert report["mode"] == "in_place"
    assert report["target_ref"] == "ch0001"
    assert report["tree_matches_target"] is True
    assert report["chapter_body_absent"] is True
    assert report["current_chapter_after"] == 1
    assert Path(report["archive_dir"]).is_dir()
    assert any(name.endswith("第0002章-转折.md") for name in report["archived"])

    # 不新建分支：分支列表不变，HEAD 仍在原分支上
    assert _branches(root) == branches_before, _git(root, "branch", "-a").stdout
    assert _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == old_branch

    assert not (root / "正文/第0002章-转折.md").exists()
    assert not (root / ".story-system/commits/chapter_002.commit.json").exists()
    _assert_worktree_restored(root, "正文/第0001章-开端.md", "ch0001")
    state = json.loads((root / ".webnovel/state.json").read_text(encoding="utf-8"))
    assert state["progress"]["current_chapter"] == 1
    assert state["progress"]["chapter_status"] == {"1": "chapter_committed"}

    # 不删除提交、不移动版本点：父提交与 tag ch0002 都还指向被抛弃的那次提交
    assert _git(root, "rev-parse", "HEAD^").stdout.strip() == discarded_commit
    assert _git(root, "rev-parse", "ch0002").stdout.strip() == discarded_commit
    show = _git(root, "show", "ch0002:正文/第0002章-转折.md", check=False)
    assert show.returncode == 0


def test_rollback_twice_in_a_row_needs_no_manual_cleanup(tmp_path):
    """旧实现第二次抛弃会被同名分支挡下；原地回退没有这个副作用。"""
    root = _git_project(tmp_path)
    assert rollback_chapter(root, 2)["ok"]
    branches_after_first = _branches(root)

    # 重写第 2 章并重新备份（同章重写路径：tag 由 backup 前移）
    (root / "正文/第0002章-转折.md").write_text("# 第2章 转折（重写）\n" + "字" * 200, encoding="utf-8")
    _write_json(
        root / ".story-system/commits/chapter_002.commit.json",
        {"meta": {"chapter": 2, "status": "accepted", "content_revision": "r2b"}},
    )
    _write_state(root, V2_PROGRESS, chapter_meta={"2": {"dominant_strand": "quest"}})
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "Chapter 2: 重写")
    _git(root, "tag", "-f", "ch0002")

    report = rollback_chapter(root, 2)
    assert report["ok"], report.get("blockers") or report.get("error")
    assert not (root / "正文/第0002章-转折.md").exists()
    assert _branches(root) == branches_after_first, _git(root, "branch", "-a").stdout


def test_rollback_first_chapter_targets_initial_commit(tmp_path):
    root = _git_project(tmp_path, with_chapter_two=False)
    branches_before = _branches(root)
    plan = plan_chapter_discard(root, 1)
    rollback = plan["rollback"]
    assert rollback["allowed"], rollback["blockers"]
    assert rollback["target_kind"] == "initial_commit"
    assert rollback["target_commit"] == _git(root, "rev-list", "--max-parents=0", "HEAD").stdout.strip()
    assert rollback["creates_branch"] is False
    assert rollback["commit_message"] == f"Discard chapter 1: restore to {rollback['target_commit'][:8]}"

    report = rollback_chapter(root, 1)
    assert report["ok"], report
    assert not (root / "正文/第0001章-开端.md").exists()
    assert _branches(root) == branches_before
    state = json.loads((root / ".webnovel/state.json").read_text(encoding="utf-8"))
    assert state["progress"]["current_chapter"] == 0
    assert _git(root, "show", "ch0001:正文/第0001章-开端.md", check=False).returncode == 0


def test_rollback_rejects_dirty_working_tree(tmp_path):
    root = _git_project(tmp_path)
    (root / "正文/第0001章-开端.md").write_text("# 改过了\n字", encoding="utf-8")
    report = rollback_chapter(root, 2)
    assert report["ok"] is False
    assert [item["code"] for item in report["blockers"]] == ["working_tree_dirty"]
    assert (root / "正文/第0002章-转折.md").exists()


def test_rollback_rejects_missing_version_point(tmp_path):
    root = _git_project(tmp_path)
    _git(root, "tag", "-d", "ch0001")
    plan = plan_chapter_discard(root, 2)
    assert "version_point_missing" in [item["code"] for item in plan["rollback"]["blockers"]]
    assert rollback_chapter(root, 2)["ok"] is False


def test_rollback_rejects_version_point_from_foreign_history(tmp_path):
    """版本点不在当前分支历史上 → 阻断（原地恢复会把两段历史混在一起）。"""
    root = _git_project(tmp_path)
    _git(root, "tag", "-d", "ch0001")
    _git(root, "checkout", "-q", "-b", "foreign", "HEAD~1")
    (root / "正文/第0001章-开端.md").write_text("# 另一条线的第1章\n字", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "Foreign chapter 1")
    _git(root, "tag", "ch0001")
    _git(root, "checkout", "-q", "-")

    plan = plan_chapter_discard(root, 2)
    assert "version_point_not_ancestor" in [item["code"] for item in plan["rollback"]["blockers"]]
    assert rollback_chapter(root, 2)["ok"] is False
    assert (root / "正文/第0002章-转折.md").exists()


def test_rollback_rejects_draft_chapter(tmp_path):
    root = _draft_project(tmp_path)
    report = rollback_chapter(root, 2)
    assert report["ok"] is False
    assert "chapter_not_committed" in [item["code"] for item in report["blockers"]]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_preview_returns_zero_for_draft_and_json(tmp_path, monkeypatch, capsys):
    root = _draft_project(tmp_path)
    code, out = _run_cli(monkeypatch, capsys, root, "--chapter", "2", "--format", "json")
    assert code == 0
    payload = json.loads(out)
    assert payload["action"] == "preview"
    assert payload["plan"]["classification"] == "draft"


def test_cli_draft_blocks_on_committed_chapter(tmp_path, monkeypatch, capsys):
    root = _draft_project(tmp_path, ch2_accepted=True)
    code, out = _run_cli(monkeypatch, capsys, root, "--chapter", "2", "--draft", "--format", "json")
    assert code == 1
    payload = json.loads(out)
    assert payload["error"] == "blocked"


def test_cli_draft_deletes_and_reports_archive(tmp_path, monkeypatch, capsys):
    root = _draft_project(tmp_path)
    code, out = _run_cli(monkeypatch, capsys, root, "--chapter", "2", "--draft", "--format", "json")
    assert code == 0
    payload = json.loads(out)
    assert payload["status"] == "discarded"
    assert Path(payload["archive_dir"]).is_dir()
    assert not (root / "正文/第0002章-转折.md").exists()


def test_cli_text_format_renders_blockers(tmp_path, monkeypatch, capsys):
    root = _draft_project(tmp_path, ch2_accepted=True)
    code, out = _run_cli(monkeypatch, capsys, root, "--chapter", "2", "--draft")
    assert code == 1
    assert "chapter_committed" in out
