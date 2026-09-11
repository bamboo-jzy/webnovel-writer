# state.json 结构说明

> 该文件为运行态精简状态，避免体量膨胀。实体等大数据存于 index.db。
>
> 以下示例与 `update_state.py` 当前校验字段保持一致。

```json
{
  "project_info": {
    "title": "",
    "genre": "",
    "genre_label": "",
    "genre_tags": {
      "route": [],
      "trope": [],
      "format": [],
      "templates": []
    },
    "target_words": 0,
    "target_chapters": 0
  },
  "progress": {
    "current_chapter": 0,
    "total_words": 0,
    "last_updated": "",
    "volumes_completed": [],
    "current_volume": 1,
    "volumes_planned": [
      {
        "volume": 1,
        "chapters_range": "1-100",
        "planned_at": "2026-02-01",
        "planning_revision": "...",
        "reload_source": "guided_plan",
        "reloaded_at": "2026-02-01",
        "stale_chapters": []
      }
    ],
    "chapters_planned": [
      {
        "chapter": 1,
        "volume": 1,
        "outline_file": "大纲/第1章-标题.md",
        "planned_at": "2026-09-09",
        "updated_at": "2026-09-09",
        "source_volume_revision": "...",
        "status": "planned",
        "stale_reason": "",
        "stale_at": ""
      }
    ],
    "chapter_revisions": {
      "0001": {
        "content_revision": "sha256...",
        "draft_revision": "sha256...",
        "validated_revision": "sha256...",
        "committed_revision": "sha256...",
        "content_status": "committed",
        "validation_input": {
          "chapter": 1,
          "content_revision": "sha256...",
          "contract_revision": "sha256...",
          "validation_id": "..."
        },
        "artifact_digest": "sha256...",
        "stale_reason": "",
        "stale_dependencies": {},
        "reload_source": "manual_reload",
        "reloaded_at": "",
        "validated_at": ""
      }
    },
    "chapter_status": {}
  },
  "protagonist_state": {
    "name": "",
    "power": {"realm": "", "layer": 0, "bottleneck": ""},
    "location": {"current": "", "last_chapter": 0},
    "golden_finger": {"name": "", "level": 0, "cooldown": 0}
  },
  "relationships": {},
  "world_settings": {
    "power_system": [],
    "factions": [],
    "locations": []
  },
  "review_checkpoints": [
    {"chapters": "1-5", "report": "审查报告/第1-5章审查报告.md", "reviewed_at": "2026-02-26 20:00:00"}
  ],
  "strand_tracker": {
    "last_quest_chapter": 0,
    "last_fire_chapter": 0,
    "last_constellation_chapter": 0,
    "current_dominant": "quest",
    "chapters_since_switch": 0,
    "history": []
  },
  "plot_threads": {
    "active_threads": [],
    "foreshadowing": []
  },
  "disambiguation_warnings": [],
  "disambiguation_pending": [],
  "chapter_meta": {
    "0001": {
      "hook": {"type": "危机钩", "content": "...", "strength": "strong"},
      "pattern": {
        "opening": "冲突开场",
        "hook": "危机钩",
        "emotion_rhythm": "低→高",
        "info_density": "medium"
      },
      "ending": {"time": "夜晚", "location": "宗门大殿", "emotion": "紧张"}
    }
  }
}
```
