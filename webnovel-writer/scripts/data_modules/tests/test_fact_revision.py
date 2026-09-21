#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""已 accepted 的 commit 改写事实的显式通道（D3）。

背景：`persist_commit` 原本在"事实有变"或"上一轮投影未全绿"时一律抛
`revision_projection_unsafe`，理由是增量投影撤不掉旧事实。但这样一来"投影失败 →
想修数据重提交 → 被拒"就成了死循环，作者也没有任何正当途径修正已提交的数据。

现在改成：默认仍然拒绝，但错误信息给出逃生口；作者显式加
`--allow-fact-revision` 后，投影阶段先撤回该章派生读模型（index 行 / 向量分块 /
story_events 镜像）再整章重建。
"""

import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest

from data_modules.chapter_commit_service import ChapterCommitService
from data_modules.chapter_reloading import (
    commit_identity,
    commit_path,
    read_object,
    validate_chapter_body,
)
from data_modules.event_log_store import EventLogStore
from data_modules.projections import retry_projection
from data_modules.vector_projection_writer import VectorProjectionWriter
from .test_chapter_reloading import accepted_commit, prepare_inputs


def _reload_and_validate(root: Path, payloads: dict) -> dict:
    inputs = prepare_inputs(root, payloads=payloads)
    assert validate_chapter_body(root, 1)["ok"]
    return inputs


def _payloads(location: str, scene: str, summary: str) -> dict:
    return {
        "review_result": {"blocking_count": 0},
        "fulfillment_result": {
            "planned_nodes": [],
            "covered_nodes": [],
            "missed_nodes": [],
            "extra_nodes": [],
        },
        "disambiguation_result": {"pending": []},
        "extraction_result": {
            "accepted_events": [],
            "state_deltas": [
                {"entity_id": "chen_yan", "field": "location.current", "old": "", "new": location}
            ],
            "entity_deltas": [],
            "entities_appeared": [
                {"id": "chen_yan", "type": "角色", "mentions": ["陈砚"], "confidence": 0.9}
            ],
            "scenes": [{"index": 1, "location": location, "summary": scene}],
            "summary_text": summary,
        },
    }


def _query(root: Path, sql: str, args: tuple = ()) -> list[tuple]:
    conn = sqlite3.connect(str(root / ".webnovel" / "index.db"))
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def _revise_facts(root: Path, *, allow: bool, reason: str = "", payloads: dict | None = None):
    """改写正文 + 事实产物，然后按给定授权重新提交。"""
    (root / "正文/第0001章.md").write_text("主角换了地方。", encoding="utf-8")
    inputs = _reload_and_validate(root, payloads or _payloads("十一楼走廊", "新场景", "新摘要"))
    service = ChapterCommitService(root)
    payload = service.build_commit(chapter=1, **inputs)
    service.persist_commit(
        payload,
        expected_previous=commit_identity(read_object(commit_path(root, 1))),
        allow_fact_revision=allow,
        revision_reason=reason,
    )
    return service, payload


def test_fact_revision_guard_names_the_escape_hatch(tmp_path):
    """默认仍然拒绝，但错误信息必须给出 identity 与授权开关。"""
    old = accepted_commit(tmp_path, payloads=_payloads("1102 餐桌", "旧场景", "旧摘要"))
    old_bytes = commit_path(tmp_path, 1).read_bytes()
    (tmp_path / "正文/第0001章.md").write_text("主角换了地方。", encoding="utf-8")
    inputs = _reload_and_validate(tmp_path, _payloads("十一楼走廊", "新场景", "新摘要"))
    service = ChapterCommitService(tmp_path)

    with pytest.raises(ValueError) as exc:
        service.persist_commit(
            service.build_commit(chapter=1, **inputs),
            expected_previous=commit_identity(old),
        )

    message = str(exc.value)
    assert message.startswith("revision_projection_unsafe")
    assert "--allow-fact-revision" in message
    assert commit_identity(old) in message
    # 被拒时不能落盘、也不能留下历史副本
    assert commit_path(tmp_path, 1).read_bytes() == old_bytes
    assert not list((commit_path(tmp_path, 1).parent / "history").rglob("*.json"))


def test_allow_fact_revision_marks_retract_and_rebuilds_index(tmp_path):
    """授权后：事实被替换，且该章读模型按新事实重建（旧行不残留）。"""
    old = accepted_commit(tmp_path, payloads=_payloads("1102 餐桌", "旧场景", "旧摘要"))
    assert _query(tmp_path, "SELECT summary FROM scenes WHERE chapter = 1") == [("旧场景",)]
    assert [row[0] for row in _query(tmp_path, "SELECT new_value FROM state_changes WHERE chapter = 1")] == [
        "1102 餐桌"
    ]

    service, payload = _revise_facts(tmp_path, allow=True, reason="事实写错，整章重建")
    on_disk = read_object(commit_path(tmp_path, 1))

    assert on_disk["meta"]["status"] == "accepted"
    assert on_disk["meta"]["revision_number"] == 2
    assert on_disk["provenance"]["retract_required"] is True
    assert on_disk["provenance"]["retract_reason"] == "事实写错，整章重建"
    assert on_disk["provenance"]["retract_previous_identity"] == commit_identity(old)
    assert not payload["provenance"].get("projection_reuse")
    # 旧版本进历史，可追溯
    assert read_object(tmp_path / on_disk["provenance"]["previous_commit"]) == old

    projected = service.apply_projections(payload)

    # index.db：场景与状态变化都只剩新事实（state_changes 是追加表，不撤回就会留两行）
    assert _query(tmp_path, "SELECT summary FROM scenes WHERE chapter = 1") == [("新场景",)]
    assert [row[0] for row in _query(tmp_path, "SELECT new_value FROM state_changes WHERE chapter = 1")] == [
        "十一楼走廊"
    ]
    assert _query(tmp_path, "SELECT summary FROM chapters WHERE chapter = 1") == [("新摘要",)]
    assert projected["projection_status"]["index"] == "done"
    assert projected["projection_status"]["state"] == "done"


def test_retract_requires_marker_unless_forced(tmp_path):
    """撤回是有门槛的：无标记不动，force=True 才动手。"""
    accepted_commit(tmp_path, payloads=_payloads("1102 餐桌", "旧场景", "旧摘要"))
    service = ChapterCommitService(tmp_path)
    payload = read_object(commit_path(tmp_path, 1))

    assert service.retract_chapter_read_models(payload) == {}
    assert _query(tmp_path, "SELECT count(*) FROM scenes WHERE chapter = 1") == [(1,)]

    forced = service.retract_chapter_read_models(payload, force=True)
    assert forced["index"]["status"] == "retracted"
    assert forced["vector"]["status"] == "retracted"
    assert _query(tmp_path, "SELECT count(*) FROM scenes WHERE chapter = 1") == [(0,)]
    assert _query(tmp_path, "SELECT count(*) FROM state_changes WHERE chapter = 1") == [(0,)]


def test_retry_with_retract_repairs_poisoned_rows(tmp_path):
    """`projections retry --retract`：被上一版事实污染的行能修回来。"""
    accepted_commit(tmp_path, payloads=_payloads("1102 餐桌", "旧场景", "旧摘要"))
    db = tmp_path / ".webnovel" / "index.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO state_changes(entity_id, field, old_value, new_value, reason, chapter)"
            " VALUES ('chen_yan', 'location.current', 'x', '被污染的值', 'legacy', 1)"
        )
        conn.commit()
    finally:
        conn.close()

    # 不带 --retract 的 retry 是纯重放，追加表里的污染行会被留下
    assert retry_projection(tmp_path, chapter=1)["ok"] is True
    assert "被污染的值" in [
        row[0] for row in _query(tmp_path, "SELECT new_value FROM state_changes WHERE chapter = 1")
    ]

    report = retry_projection(tmp_path, chapter=1, force_retract=True)

    assert report["ok"] is True
    assert report["retractions"]["index"]["status"] == "retracted"
    assert report["retractions"]["event_mirror"]["status"] == "retracted"
    assert [row[0] for row in _query(tmp_path, "SELECT new_value FROM state_changes WHERE chapter = 1")] == [
        "1102 餐桌"
    ]


def test_retract_results_are_not_written_into_commit(tmp_path):
    """撤回结果只进 projection_log：写进 commit 会破坏投影阶段的重写校验。"""
    accepted_commit(tmp_path, payloads=_payloads("1102 餐桌", "旧场景", "旧摘要"))
    _revise_facts(tmp_path, allow=True)
    on_disk = read_object(commit_path(tmp_path, 1))

    for key in ("retractions", "retracted", "retract_results"):
        assert key not in on_disk
    assert "retractions" not in (on_disk.get("projection_status") or {})


def test_event_mirror_retract_only_touches_chapter_rows(tmp_path):
    store = EventLogStore(tmp_path)
    for chapter in (1, 2):
        store.mirror_events_only(
            chapter,
            [
                {
                    "event_id": f"evt-ch{chapter}-001",
                    "chapter": chapter,
                    "event_type": "world_rule_revealed",
                    "subject": "resident_notice",
                    "payload": {"rule_content": f"规则{chapter}"},
                }
            ],
        )

    assert store.retract_chapter(1) == 1
    rows = _query(tmp_path, "SELECT chapter, event_id FROM story_events ORDER BY chapter")
    assert rows == [(2, "evt-ch2-001")]
    # 表不存在/空章号都不能炸
    assert store.retract_chapter(0) == 0


def test_vector_retract_drops_chapter_chunks_only(tmp_path):
    db = tmp_path / ".webnovel" / "vectors.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "CREATE TABLE vectors (chunk_id TEXT PRIMARY KEY, chapter INTEGER, scene_index INTEGER,"
            " content TEXT, embedding BLOB, parent_chunk_id TEXT, chunk_type TEXT, source_file TEXT)"
        )
        conn.execute("CREATE TABLE bm25_index (term TEXT, chunk_id TEXT, PRIMARY KEY (term, chunk_id))")
        conn.execute("CREATE TABLE doc_stats (chunk_id TEXT PRIMARY KEY, doc_length INTEGER)")
        for chapter in (1, 2):
            chunk_id = f"ch{chapter:04d}_summary"
            conn.execute(
                "INSERT INTO vectors(chunk_id, chapter, content, chunk_type) VALUES (?, ?, ?, 'summary')",
                (chunk_id, chapter, "内容"),
            )
            conn.execute("INSERT INTO bm25_index(term, chunk_id) VALUES (?, ?)", ("词", chunk_id))
            conn.execute("INSERT INTO doc_stats(chunk_id, doc_length) VALUES (?, 2)", (chunk_id,))
        conn.commit()
    finally:
        conn.close()

    result = VectorProjectionWriter(tmp_path).retract(1)

    assert result["retracted"] == 1
    conn = sqlite3.connect(str(db))
    try:
        assert conn.execute("SELECT chapter FROM vectors ORDER BY chapter").fetchall() == [(2,)]
        assert conn.execute("SELECT chunk_id FROM bm25_index").fetchall() == [("ch0002_summary",)]
        assert conn.execute("SELECT chunk_id FROM doc_stats").fetchall() == [("ch0002_summary",)]
    finally:
        conn.close()

    # 库不存在时降级为 no_vectors_db，不抛异常
    (tmp_path / ".webnovel" / "vectors.db").unlink()
    assert VectorProjectionWriter(tmp_path).retract(1)["reason"] == "no_vectors_db"


def test_unchanged_facts_still_reuse_projection(tmp_path):
    """回归：授权开关不能把"事实没变"的常规修订也变成整章重建。"""
    accepted_commit(tmp_path, payloads=_payloads("1102 餐桌", "旧场景", "旧摘要"))
    (tmp_path / "正文/第0001章.md").write_text("正文措辞调整，不新增事实。", encoding="utf-8")
    inputs = _reload_and_validate(tmp_path, deepcopy(_payloads("1102 餐桌", "旧场景", "旧摘要")))
    service = ChapterCommitService(tmp_path)
    payload = service.build_commit(chapter=1, **inputs)

    service.persist_commit(
        payload,
        expected_previous=commit_identity(read_object(commit_path(tmp_path, 1))),
        allow_fact_revision=True,
    )

    assert payload["provenance"].get("projection_reuse") is True
    assert not payload["provenance"].get("retract_required")
    assert service.retract_chapter_read_models(payload) == {}
