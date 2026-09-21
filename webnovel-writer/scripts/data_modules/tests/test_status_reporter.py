#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import tempfile

from data_modules.config import DataModulesConfig
from data_modules.index_manager import (
    IndexManager,
    ChapterReadingPowerMeta,
    EntityMeta,
    RelationshipMeta,
    RelationshipEventMeta,
)
from status_reporter import StatusReporter


def _write_state(project_root, state: dict):
    webnovel_dir = project_root / ".webnovel"
    webnovel_dir.mkdir(parents=True, exist_ok=True)
    (webnovel_dir / "state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def test_foreshadowing_analysis_uses_real_chapters_and_handles_missing_data():
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = DataModulesConfig.from_project_root(tmpdir).project_root

        state = {
            "progress": {"current_chapter": 120, "total_words": 360000},
            "plot_threads": {
                "foreshadowing": [
                    {
                        "content": "林家宝库铭文的秘密",
                        "status": "未回收",
                        "tier": "核心",
                        "planted_chapter": 20,
                        "target_chapter": 100,
                    },
                    {
                        "content": "神秘玉佩来历",
                        "status": "待回收",
                        "tier": "支线",
                        "added_chapter": 50,
                        "target": 150,
                    },
                    {
                        "content": "旧日誓言",
                        "status": "未回收",
                        "tier": "装饰",
                    },
                    {
                        "content": "已完成伏笔",
                        "status": "已回收",
                        "planted_chapter": 10,
                        "target_chapter": 20,
                    },
                ]
            },
        }
        _write_state(project_root, state)

        reporter = StatusReporter(str(project_root))
        assert reporter.load_state() is True

        foreshadowing = reporter.analyze_foreshadowing()
        assert len(foreshadowing) == 3

        records = {item["content"]: item for item in foreshadowing}
        assert records["林家宝库铭文的秘密"]["planted_chapter"] == 20
        assert records["林家宝库铭文的秘密"]["elapsed"] == 100
        assert records["林家宝库铭文的秘密"]["status"] == "🔴 已超期"

        assert records["神秘玉佩来历"]["planted_chapter"] == 50
        assert records["神秘玉佩来历"]["target_chapter"] == 150
        assert records["神秘玉佩来历"]["status"] in {"🟡 轻度超时", "🟢 正常"}

        assert records["旧日誓言"]["planted_chapter"] is None
        assert records["旧日誓言"]["status"] == "⚪ 数据不足"

        urgency = reporter.analyze_foreshadowing_urgency()
        urgency_by_content = {item["content"]: item for item in urgency}

        assert urgency_by_content["林家宝库铭文的秘密"]["urgency"] is not None
        assert urgency_by_content["林家宝库铭文的秘密"]["status"] == "🔴 已超期"
        assert urgency_by_content["旧日誓言"]["urgency"] is None
        assert urgency_by_content["旧日誓言"]["status"] == "⚪ 数据不足"


def test_pacing_analysis_prefers_real_coolpoint_metadata_over_estimation():
    with tempfile.TemporaryDirectory() as tmpdir:
        config = DataModulesConfig.from_project_root(tmpdir)
        config.ensure_dirs()
        project_root = config.project_root

        state = {
            "progress": {"current_chapter": 3, "total_words": 12000},
            "chapter_meta": {
                "0003": {
                    "hook": "下章有变",
                    "coolpoint_patterns": ["身份掉马", "反派翻车"],
                }
            },
        }
        _write_state(project_root, state)

        idx = IndexManager(config)
        idx.save_chapter_reading_power(
            ChapterReadingPowerMeta(
                chapter=1,
                hook_type="渴望钩",
                hook_strength="strong",
                coolpoint_patterns=["打脸权威", "身份掉马"],
            )
        )
        idx.save_chapter_reading_power(
            ChapterReadingPowerMeta(
                chapter=2,
                hook_type="悬念钩",
                hook_strength="medium",
                coolpoint_patterns=["身份掉马"],
            )
        )

        reporter = StatusReporter(str(project_root))
        assert reporter.load_state() is True
        reporter.chapters_data = [
            {"chapter": 1, "word_count": 4000, "cool_point": "", "dominant": "", "characters": []},
            {"chapter": 2, "word_count": 3000, "cool_point": "", "dominant": "", "characters": []},
            {"chapter": 3, "word_count": 5000, "cool_point": "", "dominant": "", "characters": []},
        ]

        segments = reporter.analyze_pacing()
        assert len(segments) == 1

        seg = segments[0]
        assert seg["cool_points"] == 5
        assert round(seg["words_per_point"], 2) == 2400.00
        assert seg["missing_chapters"] == 0
        assert seg["dominant_source"] == "chapter_reading_power"


def test_pacing_analysis_marks_missing_data_instead_of_assuming_one_point_per_chapter():
    with tempfile.TemporaryDirectory() as tmpdir:
        config = DataModulesConfig.from_project_root(tmpdir)
        config.ensure_dirs()
        project_root = config.project_root

        state = {
            "progress": {"current_chapter": 1, "total_words": 2000},
            "chapter_meta": {},
        }
        _write_state(project_root, state)

        reporter = StatusReporter(str(project_root))
        assert reporter.load_state() is True
        reporter.chapters_data = [
            {"chapter": 1, "word_count": 2000, "cool_point": "", "dominant": "", "characters": []}
        ]

        seg = reporter.analyze_pacing()[0]
        assert seg["cool_points"] == 0
        assert seg["words_per_point"] is None
        assert seg["rating"] == "数据不足"
        assert seg["missing_chapters"] == 1


def test_relationship_graph_prefers_index_db_data():
    with tempfile.TemporaryDirectory() as tmpdir:
        config = DataModulesConfig.from_project_root(tmpdir)
        config.ensure_dirs()
        project_root = config.project_root

        state = {
            "progress": {"current_chapter": 12, "total_words": 24000},
            "protagonist_state": {"name": "萧炎"},
            "relationships": {"allies": [{"name": "旧盟友", "relation": "友好"}], "enemies": []},
        }
        _write_state(project_root, state)

        idx = IndexManager(config)
        idx.upsert_entity(
            EntityMeta(
                id="xiaoyan",
                type="角色",
                canonical_name="萧炎",
                tier="核心",
                current={},
                first_appearance=1,
                last_appearance=12,
                is_protagonist=True,
            )
        )
        idx.upsert_entity(
            EntityMeta(
                id="yaolao",
                type="角色",
                canonical_name="药老",
                tier="重要",
                current={},
                first_appearance=1,
                last_appearance=12,
            )
        )
        idx.upsert_relationship(
            RelationshipMeta(
                from_entity="xiaoyan",
                to_entity="yaolao",
                type="师徒",
                description="师徒关系",
                chapter=10,
            )
        )
        idx.record_relationship_event(
            RelationshipEventMeta(
                from_entity="xiaoyan",
                to_entity="yaolao",
                type="师徒",
                chapter=10,
                action="create",
                polarity=1,
                strength=0.9,
                description="拜师",
                evidence="萧炎拜药老为师",
            )
        )

        reporter = StatusReporter(str(project_root))
        assert reporter.load_state() is True
        graph = reporter.generate_relationship_graph()
        assert "mermaid" in graph
        assert "药老" in graph
        assert "师徒" in graph


def _base_state(**overrides):
    state = {
        "progress": {"current_chapter": 2, "total_words": 5600},
        "project_info": {
            "genre": "悬疑",
            "genre_label": "都市+规则怪谈",
            "genre_tags": {"route": ["规则怪谈"], "templates": ["规则怪谈"]},
        },
        "strand_tracker": {
            "last_quest_chapter": 2,
            "last_fire_chapter": 0,
            "last_constellation_chapter": 0,
            "current_dominant": "quest",
            "chapters_since_switch": 2,
            "history": [{"chapter": 1, "dominant": "quest"}, {"chapter": 2, "dominant": "quest"}],
        },
        "plot_threads": {"foreshadowing": []},
    }
    state.update(overrides)
    return state


def test_foreshadowing_without_target_chapter_is_not_reported_as_healthy():
    """
    回归（2026-09-18 假阴性）：
    缺 `target_chapter` 时，旧实现用 elapsed（才 1 章）比 50 章绝对阈值 → 判「🟢 正常」，
    报告写「✅ 所有伏笔进度正常」，实际一无所知。
    """
    from status_reporter import FS_STATUS_NO_TARGET

    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = DataModulesConfig.from_project_root(tmpdir).project_root
        _write_state(
            project_root,
            _base_state(
                plot_threads={
                    "foreshadowing": [
                        {"content": "鞋主去向", "status": "未回收", "tier": "支线", "planted_chapter": 1},
                        {"content": "白块遮挡", "status": "未回收", "tier": "支线", "planted_chapter": 2},
                    ]
                }
            ),
        )

        reporter = StatusReporter(str(project_root))
        assert reporter.load_state() is True

        records = {item["content"]: item for item in reporter.analyze_foreshadowing()}
        assert records["鞋主去向"]["status"] == FS_STATUS_NO_TARGET
        assert records["鞋主去向"]["status"] != "🟢 正常"

        section = "\n".join(reporter._generate_foreshadowing_section())
        assert "所有伏笔进度正常" not in section
        assert "无法判断" in section


def test_foreshadowing_overdue_uses_remaining_not_elapsed():
    """有目标章时，逾期判据必须是「距目标章的剩余章数」，不是「距埋设的章数」。"""
    from status_reporter import FS_STATUS_DUE_SOON, FS_STATUS_ON_TRACK, FS_STATUS_OVERDUE

    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = DataModulesConfig.from_project_root(tmpdir).project_root
        _write_state(
            project_root,
            _base_state(
                progress={"current_chapter": 100, "total_words": 300000},
                plot_threads={
                    "foreshadowing": [
                        # 目标第 300 章：剩余 200 章。旧实现 elapsed=99 < 150 → 误报「轻度超时」
                        {"content": "远期真相", "status": "未回收", "planted_chapter": 1, "target_chapter": 300},
                        # 目标第 90 章：已过期
                        {"content": "过期线索", "status": "未回收", "planted_chapter": 10, "target_chapter": 90},
                        # 目标第 103 章：临近（proximity 默认 5）
                        {"content": "临期线索", "status": "未回收", "planted_chapter": 20, "target_chapter": 103},
                    ]
                },
            ),
        )

        reporter = StatusReporter(str(project_root))
        assert reporter.load_state() is True
        records = {item["content"]: item for item in reporter.analyze_foreshadowing()}

        assert records["远期真相"]["status"] == FS_STATUS_ON_TRACK
        assert records["过期线索"]["status"] == FS_STATUS_OVERDUE
        assert records["临期线索"]["status"] == FS_STATUS_DUE_SOON


def test_strand_thresholds_come_from_genre_profile_not_hardcoded_config():
    """Strand 连续性阈值必须取题材 profile（规则怪谈 quest≤4 / fire≤15），不是 config 的 5/10。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = DataModulesConfig.from_project_root(tmpdir).project_root
        _write_state(project_root, _base_state())

        reporter = StatusReporter(str(project_root))
        assert reporter.load_state() is True
        thresholds = reporter.pacing_thresholds()

        assert thresholds["source"] == "genre_profile:rules-mystery"
        assert thresholds["strand_quest_max_consecutive"] == 4
        assert thresholds["strand_fire_max_gap"] == 15


def test_strand_ratio_not_concluded_on_short_sample():
    """开篇 2 章不得因「100% Quest / 0% Fire」被判违规——占比是整卷口径。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = DataModulesConfig.from_project_root(tmpdir).project_root
        _write_state(project_root, _base_state())

        reporter = StatusReporter(str(project_root))
        assert reporter.load_state() is True
        data = reporter.analyze_strand_weave()

        assert data["has_data"] is True
        assert data["sample_adequate"] is False
        assert data["violations"] == []
        assert data["health"] == "✅ 健康"

        section = "\n".join(reporter._generate_strand_section())
        assert "样本不足" in section
        assert "不对占比下结论" in section


def test_strand_ratio_concluded_when_sample_is_adequate():
    """样本达到阈值后，占比结论必须恢复。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = DataModulesConfig.from_project_root(tmpdir).project_root
        history = [{"chapter": i, "dominant": "quest"} for i in range(1, 21)]
        _write_state(
            project_root,
            _base_state(strand_tracker={"history": history, "current_dominant": "quest"}),
        )

        reporter = StatusReporter(str(project_root))
        assert reporter.load_state() is True
        data = reporter.analyze_strand_weave()

        assert data["sample_adequate"] is True
        assert any("偏高" in v or "偏低" in v for v in data["violations"])


def test_strand_reports_threshold_source_when_no_genre_profile_matches():
    """匹配不到题材 profile 时必须如实标注阈值来自 config 默认值。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = DataModulesConfig.from_project_root(tmpdir).project_root
        _write_state(
            project_root,
            _base_state(project_info={"genre": "完全不存在的题材", "genre_tags": {}}),
        )

        reporter = StatusReporter(str(project_root))
        assert reporter.load_state() is True
        thresholds = reporter.pacing_thresholds()

        assert thresholds["source"] == "config_default"
        section = "\n".join(reporter._generate_strand_section())
        assert "config 默认值" in section


def test_constructing_reporter_does_not_write_index_db():
    """
    只读契约回归（2026-09-18）：

    `IndexManager.__init__` 会走 `_init_db()`（一堆 `CREATE TABLE IF NOT EXISTS` + commit），
    是个**有写副作用**的构造器。此前 `StatusReporter.__init__` 直接构造它，导致任何纯读取方
    （doctor / status / scope-audit）只要拿到 reporter，index.db 的文件头就会被改写。

    改为惰性构造后：只要不真正读实体/关系索引，就不建连、不落盘。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        config = DataModulesConfig.from_project_root(tmpdir)
        config.ensure_dirs()
        project_root = config.project_root

        # 先显式建库（这一步的写是预期的），随后快照字节
        IndexManager(config)
        db_path = config.index_db
        before = db_path.read_bytes()

        _write_state(project_root, _base_state())

        reporter = StatusReporter(str(project_root))
        assert reporter.load_state() is True
        reporter.analyze_strand_weave()
        reporter.pacing_thresholds()
        reporter._generate_strand_section()

        assert db_path.read_bytes() == before
