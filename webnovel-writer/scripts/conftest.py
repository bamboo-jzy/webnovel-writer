from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

import pytest


_ORIGINAL_SQLITE_CONNECT = sqlite3.connect
_ORIGINAL_TEMPORARY_DIRECTORY = tempfile.TemporaryDirectory


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _tmp_root() -> Path:
    root = _repo_root() / ".tmp" / "pytest"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_mkdtemp(suffix: str | None = None, prefix: str | None = None, dir: str | os.PathLike[str] | None = None) -> str:
    """Avoid WindowsApps Python creating inaccessible 0o700 temp dirs."""
    suffix = "" if suffix is None else suffix
    prefix = "tmp" if prefix is None else prefix
    root = Path(dir) if dir is not None else _tmp_root()
    root.mkdir(parents=True, exist_ok=True)

    for _ in range(100):
        path = root / f"{prefix}{uuid.uuid4().hex}{suffix}"
        try:
            path.mkdir()
        except FileExistsError:
            continue
        return str(path.resolve())

    raise FileExistsError(f"Unable to create unique temporary directory under {root}")


def _install_safe_tempfile() -> None:
    root = _tmp_root()
    for name in ("TMP", "TEMP", "TMPDIR"):
        os.environ[name] = str(root)
    os.environ["WEBNOVEL_TEST_RELAX_ATOMIC_REPLACE"] = "1"
    tempfile.tempdir = str(root)
    tempfile.mkdtemp = _safe_mkdtemp
    tempfile.TemporaryDirectory = _SafeTemporaryDirectory


# 项目最低支持 Python 3.12（见 scripts/requirements.txt）；tempfile.TemporaryDirectory
# 的 delete 参数也恰好是 3.12 才引入的。这里保留版本门控，是为了在更旧的解释器上
# 也不至于「每次实例化都抛 TypeError: unexpected keyword argument 'delete'」——
# 那会让数十个测试模块在收集阶段就报错，pytest 以 exit code 2（Interrupted）中断整轮，
# 把真正的兼容问题一起掩盖掉。
_TEMPDIR_SUPPORTS_DELETE = sys.version_info >= (3, 12)


class _SafeTemporaryDirectory(_ORIGINAL_TEMPORARY_DIRECTORY):
    def __init__(self, suffix=None, prefix=None, dir=None, ignore_cleanup_errors=True, *, delete=True):
        kwargs = {
            "suffix": suffix,
            "prefix": prefix,
            "dir": dir,
            "ignore_cleanup_errors": ignore_cleanup_errors,
        }
        if _TEMPDIR_SUPPORTS_DELETE:
            kwargs["delete"] = delete
        # 旧解释器没有 delete 能力：忽略该参数，行为等同 3.12+ 的默认值（真的删）。
        super().__init__(**kwargs)


def _safe_sqlite_connect(*args, **kwargs):
    conn = _ORIGINAL_SQLITE_CONNECT(*args, **kwargs)
    try:
        conn.execute("PRAGMA journal_mode=MEMORY")
    except sqlite3.DatabaseError:
        pass
    return conn


def _install_safe_sqlite() -> None:
    sqlite3.connect = _safe_sqlite_connect


def pytest_configure(config: pytest.Config) -> None:
    _install_safe_tempfile()
    _install_safe_sqlite()


@pytest.fixture
def tmp_path(request: pytest.FixtureRequest) -> Path:
    safe_name = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in request.node.name)
    path = _tmp_root() / f"{safe_name}_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        if os.environ.get("WEBNOVEL_KEEP_TEST_TMP") != "1":
            shutil.rmtree(path, ignore_errors=True)


_install_safe_tempfile()
_install_safe_sqlite()
