"""通知模块单元测试：分发器聚合、渠道工厂、结果格式化。"""

from __future__ import annotations

import pytest

from app.core.schemas import BridgeResponse, TaskStatus
from app.notify.base import Notification, Notifier
from app.notify.dispatcher import NotificationDispatcher
from app.pipelines.formatter import ResultFormatter


class _FakeNotifier:
    def __init__(self, name: str, *, result: bool = True, raises: bool = False) -> None:
        self.name = name
        self._result = result
        self._raises = raises
        self.received: list[Notification] = []

    async def send(self, notification: Notification) -> bool:
        self.received.append(notification)
        if self._raises:
            raise RuntimeError("boom")
        return self._result


def test_fake_notifier_satisfies_protocol() -> None:
    assert isinstance(_FakeNotifier("x"), Notifier)


async def test_dispatch_without_channels_returns_false() -> None:
    dispatcher = NotificationDispatcher([])
    assert not dispatcher.enabled
    assert await dispatcher.dispatch(Notification(title="t", body="b")) is False


async def test_dispatch_fans_out_and_any_success_counts() -> None:
    ok = _FakeNotifier("ok")
    bad = _FakeNotifier("bad", result=False)
    broken = _FakeNotifier("broken", raises=True)
    dispatcher = NotificationDispatcher([bad, broken, ok])

    note = Notification(title="t", body="b")
    assert await dispatcher.dispatch(note) is True
    assert dispatcher.channel_names == ["bad", "broken", "ok"]
    for notifier in (ok, bad, broken):
        assert notifier.received == [note]


async def test_dispatch_all_fail_returns_false() -> None:
    dispatcher = NotificationDispatcher([_FakeNotifier("a", result=False), _FakeNotifier("b", raises=True)])
    assert await dispatcher.dispatch(Notification(title="t", body="b")) is False


@pytest.mark.parametrize(
    ("bark_url", "expected"),
    [("", []), ("   ", []), (None, []), ("https://bark.example.com/KEY/", ["bark"])],
)
def test_factory_builds_channels_from_settings(bark_url: str | None, expected: list[str]) -> None:
    from types import SimpleNamespace

    from app.notify.factory import build_dispatcher

    dispatcher = build_dispatcher(SimpleNamespace(bark_push_url=bark_url))  # type: ignore[arg-type]
    assert dispatcher.channel_names == expected


@pytest.mark.parametrize(
    ("kind", "status", "title"),
    [
        ("receipt", TaskStatus.Saved, "记账成功"),
        ("receipt", TaskStatus.NeedsInput, "记账需补充"),
        ("balance", TaskStatus.Adjusted, "对账已调整"),
        ("balance", TaskStatus.Failed, "对账失败"),
    ],
)
def test_result_formatter_title_and_body(kind: str, status: TaskStatus, title: str) -> None:
    result = BridgeResponse(status=status, request_id="r1", message="  正文  ")
    note = ResultFormatter().format(kind=kind, result=result)
    assert note.title == title
    assert note.body == "正文"


def test_result_formatter_empty_message_falls_back_to_status() -> None:
    result = BridgeResponse(status=TaskStatus.Failed, request_id="r1", message="")
    assert ResultFormatter().format(kind="receipt", result=result).body == "status=failed"
