from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable


class NotificationLevel(str, Enum):
    Active = "active"
    TimeSensitive = "timeSensitive"
    Passive = "passive"


@dataclass(frozen=True)
class Notification:
    title: str
    body: str
    group: str = "ez-bridge"
    level: NotificationLevel = NotificationLevel.Active


@runtime_checkable
class Notifier(Protocol):
    """通知渠道接口。实现方须自行吞掉异常并返回 False，不得向流程层抛出。"""

    name: str

    async def send(self, notification: Notification) -> bool: ...
