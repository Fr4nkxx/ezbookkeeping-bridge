"""Load per-user ez credentials without sharing clients or notifications between users."""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import Settings


class BridgeUser(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    bridge_token: str = Field(min_length=16)
    ez_api_token: str = Field(min_length=1)
    ez_base_url: str | None = None
    bark_push_url: str | None = None
    default_cash_account_name: str | None = None
    initialize_from_default: bool = False


class BridgeUsersFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    users: list[BridgeUser] = Field(min_length=1)


def load_user_settings(settings: Settings) -> list[tuple[str, str, Settings]]:
    """Return (user id, Bridge token, isolated settings) for each configured user."""
    if not settings.bridge_users_file:
        if not settings.bridge_api_token or not settings.ez_api_token:
            raise ValueError("单用户模式需要 BRIDGE_API_TOKEN 和 EZ_API_TOKEN")
        return [("default", settings.bridge_api_token, settings)]

    path = Path(settings.bridge_users_file)
    try:
        config = BridgeUsersFile.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValidationError):
        raise ValueError(f"无法读取或解析用户配置文件 {path}") from None

    ids: set[str] = set()
    tokens: set[str] = set()
    users: list[tuple[str, str, Settings]] = []
    for user in config.users:
        if user.id in ids or user.bridge_token in tokens:
            raise ValueError("用户配置中的 id 和 bridge_token 必须分别唯一")
        ids.add(user.id)
        tokens.add(user.bridge_token)
        ez_base_url = user.ez_base_url or settings.ez_base_url
        if not ez_base_url:
            raise ValueError(f"用户 {user.id} 缺少 ez_base_url 或 EZ_BASE_URL")
        user_settings = settings.model_copy(update={
            "ez_base_url": ez_base_url,
            "ez_api_token": user.ez_api_token,
            "bridge_api_token": user.bridge_token,
            "bark_push_url": user.bark_push_url,
            "default_cash_account_name": (
                user.default_cash_account_name or settings.default_cash_account_name
            ),
        })
        users.append((user.id, user.bridge_token, user_settings))
    return users
