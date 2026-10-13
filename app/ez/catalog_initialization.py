"""One-time, flag-driven catalog initialization for non-default users."""

import json
import logging
import os
import tempfile
from pathlib import Path

from app.core.config import Settings
from app.core.users import BridgeUsersFile, load_user_settings
from app.ez.catalog_copy import CopyReport, copy_catalog
from app.ez.client import EzClient

logger = logging.getLogger("uvicorn.error")


def _clear_flag(path: Path, user_id: str) -> None:
    """Persist completion only after the remote work succeeded."""
    data = json.loads(path.read_text(encoding="utf-8"))
    for item in data["users"]:
        if item["id"] == user_id:
            item["initialize_from_default"] = False
            break
    else:
        raise ValueError(f"写回标记时找不到用户：{user_id}")
    mode = path.stat().st_mode & 0o777
    fd, temp_name = tempfile.mkstemp(prefix=".users-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


async def initialize_marked_users(
    settings: Settings, *, target_id: str | None = None, apply: bool = False,
) -> dict[str, CopyReport]:
    """Initialize flagged users from default, clearing each flag after success."""
    if not settings.bridge_users_file:
        logger.info("分类初始化检查：未配置 BRIDGE_USERS_FILE，跳过")
        return {}
    path = Path(settings.bridge_users_file)
    logger.info("分类初始化检查：读取用户配置 %s", path)
    config = BridgeUsersFile.model_validate_json(path.read_text(encoding="utf-8"))
    flagged = [
        user for user in config.users
        if user.initialize_from_default and (target_id is None or user.id == target_id)
    ]
    if target_id and not flagged:
        raise ValueError(f"找不到待初始化用户：{target_id}")
    if not flagged:
        logger.info("分类初始化检查：没有 initialize_from_default=true 的用户，跳过")
        return {}
    logger.info("分类初始化检查：发现 %d 位待初始化用户：%s", len(flagged), ", ".join(user.id for user in flagged))
    if config.users[0].id != "default":
        raise ValueError("用户配置的第一位必须是 id=default")
    if config.users[0].initialize_from_default:
        raise ValueError("default 用户不能设置 initialize_from_default")
    if target_id == "default":
        raise ValueError("不能初始化 default 用户")
    users = load_user_settings(settings)
    source_settings = users[0][2]
    source = EzClient(source_settings)
    marked = [
        (item, user_settings)
        for item, (_, _, user_settings) in zip(config.users[1:], users[1:])
        if item.initialize_from_default and (target_id is None or item.id == target_id)
    ]
    reports: dict[str, CopyReport] = {}
    for user, target_settings in marked:
        logger.info("分类初始化开始：用户 %s，来源 default，执行=%s", user.id, apply)
        try:
            if source_settings.ez_api_token == target_settings.ez_api_token:
                raise ValueError(f"用户 {user.id} 与 default 使用同一 ez 令牌，已停止初始化")
            report = await copy_catalog(
                source, EzClient(target_settings), apply=apply, replace_categories=True
            )
            reports[user.id] = report
            logger.info(
                "分类初始化结果：用户 %s，删除目标分类 %d 个，新增账户 %d 个，新增分类 %d 个，冲突 %d 个",
                user.id, report.categories_to_delete, report.accounts_to_create,
                report.categories_to_create, len(report.conflicts),
            )
            if report.conflicts:
                if apply:
                    raise ValueError(f"用户 {user.id} 初始化冲突：{'；'.join(report.conflicts)}")
                continue
            if apply:
                _clear_flag(path, user.id)
                logger.info("分类初始化完成：用户 %s，initialize_from_default 已写回 false", user.id)
        except Exception:
            logger.exception("分类初始化失败：用户 %s，标记保持 true", user.id)
            raise
    return reports
