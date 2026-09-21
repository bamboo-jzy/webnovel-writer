#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VectorProjectionWriter 单元测试。"""
import pytest

from data_modules.vector_projection_writer import VectorProjectionWriter


def test_event_to_text_formats_power_breakthrough():
    writer = VectorProjectionWriter.__new__(VectorProjectionWriter)
    event = {
        "event_type": "power_breakthrough",
        "chapter": 47,
        "subject": "韩立",
        "payload": {"field": "realm", "new": "筑基初期"},
    }
    text = writer._event_to_text(event)
    assert "第47章" in text
    assert "韩立" in text
    assert "筑基初期" in text


def test_delta_to_text_formats_relationship():
    writer = VectorProjectionWriter.__new__(VectorProjectionWriter)
    delta = {
        "from_entity": "韩立",
        "to_entity": "陈巧倩",
        "relationship_type": "合作",
        "chapter": 47,
    }
    text = writer._delta_to_text(delta)
    assert "第47章" in text
    assert "韩立" in text
    assert "陈巧倩" in text
    assert "合作" in text


def test_collect_chunks_from_commit():
    writer = VectorProjectionWriter.__new__(VectorProjectionWriter)
    payload = {
        "meta": {"chapter": 47, "status": "accepted"},
        "accepted_events": [
            {
                "event_type": "power_breakthrough",
                "chapter": 47,
                "subject": "韩立",
                "payload": {"field": "realm", "new": "筑基初期"},
            },
        ],
        "entity_deltas": [
            {
                "from_entity": "韩立",
                "to_entity": "陈巧倩",
                "relationship_type": "合作",
                "chapter": 47,
            },
        ],
    }
    chunks = writer._collect_chunks(payload)
    assert len(chunks) == 2
    assert chunks[0]["chunk_type"] == "event"
    assert chunks[1]["chunk_type"] == "entity_delta"
    assert chunks[0]["chunk_id"] != chunks[1]["chunk_id"]


def test_collect_chunks_assigns_unique_ids_for_same_chapter_events():
    writer = VectorProjectionWriter.__new__(VectorProjectionWriter)
    payload = {
        "meta": {"chapter": 47, "status": "accepted"},
        "accepted_events": [
            {
                "event_type": "character_state_changed",
                "chapter": 47,
                "subject": "韩立",
                "payload": {"field": "状态", "new": "警觉"},
            },
            {
                "event_type": "character_state_changed",
                "chapter": 47,
                "subject": "陈巧倩",
                "payload": {"field": "状态", "new": "迟疑"},
            },
        ],
        "entity_deltas": [],
    }

    chunks = writer._collect_chunks(payload)

    assert len(chunks) == 2
    assert len({chunk["chunk_id"] for chunk in chunks}) == 2
    assert all(chunk["scene_index"] == 0 for chunk in chunks)


def test_collect_chunks_keeps_event_id_stable_when_order_changes():
    writer = VectorProjectionWriter.__new__(VectorProjectionWriter)
    event_a = {
        "event_id": "evt-a",
        "event_type": "character_state_changed",
        "chapter": 47,
        "subject": "韩立",
        "payload": {"field": "状态", "new": "警觉"},
    }
    event_b = {
        "event_id": "evt-b",
        "event_type": "character_state_changed",
        "chapter": 47,
        "subject": "陈巧倩",
        "payload": {"field": "状态", "new": "迟疑"},
    }

    first = writer._collect_chunks(
        {"meta": {"chapter": 47}, "accepted_events": [event_a, event_b], "entity_deltas": []}
    )
    second = writer._collect_chunks(
        {"meta": {"chapter": 47}, "accepted_events": [event_b, event_a], "entity_deltas": []}
    )

    first_ids = {chunk["content"]: chunk["chunk_id"] for chunk in first}
    second_ids = {chunk["content"]: chunk["chunk_id"] for chunk in second}
    assert first_ids == second_ids


def test_rejected_commit_returns_not_applied():
    writer = VectorProjectionWriter.__new__(VectorProjectionWriter)
    writer.project_root = None
    result = writer.apply({"meta": {"status": "rejected", "chapter": 1}})
    assert result["applied"] is False


def test_store_zero_for_required_chunks_is_error(monkeypatch, tmp_path):
    # 前提：凭证是配好的，失败来自存储环节本身 —— 这样才能测到"真失败"路径。
    # 未配凭证属于 embedding_unavailable 降级，另有专门用例覆盖。
    monkeypatch.setenv("EMBED_API_KEY", "sk-test-key")
    writer = VectorProjectionWriter(tmp_path)
    monkeypatch.setattr(writer, "_store_chunks", lambda chunks: 0)

    result = writer.apply(
        {
            "meta": {"status": "accepted", "chapter": 47},
            "summary_text": "韩立在坊市发现丹方线索。",
            "accepted_events": [],
            "entity_deltas": [],
        }
    )

    assert result["applied"] is False
    assert result["reason"] == "error:store_failed"


def test_collect_chunks_includes_summary_and_scenes():
    writer = VectorProjectionWriter.__new__(VectorProjectionWriter)
    payload = {
        "meta": {"chapter": 47, "status": "accepted"},
        "summary_text": "韩立在坊市发现丹方线索。",
        "scenes": [
            {"index": 1, "summary": "韩立入坊市观察摊位", "location": "坊市"},
            {"scene_index": 2, "content": "陈巧倩暗中提醒韩立有人跟踪。"},
        ],
        "accepted_events": [],
        "entity_deltas": [],
    }

    chunks = writer._collect_chunks(payload)
    by_type = {}
    for chunk in chunks:
        by_type.setdefault(chunk["chunk_type"], []).append(chunk)

    assert by_type["summary"][0]["chunk_id"] == "ch0047_summary"
    assert by_type["summary"][0]["parent_chunk_id"] is None
    assert by_type["scene"][0]["parent_chunk_id"] == "ch0047_summary"
    assert by_type["scene"][0]["content"].startswith("坊市：")
    assert any(chunk["scene_index"] == 2 for chunk in by_type["scene"])


@pytest.mark.asyncio
async def test_run_store_coro_works_inside_active_event_loop():
    writer = VectorProjectionWriter.__new__(VectorProjectionWriter)

    async def store():
        return 3

    assert writer._run_store_coro(store()) == 3


def _accepted_payload() -> dict:
    return {
        "meta": {"status": "accepted", "chapter": 47},
        "summary_text": "韩立在坊市发现丹方线索。",
        "accepted_events": [],
        "entity_deltas": [],
    }


def test_missing_credentials_degrades_without_touching_store(monkeypatch, tmp_path):
    """没配凭证 → 直接降级为 embedding_unavailable，连网络请求都不该发起。"""
    monkeypatch.delenv("EMBED_API_KEY", raising=False)

    writer = VectorProjectionWriter(tmp_path)
    calls: list[int] = []
    monkeypatch.setattr(writer, "_store_chunks", lambda chunks: calls.append(1) or 0)

    result = writer.apply(_accepted_payload())

    assert result["applied"] is False
    assert result["reason"] == "embedding_unavailable"
    assert result["detail"] == "embedding_not_configured"
    assert calls == []


def test_vector_projection_switch_disables_projection(monkeypatch, tmp_path):
    """VECTOR_PROJECTION_ENABLED=0 → 主动关闭，判为可跳过而不是失败。"""
    monkeypatch.setenv("EMBED_API_KEY", "sk-test-key")
    monkeypatch.setenv("VECTOR_PROJECTION_ENABLED", "0")

    writer = VectorProjectionWriter(tmp_path)
    monkeypatch.setattr(writer, "_store_chunks", lambda chunks: 99)

    result = writer.apply(_accepted_payload())

    assert result["applied"] is False
    assert result["reason"] == "vector_projection_disabled"


def test_adapter_degraded_reason_turns_store_zero_into_degrade(monkeypatch, tmp_path):
    """凭证配了但端点 401：adapter 给出归因 → 降级，而不是报 failed:store_failed。"""
    monkeypatch.setenv("EMBED_API_KEY", "sk-test-key")
    monkeypatch.delenv("VECTOR_PROJECTION_ENABLED", raising=False)

    observed: dict = {}

    class _StubAdapter:
        degraded_mode_reason = "embedding_auth_failed"

        def __init__(self, config):
            observed["config"] = config

        async def store_chunks(self, chunks):
            return 0

    monkeypatch.setattr("data_modules.rag_adapter.RAGAdapter", _StubAdapter)

    writer = VectorProjectionWriter(tmp_path)
    result = writer.apply(_accepted_payload())

    assert writer._last_embedding_diagnosis == "embedding_auth_failed"
    assert result["reason"] == "embedding_unavailable"
    assert result["detail"] == "embedding_auth_failed"


def test_store_zero_without_diagnosis_stays_a_hard_failure(monkeypatch, tmp_path):
    """没有归因的 zero store 仍必须是 failed —— 降级分支不能吞掉真故障。"""
    monkeypatch.setenv("EMBED_API_KEY", "sk-test-key")
    monkeypatch.delenv("VECTOR_PROJECTION_ENABLED", raising=False)

    class _StubAdapter:
        degraded_mode_reason = None

        def __init__(self, config):
            pass

        async def store_chunks(self, chunks):
            return 0

    monkeypatch.setattr("data_modules.rag_adapter.RAGAdapter", _StubAdapter)

    writer = VectorProjectionWriter(tmp_path)
    result = writer.apply(_accepted_payload())

    assert writer._last_embedding_diagnosis == ""
    assert result["reason"] == "error:store_failed"
