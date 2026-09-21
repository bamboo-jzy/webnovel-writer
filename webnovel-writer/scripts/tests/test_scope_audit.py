#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scope_audit 单测（2026-09-18 新增，批一三检查器）。"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest


def _ensure_scripts_on_path() -> None:
    scripts_dir = Path(__file__).resolve().parents[1]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


_ensure_scripts_on_path()

from scope_audit import (  # noqa: E402
    CHECK_S1,
    CHECK_S4,
    CHECK_S5,
    ScopeAuditor,
    render_markdown,
    render_text,
)

_CHAPTER_TEMPLATE = """# 第{chapter}章 测试章

陈砚把钥匙挂回值班室的钩子上，没有回头。

「别抬头。」韩守德只说了这三个字，就转身走了。

{extra}
"""


def _build_project(tmpdir: str, *, chapters=(1, 2), with_index: bool = True,
                   state_overrides=None) -> Path:
    root = Path(tmpdir)
    (root / ".webnovel").mkdir(parents=True, exist_ok=True)
    (root / "正文").mkdir(parents=True, exist_ok=True)

    for chapter in chapters:
        (root / "正文" / f"第{chapter:04d}章-测试章.md").write_text(
            _CHAPTER_TEMPLATE.format(chapter=chapter, extra="他在楼道里站了很久。" * chapter),
            encoding="utf-8",
        )

    state = {
        "progress": {"current_chapter": max(chapters) if chapters else 0},
        "project_info": {
            "genre": "悬疑",
            "genre_label": "都市+规则怪谈",
            "genre_tags": {"route": ["规则怪谈"], "templates": ["规则怪谈"]},
        },
        "strand_tracker": {
            "history": [{"chapter": c, "dominant": "quest"} for c in chapters],
            "current_dominant": "quest",
        },
        "plot_threads": {"foreshadowing": []},
    }
    if state_overrides:
        state.update(state_overrides)
    (root / ".webnovel" / "state.json").write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8"
    )

    if with_index:
        conn = sqlite3.connect(str(root / ".webnovel" / "index.db"))
        conn.executescript(
            """
            CREATE TABLE chapters (chapter INTEGER PRIMARY KEY, title TEXT, location TEXT,
                word_count INTEGER, characters TEXT, summary TEXT, created_at TEXT);
            CREATE TABLE entities (id TEXT PRIMARY KEY, type TEXT, canonical_name TEXT, tier TEXT,
                desc TEXT, current_json TEXT, first_appearance INTEGER DEFAULT 0,
                last_appearance INTEGER DEFAULT 0, is_protagonist INTEGER DEFAULT 0,
                is_archived INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT);
            CREATE TABLE appearances (id INTEGER PRIMARY KEY, entity_id TEXT, chapter INTEGER,
                mentions TEXT, confidence REAL);
            CREATE TABLE state_changes (id INTEGER PRIMARY KEY, entity_id TEXT, field TEXT,
                old_value TEXT, new_value TEXT, reason TEXT, chapter INTEGER, created_at TEXT);
            CREATE TABLE relationships (id INTEGER PRIMARY KEY, from_entity TEXT, to_entity TEXT,
                type TEXT, description TEXT, chapter INTEGER, created_at TEXT);
            """
        )
        for chapter in chapters:
            conn.execute(
                "INSERT INTO chapters VALUES (?,?,?,?,?,?,?)",
                (chapter, "测试章", "", 2000, "[]", "", "2026-01-01 00:00:00"),
            )
        conn.execute(
            "INSERT INTO entities VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("lao_han", "角色", "韩守德", "支线", "", "{}", 0, 0, 0, 0, "", ""),
        )
        conn.execute(
            "INSERT INTO entities VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("elevator", "地点", "7号楼电梯轿厢", "装饰", "", "{}", 0, 0, 0, 0, "", ""),
        )
        conn.execute(
            "INSERT INTO appearances VALUES (1,'lao_han',1,?,0.9)",
            (json.dumps(["韩守德", "韩师傅"], ensure_ascii=False),),
        )
        conn.execute(
            "INSERT INTO appearances VALUES (2,'lao_han',2,?,0.9)",
            (json.dumps(["韩主任"], ensure_ascii=False),),
        )
        conn.execute(
            "INSERT INTO appearances VALUES (3,'elevator',1,?,0.8)",
            (json.dumps(["7 号楼电梯轿厢"], ensure_ascii=False),),
        )
        conn.execute(
            "INSERT INTO appearances VALUES (4,'elevator',2,?,0.8)",
            (json.dumps(["电梯"], ensure_ascii=False),),
        )
        conn.execute(
            "INSERT INTO state_changes VALUES (1,'lao_han','stance','按规程办事','第一次说了『别抬头』',"
            "'',1,'2026-01-01 00:00:00')"
        )
        conn.execute(
            "INSERT INTO state_changes VALUES (2,'lao_han','stance','第一次说了『别抬头』','按规程办事',"
            "'',2,'2026-01-01 00:00:00')"
        )
        conn.commit()
        conn.close()

    return root


def test_s1_uses_genre_profile_and_guards_short_sample():
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        root = _build_project(tmpdir, chapters=(1, 2))
        payload = ScopeAuditor(root).run([CHECK_S1])

    s1 = payload["s1_strand"]
    assert s1["available"] is True
    assert s1["thresholds"]["source"] == "genre_profile:rules-mystery"
    assert s1["thresholds"]["strand_quest_max_consecutive"] == 4
    assert s1["sample_adequate"] is False
    assert s1["violations"] == []
    assert any("不对占比下结论" in note for note in payload["notes"])


def test_s4_splits_character_alias_drift_from_non_character():
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        root = _build_project(tmpdir)
        payload = ScopeAuditor(root).run([CHECK_S4])

    s4 = payload["s4_drift"]
    assert s4["available"] is True
    char_drift = s4["alias_drift_characters"]
    assert [d["entity_id"] for d in char_drift] == ["lao_han"]
    assert [d["entity_id"] for d in s4["alias_drift_non_characters"]] == ["elevator"]

    codes = {f["code"] for f in payload["findings"]}
    assert "alias_drift" in codes
    assert "state_oscillation" in codes
    assert "entities_appearance_columns_dead" in codes


def test_s5_reports_metrics_and_withholds_deviation_on_two_chapters():
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        root = _build_project(tmpdir, chapters=(1, 2))
        payload = ScopeAuditor(root).run([CHECK_S5])

    s5 = payload["s5_style"]
    assert s5["available"] is True
    assert s5["baseline_adequate"] is False
    assert len(s5["chapters"]) == 2
    for item in s5["chapters"]:
        assert item["cjk_chars"] > 0
        assert item["sentences"] > 0
    codes = {f["code"] for f in payload["findings"]}
    assert "sentence_length_drift" not in codes
    assert any("不对偏离下结论" in note for note in payload["notes"])


def test_s5_flags_sentence_drift_when_baseline_is_adequate():
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        root = _build_project(tmpdir, chapters=(1, 2, 3))
        # 第 3 章换成超长无标点的句子，制造明显的句长漂移
        (root / "正文" / "第0003章-测试章.md").write_text(
            "# 第3章\n\n" + "陈砚在楼道里站着" * 60 + "。\n\n" + "他走了。\n",
            encoding="utf-8",
        )
        payload = ScopeAuditor(root).run([CHECK_S5])

    s5 = payload["s5_style"]
    assert s5["baseline_adequate"] is True
    codes = {f["code"] for f in payload["findings"]}
    assert "sentence_length_drift" in codes


def test_s5_repeated_phrases_keep_maximal_repeats_only():
    """
    回归（2026-09-18 噪音）：

    裸 4-gram 计数会把一段长句里每个滑窗都算成「重复」，导致表格里塞满
    `鞋头对齐` / `鞋尖朝着` 这类跨词碎片。判定必须满足两条：
    ① 出现次数 ≥ `PHRASE_MIN_COUNT`（偶发措辞不算）；
    ② 只保留**极大重复**——短片段若是某个更长片段的滑窗且次数相同，则丢弃。
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        root = _build_project(tmpdir, chapters=(1, 2))
        (root / "正文" / "第0001章-测试章.md").write_text(
            "# 第1章\n\n"
            + "陈砚在楼道里站着。" * 3          # 8 字片段反复 → 应留下 6 字极大形式
            + "\n\n"
            + "他看了一眼表。她看了一眼门。我看了一眼窗。"  # → 应留下「看了一眼」
            + "\n\n"
            + "深棕色的鞋。深棕色的门。"          # 只出现 2 次 → 低于阈值，应被过滤
            + "\n",
            encoding="utf-8",
        )
        payload = ScopeAuditor(root).run([CHECK_S5])

    grams = {item["gram"] for item in payload["s5_style"]["top_ngrams"]}

    # ① 反复出现且有信息量的片段保留
    assert "看了一眼" in grams
    # ② 极大性：反复出现的整段保留为一个片段，所有滑窗变体被丢弃
    assert "陈砚在楼道里站着" in grams
    assert "陈砚在楼道里站" not in grams
    assert "陈砚在楼道里" not in grams
    assert "陈砚在楼道" not in grams
    # ③ 低于最小重复次数的偶发措辞不出现
    assert "深棕色的" not in grams
    # ④ 片段长度不得小于最小长度（「年」「浅天蓝」这类 1–3 字串不是口癖）
    assert all(len(gram) >= 4 for gram in grams)
    assert "年" not in grams


def test_state_change_chain_marks_truncation_with_ellipsis():
    """回归：表格里截断长文本必须带省略号，否则读者会误判成数据缺失。"""
    import tempfile

    long_value = "手上有了一条能对上日期的线，从 2023-10-11 那天起就一直没断过"

    with tempfile.TemporaryDirectory() as tmpdir:
        root = _build_project(tmpdir)
        conn = sqlite3.connect(str(root / ".webnovel" / "index.db"))
        conn.execute(
            "INSERT INTO state_changes VALUES (9,'lao_han','stance','旧值',?,'',2,"
            "'2026-01-01 00:00:00')",
            (long_value,),
        )
        conn.commit()
        conn.close()

        payload = ScopeAuditor(root).run([CHECK_S4])

    text = render_markdown(payload)
    assert "…" in text
    assert long_value not in text


def test_missing_chapter_file_is_reported_not_raised():
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        root = _build_project(tmpdir, chapters=(1, 2))
        (root / "正文" / "第0002章-测试章.md").unlink()
        payload = ScopeAuditor(root).run([CHECK_S5])

    s5 = payload["s5_style"]
    assert s5["missing_chapters"] == [2]
    assert {f["code"] for f in payload["findings"]} >= {"chapter_file_missing"}


def test_missing_index_is_reported_and_s4_degrades_gracefully():
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        root = _build_project(tmpdir, with_index=False)
        payload = ScopeAuditor(root).run([CHECK_S4])

    assert payload["s4_drift"]["available"] is False
    assert "index_missing" in {f["code"] for f in payload["findings"]}
    assert payload["summary"]["total"] >= 1


def test_unknown_check_name_returns_error_exit_code():
    import tempfile

    from scope_audit import main

    with tempfile.TemporaryDirectory() as tmpdir:
        root = _build_project(tmpdir)
        assert main(["--project-root", str(root), "--checks", "s9"]) == 2


def test_output_flag_writes_file_for_markdown_format():
    """回归：`--format markdown --output PATH` 必须真的写文件（此前 --output 被静默忽略）。"""
    import tempfile

    from scope_audit import main

    with tempfile.TemporaryDirectory() as tmpdir:
        root = _build_project(tmpdir)
        target = root / "报告" / "audit.md"
        assert main([
            "--project-root", str(root),
            "--format", "markdown",
            "--output", str(target),
        ]) == 0
        assert target.is_file()
        assert "# 范围级回扫报告" in target.read_text(encoding="utf-8")


def test_markdown_and_text_rendering_include_all_sections():
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        root = _build_project(tmpdir)
        payload = ScopeAuditor(root).run([CHECK_S1, CHECK_S4, CHECK_S5])

    markdown = render_markdown(payload)
    assert "# 范围级回扫报告" in markdown
    assert "## S1 Strand 配比" in markdown
    assert "## S4 角色·关系·称谓漂移" in markdown
    assert "## S5 文体漂移" in markdown
    assert "## 结论清单" in markdown
    assert "只读" in markdown

    text = render_text(payload)
    assert "scope-audit" in text
    assert "findings:" in text


def test_audit_does_not_modify_chapter_files_or_index(tmp_path):
    """只读契约：跑完后正文与 index.db 的字节内容必须不变。"""
    root = _build_project(str(tmp_path))
    chapter = root / "正文" / "第0001章-测试章.md"
    before_chapter = chapter.read_bytes()
    before_index = (root / ".webnovel" / "index.db").read_bytes()

    ScopeAuditor(root).run([CHECK_S1, CHECK_S4, CHECK_S5])

    assert chapter.read_bytes() == before_chapter
    assert (root / ".webnovel" / "index.db").read_bytes() == before_index


def test_from_to_chapter_range_limits_scope():
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        root = _build_project(tmpdir, chapters=(1, 2, 3))
        payload = ScopeAuditor(root, from_chapter=2, to_chapter=2).run([CHECK_S5])

    assert payload["chapters"] == [2]
    assert [item["chapter"] for item in payload["s5_style"]["chapters"]] == [2]


# ---------------------------------------------------------------------------
# N8（2026-09-21）：S5 复用文风档案基线，跨卷可比
# ---------------------------------------------------------------------------

def _write_profile_json(
    root: Path,
    *,
    sentence_median=8.0,
    dialogue_median=0.33,
    chapters=(1, 2, 3),
    generated_at="2026-09-21T00:00:00+08:00",
    source_mode="accepted",
    schema="style-profile/v1",
    baseline_adequate=True,
    raw=None,
) -> None:
    """写一份最小可用的 `.webnovel/style_profile.json`（只含 S5 基线需要的字段）。"""
    webnovel_dir = root / ".webnovel"
    webnovel_dir.mkdir(parents=True, exist_ok=True)
    target = webnovel_dir / "style_profile.json"
    if raw is not None:
        target.write_text(raw, encoding="utf-8")
        return
    profile = {
        "schema_version": schema,
        "generated_at": generated_at,
        "observed": {
            "source_mode": source_mode,
            "sampled_chapters": list(chapters),
            "baseline_adequate": baseline_adequate,
            "summary": {
                "chapter_count": len(chapters),
                "scalars": {
                    "avg_sentence": {"median": sentence_median},
                    "dialogue_paragraph_ratio": {"median": dialogue_median},
                },
            },
        },
    }
    target.write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")


def test_s5_prefers_profile_baseline_over_self_comparison():
    """旧口径下「只审 1 章」= 自己跟自己比 → 永远不漂移；复用档案后才判得出来。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        root = _build_project(tmpdir, chapters=(1, 2, 3))
        _write_profile_json(root, sentence_median=8.0, dialogue_median=0.33)
        payload = ScopeAuditor(root, from_chapter=3, to_chapter=3).run([CHECK_S5])

    s5 = payload["s5_style"]
    assert s5["baseline"]["source"] == "profile"
    assert s5["baseline"]["avg_sentence"] == 8.0
    assert s5["baseline_adequate"] is True
    assert {f["code"] for f in payload["findings"]} == {"sentence_length_drift"}
    assert any("文风档案" in note for note in payload["notes"])


def test_s5_range_mode_keeps_legacy_self_baseline():
    """显式 `range` 时即便档案可用也不用它——旧口径必须能被主动取回。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        root = _build_project(tmpdir, chapters=(1, 2, 3))
        _write_profile_json(root, sentence_median=8.0)
        payload = ScopeAuditor(root, baseline_mode="range").run([CHECK_S5])

    s5 = payload["s5_style"]
    assert s5["baseline"]["source"] == "range_median"
    assert s5["baseline_mode"] == "range"
    assert s5["baseline_adequate"] is True
    assert "sentence_length_drift" not in {f["code"] for f in payload["findings"]}


def test_s5_without_profile_falls_back_and_says_so():
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        root = _build_project(tmpdir, chapters=(1, 2))
        payload = ScopeAuditor(root).run([CHECK_S5])

    s5 = payload["s5_style"]
    assert s5["baseline"]["source"] == "range_median"
    assert s5["baseline_adequate"] is False
    assert any("不对偏离下结论" in note for note in payload["notes"])
    markdown = render_markdown(payload)
    assert "本次范围内章节的中位数" in markdown


def test_s5_profile_mode_reports_unavailable_archive_instead_of_falling_back():
    """点名要档案基线却拿不到时，不能静默改用范围基线糊弄过去。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        root = _build_project(tmpdir, chapters=(1, 2, 3, 4))
        payload = ScopeAuditor(root, baseline_mode="profile").run([CHECK_S5])

    s5 = payload["s5_style"]
    assert s5["baseline_adequate"] is False
    finding = next(f for f in payload["findings"] if f["code"] == "profile_baseline_unavailable")
    assert "不对偏离下结论" in finding["detail"]
    assert "style-profile build" in finding["detail"]
    assert "sentence_length_drift" not in {f["code"] for f in payload["findings"]}


def test_s5_profile_mode_rejects_undersized_archive():
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        root = _build_project(tmpdir, chapters=(1, 2, 3, 4))
        _write_profile_json(root, chapters=(1, 2), baseline_adequate=False)
        payload = ScopeAuditor(root, baseline_mode="profile").run([CHECK_S5])

    s5 = payload["s5_style"]
    assert s5["baseline_adequate"] is False
    finding = next(f for f in payload["findings"] if f["code"] == "profile_baseline_unavailable")
    assert "样本不足" in finding["detail"]


def test_s5_ignores_corrupt_or_foreign_archive():
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        root = _build_project(tmpdir, chapters=(1, 2, 3))
        _write_profile_json(root, raw="{not json")
        payload = ScopeAuditor(root).run([CHECK_S5])

    s5 = payload["s5_style"]
    assert s5["baseline"]["source"] == "range_median"
    assert "profile_baseline_unavailable" not in {f["code"] for f in payload["findings"]}

    # 同族但更高版本的 schema 仍可用（只读两个数字字段，形状变了也不会误读）
    with tempfile.TemporaryDirectory() as tmpdir2:
        root2 = _build_project(tmpdir2, chapters=(1, 2, 3))
        _write_profile_json(root2, schema="style-profile/v9", sentence_median=8.0)
        payload2 = ScopeAuditor(root2).run([CHECK_S5])
    assert payload2["s5_style"]["baseline"]["source"] == "profile"

    # 别的档案族（字段语义无保证）不得拿来当基线
    with tempfile.TemporaryDirectory() as tmpdir3:
        root3 = _build_project(tmpdir3, chapters=(1, 2, 3))
        _write_profile_json(root3, schema="narrative-profile/v1", sentence_median=8.0)
        payload3 = ScopeAuditor(root3).run([CHECK_S5])
    assert payload3["s5_style"]["baseline"]["source"] == "range_median"


def test_s5_notes_stale_and_overreaching_archive():
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        root = _build_project(tmpdir, chapters=(1, 2, 3, 4))
        _write_profile_json(
            root, chapters=(1, 2), source_mode="all", sentence_median=8.0
        )
        payload = ScopeAuditor(root, from_chapter=3, to_chapter=4).run([CHECK_S5])

    notes = " ".join(payload["notes"])
    assert "档案最新样本为第 2 章" in notes
    assert "落后于正文" in notes
    assert "越权口径" in notes
    assert payload["s5_style"]["baseline"]["source"] == "profile"


def test_s5_consumes_real_style_profile_build(tmp_path):
    """集成口径：S5 读的是 `style_profile.build_profile` 真产出的文件，不是自造字段。"""
    from style_profile import build_profile, write_outputs

    root = _build_project(str(tmp_path), chapters=(1, 2, 3, 4, 5))
    (root / "正文" / "第0005章-测试章.md").write_text(
        "# 第5章\n\n" + "陈砚在楼道里站着" * 60 + "。\n", encoding="utf-8"
    )
    profile = build_profile(root, source_mode="all", max_chapters=60)
    write_outputs(root, profile)

    payload = ScopeAuditor(root, from_chapter=5, to_chapter=5).run([CHECK_S5])

    s5 = payload["s5_style"]
    assert s5["baseline"]["source"] == "profile"
    assert s5["baseline_adequate"] is True
    assert s5["baseline"]["profile"]["chapter_count"] == 5
    codes = {f["code"] for f in payload["findings"]}
    assert "sentence_length_drift" in codes
    assert any("越权口径" in note for note in payload["notes"])
    assert "文风档案" in render_markdown(payload)
