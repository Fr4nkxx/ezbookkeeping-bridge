"""Flagged initialization never writes to the source and clears flags only on success."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.ez import catalog_initialization


def _settings(path) -> Settings:
    return Settings(_env_file=None, **{
        "EZ_BASE_URL": "https://ez.example.com",
        "BRIDGE_USERS_FILE": str(path),
        "LLM_BASE_URL": "https://llm.example.com/v1",
        "LLM_API_KEY": "test-key",
        "LLM_MODEL": "test-model",
    })


def _write_users(path, *, same_token=False, flagged=True) -> None:
    path.write_text(json.dumps({"users": [
        {"id": "default", "bridge_token": "default-bridge-token-123", "ez_api_token": "source-ez"},
        {"id": "person", "bridge_token": "person-bridge-token-456",
         "ez_api_token": "source-ez" if same_token else "target-ez",
         "initialize_from_default": flagged},
    ]}), encoding="utf-8")


def test_success_clears_flag_and_second_run_skips(monkeypatch, tmp_path) -> None:
    path = tmp_path / "users.json"
    _write_users(path)
    clients = {}

    def client(settings):
        token = settings.ez_api_token
        if token not in clients:
            clients[token] = type("FakeClient", (), {})()
            clients[token].list_accounts = AsyncMock(return_value=[])
            clients[token].list_categories = AsyncMock(return_value={})
            clients[token].delete_category = AsyncMock(return_value=True)
            clients[token].add_account = AsyncMock()
            clients[token].add_category = AsyncMock(return_value={"id": "new-parent"})
        return clients[token]

    monkeypatch.setattr(catalog_initialization, "EzClient", client)
    result = asyncio.run(catalog_initialization.initialize_marked_users(_settings(path), apply=True))
    assert list(result) == ["person"]
    assert json.loads(path.read_text())["users"][1]["initialize_from_default"] is False
    assert asyncio.run(catalog_initialization.initialize_marked_users(_settings(path), apply=True)) == {}
    clients["source-ez"].delete_category.assert_not_awaited()


def test_same_token_rejected_before_any_remote_request(monkeypatch, tmp_path) -> None:
    path = tmp_path / "users.json"
    _write_users(path, same_token=True)
    remote = AsyncMock()
    monkeypatch.setattr(catalog_initialization, "EzClient", lambda settings: remote)

    with pytest.raises(ValueError, match="同一 ez 令牌"):
        asyncio.run(catalog_initialization.initialize_marked_users(_settings(path), apply=True))
    remote.list_categories.assert_not_awaited()
    assert json.loads(path.read_text())["users"][1]["initialize_from_default"] is True


def test_delete_failure_preserves_flag_and_never_writes_source(monkeypatch, tmp_path) -> None:
    path = tmp_path / "users.json"
    _write_users(path)
    clients = {}

    def client(settings):
        token = settings.ez_api_token
        remote = clients.setdefault(token, type("FakeClient", (), {})())
        remote.list_accounts = AsyncMock(return_value=[])
        remote.list_categories = AsyncMock(return_value=(
            {} if token == "source-ez" else
            {"2": [{"id": "old", "name": "旧分类", "subCategories": []}]}
        ))
        remote.delete_category = AsyncMock(side_effect=RuntimeError("分类正在使用"))
        remote.add_account = AsyncMock()
        remote.add_category = AsyncMock()
        return remote

    monkeypatch.setattr(catalog_initialization, "EzClient", client)
    with pytest.raises(RuntimeError, match="分类正在使用"):
        asyncio.run(catalog_initialization.initialize_marked_users(_settings(path), apply=True))
    assert json.loads(path.read_text())["users"][1]["initialize_from_default"] is True
    clients["source-ez"].delete_category.assert_not_awaited()
    clients["target-ez"].add_category.assert_not_awaited()
