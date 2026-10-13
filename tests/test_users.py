"""Multi-user token routing and settings isolation."""

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

from app.api.deps import verify_bridge_token
from app.core.config import Settings
from app.core.users import load_user_settings
from app.middleware.rate_limit import RateLimiter


def _settings(**updates: str) -> Settings:
    values = {
        "EZ_BASE_URL": "https://ez.example.com",
        "EZ_API_TOKEN": "legacy-ez",
        "BRIDGE_API_TOKEN": "legacy-bridge",
        "LLM_BASE_URL": "https://llm.example.com/v1",
        "LLM_API_KEY": "test-key",
        "LLM_MODEL": "test-model",
        "BARK_PUSH_URL": "https://bark.example.com/legacy",
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


def test_multi_user_settings_do_not_share_tokens_or_bark(tmp_path) -> None:
    path = tmp_path / "users.json"
    path.write_text(json.dumps({"users": [
        {"id": "alice", "bridge_token": "alice-bridge-token-123", "ez_api_token": "alice-ez",
         "bark_push_url": "https://bark.example.com/alice"},
        {"id": "bob", "bridge_token": "bob-bridge-token-456", "ez_api_token": "bob-ez"},
    ]}), encoding="utf-8")

    users = load_user_settings(_settings(BRIDGE_USERS_FILE=str(path)))

    assert [user_id for user_id, _, _ in users] == ["alice", "bob"]
    assert users[0][2].ez_api_token == "alice-ez"
    assert users[1][2].ez_api_token == "bob-ez"
    assert users[0][2].bark_push_url == "https://bark.example.com/alice"
    assert users[1][2].bark_push_url is None
    assert users[1][2].llm_api_key == "test-key"


def test_legacy_single_user_configuration_still_loads() -> None:
    users = load_user_settings(_settings())

    assert len(users) == 1
    assert users[0][0] == "default"
    assert users[0][1] == "legacy-bridge"
    assert users[0][2].ez_api_token == "legacy-ez"


def test_duplicate_bridge_tokens_are_rejected(tmp_path) -> None:
    path = tmp_path / "users.json"
    path.write_text(json.dumps({"users": [
        {"id": "alice", "bridge_token": "same-bridge-token-123", "ez_api_token": "alice-ez"},
        {"id": "bob", "bridge_token": "same-bridge-token-123", "ez_api_token": "bob-ez"},
    ]}), encoding="utf-8")

    with pytest.raises(ValueError, match="必须分别唯一"):
        load_user_settings(_settings(BRIDGE_USERS_FILE=str(path)))


def test_bridge_token_selects_only_its_own_container() -> None:
    app = FastAPI()
    alice = object()
    bob = object()
    app.state.user_runtimes = [
        SimpleNamespace(bridge_token="alice-bridge-token-123", container=alice,
                        rate_limiter=RateLimiter(10)),
        SimpleNamespace(bridge_token="bob-bridge-token-456", container=bob,
                        rate_limiter=RateLimiter(10)),
    ]
    request = Request({"type": "http", "app": app})

    assert verify_bridge_token(
        request, HTTPAuthorizationCredentials(scheme="Bearer", credentials="bob-bridge-token-456")
    ) is bob
    with pytest.raises(HTTPException) as exc_info:
        verify_bridge_token(
            request, HTTPAuthorizationCredentials(scheme="Bearer", credentials="legacy-bridge")
        )
    assert exc_info.value.status_code == 403
