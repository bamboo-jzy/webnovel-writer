#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
from pathlib import Path

import pytest


def _ensure_scripts_on_path() -> None:
    scripts_dir = Path(__file__).resolve().parents[2]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


@pytest.fixture(autouse=True)
def isolate_project_locator_environment(monkeypatch, tmp_path):
    monkeypatch.delenv("WEBNOVEL_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.setenv("WEBNOVEL_CLAUDE_HOME", str(tmp_path / "empty-claude-home"))


def test_resolve_project_root_prefers_cwd_project(tmp_path):
    _ensure_scripts_on_path()

    from project_locator import resolve_project_root

    (tmp_path / ".git").mkdir(parents=True, exist_ok=True)
    project_root = tmp_path / "workspace"
    (project_root / ".webnovel").mkdir(parents=True, exist_ok=True)
    (project_root / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")

    resolved = resolve_project_root(cwd=project_root)
    assert resolved == project_root.resolve()


def test_resolve_project_root_stops_at_git_root(tmp_path):
    _ensure_scripts_on_path()

    from project_locator import resolve_project_root

    repo_root = tmp_path / "repo"
    (repo_root / ".git").mkdir(parents=True, exist_ok=True)

    nested = repo_root / "sub" / "dir"
    nested.mkdir(parents=True, exist_ok=True)

    outside_project = tmp_path / "outside_project"
    (outside_project / ".webnovel").mkdir(parents=True, exist_ok=True)
    (outside_project / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")

    try:
        resolve_project_root(cwd=nested)
        assert False, "Expected FileNotFoundError when only parent outside git root has project"
    except FileNotFoundError:
        pass


def test_resolve_project_root_finds_default_subdir_within_git_root(tmp_path):
    _ensure_scripts_on_path()

    from project_locator import resolve_project_root

    repo_root = tmp_path / "repo"
    (repo_root / ".git").mkdir(parents=True, exist_ok=True)

    default_project = repo_root / "webnovel-project"
    (default_project / ".webnovel").mkdir(parents=True, exist_ok=True)
    (default_project / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")

    nested = repo_root / "sub" / "dir"
    nested.mkdir(parents=True, exist_ok=True)

    resolved = resolve_project_root(cwd=nested)
    assert resolved == default_project.resolve()


def test_resolve_project_root_uses_workspace_pointer(tmp_path):
    _ensure_scripts_on_path()

    from project_locator import resolve_project_root, write_current_project_pointer

    workspace = tmp_path / "workspace"
    (workspace / ".claude").mkdir(parents=True, exist_ok=True)

    project_root = workspace / "凡人资本论"
    (project_root / ".webnovel").mkdir(parents=True, exist_ok=True)
    (project_root / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")

    pointer_file = write_current_project_pointer(project_root, workspace_root=workspace)
    assert pointer_file is not None
    assert pointer_file.is_file()

    resolved = resolve_project_root(cwd=workspace)
    assert resolved == project_root.resolve()


def test_resolve_project_root_explicit_workspace_uses_unique_child_project(tmp_path):
    _ensure_scripts_on_path()

    from project_locator import resolve_project_root

    workspace = tmp_path / "workspace"
    (workspace / ".git").mkdir(parents=True, exist_ok=True)
    project_root = workspace / "凡人资本论"
    (project_root / ".webnovel").mkdir(parents=True, exist_ok=True)
    (project_root / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")

    resolved = resolve_project_root(str(workspace))
    assert resolved == project_root.resolve()


def test_resolve_project_root_ignores_stale_pointer_and_fallbacks(tmp_path):
    _ensure_scripts_on_path()

    from project_locator import resolve_project_root

    workspace = tmp_path / "workspace"
    (workspace / ".git").mkdir(parents=True, exist_ok=True)
    (workspace / ".claude").mkdir(parents=True, exist_ok=True)
    # stale pointer
    (workspace / ".claude" / ".webnovel-current-project").write_text(
        str(workspace / "missing-project"), encoding="utf-8"
    )

    default_project = workspace / "webnovel-project"
    (default_project / ".webnovel").mkdir(parents=True, exist_ok=True)
    (default_project / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")

    resolved = resolve_project_root(cwd=workspace)
    assert resolved == default_project.resolve()


# ---------------------------------------------------------------------------
# 单书工作区规约（一个工作区只放一本书，2026-09-17）
# ---------------------------------------------------------------------------


def _make_book(root):
    (root / ".webnovel").mkdir(parents=True, exist_ok=True)
    (root / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
    return root


def test_find_child_project_roots_lists_book_projects(tmp_path):
    _ensure_scripts_on_path()

    from project_locator import find_child_project_roots

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    book_a = _make_book(workspace / "凡人资本论")
    book_b = _make_book(workspace / "剑走偏锋")
    (workspace / "非书目录").mkdir(parents=True, exist_ok=True)

    found = find_child_project_roots(workspace)
    assert found == sorted([book_a.resolve(), book_b.resolve()])
    assert find_child_project_roots(tmp_path / "不存在") == []


def test_resolve_project_root_rejects_workspace_with_multiple_books(tmp_path):
    _ensure_scripts_on_path()

    from project_locator import WorkspaceHasMultipleBooksError, resolve_project_root

    workspace = tmp_path / "workspace"
    (workspace / ".git").mkdir(parents=True, exist_ok=True)
    book_a = _make_book(workspace / "凡人资本论")
    book_b = _make_book(workspace / "剑走偏锋")

    try:
        resolve_project_root(str(workspace))
        assert False, "Expected WorkspaceHasMultipleBooksError"
    except WorkspaceHasMultipleBooksError as exc:
        # 仍可被 FileNotFoundError 捕获（既有调用方行为不变）
        assert isinstance(exc, FileNotFoundError)
        assert exc.workspace_root == workspace.resolve()
        assert [book.name for book in exc.books] == ["凡人资本论", "剑走偏锋"]
        message = str(exc)
        assert "凡人资本论" in message
        assert "剑走偏锋" in message
        assert "一个工作区一本书" in message
    assert book_a.is_dir() and book_b.is_dir()


def test_resolve_project_root_rejects_ambiguous_workspace_from_cwd(tmp_path):
    _ensure_scripts_on_path()

    from project_locator import WorkspaceHasMultipleBooksError, resolve_project_root

    workspace = tmp_path / "workspace"
    (workspace / ".git").mkdir(parents=True, exist_ok=True)
    _make_book(workspace / "凡人资本论")
    _make_book(workspace / "剑走偏锋")

    try:
        resolve_project_root(cwd=workspace)
        assert False, "Expected WorkspaceHasMultipleBooksError"
    except WorkspaceHasMultipleBooksError:
        pass


def test_resolve_project_root_prefers_valid_pointer_over_multi_book_ambiguity(tmp_path):
    _ensure_scripts_on_path()

    from project_locator import resolve_project_root, write_current_project_pointer

    workspace = tmp_path / "workspace"
    (workspace / ".git").mkdir(parents=True, exist_ok=True)
    (workspace / ".claude").mkdir(parents=True, exist_ok=True)
    book_a = _make_book(workspace / "凡人资本论")
    _make_book(workspace / "剑走偏锋")

    write_current_project_pointer(book_a, workspace_root=workspace)

    # 指针是兜底：有效时不因“工作区有两本书”而报错
    assert resolve_project_root(str(workspace)) == book_a.resolve()
    assert resolve_project_root(cwd=workspace) == book_a.resolve()


def test_detect_workspace_single_book_conflicts_reports_other_books(tmp_path):
    _ensure_scripts_on_path()

    from project_locator import detect_workspace_single_book_conflicts

    workspace = tmp_path / "workspace"
    (workspace / ".claude").mkdir(parents=True, exist_ok=True)
    book_a = _make_book(workspace / "凡人资本论")
    book_b = _make_book(workspace / "剑走偏锋")

    conflicts = detect_workspace_single_book_conflicts(book_b)
    assert conflicts["workspace_root"] == str(workspace.resolve())
    assert conflicts["other_books"] == [str(book_a.resolve())]

    # 单书布局无冲突
    solo = tmp_path / "solo"
    (solo / ".claude").mkdir(parents=True, exist_ok=True)
    only = _make_book(solo / "唯一一本")
    assert detect_workspace_single_book_conflicts(only)["other_books"] == []


def test_read_current_project_pointer_returns_none_when_invalid(tmp_path):
    _ensure_scripts_on_path()

    from project_locator import read_current_project_pointer

    workspace = tmp_path / "workspace"
    (workspace / ".claude").mkdir(parents=True, exist_ok=True)
    assert read_current_project_pointer(workspace) is None

    (workspace / ".claude" / ".webnovel-current-project").write_text(
        str(workspace / "missing"), encoding="utf-8"
    )
    assert read_current_project_pointer(workspace) is None

    book = _make_book(workspace / "凡人资本论")
    (workspace / ".claude" / ".webnovel-current-project").write_text(str(book), encoding="utf-8")
    assert read_current_project_pointer(workspace) == book.resolve()

