#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""genre_profile_loader 单测（2026-09-18 新增）。"""

import tempfile
from pathlib import Path

from data_modules.config import DataModulesConfig
from data_modules.genre_profile_loader import (
    build_project_info,
    default_profiles_path,
    load_genre_profiles,
    resolve_genre_profile,
    resolve_pacing_thresholds,
)

_SAMPLE_PROFILES = """# sample

### 2.1 爽文/系统流 (shuangwen)

```yaml
id: shuangwen
name: 爽文/系统流
pacing_config:
  stagnation_threshold: 3
  strand_quest_max: 5
  strand_fire_gap_max: 15
```

### 2.5 规则怪谈 (rules-mystery)

```yaml
id: rules-mystery
name: 规则怪谈
hook_config:
  preferred_types: [危机钩, 悬念钩]
  strength_baseline: strong
pacing_config:
  stagnation_threshold: 2
  strand_quest_max: 4
  strand_fire_gap_max: 15
micropayoff_config:
  preferred_types: [信息兑现]
  min_per_chapter: 1
```
"""


def _write_profiles(tmpdir: str) -> Path:
    path = Path(tmpdir) / "genre-profiles.md"
    path.write_text(_SAMPLE_PROFILES, encoding="utf-8")
    return path


def test_load_parses_nested_blocks_and_scalars():
    with tempfile.TemporaryDirectory() as tmpdir:
        profiles = load_genre_profiles(_write_profiles(tmpdir))

    assert set(profiles) == {"shuangwen", "rules-mystery"}
    assert profiles["rules-mystery"]["_name"] == "规则怪谈"
    assert profiles["rules-mystery"]["pacing_config"]["strand_quest_max"] == 4
    assert profiles["rules-mystery"]["hook_config"]["preferred_types"] == ["危机钩", "悬念钩"]
    assert profiles["rules-mystery"]["micropayoff_config"]["min_per_chapter"] == 1


def test_load_missing_file_returns_empty_without_raising():
    profiles = load_genre_profiles(Path("/definitely/not/here/genre-profiles.md"))
    assert profiles == {}


def test_resolve_prefers_route_over_canonical():
    """路由标签（更具体）应压过正典题材（更宽）。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        profiles = load_genre_profiles(_write_profiles(tmpdir))

    info = {
        "genre": "悬疑",
        "genre_label": "都市+规则怪谈",
        "genre_tags": {"route": ["规则怪谈"], "templates": ["规则怪谈"]},
    }
    match = resolve_genre_profile(info, profiles)
    assert match is not None
    assert match.profile_id == "rules-mystery"
    assert match.matched_by == "route"


def test_resolve_returns_none_when_nothing_matches():
    with tempfile.TemporaryDirectory() as tmpdir:
        profiles = load_genre_profiles(_write_profiles(tmpdir))
    assert resolve_genre_profile({"genre": "完全不存在"}, profiles) is None


def test_resolve_pacing_thresholds_falls_back_to_config():
    """读不到 profile 时必须回落 config，且 source 标明 config_default。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = DataModulesConfig.from_project_root(tmpdir).project_root
        config = DataModulesConfig(project_root=project_root)
        resolved = resolve_pacing_thresholds({"genre": "不存在"}, config, {})

    assert resolved["source"] == "config_default"
    assert resolved["strand_quest_max_consecutive"] == config.strand_quest_max_consecutive
    assert resolved["strand_fire_max_gap"] == config.strand_fire_max_gap
    assert resolved["overridden"] == []


def test_resolve_pacing_thresholds_overrides_and_records_overridden_fields():
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = DataModulesConfig.from_project_root(tmpdir).project_root
        config = DataModulesConfig(project_root=project_root)
        profiles = load_genre_profiles(_write_profiles(tmpdir))
        resolved = resolve_pacing_thresholds(
            {"genre_tags": {"route": ["规则怪谈"]}}, config, profiles
        )

    assert resolved["source"] == "genre_profile:rules-mystery"
    assert resolved["strand_quest_max_consecutive"] == 4
    assert resolved["stagnation_threshold"] == 2
    assert "strand_quest_max_consecutive" in resolved["overridden"]
    # genre-profiles 未定义 Constellation 断档，必须保留 config 值
    assert resolved["strand_constellation_max_gap"] == config.strand_constellation_max_gap
    assert "strand_constellation_max_gap" not in resolved["overridden"]


def test_build_project_info_supports_flat_legacy_state():
    assert build_project_info({"genre": "悬疑", "genre_label": "x"})["genre"] == "悬疑"
    assert build_project_info(None) == {}


def test_default_profiles_path_points_at_plugin_references():
    path = default_profiles_path()
    assert path.name == "genre-profiles.md"
    assert path.parent.name == "references"


def test_real_genre_profiles_file_parses_into_expected_profile_ids():
    """对着仓库真实文件跑一次，防止格式漂移导致解析整体失效。"""
    real_path = default_profiles_path()
    if not real_path.is_file():
        return

    profiles = load_genre_profiles(real_path)
    assert len(profiles) >= 10
    for expected in ("shuangwen", "xianxia", "romance", "mystery", "rules-mystery"):
        assert expected in profiles
    assert profiles["mystery"]["pacing_config"]["strand_quest_max"] == 8
    assert profiles["rules-mystery"]["pacing_config"]["strand_fire_gap_max"] == 15
