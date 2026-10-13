import logging

import httpx

from app.notify.base import Notification, NotificationLevel

logger = logging.getLogger(__name__)


async def send_bark(push_url: str, title: str, body: str, *, group: str = "ez-bridge") -> None:
    """直接推送一条 Bark；失败抛出 httpx 异常。"""
    url = push_url.rstrip("/")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            url,
            json={
                "title": title,
                "body": body,
                "group": group,
            },
        )
        response.raise_for_status()


class BarkNotifier:
    name = "bark"

    def __init__(self, push_url: str, *, timeout_seconds: float = 30.0) -> None:
        self._url = push_url.strip().rstrip("/")
        self._timeout = timeout_seconds

    async def send(self, notification: Notification) -> bool:
        payload: dict[str, str] = {
            "title": notification.title,
            "body": notification.body,
            "group": notification.group,
        }
        if notification.level != NotificationLevel.Active:
            payload["level"] = notification.level.value
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(self._url, json=payload)
                response.raise_for_status()
        except Exception as exc:
            logger.warning("Bark 推送失败：%s", exc)
            return False
        return True
