from app.core.config import Settings
from app.notify.bark import BarkNotifier
from app.notify.base import Notifier
from app.notify.dispatcher import NotificationDispatcher


def build_notifiers(settings: Settings) -> list[Notifier]:
    """按配置构建通知渠道。新增渠道：实现 Notifier，并在此追加一个分支。"""
    notifiers: list[Notifier] = []
    bark_url = (settings.bark_push_url or "").strip()
    if bark_url:
        notifiers.append(BarkNotifier(bark_url))
    return notifiers


def build_dispatcher(settings: Settings) -> NotificationDispatcher:
    return NotificationDispatcher(build_notifiers(settings))
