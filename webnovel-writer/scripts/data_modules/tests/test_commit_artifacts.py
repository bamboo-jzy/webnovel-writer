#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from data_modules.commit_artifacts import (
    canonical_fact_snapshot,
    extraction_list,
    extraction_result_from_commit,
    extraction_text,
    fact_snapshot_diff,
)


def test_extraction_result_prefers_canonical_nested_payload():
    payload = {
        "extraction_result": {
            "accepted_events": [{"event_id": "nested"}],
            "summary_text": "nested summary",
        },
        "accepted_events": [{"event_id": "legacy"}],
        "summary_text": "legacy summary",
    }

    extraction = extraction_result_from_commit(payload)

    assert extraction["accepted_events"] == [{"event_id": "nested"}]
    assert extraction["summary_text"] == "nested summary"
    assert extraction_list(payload, "accepted_events") == [{"event_id": "nested"}]
    assert extraction_text(payload, "summary_text") == "nested summary"


def test_extraction_result_keeps_read_compatibility_for_legacy_commit_payload():
    payload = {
        "accepted_events": [{"event_id": "legacy"}],
        "summary_text": "legacy summary",
    }

    extraction = extraction_result_from_commit(payload)

    assert extraction["accepted_events"] == [{"event_id": "legacy"}]
    assert extraction["summary_text"] == "legacy summary"


def test_canonical_fact_snapshot_is_stable_across_order_and_event_ids():
    first = {
        "meta": {"chapter": 4},
        "extraction_result": {
            "accepted_events": [
                {
                    "event_id": "first-id",
                    "event_type": "relationship_changed",
                    "subject": "林舟",
                    "payload": {"to_entity": "沈月", "relationship_type": "盟友"},
                },
                {
                    "event_id": "second-id",
                    "event_type": "artifact_obtained",
                    "subject": "林舟",
                    "payload": {"artifact_id": "玄铁剑", "holder": "林舟"},
                },
            ],
            "state_deltas": [
                {"entity_id": "林舟", "field": "location", "old": "城外", "new": "城内"}
            ],
            "entity_deltas": [],
        },
    }
    second = {
        "meta": {"chapter": 4},
        "extraction_result": {
            "accepted_events": [
                {
                    "event_id": "different-id",
                    "event_type": "artifact_obtained",
                    "subject": "林舟",
                    "payload": {"holder": "林舟", "artifact_id": "玄铁剑"},
                },
                {
                    "event_id": "another-id",
                    "event_type": "relationship_changed",
                    "subject": "林舟",
                    "payload": {"relationship_type": "盟友", "to_entity": "沈月"},
                },
            ],
            "state_deltas": [
                {"field": "location", "new": "城内", "old": "城外", "entity_id": "林舟"}
            ],
            "entity_deltas": [],
        },
    }

    assert fact_snapshot_diff(canonical_fact_snapshot(first), canonical_fact_snapshot(second)) == {
        "added": [], "removed": [], "changed": []
    }


def test_fact_snapshot_diff_reports_entity_relationship_timeline_and_open_loop_changes():
    old = {
        "meta": {"chapter": 4},
        "extraction_result": {
            "accepted_events": [
                {"event_type": "open_loop_created", "subject": "林舟", "payload": {"loop_id": "gate", "question": "谁在门后"}},
                {"event_type": "relationship_changed", "subject": "林舟", "payload": {"to_entity": "沈月", "relationship_type": "陌生人"}},
            ],
            "state_deltas": [{"entity_id": "林舟", "field": "time", "old": "辰时", "new": "午时"}],
            "entity_deltas": [{"entity_id": "玄铁剑", "canonical_name": "玄铁剑", "type": "物品", "current": {"holder": "林舟"}}],
        },
    }
    new = {
        "meta": {"chapter": 4},
        "extraction_result": {
            "accepted_events": [
                {"event_type": "open_loop_closed", "subject": "林舟", "payload": {"loop_id": "gate", "question": "谁在门后"}},
                {"event_type": "relationship_changed", "subject": "林舟", "payload": {"to_entity": "沈月", "relationship_type": "盟友"}},
                {"event_type": "open_loop_created", "subject": "林舟", "payload": {"loop_id": "tower", "question": "塔顶有什么"}},
            ],
            "state_deltas": [{"entity_id": "林舟", "field": "time", "old": "辰时", "new": "未时"}],
            "entity_deltas": [{"entity_id": "玄铁剑", "canonical_name": "玄铁剑", "type": "物品", "current": {"holder": "沈月"}}],
        },
    }

    diff = fact_snapshot_diff(canonical_fact_snapshot(old), canonical_fact_snapshot(new))

    assert any(item["category"] == "relationship" and item["before"] and item["after"] for item in diff["changed"])
    assert any(item["category"] == "timeline" for item in diff["changed"])
    assert any(item["category"] == "open_loop" and item["key"] == "gate" for item in diff["changed"])
    assert any(item["category"] == "open_loop" and item["key"] == "tower" for item in diff["added"])
    assert any(item["category"] == "entity" for item in diff["changed"])


def test_fact_snapshot_supports_location_time_and_entity_appearance_facts():
    old = {
        "meta": {"chapter": 5},
        "extraction_result": {
            "accepted_events": [
                {"event_type": "character_state_changed", "subject": "林舟", "payload": {"field": "location", "old": "城外", "new": "城内"}},
                {"event_type": "character_state_changed", "subject": "林舟", "payload": {"field": "time", "old": "辰时", "new": "午时"}},
            ],
            "state_deltas": [],
            "entity_deltas": [],
            "entities_appeared": [{"entity_id": "沈月", "canonical_name": "沈月", "type": "角色"}],
        },
    }
    new = {
        "meta": {"chapter": 5},
        "extraction_result": {
            "accepted_events": [
                {"event_type": "character_state_changed", "subject": "林舟", "payload": {"field": "location", "old": "城外", "new": "城内"}},
                {"event_type": "character_state_changed", "subject": "林舟", "payload": {"field": "time", "old": "辰时", "new": "未时"}},
            ],
            "state_deltas": [],
            "entity_deltas": [],
            "entities_appeared": [{"entity_id": "沈月", "canonical_name": "沈月", "type": "角色"}],
        },
    }

    diff = fact_snapshot_diff(canonical_fact_snapshot(old), canonical_fact_snapshot(new))

    assert any(item["category"] == "timeline" and item["key"] == "林舟|time" for item in diff["changed"])
    assert canonical_fact_snapshot(old)["facts"]["location"]["林舟"]["value"]
    assert canonical_fact_snapshot(old)["facts"]["entity"]["沈月"]["value"]



    payload = {
        "meta": {"chapter": 4},
        "extraction_result": {
            "accepted_events": [],
            "state_deltas": [{"field": "realm", "new": "筑基"}],
            "entity_deltas": [],
        },
    }

    import pytest
    with pytest.raises(ValueError, match="entity identifier"):
        canonical_fact_snapshot(payload)
