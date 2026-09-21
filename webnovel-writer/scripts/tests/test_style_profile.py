#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_style_profile.py - 文风档案（scripts/style_profile.py）测试

覆盖四类不能出错的口径：
1. 源 A 只认 accepted 提交（未提交/被拒的稿不得进入现状画像）；
2. 现状与目标分开存放，缺目标时不得拿现状冒充；
3. `build` 覆盖脚本段但**原样保留**归纳段；
4. 样本不足时不给「正常」结论。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from style_profile import (  # noqa: E402
    ARCHIVE_FILENAME,
    INJECTION_DIGEST_MAX_CHARS,
    SCRIPT_BEGIN,
    SCRIPT_END,
    _extract_hand_section,
    build_profile,
    load_profile,
    main,
    render_archive,
    select_sample,
    write_outputs,
)

_SHORT_BODY = """# 第{ch}章 测试

陈砚把钥匙挂回值班室的钩子上，没有回头。

「别抬头。」韩守德只说了这三个字，就转身走了。

他在楼道里站了很久。灯灭了。
"""

_LONG_BODY = """# 第{ch}章 长句

他在楼道里站了很久很久直到声控灯自己灭下去而黑暗里只剩下鞋底摩擦水泥的沙沙声那种声音很轻却让他后颈一阵发紧于是他决定不再回头继续往前走。

走廊尽头的门半开着有风从里面出来吹得他衣角不停摆动而他忽然想起那本记事本上被撕掉的一页纸的边缘是整齐的。
"""

_REFERENCE = """他推开门。风灌进来，卷起桌上一沓纸。

「进来吧。」老人的声音很干，像被砂纸磨过。

屋里没有灯。他在门口站了两秒。箱子是铁的。
"""


def _make_project(tmp_path: Path) -> Path:
    root = tmp_path
    (root / ".webnovel").mkdir(parents=True, exist_ok=True)
    (root / "正文").mkdir(parents=True, exist_ok=True)
    (root / ".story-system" / "commits").mkdir(parents=True, exist_ok=True)
    (root / ".webnovel" / "state.json").write_text(
        json.dumps({"progress": {"current_chapter": 0}}, ensure_ascii=False), encoding="utf-8"
    )
    return root


def _write_chapter(root: Path, chapter: int, body: str = _SHORT_BODY) -> None:
    (root / "正文" / f"第{chapter:04d}章-测试.md").write_text(
        body.format(ch=chapter), encoding="utf-8"
    )


def _write_commit(root: Path, chapter: int, status: str) -> None:
    (root / ".story-system" / "commits" / f"chapter_{chapter:03d}.commit.json").write_text(
        json.dumps({"meta": {"status": status, "chapter": chapter}}, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_reference(root: Path, name: str = "参考样本.md", text: str = _REFERENCE) -> None:
    (root / "文风").mkdir(exist_ok=True)
    (root / "文风" / name).write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# 源 A 口径
# ---------------------------------------------------------------------------

def test_build_only_counts_accepted_commits(tmp_path):
    root = _make_project(tmp_path)
    for chapter in (1, 2, 3, 4):
        _write_chapter(root, chapter)
    _write_commit(root, 1, "accepted")
    _write_commit(root, 2, "accepted")
    _write_commit(root, 3, "accepted")
    _write_commit(root, 4, "rejected")

    profile = build_profile(root, source_mode="accepted", max_chapters=60)
    observed = profile["observed"]
    assert observed["candidate_chapters"] == [1, 2, 3]
    assert observed["sampled_chapters"] == [1, 2, 3]
    assert observed["summary"]["chapter_count"] == 3
    assert any("rejected" in note for note in profile["notes"]), profile["notes"]


def test_build_without_accepted_commits_reports_instead_of_guessing(tmp_path):
    root = _make_project(tmp_path)
    for chapter in (1, 2):
        _write_chapter(root, chapter)

    profile = build_profile(root, source_mode="accepted", max_chapters=60)
    assert profile["observed"]["summary"]["chapter_count"] == 0
    assert any("没有 accepted 提交" in note for note in profile["notes"])
    # 无样本时不得给「通过」结论
    assert profile["hard_constraints"] == []


def test_source_all_is_explicit_and_disclosed(tmp_path):
    root = _make_project(tmp_path)
    _write_chapter(root, 1)
    _write_chapter(root, 2)

    profile = build_profile(root, source_mode="all", max_chapters=60)
    assert profile["observed"]["summary"]["chapter_count"] == 2
    assert any("--source all" in note for note in profile["notes"])


def test_missing_body_file_is_reported(tmp_path):
    root = _make_project(tmp_path)
    _write_chapter(root, 1)
    _write_commit(root, 1, "accepted")
    _write_commit(root, 2, "accepted")  # commit 存在但正文缺失

    profile = build_profile(root, source_mode="accepted", max_chapters=60)
    assert profile["observed"]["missing_chapters"] == [2]
    assert any("读不到正文" in note for note in profile["notes"])


# ---------------------------------------------------------------------------
# 抽样
# ---------------------------------------------------------------------------

def test_select_sample_keeps_recent_chapters_and_respects_cap():
    chapters = list(range(1, 121))
    picked = select_sample(chapters, 20, recent=5)
    assert len(picked) <= 20
    assert set(range(116, 121)) <= set(picked), "最近 5 章必须入选"
    assert picked == sorted(picked)


def test_select_sample_returns_all_when_under_cap():
    assert select_sample([3, 1, 2], 10) == [1, 2, 3]


def test_select_sample_tiny_cap_does_not_overflow():
    picked = select_sample(list(range(1, 50)), 1)
    assert picked == [49]


# ---------------------------------------------------------------------------
# 源 B 与差异
# ---------------------------------------------------------------------------

def test_missing_target_yields_explicit_no_target_state(tmp_path):
    root = _make_project(tmp_path)
    for chapter in (1, 2, 3):
        _write_chapter(root, chapter)
        _write_commit(root, chapter, "accepted")

    profile = build_profile(root, source_mode="accepted", max_chapters=60)
    assert profile["target"]["available"] is False
    assert profile["gaps"] == []
    assert "无参考文本" in profile["injection_digest"]


def test_target_present_produces_gaps_and_excerpt(tmp_path):
    root = _make_project(tmp_path)
    for chapter in (1, 2, 3):
        _write_chapter(root, chapter, _LONG_BODY)
        _write_commit(root, chapter, "accepted")
    _write_reference(root)

    profile = build_profile(root, source_mode="accepted", max_chapters=60)
    target = profile["target"]
    assert target["available"] is True
    assert target["summary"]["file_count"] == 1
    assert target["files"][0]["excerpt"], "参考文件必须留下可识别的短样本"
    assert len(target["files"][0]["excerpt"]) <= 130

    assert profile["gaps"], "长句正文 vs 短句参考应产生待收敛项"
    top = profile["gaps"][0]
    assert top["metric"] in {"avg_sentence", "median_sentence"}
    assert top["observed_median"] > top["target_median"]
    assert top["direction"] == "高于目标"


def test_reference_files_excluded_from_target_scan(tmp_path):
    root = _make_project(tmp_path)
    _write_reference(root, "a.md")
    _write_reference(root, "b.txt")
    # 档案自身不是参考文本
    (root / "文风" / ARCHIVE_FILENAME).write_text("# 文风档案\n\n正文", encoding="utf-8")

    profile = build_profile(root, source_mode="accepted", max_chapters=60)
    names = sorted(item["file_name"] if "file_name" in item else item["file"] for item in profile["target"]["files"])
    assert names == ["a.md", "b.txt"]


# ---------------------------------------------------------------------------
# 硬约束
# ---------------------------------------------------------------------------

def test_long_sentence_red_line_fails(tmp_path):
    root = _make_project(tmp_path)
    for chapter in (1, 2, 3):
        _write_chapter(root, chapter, _LONG_BODY)
        _write_commit(root, chapter, "accepted")

    profile = build_profile(root, source_mode="accepted", max_chapters=60)
    checks = {item["code"]: item for item in profile["hard_constraints"]}
    assert checks["long_sentence_ratio"]["passed"] is False
    assert checks["long_sentence_ratio"]["limit"] == 0.10


def test_short_sentence_body_passes_hard_constraints(tmp_path):
    root = _make_project(tmp_path)
    for chapter in (1, 2, 3):
        _write_chapter(root, chapter)
        _write_commit(root, chapter, "accepted")

    profile = build_profile(root, source_mode="accepted", max_chapters=60)
    checks = {item["code"]: item for item in profile["hard_constraints"]}
    assert checks["long_sentence_ratio"]["passed"] is True
    assert checks["said_tag_ratio"]["passed"] is True


# ---------------------------------------------------------------------------
# 档案落盘：脚本段覆盖写、归纳段保留
# ---------------------------------------------------------------------------

def test_build_creates_archive_and_profile(tmp_path):
    root = _make_project(tmp_path)
    _write_chapter(root, 1)
    _write_commit(root, 1, "accepted")

    profile = build_profile(root, source_mode="accepted", max_chapters=60)
    json_path, archive_path = write_outputs(root, profile)

    assert json_path.is_file() and archive_path.is_file()
    text = archive_path.read_text(encoding="utf-8")
    assert SCRIPT_BEGIN in text and SCRIPT_END in text
    assert "归纳段" in text
    reloaded = load_profile(root)
    assert reloaded["schema_version"] == "profile/v1".replace("profile", "style-profile")
    assert reloaded["injection_digest"]


def test_hand_section_survives_rebuild(tmp_path):
    root = _make_project(tmp_path)
    _write_chapter(root, 1)
    _write_commit(root, 1, "accepted")

    profile = build_profile(root, source_mode="accepted", max_chapters=60)
    _, archive_path = write_outputs(root, profile)
    text = archive_path.read_text(encoding="utf-8").replace("- （待填写）", "- 口癖：反复写「没有回头」。")
    archive_path.write_text(text, encoding="utf-8")

    second = build_profile(root, source_mode="accepted", max_chapters=60)
    _, archive_path = write_outputs(root, second)
    after = archive_path.read_text(encoding="utf-8")
    assert "口癖：反复写「没有回头」" in after, "归纳段被 build 覆盖"
    assert after.count(SCRIPT_BEGIN) == 1


def test_archive_without_markers_is_treated_as_hand_written():
    existing = "# 文风档案\n\n## 我手写的规则\n\n- 少用成语。\n"
    hand = _extract_hand_section(existing)
    assert "我手写的规则" in hand
    assert not hand.startswith("# 文风档案")
    assert SCRIPT_BEGIN not in hand


def test_repeated_builds_do_not_duplicate_script_section(tmp_path):
    """回归：说明文字里出现过标记字样时，多次 build 不得把旧脚本段当人工内容再拼一次。"""
    root = _make_project(tmp_path)
    _write_chapter(root, 1)
    _write_commit(root, 1, "accepted")

    sizes = []
    for _ in range(3):
        profile = build_profile(root, source_mode="accepted", max_chapters=60)
        _, archive_path = write_outputs(root, profile)
        text = archive_path.read_text(encoding="utf-8")
        assert text.count(SCRIPT_BEGIN) == 1, text.count(SCRIPT_BEGIN)
        assert text.count(SCRIPT_END) == 1
        sizes.append(len(text))
    assert sizes[0] == sizes[-1], f"档案随 build 膨胀: {sizes}"


def test_render_archive_keeps_hand_and_script_sections():
    profile = {"generated_at": "2026-09-21T00:00:00+08:00", "observed": {"source_mode": "accepted", "sampling_note": "x", "sampled_chapters": [], "summary": {}, "sentence_length_histogram": {}, "top_phrases": []}, "target": {"available": False, "reason": "无"}, "gaps": [], "hard_constraints": [], "notes": [], "injection_digest": "摘要"}
    text = render_archive(profile, "# 文风档案\n\n## 归纳段\n\n- 保留我\n")
    assert "保留我" in text
    assert text.count(SCRIPT_BEGIN) == 1


# ---------------------------------------------------------------------------
# 摘要预算
# ---------------------------------------------------------------------------

def test_injection_digest_within_budget(tmp_path):
    root = _make_project(tmp_path)
    for chapter in range(1, 6):
        _write_chapter(root, chapter)
        _write_commit(root, chapter, "accepted")
    _write_reference(root)

    profile = build_profile(root, source_mode="accepted", max_chapters=60)
    assert 0 < len(profile["injection_digest"]) <= INJECTION_DIGEST_MAX_CHARS


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_show_and_diff_before_build_report_error(tmp_path, capsys):
    root = _make_project(tmp_path)
    assert main(["--project-root", str(root), "show"]) == 2
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["status"] == "error"
    assert "build" in payload.get("suggestion", "")


def test_cli_build_show_diff_round_trip(tmp_path, capsys):
    root = _make_project(tmp_path)
    for chapter in (1, 2, 3):
        _write_chapter(root, chapter, _LONG_BODY)
        _write_commit(root, chapter, "accepted")
    _write_reference(root)

    assert main(["--project-root", str(root), "build"]) == 0
    assert main(["--project-root", str(root), "show"]) == 0
    assert main(["--project-root", str(root), "diff"]) == 0
    out = capsys.readouterr().out
    assert "→" in out

    assert main(["--project-root", str(root), "diff", "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["status"] == "success"
    assert payload["target_available"] is True
    assert payload["gaps"]


def test_cli_diff_without_target_says_so(tmp_path, capsys):
    root = _make_project(tmp_path)
    _write_chapter(root, 1)
    _write_commit(root, 1, "accepted")
    assert main(["--project-root", str(root), "build"]) == 0
    capsys.readouterr()

    assert main(["--project-root", str(root), "diff"]) == 0
    assert "无目标画像" in capsys.readouterr().out


def test_cli_unknown_command_reports_error(tmp_path, capsys):
    root = _make_project(tmp_path)
    assert main(["--project-root", str(root)]) == 2
    assert json.loads(capsys.readouterr().out.strip())["status"] == "error"


@pytest.mark.parametrize("flag", ["--source", "--max-chapters"])
def test_cli_build_accepts_tuning_flags(tmp_path, flag, capsys):
    root = _make_project(tmp_path)
    _write_chapter(root, 1)
    _write_commit(root, 1, "accepted")
    argv = ["--project-root", str(root), "build"]
    argv += ["--source", "all"] if flag == "--source" else ["--max-chapters", "1"]
    assert main(argv) == 0
    capsys.readouterr()
