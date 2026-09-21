#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Config tests
"""

import os

from data_modules import config as config_module
from data_modules.config import DataModulesConfig, get_config, set_project_root


def test_config_paths_and_defaults(tmp_path):
    cfg = DataModulesConfig.from_project_root(tmp_path)
    assert cfg.project_root == tmp_path
    assert cfg.webnovel_dir.name == ".webnovel"
    assert cfg.state_file.name == "state.json"
    assert cfg.scratchpad_file.name == "memory_scratchpad.json"
    assert cfg.index_db.name == "index.db"
    assert cfg.rag_db.name == "rag.db"
    assert cfg.vector_db.name == "vectors.db"

    cfg.ensure_dirs()
    assert cfg.webnovel_dir.exists()


def test_get_config_and_set_project_root(tmp_path):
    set_project_root(tmp_path)
    cfg = get_config()
    assert cfg.project_root == tmp_path


def test_load_dotenv(monkeypatch, tmp_path):
    # prepare .env
    env_path = tmp_path / ".env"
    env_path.write_text("EMBED_BASE_URL=https://example.com\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("EMBED_BASE_URL", raising=False)

    # call loader explicitly
    config_module._load_dotenv()
    assert os.environ.get("EMBED_BASE_URL") == "https://example.com"


def test_config_default_context_template_weights_dynamic_is_available(tmp_path):
    cfg = DataModulesConfig.from_project_root(tmp_path)
    dynamic = cfg.context_template_weights_dynamic

    assert isinstance(dynamic, dict)
    assert "early" in dynamic
    assert "mid" in dynamic
    assert "late" in dynamic
    assert "plot" in dynamic["early"]


def test_config_dynamic_template_weights_are_independent_instances(tmp_path):
    cfg1 = DataModulesConfig.from_project_root(tmp_path)
    cfg2 = DataModulesConfig.from_project_root(tmp_path)

    cfg1.context_template_weights_dynamic["early"]["plot"]["core"] = 0.77

    assert cfg2.context_template_weights_dynamic["early"]["plot"]["core"] != 0.77


def test_vector_projection_switch_reads_env(monkeypatch, tmp_path):
    """向量投影总开关：默认开，显式关得掉，写错不炸（回退默认）。"""
    for raw in ("0", "false", "FALSE", "no", "off"):
        monkeypatch.setenv("VECTOR_PROJECTION_ENABLED", raw)
        assert DataModulesConfig.from_project_root(tmp_path).vector_projection_enabled is False

    for raw in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("VECTOR_PROJECTION_ENABLED", raw)
        assert DataModulesConfig.from_project_root(tmp_path).vector_projection_enabled is True

    monkeypatch.setenv("VECTOR_PROJECTION_ENABLED", "不是布尔值")
    assert DataModulesConfig.from_project_root(tmp_path).vector_projection_enabled is True

    monkeypatch.delenv("VECTOR_PROJECTION_ENABLED", raising=False)
    assert DataModulesConfig.from_project_root(tmp_path).vector_projection_enabled is True


def test_embedding_config_gap_detects_missing_credentials(monkeypatch, tmp_path):
    monkeypatch.delenv("EMBED_API_KEY", raising=False)
    assert (
        DataModulesConfig.from_project_root(tmp_path).embedding_config_gap()
        == "embedding_not_configured"
    )

    monkeypatch.setenv("EMBED_API_KEY", "sk-test-key")
    assert DataModulesConfig.from_project_root(tmp_path).embedding_config_gap() == ""

    # 只有空白字符等同于没配 —— 否则会把注定 401 的配置当成"配好了"。
    monkeypatch.setenv("EMBED_API_KEY", "   ")
    assert (
        DataModulesConfig.from_project_root(tmp_path).embedding_config_gap()
        == "embedding_not_configured"
    )
