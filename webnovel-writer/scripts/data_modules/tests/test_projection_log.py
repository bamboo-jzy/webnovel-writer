#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
from pathlib import Path

import pytest


def _ensure_scripts_on_path() -> None:
    scripts_dir = Path(__file__).resolve().parents[2]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


_ensure_scripts_on_path()

from .test_chapter_reloading import install_projection_fixture

from data_modules.chapter_commit_service import ChapterCommitService  # noqa: E402
from data_modules.projection_log import (  # noqa: E402
    append_projection_run,
    latest_projection_run,
    projection_log_path,
    projection_run_pending,
    projection_run_failed,
    projection_status_from_run,
    read_projection_runs,
    ProjectionLogCorruptionError,
)


def test_projection_log_appends_and_reads_jsonl(tmp_path):
    payload = {
        "meta": {"chapter": 3, "status": "accepted"},
        "projection_status": {"state": "done", "index": "skipped"},
    }

    record = append_projection_run(
        tmp_path,
        payload,
        {"state": {"status": "done"}, "index": {"status": "skipped"}},
    )

    assert projection_log_path(tmp_path).is_file()
    assert record["status"] == "done"
    assert read_projection_runs(tmp_path, chapter=3)[0]["run_id"] == record["run_id"]
    assert latest_projection_run(tmp_path, chapter=3)["commit_hash"] == record["commit_hash"]


def test_projection_status_from_run_prefers_writer_statuses(tmp_path):
    payload = {
        "meta": {"chapter": 3, "status": "accepted"},
        "projection_status": {"state": "done", "vector": "done"},
    }

    record = append_projection_run(
        tmp_path,
        payload,
        {"vector": {"status": "failed:timeout", "error": "timeout"}},
    )

    assert projection_status_from_run(record) == {"vector": "failed:timeout"}
    assert projection_run_failed(record) is True


def test_projection_log_reports_bad_chapter_even_when_filtering(tmp_path):
    path = projection_log_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                '{"chapter":"bad","writers":{"state":{"status":"done"}}}',
                '{"chapter":3,"writers":{"state":{"status":"done"}}}',
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ProjectionLogCorruptionError, match="chapter"):
        read_projection_runs(tmp_path, chapter=3)


@pytest.mark.parametrize("line", [
    '{"chapter":3,"writers":{"state":[]},"projection_status":{"state":"done"}}',
    '{"chapter":3,"writers":{"state":{}}}',
    '{"chapter":3,"projection_status":{"state":null}}',
])
def test_projection_log_rejects_malformed_writer_status(tmp_path, line):
    path = projection_log_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(line, encoding="utf-8")
    with pytest.raises(ProjectionLogCorruptionError):
        latest_projection_run(tmp_path, chapter=3)
    with pytest.raises(ProjectionLogCorruptionError):
        append_projection_run(tmp_path, {"meta": {"chapter": 3}}, {"state": {"status": "done"}})
    assert path.read_text(encoding="utf-8") == line


def test_projection_log_reports_truncated_line(tmp_path):
    path = projection_log_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"chapter":3,"status":"done"}\n{"chapter":4', encoding="utf-8")

    try:
        read_projection_runs(tmp_path)
    except ProjectionLogCorruptionError as exc:
        assert ":2:" in str(exc)
    else:
        raise AssertionError("truncated projection log line must be reported")


def test_projection_run_pending_detects_overall_and_writer_pending():
    assert projection_run_pending({"status": "pending", "writers": {}}) is True
    assert projection_run_pending({"writers": {"state": {"status": "pending"}}}) is True


def test_chapter_commit_service_writes_projection_log(tmp_path):
    (tmp_path / ".webnovel").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")

    service = ChapterCommitService(tmp_path)
    payload = service.build_commit(
        chapter=7,
        review_result={"blocking_count": 1},
        fulfillment_result={
            "planned_nodes": ["进入坊市"],
            "covered_nodes": ["进入坊市"],
            "missed_nodes": [],
            "extra_nodes": [],
        },
        disambiguation_result={"pending": []},
        extraction_result={"state_deltas": [], "entity_deltas": [], "accepted_events": []},
    )

    install_projection_fixture(tmp_path, payload)
    service.apply_projections(payload)

    runs = read_projection_runs(tmp_path, chapter=7)
    assert len(runs) == 1
    assert runs[0]["commit_status"] == "rejected"
    assert runs[0]["writers"]["state"]["status"] == "done"
    assert runs[0]["projection_status"]["state"] == "done"


def test_chapter_commit_service_marks_vector_store_zero_as_failed(monkeypatch, tmp_path):
    # 凭证配好、存储环节失败 → 必须报 failed（不能因为加了降级分支就把真故障也吞成 skipped）。
    monkeypatch.setenv("EMBED_API_KEY", "sk-test-key")
    (tmp_path / ".webnovel").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "data_modules.vector_projection_writer.VectorProjectionWriter._store_chunks",
        lambda self, chunks: 0,
    )

    service = ChapterCommitService(tmp_path)
    payload = service.build_commit(
        chapter=8,
        review_result={"blocking_count": 0},
        fulfillment_result={
            "planned_nodes": ["突破"],
            "covered_nodes": ["突破"],
            "missed_nodes": [],
            "extra_nodes": [],
        },
        disambiguation_result={"pending": []},
        extraction_result={
            "state_deltas": [],
            "entity_deltas": [],
            "accepted_events": [
                {
                    "event_id": "evt-breakthrough",
                    "event_type": "power_breakthrough",
                    "chapter": 8,
                    "subject": "韩立",
                    "payload": {"field": "realm", "to": "筑基初期"},
                }
            ],
        },
    )

    install_projection_fixture(tmp_path, payload)
    projected = service.apply_projections(payload)

    assert projected["projection_status"]["vector"] == "failed:store_failed"
    latest = latest_projection_run(tmp_path, chapter=8)
    assert latest is not None
    assert projection_run_failed(latest) is True
    assert latest["writers"]["vector"]["status"] == "failed:store_failed"


def test_missing_embedding_credentials_degrades_vector_instead_of_blocking(tmp_path, monkeypatch):
    """D2：没有可用 embedding 时 vector 降级为 skipped 并放行，而不是把整章卡死。

    只改 vector 的判定，其余四项照常投影 —— 降级必须是局部行为。
    """
    monkeypatch.delenv("EMBED_API_KEY", raising=False)
    monkeypatch.delenv("VECTOR_PROJECTION_ENABLED", raising=False)
    (tmp_path / ".webnovel").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")

    service = ChapterCommitService(tmp_path)
    payload = service.build_commit(
        chapter=9,
        review_result={"blocking_count": 0},
        fulfillment_result={
            "planned_nodes": ["突破"],
            "covered_nodes": ["突破"],
            "missed_nodes": [],
            "extra_nodes": [],
        },
        disambiguation_result={"pending": []},
        extraction_result={
            "state_deltas": [],
            "entity_deltas": [],
            "accepted_events": [
                {
                    "event_id": "evt-breakthrough-9",
                    "event_type": "power_breakthrough",
                    "chapter": 9,
                    "subject": "韩立",
                    "payload": {"field": "realm", "to": "筑基中期"},
                }
            ],
        },
    )

    install_projection_fixture(tmp_path, payload)
    projected = service.apply_projections(payload)

    assert projected["projection_status"]["vector"] == "skipped"
    # 其它投影项不受影响，该 done 的仍然是 done。
    assert projected["projection_status"]["state"] == "done"
    assert projected["projection_status"]["index"] == "done"

    latest = latest_projection_run(tmp_path, chapter=9)
    assert latest is not None
    assert projection_run_failed(latest) is False
    assert latest["writers"]["vector"]["status"] == "skipped"
    # 降级原因必须留在日志里，供 project-status / doctor 提醒作者（不能静默）。
    assert latest["writers"]["vector"]["result"]["reason"] == "embedding_unavailable"
    assert latest["writers"]["vector"]["result"]["detail"] == "embedding_not_configured"
