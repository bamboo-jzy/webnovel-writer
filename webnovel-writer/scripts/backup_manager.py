#!/usr/bin/env python3
"""
Git 集成备份管理系统 (Backup Manager with Git)

核心理念：写 200 万字必然会"写废设定"，需要每章都留下一个可回去的版本点。

🔧 使用 Git 进行原子性版本控制

为什么选择 Git：
1. ✅ 原子性版本点：state.json + 正文/*.md 属于同一个提交，数据 100% 一致
2. ✅ 增量存储：只存储 diff，节省 95% 空间
3. ✅ 成熟稳定：经过 20 年验证的版本控制系统
4. ✅ 恢复不外挂：回退用 git 原生命令，本模块不重复实现一套恢复器

功能：
1. 自动 Git 提交：每次 /webnovel-write 完成后自动 commit
2. 版本点 tag：每章一个 commit + tag（如 ch0045），指向该章最新已备份状态
3. 版本点前移：同一章重写或修订后再次备份时，chNNNN 前移到新提交，
   旧版本点另存为 chNNNN-prev-<时间戳>，历史不丢、章节号不被占用
4. 版本历史：git log 查看完整历史
5. 差异对比：git diff 查看任意两个版本的差异
6. 分支创建：git branch 从任意时间点创建分支

使用方式：
  # 在第 45 章完成后自动备份（git commit + tag ch0045）
  python backup_manager.py --chapter 45

  # 查看第 20 章和第 40 章的差异（git diff）
  python backup_manager.py --diff 20 40

  # 从第 50 章创建分支（git branch）
  python backup_manager.py --create-branch 50 --branch-name "alternative-ending"

  # 列出所有备份（当前版本点 + 历史点）
  python backup_manager.py --list

恢复到第 N 章：直接用 git，不再经过本模块
  git log --oneline ch0030..HEAD              # 第 30 章之后有哪些提交
  git switch -c rewrite-from-ch0030 ch0030    # 工作树整体回到第 30 章，另开分支，历史不动

Git 提交规范：
  - 提交信息格式: "Chapter {N}: {章节标题}"
  - 当前版本点: "ch{N}" (如 ch0045)，可前移，指向该章最新已备份状态
  - 历史版本点: "ch{N}-prev-<时间戳>"，只增不改，指向被前移前的状态
  - 每个章节对应一个 commit + 一个 chNNNN tag

数据一致性保证：
  ✅ 一个提交同时包含 state.json 和所有 .md 文件
  ✅ 不会出现"状态记录筑基期，但文件里写着金丹期"的数据撕裂
  ✅ 原子性操作，要么全部成功，要么全部失败
"""

import subprocess
import json
import hashlib
import re
import shutil
import sqlite3
import stat
import sys
from pathlib import Path

from runtime_compat import enable_windows_utf8_stdio
from datetime import datetime
from typing import Optional, List, Tuple

# ============================================================================
# 安全修复：导入安全工具函数（P1 MEDIUM）
# ============================================================================
from security_utils import AtomicWriteError, FileLock, HAS_FILELOCK, atomic_write_json, sanitize_commit_message, is_git_available, is_git_repo, git_graceful_operation
from chapter_paths import find_chapter_file
from project_locator import resolve_project_root

# Windows 编码兼容性修复
if sys.platform == "win32":
    enable_windows_utf8_stdio()


class BackupError(RuntimeError):
    """Git backup operation failed."""


class GitBackupManager:
    """基于 Git 的备份管理器（支持优雅降级）"""

    #: 章节当前版本点的严格命名；其他 tag 一律视为历史点，不得参与章节号解析
    _CHAPTER_TAG_PATTERN = re.compile(r"^ch(\d{4})$")

    def __init__(self, project_root: str, *, auto_init: bool = True):
        self.project_root = Path(project_root)
        self.git_dir = self.project_root / ".git"
        self.git_available = is_git_available()

        if not self.git_available:
            if auto_init:
                print("Git 不可用，将使用本地备份模式")
            return

        if not self.git_dir.exists() and auto_init:
            print("⚠️  Git 未初始化，请先运行 /webnovel-init 或手动执行 git init")
            print("💡 现在自动初始化 Git...")
            self._init_git()

    def _selected_backup_paths(self) -> list[Path]:
        # 文风/ 进版本点的理由：它是源 B（目标画像）的唯一来源，丢了就无法重建文风档案。
        # 代价是参考文本会随 Git 提交——所以 style_profile 只存统计特征与短样本，
        # 不建议把整本参考书放进该目录。
        paths = [
            self.project_root / name
            for name in ("正文", "大纲", "设定集", "文风", ".story-system")
        ]
        webnovel = self.project_root / ".webnovel"
        for name in (
            "state.json",
            "index.db",
            "vectors.db",
            "project_memory.json",
            "memory_scratchpad.json",
            "projection_log.jsonl",
            "style_profile.json",
            "summaries",
        ):
            paths.append(webnovel / name)
        return [path for path in paths if path.exists()]

    @staticmethod
    def _excluded_snapshot_path(path: Path) -> bool:
        return any(
            part in {"backups", "tmp", "__pycache__", ".pytest_cache", ".git"}
            or part.startswith(".env")
            or part.endswith(".pyc")
            for part in path.parts
        )

    @staticmethod
    def _is_link_or_reparse(path: Path) -> bool:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
        try:
            attributes = getattr(path.lstat(), "st_file_attributes", 0)
        except OSError:
            return False
        return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))

    def _snapshot_files(self) -> list[Path]:
        files = []
        for source in self._selected_backup_paths():
            if self._is_link_or_reparse(source):
                continue
            candidates = source.rglob("*") if source.is_dir() else [source]
            files.extend(
                path
                for path in candidates
                if path.is_file()
                and not self._is_link_or_reparse(path)
                and not self._excluded_snapshot_path(path)
            )
        return sorted(set(files), key=lambda path: path.relative_to(self.project_root).as_posix())

    @staticmethod
    def _allowed_snapshot_path(relative: str) -> bool:
        parts = Path(relative).parts
        if not parts or parts[0] in {"正文", "大纲", "设定集", "文风", ".story-system"}:
            return bool(parts)
        if parts[0] != ".webnovel" or len(parts) < 2:
            return False
        if parts[1] == "summaries":
            return len(parts) >= 3
        return len(parts) == 2 and parts[1] in {
            "state.json",
            "index.db",
            "vectors.db",
            "project_memory.json",
            "memory_scratchpad.json",
            "projection_log.jsonl",
            # 文风档案（机读）：理论上可由 正文/ + 文风/ 重建，但两者都在版本点里时
            # 多收一个 json 的成本极低，省掉「回退后忘了重建档案」这类隐性不一致。
            "style_profile.json",
        }

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _copy_snapshot_file(self, source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix == ".db":
            try:
                with sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True) as source_db:
                    with sqlite3.connect(destination) as destination_db:
                        source_db.backup(destination_db)
                return
            except sqlite3.DatabaseError:
                if destination.exists():
                    destination.unlink()
        shutil.copy2(source, destination)

    def _write_snapshot_manifest(self, backup_path: Path, files: list[Path], chapter_num: int) -> None:
        manifest = {
            "schema_version": "snapshot/v1",
            "chapter": chapter_num,
            "created_at": datetime.now().astimezone().isoformat(),
            "files": [
                {
                    "path": source.relative_to(self.project_root).as_posix(),
                    "size": (backup_path / source.relative_to(self.project_root)).stat().st_size,
                    "sha256": self._sha256(backup_path / source.relative_to(self.project_root)),
                }
                for source in files
            ],
        }
        (backup_path / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _read_snapshot_manifest(self, snapshot_path: Path) -> tuple[dict, str]:
        manifest_path = snapshot_path / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {}, f"快照 manifest 无法读取: {exc}"
        if not isinstance(manifest, dict) or manifest.get("schema_version") != "snapshot/v1":
            return {}, "快照 manifest schema_version 无效"
        entries = manifest.get("files")
        if not isinstance(entries, list):
            return {}, "快照 manifest files 必须是列表"
        seen: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                return {}, "快照 manifest 含有无效文件记录"
            relative = entry.get("path")
            if not isinstance(relative, str) or not relative or relative in seen:
                return {}, "快照 manifest 含有重复或无效路径"
            candidate = Path(relative)
            if "\\" in relative or candidate.is_absolute() or ".." in candidate.parts:
                return {}, f"快照路径越界: {relative}"
            if not self._allowed_snapshot_path(relative):
                return {}, f"快照路径不在允许的故事范围内: {relative}"
            if not isinstance(entry.get("size"), int) or entry.get("size") < 0:
                return {}, f"快照文件大小无效: {relative}"
            if not isinstance(entry.get("sha256"), str) or len(entry["sha256"]) != 64:
                return {}, f"快照文件 checksum 无效: {relative}"
            source = snapshot_path / candidate
            try:
                inside_snapshot = source.resolve().is_relative_to(snapshot_path.resolve())
            except AttributeError:
                inside_snapshot = str(source.resolve()).startswith(str(snapshot_path.resolve()))
            if not inside_snapshot or not source.is_file():
                return {}, f"快照文件缺失或越界: {relative}"
            if source.stat().st_size != entry["size"] or self._sha256(source) != entry["sha256"]:
                return {}, f"快照 checksum 校验失败: {relative}"
            seen.add(relative)
        return manifest, ""

    def _init_git(self) -> bool:
        """初始化 Git 仓库"""
        try:
            # git init
            subprocess.run(
                ["git", "init"],
                cwd=self.project_root,
                check=True,
                capture_output=True
            )

            # 创建 .gitignore
            gitignore_file = self.project_root / ".gitignore"
            if not gitignore_file.exists():
                with open(gitignore_file, 'w', encoding='utf-8') as f:
                    f.write("""# Python
__pycache__/
*.py[cod]
*.so

# Temporary files
*.tmp
*.bak
.DS_Store

# IDE
.vscode/
.idea/

# Don't ignore .webnovel (we need to track state.json)
# But ignore cache files
.webnovel/context_cache.json

# Env files
.env
.env.*
!.env.example
""")

            # 初始提交
            subprocess.run(
                ["git", "add", "."],
                cwd=self.project_root,
                check=True,
                capture_output=True
            )

            subprocess.run(
                ["git", "commit", "-m", "Initial commit: Project initialized"],
                cwd=self.project_root,
                check=True,
                capture_output=True
            )

            print("✅ Git 仓库已初始化")
            return True

        except subprocess.CalledProcessError as e:
            print(f"❌ Git 初始化失败: {e}")
            return False

    def _run_git_command(self, args: List[str], check: bool = True) -> Tuple[bool, str, str]:
        """执行 Git 命令（支持优雅降级）"""
        if not self.git_available:
            return False, "", "Git 不可用"

        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60
            )
            ok = result.returncode == 0
            if check and not ok:
                message = (result.stderr or result.stdout).strip()
                raise BackupError(f"git {' '.join(args)} 失败: {message}")
            return ok, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            if check:
                raise BackupError(f"git {' '.join(args)} 失败: Git 命令超时")
            return False, "", "Git 命令超时"
        except OSError as e:
            if check:
                raise BackupError(f"git {' '.join(args)} 失败: {e}")
            return False, "", str(e)

    @staticmethod
    def _format_git_output(stdout: str, stderr: str) -> str:
        """合并 Git 输出，优先保留 stderr 中的故障信息。"""
        return "\n".join(part.strip() for part in (stderr, stdout) if part.strip())

    def _backup_receipt_path(self) -> Path:
        return self.project_root / ".webnovel" / "backup_receipts.json"

    def _write_backup_receipt(self, chapter_num: int, receipt: dict) -> bool:
        path = self._backup_receipt_path()
        try:
            if not HAS_FILELOCK:
                raise OSError("backup receipts require filelock")
            path.parent.mkdir(parents=True, exist_ok=True)
            with FileLock(str(path) + ".lock", timeout=10):
                receipts = {}
                if path.exists():
                    receipts = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(receipts, dict):
                        raise ValueError("receipt root must be object")
                receipts[str(int(chapter_num))] = receipt
                atomic_write_json(path, receipts, use_lock=False, backup=True)
            return True
        except (OSError, ValueError, AtomicWriteError) as exc:
            print(f"备份 receipt 写入失败: {exc}")
            return False

    def verified_backup(self, chapter_num: int) -> dict:
        receipt_path = self._backup_receipt_path()
        try:
            receipts = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.exists() else {}
            if not isinstance(receipts, dict):
                return {}
            receipt = receipts.get(str(chapter_num))
            if receipt is not None:
                if not isinstance(receipt, dict) or receipt.get("schema_version") != "backup-receipt/v1" or receipt.get("chapter") != chapter_num:
                    return {}
                if receipt.get("type") == "git":
                    tag = f"ch{chapter_num:04d}"
                    ok, commit, _ = self._run_git_command(["rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}"], check=False)
                    if ok and receipt.get("tag") == tag and receipt.get("tag_commit") == commit.strip() == receipt.get("head_commit") and self._git_matches_chapter(commit.strip(), chapter_num):
                        return receipt
                elif receipt.get("type") == "snapshot":
                    relative = receipt.get("snapshot")
                    if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
                        return {}
                    snapshot = self.project_root / relative
                    if not snapshot.resolve().is_relative_to((self.project_root / ".webnovel/backups").resolve()):
                        return {}
                    if self._sha256(snapshot / "manifest.json") == receipt.get("manifest_sha256") and self._snapshot_matches_chapter(snapshot, chapter_num):
                        return receipt
                return {}
            tag = f"ch{chapter_num:04d}"
            ok, commit, _ = self._run_git_command(["rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}"], check=False)
            if ok and self._git_matches_chapter(commit.strip(), chapter_num):
                return {"type": "git", "tag": tag, "tag_commit": commit.strip(), "legacy": True}
            for snapshot in sorted((self.project_root / ".webnovel/backups").glob(f"snapshot_ch{chapter_num:04d}_*"), reverse=True):
                if self._snapshot_matches_chapter(snapshot, chapter_num):
                    return {"type": "snapshot", "snapshot": snapshot.relative_to(self.project_root).as_posix(), "legacy": True}
        except (OSError, ValueError, TypeError):
            return {}
        return {}

    def _chapter_backup_files(self, chapter_num: int) -> list[Path]:
        body = find_chapter_file(self.project_root, chapter_num)
        if body is None:
            return []
        paths = [body]
        commit = self.project_root / ".story-system/commits" / f"chapter_{chapter_num:03d}.commit.json"
        if commit.exists():
            paths.append(commit)
        return paths

    def _git_matches_chapter(self, commit: str, chapter_num: int) -> bool:
        files = self._chapter_backup_files(chapter_num)
        for path in files:
            relative = path.relative_to(self.project_root).as_posix()
            saved_ok, saved, _ = self._run_git_command(["rev-parse", "--verify", f"{commit}:{relative}"], check=False)
            current_ok, current, _ = self._run_git_command(["hash-object", f"--path={relative}", "--", relative], check=False)
            if not saved_ok or not current_ok or saved.strip() != current.strip():
                return False
        return bool(files)

    def _snapshot_matches_chapter(self, snapshot: Path, chapter_num: int) -> bool:
        manifest, error = self._read_snapshot_manifest(snapshot)
        if error or manifest.get("chapter") != chapter_num:
            return False
        entries = {entry["path"]: entry for entry in manifest["files"]}
        files = self._chapter_backup_files(chapter_num)
        return bool(files) and all(
            entries.get(path.relative_to(self.project_root).as_posix(), {}).get("sha256") == self._sha256(path)
            for path in files
        )

    def _record_snapshot_receipt(self, chapter_num: int, backup_path: Path) -> bool:
        manifest_path = backup_path / "manifest.json"
        return self._write_backup_receipt(
            chapter_num,
            {
                "schema_version": "backup-receipt/v1",
                "chapter": int(chapter_num),
                "type": "snapshot",
                "snapshot": str(backup_path.relative_to(self.project_root).as_posix()),
                "manifest": str(manifest_path.relative_to(self.project_root).as_posix()),
                "manifest_sha256": self._sha256(manifest_path),
                "created_at": datetime.now().astimezone().isoformat(),
            },
        )

    def _record_git_receipt(self, chapter_num: int, tag_name: str, commit: str) -> bool:
        return self._write_backup_receipt(
            chapter_num,
            {
                "schema_version": "backup-receipt/v1",
                "chapter": int(chapter_num),
                "type": "git",
                "tag": tag_name,
                "tag_commit": commit,
                "head_commit": commit,
                "created_at": datetime.now().astimezone().isoformat(),
            },
        )

    def _archive_tag(self, tag_name: str, commit: str) -> Optional[str]:
        """把即将被前移的章节版本点另存为 <tag>-prev-<时间戳>，返回新 tag 名；失败返回 None。

        归档只新增 tag，不改动任何提交；即使之后前移失败，旧版本点也仍然可达。
        """
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        candidate = f"{tag_name}-prev-{stamp}"
        suffix = 1
        while True:
            exists_ok, _, _ = self._run_git_command(
                ["rev-parse", "--verify", f"refs/tags/{candidate}"], check=False
            )
            if not exists_ok:
                break
            suffix += 1
            candidate = f"{tag_name}-prev-{stamp}-{suffix}"
        success, stdout, stderr = self._run_git_command(["tag", candidate, commit], check=False)
        if not success:
            print(f"  归档旧版本点失败: {self._format_git_output(stdout, stderr)}")
            return None
        return candidate

    def _local_backup(self, chapter_num: int) -> bool:
        """本地备份（Git 不可用时的降级方案）"""
        backup_dir = self.project_root / ".webnovel" / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_name = f"snapshot_ch{chapter_num:04d}_{timestamp}"
        backup_path = backup_dir / backup_name
        files = self._snapshot_files()

        try:
            backup_path.mkdir(parents=True, exist_ok=False)
            copied = []
            for source in files:
                relative = source.relative_to(self.project_root)
                destination = backup_path / relative
                self._copy_snapshot_file(source, destination)
                copied.append(relative.as_posix())
            self._write_snapshot_manifest(backup_path, files, chapter_num)
            if not self._record_snapshot_receipt(chapter_num, backup_path):
                raise OSError("backup receipt unavailable")

            snapshots = sorted(
                (path for path in backup_dir.glob("snapshot_ch*") if path.is_dir()),
                key=lambda path: path.name,
            )
            for old_snapshot in snapshots[:-10]:
                shutil.rmtree(old_snapshot)

            print(f"✅ 本地备份完成: {backup_path}")
            if copied:
                print(f"📦 已备份: {', '.join(copied)}")
            else:
                print("⚠️  未找到可备份的故事主链或运行状态")
            return True
        except (OSError, sqlite3.Error) as e:
            print(f"❌ 本地备份失败: {e}")
            if backup_path.exists():
                shutil.rmtree(backup_path, ignore_errors=True)
            return False

    def _git_backup_scope(self) -> tuple[str, ...]:
        return (
            "正文",
            "大纲",
            "设定集",
            ".story-system",
            ".webnovel/state.json",
            ".webnovel/index.db",
            ".webnovel/vectors.db",
            ".webnovel/project_memory.json",
            ".webnovel/memory_scratchpad.json",
            ".webnovel/projection_log.jsonl",
            ".webnovel/summaries",
        )

    def _in_git_backup_scope(self, path: str) -> bool:
        return any(
            path == root or path.startswith(root.rstrip("/") + "/")
            for root in self._git_backup_scope()
        )

    def _git_backup_paths(self) -> list[str]:
        return [
            path.relative_to(self.project_root).as_posix()
            for path in self._selected_backup_paths()
        ]

    def backup(self, chapter_num: int, chapter_title: str = "") -> bool:
        """
        备份当前状态（Git commit + tag，或本地备份）

        Args:
            chapter_num: 章节号
            chapter_title: 章节标题（可选）
        """
        print(f"📝 正在备份第 {chapter_num} 章...")

        # 如果 Git 不可用，使用本地备份
        if not self.git_available:
            return self._local_backup(chapter_num)

        # Step 1: stage only story data and derived state; never stage the plugin or secrets.
        backup_paths = self._git_backup_paths()
        if not backup_paths:
            print("❌ 备份失败：未找到可备份的故事主链或运行状态")
            return False
        success, stdout, stderr = self._run_git_command(["add", "--", *backup_paths], check=False)
        if not success:
            print(f"❌ 备份失败：git add 失败: {self._format_git_output(stdout, stderr)}")
            return False

        # Step 2: git commit
        commit_message = f"Chapter {chapter_num}"
        if chapter_title:
            # ============================================================================
            # 安全修复：清理提交消息，防止命令注入 (CWE-77) - P1 MEDIUM
            # 原代码: commit_message += f": {chapter_title}"
            # 漏洞: chapter_title可能包含 Git 标志（如 --author, --amend）导致命令注入
            # ============================================================================
            safe_chapter_title = sanitize_commit_message(chapter_title)
            commit_message += f": {safe_chapter_title}"

        staged_ok, staged_files, staged_error = self._run_git_command(
            ["diff", "--cached", "--name-only"], check=False
        )
        if not staged_ok:
            print(f"备份失败：无法检查暂存区: {self._format_git_output(staged_files, staged_error)}")
            return False
        if staged_files.strip():
            success, stdout, stderr = self._run_git_command(
                ["commit", "-m", commit_message], check=False
            )
            if not success:
                print(f"备份失败：git commit 失败: {self._format_git_output(stdout, stderr)}")
                return False
            print(f"Git 提交完成: {commit_message}")
        else:
            success, stdout, stderr = self._run_git_command(
                ["rev-parse", "--verify", "HEAD^{commit}"], check=False
            )
            if not success:
                print("备份失败：尚无可用提交")
                return False
            print("本章无变更，使用当前提交作为备份点")

        # Step 3: 维护章节版本点。chNNNN 始终指向「第 N 章最新已备份状态」：
        # 首次备份直接建 tag；同章重写/修订后再次备份时，先把旧点归档为 chNNNN-prev-<时间戳>，
        # 再把 tag 前移到当前提交。归档只新增引用，历史提交永不丢失。
        tag_name = f"ch{chapter_num:04d}"
        head_ok, head_output, head_error = self._run_git_command(
            ["rev-parse", "--verify", "HEAD^{commit}"], check=False
        )
        if not head_ok:
            print(f"备份失败：无法解析 HEAD: {self._format_git_output(head_output, head_error)}")
            return False
        current_commit = head_output.strip()

        existing_ok, existing_output, _ = self._run_git_command(
            ["rev-parse", "--verify", f"refs/tags/{tag_name}^{{commit}}"], check=False
        )
        existing_commit = existing_output.strip()

        if existing_ok and existing_commit == current_commit:
            print(f"✅ Git tag 已存在且指向当前提交: {tag_name}")
            return self._record_git_receipt(chapter_num, tag_name, current_commit)

        if existing_ok:
            archived = self._archive_tag(tag_name, existing_commit)
            if archived is None:
                print(f"❌ 备份失败：旧版本点 {tag_name} 归档失败，为保住历史未前移 tag")
                return False
            success, stdout, stderr = self._run_git_command(
                ["tag", "-f", tag_name, current_commit], check=False
            )
            if not success:
                print(f"备份失败：前移 tag 失败: {self._format_git_output(stdout, stderr)}")
                print(f"  旧版本点已保留为 {archived}，重跑备份即可重试")
                return False
            print(f"✅ Git tag 已前移: {tag_name}（旧版本点保留为 {archived}）")
        else:
            success, stdout, stderr = self._run_git_command(["tag", tag_name, current_commit], check=False)
            if not success:
                print(f"备份失败：创建 tag 失败: {self._format_git_output(stdout, stderr)}")
                return False
            print(f"✅ Git tag 已创建: {tag_name}")

        if not self._record_git_receipt(chapter_num, tag_name, current_commit):
            return False

        return True

    def diff(self, chapter_a: int, chapter_b: int):
        """对比两个版本的差异（Git diff）"""

        tag_a = f"ch{chapter_a:04d}"
        tag_b = f"ch{chapter_b:04d}"

        print(f"📊 对比第 {chapter_a} 章 与 第 {chapter_b} 章的差异...\n")

        success, output, error = self._run_git_command(["diff", tag_a, tag_b, "--stat"], check=False)

        if not success:
            print(f"❌ 对比失败: {self._format_git_output(output, error)}")
            return

        print("📈 文件变更统计：")
        print(output)

        # 显示 state.json 的详细差异
        print("\n📝 state.json 详细差异：")
        success, state_diff, _ = self._run_git_command(
            ["diff", tag_a, tag_b, "--", ".webnovel/state.json"],
            check=False,
        )

        if success and state_diff:
            print(state_diff[:2000])  # 限制输出长度
            if len(state_diff) > 2000:
                print("\n...(输出过长，已截断)")
        else:
            print("(无变更)")

    def list_backups(self):
        """列出所有备份（章节当前版本点 + 被前移的历史点）"""

        print("\n📚 备份列表（Git tags）：\n")

        # 获取所有 tags
        success, tags_output, _ = self._run_git_command(["tag", "-l", "ch*"], check=False)

        if not success or not tags_output.strip():
            print("⚠️  暂无备份")
            return

        chapters: dict[str, str] = {}
        archived: dict[str, list[str]] = {}
        for raw in tags_output.splitlines():
            tag = raw.strip()
            if not tag:
                continue
            if self._CHAPTER_TAG_PATTERN.match(tag):
                info_ok, commit_info, _ = self._run_git_command(
                    ["log", tag, "-1", "--format=%h %ci %s"],
                    check=False,
                )
                chapters[tag] = commit_info.strip() if info_ok else "(无法读取提交信息)"
                continue
            base = tag.split("-prev-", 1)[0] if "-prev-" in tag else tag
            archived.setdefault(base, []).append(tag)

        for tag in sorted(chapters):
            print(f"📖 {tag} | {chapters[tag]}")
            for old in sorted(archived.get(tag, [])):
                print(f"     ↳ 历史点 {old}")

        for tag in sorted(set(archived) - set(chapters)):
            print(f"📖 {tag} | (该章号当前无版本点)")
            for old in sorted(archived[tag]):
                print(f"     ↳ 历史点 {old}")

        archived_total = sum(len(entries) for entries in archived.values())
        print(f"\n总计：{len(chapters)} 个章节版本点，{archived_total} 个历史点")

        # 显示最近 5 次提交
        print("\n📜 最近提交历史：\n")
        success, log_output, _ = self._run_git_command(
            ["log", "--oneline", "-5"],
            check=False,
        )

        if success:
            print(log_output)

    def create_branch(self, chapter_num: int, branch_name: str) -> bool:
        """从指定章节创建分支（Git branch）"""

        tag_name = f"ch{chapter_num:04d}"

        print(f"🌿 从第 {chapter_num} 章创建分支: {branch_name}")

        # 检查 tag 是否存在
        success, _, _ = self._run_git_command(["rev-parse", tag_name], check=False)

        if not success:
            print(f"❌ Tag '{tag_name}' 不存在")
            return False

        # 创建分支
        success, output, error = self._run_git_command(["branch", branch_name, tag_name], check=False)

        if not success:
            print(f"❌ 创建分支失败: {self._format_git_output(output, error)}")
            return False

        print(f"✅ 分支已创建: {branch_name}")
        print(f"\n💡 切换到分支:")
        print(f"  git checkout {branch_name}")

        return True

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Git 集成备份管理系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  # 在第 45 章完成后自动备份
  python backup_manager.py --chapter 45

  # 查看第 20 章和第 40 章的差异
  python backup_manager.py --diff 20 40

  # 从第 50 章创建分支
  python backup_manager.py --create-branch 50 --branch-name "alternative-ending"

  # 列出所有备份
  python backup_manager.py --list

恢复到历史版本请直接使用 git（见本文件 docstring）：本模块只负责建立版本点。
        """
    )

    parser.add_argument('--chapter', type=int, help='备份章节号')
    parser.add_argument('--chapter-title', help='章节标题（可选）')
    parser.add_argument('--diff', nargs=2, type=int, metavar=('A', 'B'), help='对比两个版本')
    parser.add_argument('--create-branch', type=int, metavar='CHAPTER', help='从指定章节创建分支')
    parser.add_argument('--branch-name', help='分支名称')
    parser.add_argument('--list', action='store_true', help='列出所有备份')
    parser.add_argument('--project-root', default='.', help='项目根目录')

    args = parser.parse_args()

    # 解析项目根目录（允许传入“工作区根目录”，统一解析到真正的 book project_root）
    try:
        project_root = str(resolve_project_root(args.project_root))
    except FileNotFoundError as exc:
        print(f"❌ 无法定位项目根目录（需要包含 .webnovel/state.json）: {exc}", file=sys.stderr)
        sys.exit(1)

    # 创建管理器
    manager = GitBackupManager(project_root)

    # 执行操作
    if args.chapter:
        success = manager.backup(args.chapter, args.chapter_title or "")
        if success is False:
            sys.exit(1)

    elif args.diff:
        manager.diff(args.diff[0], args.diff[1])

    elif args.create_branch:
        if not args.branch_name:
            print("❌ 创建分支需要 --branch-name 参数")
            sys.exit(1)
        manager.create_branch(args.create_branch, args.branch_name)

    elif args.list:
        manager.list_backups()

    else:
        parser.print_help()

if __name__ == "__main__":
    main()
