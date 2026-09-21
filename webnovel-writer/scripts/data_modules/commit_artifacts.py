#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from typing import Any


EXTRACTION_FIELDS = (
    "accepted_events",
    "state_deltas",
    "entity_deltas",
    "entities_appeared",
    "scenes",
    "chapter_meta",
    "dominant_strand",
    "summary_text",
)


FACT_SNAPSHOT_VERSION = "story-facts/v1"


def extraction_result_from_commit(commit_payload: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical extraction artifact from a commit.

    New commits store the extraction snapshot under ``extraction_result``.
    Older commits stored these fields at top level, so this helper keeps
    projections readable without preserving two write shapes. If the
    canonical nested artifact exists, it is the only source of truth.
    """
    nested = commit_payload.get("extraction_result")
    if isinstance(nested, dict):
        return dict(nested)

    result: dict[str, Any] = {}
    for field in EXTRACTION_FIELDS:
        if field in commit_payload:
            result[field] = commit_payload.get(field)
    return result


def extraction_list(commit_payload: dict[str, Any], field: str) -> list[Any]:
    value = extraction_result_from_commit(commit_payload).get(field)
    return value if isinstance(value, list) else []


def extraction_dict(commit_payload: dict[str, Any], field: str) -> dict[str, Any]:
    value = extraction_result_from_commit(commit_payload).get(field)
    return value if isinstance(value, dict) else {}


def extraction_text(commit_payload: dict[str, Any], field: str) -> str:
    value = extraction_result_from_commit(commit_payload).get(field)
    return str(value or "").strip()


def resolve_scene_index(scene: dict[str, Any], fallback: int) -> int:
    """Return a scene's ordinal, honouring an explicit ``0``.

    ``scene.get("scene_index") or scene.get("index") or fallback`` treats an
    explicit ``0`` as "missing" and silently falls back to the enumeration
    position, so two scenes end up sharing one ordinal and the projection dies
    on ``UNIQUE(chapter, scene_index)``. Presence of the key decides here: only
    an absent, empty or unparsable value falls back.
    """
    for key in ("scene_index", "index"):
        if key not in scene:
            continue
        raw = scene.get(key)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            return fallback
    return fallback


def canonical_fact_snapshot(commit_or_extraction: dict[str, Any]) -> dict[str, Any]:
    extraction = extraction_result_from_commit(commit_or_extraction)
    chapter = _chapter_number(commit_or_extraction, extraction)
    facts: dict[str, dict[str, dict[str, Any]]] = {}

    for index, event in enumerate(_required_list(extraction, "accepted_events"), start=1):
        if not isinstance(event, dict):
            raise ValueError(f"accepted_events[{index - 1}] must be a JSON object")
        normalized = _normalize_event(event, chapter, index)
        _add_event_fact(facts, normalized, index)

    for field in ("state_deltas", "entity_deltas"):
        for index, delta in enumerate(_required_list(extraction, field)):
            if not isinstance(delta, dict):
                raise ValueError(f"{field}[{index}] must be a JSON object")
            _add_delta_fact(facts, field, delta, chapter, index)

    for index, entity in enumerate(_required_list(extraction, "entities_appeared")):
        if not isinstance(entity, dict):
            raise ValueError(f"entities_appeared[{index}] must be a JSON object")
        entity_id = _text(entity, "entity_id", "id", "canonical_name", "name")
        if not entity_id:
            raise ValueError(f"entities_appeared[{index}] requires an entity identifier")
        _add_fact(
            facts,
            "entity",
            entity_id,
            {key: value for key, value in entity.items() if key not in {"source", "event_id", "delta_id"}},
            {"kind": "entities_appeared", "index": index},
        )

    return {
        "schema_version": FACT_SNAPSHOT_VERSION,
        "facts": {
            category: {
                key: facts[category][key]
                for key in sorted(facts[category])
            }
            for category in sorted(facts)
        },
    }


def fact_snapshot_diff(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    previous_facts = _snapshot_facts(previous)
    current_facts = _snapshot_facts(current)
    changes: dict[str, list[dict[str, Any]]] = {"added": [], "removed": [], "changed": []}

    for category in sorted(set(previous_facts) | set(current_facts)):
        old_rows = previous_facts.get(category, {})
        new_rows = current_facts.get(category, {})
        for key in sorted(set(old_rows) | set(new_rows)):
            old = old_rows.get(key)
            new = new_rows.get(key)
            if old is None:
                changes["added"].append(_diff_item(category, key, None, new))
            elif new is None:
                changes["removed"].append(_diff_item(category, key, old, None))
            elif old.get("value") != new.get("value"):
                changes["changed"].append(_diff_item(category, key, old, new))
    return changes


def _chapter_number(payload: dict[str, Any], extraction: dict[str, Any]) -> int:
    for value in (
        payload.get("chapter"),
        (payload.get("meta") or {}).get("chapter"),
        (extraction.get("source") or {}).get("chapter"),
    ):
        try:
            chapter = int(value or 0)
        except (TypeError, ValueError):
            continue
        if chapter > 0:
            return chapter
    return 0


def _required_list(extraction: dict[str, Any], field: str) -> list[Any]:
    value = extraction.get(field, [])
    if not isinstance(value, list):
        raise ValueError(f"extraction_result.{field} must be a list")
    return value


def _normalize_event(event: dict[str, Any], chapter: int, index: int) -> dict[str, Any]:
    result = dict(event)
    event_type = str(result.get("event_type") or result.get("type") or "").strip().lower().replace("-", "_")
    if not event_type:
        raise ValueError(f"accepted_events[{index - 1}].event_type must be non-empty")
    try:
        from .chapter_commit_schema import normalize_accepted_events

        result = normalize_accepted_events(chapter or 1, [result])[0]
    except (ImportError, ValueError, TypeError, KeyError) as exc:
        raise ValueError(f"accepted_events[{index - 1}] cannot be normalized: {exc}") from exc
    if not str(result.get("event_type") or "").strip() or not str(result.get("subject") or "").strip():
        raise ValueError(f"accepted_events[{index - 1}] requires event_type and subject")
    return result


def _add_event_fact(facts: dict[str, dict[str, dict[str, Any]]], event: dict[str, Any], index: int) -> None:
    event_type = str(event["event_type"]).strip()
    subject = str(event.get("subject") or "").strip()
    payload = event["payload"]
    source = {"kind": "accepted_event", "event_type": event_type, "index": index}

    if event_type in {"character_state_changed", "power_breakthrough"}:
        field = _text(payload, "field", "field_path") or ("realm" if event_type == "power_breakthrough" else "")
        if not field:
            raise ValueError(f"accepted_events[{index - 1}] state event requires payload.field")
        entity = _text(payload, "entity_id") or subject
        _add_fact(facts, "state", f"{entity}|{field}", _transition(payload), source)
    elif event_type == "relationship_changed":
        source_entity = _text(payload, "from_entity") or subject
        target_entity = _text(payload, "to_entity", "to")
        relation = _text(payload, "relationship_type", "relation_type", "type")
        if not source_entity or not target_entity or not relation:
            raise ValueError(f"accepted_events[{index - 1}] relationship event is incomplete")
        _add_fact(facts, "relationship", f"{source_entity}|{target_entity}", payload, source)
    elif event_type == "artifact_obtained":
        artifact = _text(payload, "artifact_id", "item_id", "artifact", "item", "name")
        if not artifact:
            raise ValueError(f"accepted_events[{index - 1}] artifact event requires an artifact identifier")
        _add_fact(facts, "artifact", artifact, payload, source)
    elif event_type in {"open_loop_created", "open_loop_closed", "promise_created", "promise_paid_off"}:
        identity = _text(payload, "loop_id", "open_loop_id", "promise_id", "id", "question", "content", "description") or subject
        if not identity:
            raise ValueError(f"accepted_events[{index - 1}] open-loop/promise event is incomplete")
        status = "closed" if event_type in {"open_loop_closed", "promise_paid_off"} else "open"
        _add_fact(facts, "open_loop" if event_type.startswith("open_loop") else "promise", identity, {"status": status, **payload}, source)
    elif event_type in {"world_rule_revealed", "world_rule_broken"}:
        identity = _text(payload, "rule_id", "world_rule_id", "id") or subject
        _add_fact(facts, "world_rule", identity, {"event_type": event_type, **payload}, source)
    else:
        _add_fact(facts, "event", f"{event_type}|{subject}", payload, source)

    field_name = _text(payload, "field", "field_path").casefold()
    location = _text(payload, "location", "from_location", "to_location")
    if not location and field_name in {"location", "地点", "current_location"}:
        location = subject
    if location:
        location_value = {"location": location}
        transition = _optional_transition(payload)
        if transition is not None:
            location_value.update(transition)
        _add_fact(facts, "location", subject, location_value, source)
    timeline = _text(payload, "time_anchor", "timestamp", "date", "time", "from_time", "to_time")
    if not timeline and field_name in {"time", "timeline", "time_anchor", "时间", "时间线"}:
        timeline = subject
    if timeline:
        timeline_key = _text(payload, "timeline_id", "time_key", "anchor_id") or f"{subject}|time"
        timeline_value = {"time": timeline}
        transition = _optional_transition(payload)
        if transition is not None:
            timeline_value.update(transition)
        _add_fact(facts, "timeline", timeline_key, timeline_value, source)


def _add_delta_fact(facts: dict[str, dict[str, dict[str, Any]]], field: str, delta: dict[str, Any], chapter: int, index: int) -> None:
    entity = _text(delta, "entity_id", "entity", "subject", "id")
    if not entity:
        raise ValueError(f"{field}[{index}] requires an entity identifier")
    source = {"kind": field, "index": index}
    if field == "state_deltas":
        state_field = _text(delta, "field", "field_path", "key")
        if not state_field:
            raise ValueError(f"state_deltas[{index}] requires field")
        _add_fact(facts, "state", f"{entity}|{state_field}", _transition(delta), source)
        location = _text(delta, "location", "from_location", "to_location")
        if state_field.casefold() in {"location", "地点", "current_location"} or location:
            _add_fact(facts, "location", entity, {"field": state_field, **_transition(delta)}, source)
        time_value = _text(delta, "time_anchor", "timestamp", "date", "time", "from_time", "to_time")
        if state_field.casefold() in {"time", "timeline", "time_anchor", "时间", "时间线"} or time_value:
            _add_fact(facts, "timeline", f"{entity}|{state_field}", {"field": state_field, **_transition(delta)}, source)
    else:
        value = {key: value for key, value in delta.items() if key not in {"source"}}
        _add_fact(facts, "entity", entity, value, source)
        from_entity = _text(delta, "from_entity")
        to_entity = _text(delta, "to_entity")
        relation = _text(delta, "relationship_type", "relation_type", "type")
        if from_entity and to_entity and relation:
            _add_fact(facts, "relationship", f"{from_entity}|{to_entity}", value, source)


def _add_fact(facts: dict[str, dict[str, dict[str, Any]]], category: str, key: str, value: Any, source: dict[str, Any]) -> None:
    category_facts = facts.setdefault(category, {})
    row = category_facts.setdefault(key, {"value": [], "source": []})
    canonical_value = _canonical(value)
    if canonical_value not in row["value"]:
        row["value"].append(canonical_value)
        row["value"].sort(key=_canonical_text)
    source_value = _canonical(source)
    if source_value not in row["source"]:
        row["source"].append(source_value)
        row["source"].sort(key=_canonical_text)


def _transition(item: dict[str, Any]) -> dict[str, Any]:
    transition = _optional_transition(item)
    if transition is None:
        raise ValueError("fact transition requires old/new or value")
    return transition


def _optional_transition(item: dict[str, Any]) -> dict[str, Any] | None:
    old = _first(item, "old", "from", "old_value", "previous_state", "from_location", "from_time")
    new = _first(item, "new", "to", "new_value", "new_state", "to_location", "to_time", "value")
    if old is None and new is None:
        return None
    return {"before": _canonical(old), "after": _canonical(new)}


def _first(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in item:
            return item[key]
    return None


def _text(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    if isinstance(value, tuple):
        return [_canonical(item) for item in value]
    return value


def _canonical_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _snapshot_facts(snapshot: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != FACT_SNAPSHOT_VERSION:
        raise ValueError("invalid fact snapshot schema")
    facts = snapshot.get("facts")
    if not isinstance(facts, dict):
        raise ValueError("fact snapshot facts must be an object")
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for category, rows in facts.items():
        if not isinstance(category, str) or not isinstance(rows, dict):
            raise ValueError("fact snapshot categories must be objects")
        result[category] = {}
        for key, row in rows.items():
            if not isinstance(key, str) or not isinstance(row, dict) or "value" not in row:
                raise ValueError("fact snapshot contains malformed fact")
            result[category][key] = {"value": _canonical(row["value"]), "source": _canonical(row.get("source", []))}
    return result


def _diff_item(category: str, key: str, before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "category": category,
        "key": key,
        "before": None if before is None else before.get("value"),
        "after": None if after is None else after.get("value"),
        "source": {
            "before": None if before is None else before.get("source", []),
            "after": None if after is None else after.get("source", []),
        },
    }
