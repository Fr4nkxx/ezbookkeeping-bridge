import asyncio
import logging
from collections.abc import Sequence

from app.notify.base import Notification, Notifier

logger = logging.getLogger(__name__)


class NotificationDispatcher:
    """并发分发到所有已启用渠道；至少一路成功即视为已通知。"""

    def __init__(self, notifiers: Sequence[Notifier]) -> None:
        self._notifiers = list(notifiers)

    @property
    def enabled(self) -> bool:
        return bool(self._notifiers)

    @property
    def channel_names(self) -> list[str]:
        return [n.name for n in self._notifiers]

    async def dispatch(self, notification: Notification) -> bool:
        if not self._notifiers:
            logger.info("未配置任何通知渠道，跳过通知：%s", notification.title)
            return False
        results = await asyncio.gather(
            *(n.send(notification) for n in self._notifiers),
            return_exceptions=True,
        )
        delivered = False
        for notifier, result in zip(self._notifiers, results):
            if isinstance(result, BaseException):
                logger.warning("通知渠道 %s 异常：%s", notifier.name, result)
            elif result:
                delivered = True
        return delivered
