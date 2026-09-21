from __future__ import annotations

import json
import subprocess

import backup_manager
from backup_manager import GitBackupManager


def test_backup_manager_gitignore_excludes_env(tmp_path, monkeypatch):
    def fake_run(args, cwd=None, check=False, capture_output=False, text=False, encoding=None, timeout=None):
        if args == ["git", "init"]:
            (tmp_path / ".git").mkdir(exist_ok=True)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(backup_manager, "is_git_available", lambda: True)
    monkeypatch.setattr(backup_manager.subprocess, "run", fake_run)

    GitBackupManager(str(tmp_path))

    gitignore = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gitignore
    assert ".env.*" in gitignore
    assert "!.env.example" in gitignore


def _run_git(project_root, *args):
    return subprocess.run(
        ["git", *args],
        cwd=project_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def _configure_git_identity(project_root):
    assert _run_git(project_root, "config", "user.name", "Test Author").returncode == 0
    assert _run_git(project_root, "config", "user.email", "author@example.com").returncode == 0


def test_backup_aborts_when_git_commit_fails_without_identity(tmp_path, monkeypatch, capsys):
    isolated_home = tmp_path / "home"
    isolated_home.mkdir()
    project_root = tmp_path / "project"
    project_root.mkdir()

    monkeypatch.setenv("HOME", str(isolated_home))
    monkeypatch.setenv("USERPROFILE", str(isolated_home))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    assert _run_git(project_root, "init", "-b", "main").returncode == 0
    assert _run_git(project_root, "config", "--local", "user.useConfigOnly", "true").returncode == 0
    _run_git(project_root, "config", "--local", "--unset", "user.name")
    _run_git(project_root, "config", "--local", "--unset", "user.email")

    manuscript_dir = project_root / "正文"
    manuscript_dir.mkdir()
    (manuscript_dir / "第0001章-test.md").write_text("正文", encoding="utf-8")

    manager = GitBackupManager(str(project_root))

    assert manager.backup(1, "身份缺失") is False

    output = capsys.readouterr().out
    assert "备份失败" in output
    assert _run_git(project_root, "rev-parse", "--verify", "ch0001").returncode != 0


def test_git_backup_writes_verifiable_receipt(tmp_path):
    assert _run_git(tmp_path, "init", "-b", "main").returncode == 0
    _configure_git_identity(tmp_path)
    chapter = tmp_path / "正文" / "第0001章.md"
    chapter.parent.mkdir()
    chapter.write_text("正文", encoding="utf-8")

    manager = GitBackupManager(str(tmp_path))
    assert manager.backup(1, "测试") is True

    receipt = json.loads((tmp_path / ".webnovel" / "backup_receipts.json").read_text(encoding="utf-8"))
    assert receipt["1"]["type"] == "git"
    assert receipt["1"]["tag"] == "ch0001"
    assert receipt["1"]["tag_commit"] == _run_git(tmp_path, "rev-parse", "HEAD").stdout.strip()


def test_git_backup_rejects_tag_creation_failure(tmp_path, monkeypatch):
    manager = GitBackupManager(str(tmp_path), auto_init=False)
    monkeypatch.setattr(manager, "git_available", True)
    monkeypatch.setattr(manager, "_git_backup_paths", lambda: ["正文"])

    def fake_git(args, check=True):
        if args[0] == "diff":
            return True, "正文/第0001章.md\n", ""
        if args[0] in {"add", "commit"}:
            return True, "", ""
        return False, "", "tag unavailable"

    monkeypatch.setattr(manager, "_run_git_command", fake_git)
    assert manager.backup(1) is False
    assert not manager._backup_receipt_path().exists()


def test_git_receipt_rejects_moved_tag(tmp_path):
    assert _run_git(tmp_path, "init", "-b", "main").returncode == 0
    _configure_git_identity(tmp_path)
    chapter = tmp_path / "正文/第0001章.md"
    chapter.parent.mkdir()
    chapter.write_text("正文", encoding="utf-8")
    manager = GitBackupManager(str(tmp_path))
    assert manager.backup(1)
    assert manager.verified_backup(1)
    (tmp_path / "正文/第0002章.md").write_text("下一章", encoding="utf-8")
    assert manager.backup(2)
    assert manager.verified_backup(1)
    assert _run_git(tmp_path, "tag", "-f", "ch0001", "HEAD").returncode == 0
    assert not manager.verified_backup(1)


def test_snapshot_receipt_rejects_manifest_tampering(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_manager, "is_git_available", lambda: False)
    chapter = tmp_path / "正文/第0001章.md"
    chapter.parent.mkdir()
    chapter.write_text("正文", encoding="utf-8")
    manager = GitBackupManager(str(tmp_path))
    assert manager.backup(1)
    receipt = manager.verified_backup(1)
    manifest = tmp_path / receipt["manifest"]
    manifest.write_text(manifest.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert not manager.verified_backup(1)


def test_local_backup_includes_story_system_manifest_and_excludes_runtime_noise(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_manager, "is_git_available", lambda: False)

    (tmp_path / ".webnovel").mkdir()
    (tmp_path / ".story-system" / "reconciliations").mkdir(parents=True)
    (tmp_path / "正文").mkdir()
    (tmp_path / "正文" / "第0001章.md").write_text("正文", encoding="utf-8")
    (tmp_path / ".story-system" / "reconciliations" / "decision.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".webnovel" / "summaries").mkdir()
    (tmp_path / ".webnovel" / "summaries" / "ch0001.md").write_text("摘要", encoding="utf-8")
    (tmp_path / ".webnovel" / "context_cache.json").write_text("缓存", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=do-not-copy", encoding="utf-8")

    manager = GitBackupManager(str(tmp_path))
    assert manager.backup(1) is True

    snapshot = next((tmp_path / ".webnovel" / "backups").glob("snapshot_ch0001_*"))
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    paths = {item["path"] for item in manifest["files"]}
    assert "正文/第0001章.md" in paths
    assert ".story-system/reconciliations/decision.json" in paths
    assert ".webnovel/summaries/ch0001.md" in paths
    assert ".webnovel/context_cache.json" not in paths
    assert ".env" not in paths
    assert not (snapshot / ".env").exists()
    receipt = json.loads((tmp_path / ".webnovel" / "backup_receipts.json").read_text(encoding="utf-8"))
    assert receipt["1"]["type"] == "snapshot"
    assert receipt["1"]["manifest_sha256"] == manager._sha256(snapshot / "manifest.json")


def test_local_backup_copies_manuscript_when_git_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_manager, "is_git_available", lambda: False)

    webnovel_dir = tmp_path / ".webnovel"
    manuscript_dir = tmp_path / "正文"
    outline_dir = tmp_path / "大纲"
    settings_dir = tmp_path / "设定集"
    webnovel_dir.mkdir()
    manuscript_dir.mkdir()
    outline_dir.mkdir()
    settings_dir.mkdir()
    (webnovel_dir / "state.json").write_text('{"current_chapter": 1}', encoding="utf-8")
    (manuscript_dir / "第0001章-x.md").write_text("正文内容", encoding="utf-8")
    (outline_dir / "第0001章.md").write_text("大纲内容", encoding="utf-8")
    (settings_dir / "人物.md").write_text("设定内容", encoding="utf-8")

    manager = GitBackupManager(str(tmp_path))

    assert manager.backup(1) is True

    snapshots = sorted((webnovel_dir / "backups").glob("snapshot_ch0001_*"))
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert (snapshot / "正文" / "第0001章-x.md").read_text(encoding="utf-8") == "正文内容"
    assert (snapshot / "大纲" / "第0001章.md").read_text(encoding="utf-8") == "大纲内容"
    assert (snapshot / "设定集" / "人物.md").read_text(encoding="utf-8") == "设定内容"
    assert (snapshot / ".webnovel" / "state.json").read_text(encoding="utf-8") == '{"current_chapter": 1}'

    for chapter in range(2, 13):
        assert manager.backup(chapter) is True

    snapshots = sorted((webnovel_dir / "backups").glob("snapshot_ch*"))
    assert len(snapshots) == 10
    assert snapshot not in snapshots


def _archived_tags(project_root, pattern):
    return [tag for tag in _run_git(project_root, "tag", "-l", pattern).stdout.split() if tag]


def test_backup_moves_chapter_tag_and_archives_previous_point(tmp_path):
    """重写同一章后再次备份：chNNNN 前移到新提交，旧版本点归档保留。"""
    assert _run_git(tmp_path, "init", "-b", "main").returncode == 0
    _configure_git_identity(tmp_path)
    chapter = tmp_path / "正文/第0001章.md"
    chapter.parent.mkdir()
    chapter.write_text("正文 v1", encoding="utf-8")
    manager = GitBackupManager(str(tmp_path))

    assert manager.backup(1, "初稿") is True
    first_commit = _run_git(tmp_path, "rev-parse", "ch0001").stdout.strip()
    assert manager.verified_backup(1)

    chapter.write_text("正文 v1 重写", encoding="utf-8")
    assert manager.backup(1, "重写") is True

    new_commit = _run_git(tmp_path, "rev-parse", "ch0001").stdout.strip()
    head_commit = _run_git(tmp_path, "rev-parse", "HEAD").stdout.strip()
    assert new_commit == head_commit
    assert new_commit != first_commit

    archived = _archived_tags(tmp_path, "ch0001-prev-*")
    assert len(archived) == 1
    assert _run_git(tmp_path, "rev-parse", archived[0]).stdout.strip() == first_commit
    assert _run_git(tmp_path, "cat-file", "-e", first_commit).returncode == 0

    receipt = manager.verified_backup(1)
    assert receipt and receipt["tag"] == "ch0001" and receipt["tag_commit"] == new_commit


def test_backup_same_commit_is_idempotent_without_archiving(tmp_path):
    """HEAD 未变时重复备份只刷新 receipt，不产生历史点。"""
    assert _run_git(tmp_path, "init", "-b", "main").returncode == 0
    _configure_git_identity(tmp_path)
    chapter = tmp_path / "正文/第0001章.md"
    chapter.parent.mkdir()
    chapter.write_text("正文", encoding="utf-8")
    manager = GitBackupManager(str(tmp_path))

    assert manager.backup(1) is True
    assert manager.backup(1) is True

    assert _archived_tags(tmp_path, "ch0001-prev-*") == []
    assert manager.verified_backup(1)


def test_backup_after_git_switch_rewrite_keeps_chapters_tagged(tmp_path):
    """按文档口径 git switch 回退后重写：同章号与后续章号都能建出新版本点。"""
    assert _run_git(tmp_path, "init", "-b", "main").returncode == 0
    _configure_git_identity(tmp_path)
    body_dir = tmp_path / "正文"
    body_dir.mkdir()
    (body_dir / "第0001章.md").write_text("正文 1 v1", encoding="utf-8")
    manager = GitBackupManager(str(tmp_path))
    assert manager.backup(1)
    first_commit = _run_git(tmp_path, "rev-parse", "ch0001").stdout.strip()

    (body_dir / "第0002章.md").write_text("正文 2", encoding="utf-8")
    assert manager.backup(2)
    stale_ch2_commit = _run_git(tmp_path, "rev-parse", "ch0002").stdout.strip()

    assert _run_git(tmp_path, "switch", "-c", "rewrite-from-ch0001", "ch0001").returncode == 0
    assert not (body_dir / "第0002章.md").exists()

    (body_dir / "第0001章.md").write_text("正文 1 v2", encoding="utf-8")
    assert manager.backup(1) is True
    assert _run_git(tmp_path, "rev-parse", "ch0001").stdout.strip() != first_commit

    (body_dir / "第0002章.md").write_text("正文 2 重写", encoding="utf-8")
    assert manager.backup(2) is True
    assert _run_git(tmp_path, "rev-parse", "ch0002").stdout.strip() != stale_ch2_commit

    assert manager.verified_backup(1)
    assert manager.verified_backup(2)
    main_ch2 = _archived_tags(tmp_path, "ch0002-prev-*")
    assert len(main_ch2) == 1
    assert _run_git(tmp_path, "rev-parse", main_ch2[0]).stdout.strip() == stale_ch2_commit


def test_list_backups_separates_current_and_archived_points(tmp_path, capsys):
    assert _run_git(tmp_path, "init", "-b", "main").returncode == 0
    _configure_git_identity(tmp_path)
    chapter = tmp_path / "正文/第0001章.md"
    chapter.parent.mkdir()
    chapter.write_text("正文 v1", encoding="utf-8")
    manager = GitBackupManager(str(tmp_path))
    assert manager.backup(1)
    chapter.write_text("正文 v2", encoding="utf-8")
    assert manager.backup(1)

    manager.list_backups()

    output = capsys.readouterr().out
    assert "ch0001" in output
    assert "历史点 ch0001-prev-" in output
    assert "1 个章节版本点，1 个历史点" in output
