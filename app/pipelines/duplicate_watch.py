"""近 N 天相同金额交易监测：查询 -> 分组 -> 生成通知 -> 分发。"""

import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.core.errors import EzApiError
from app.ez.client import EzClient
from app.notify.base import Notification
from app.notify.dispatcher import NotificationDispatcher
from app.scheduler.daily import run_daily
from app.utils.amount import ez_int_to_decimal, format_amount_display

DEFAULT_LOOKBACK_DAYS = 3
_MAX_ROWS_PER_GROUP = 8
_TYPE_LABEL = {
    1: "余额调整",
    2: "收入",
    3: "支出",
    4: "转账",
}


@dataclass
class DuplicateReport:
    total_count: int
    groups: dict[int, list[dict[str, Any]]] = field(default_factory=dict)


def group_by_amount(items: list[Any]) -> dict[int, list[dict[str, Any]]]:
    by_amount: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            amount = abs(int(item.get("sourceAmount") or 0))
        except (TypeError, ValueError):
            continue
        if amount == 0:
            continue
        by_amount[amount].append(item)
    return {amount: rows for amount, rows in by_amount.items() if len(rows) >= 2}


def format_txn_line(item: dict[str, Any], tz_name: str) -> str:
    amount = format_amount_display(ez_int_to_decimal(int(item.get("sourceAmount") or 0)))
    txn_type = _TYPE_LABEL.get(int(item.get("type") or 0), str(item.get("type")))
    comment = str(item.get("comment") or "").strip() or "（无备注）"
    account = ""
    source = item.get("sourceAccount") or {}
    if isinstance(source, dict):
        account = str(source.get("name") or "")
    unix_time = int(item.get("time") or 0)
    when = (
        datetime.fromtimestamp(unix_time, tz=ZoneInfo(tz_name)).strftime("%m-%d %H:%M")
        if unix_time
        else "?"
    )
    return f"· {when} {txn_type} {amount} {account} {comment}".strip()


class DuplicateWatchPipeline:
    def __init__(
        self,
        ez: EzClient,
        notifier: NotificationDispatcher,
        *,
        timezone_name: str,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    ) -> None:
        self._ez = ez
        self._notifier = notifier
        self._tz_name = timezone_name
        self._lookback_days = lookback_days

    async def scan(self) -> DuplicateReport:
        now = int(time.time())
        start_time = now - self._lookback_days * 24 * 3600
        items = await self._ez.list_transactions_all(start_time=start_time, end_time=now, txn_type=0)
        return DuplicateReport(total_count=len(items), groups=group_by_amount(items))

    def build_notification(self, report: DuplicateReport) -> Notification | None:
        if not report.groups:
            return None
        lines: list[str] = [f"近{self._lookback_days}天发现 {len(report.groups)} 组相同金额："]
        for amount in sorted(report.groups.keys(), reverse=True):
            rows = report.groups[amount]
            display = format_amount_display(ez_int_to_decimal(amount))
            lines.append(f"\n【{display}】共 {len(rows)} 笔")
            for row in sorted(rows, key=lambda x: int(x.get("time") or 0), reverse=True)[:_MAX_ROWS_PER_GROUP]:
                lines.append(format_txn_line(row, self._tz_name))
            if len(rows) > _MAX_ROWS_PER_GROUP:
                lines.append(f"· …另有 {len(rows) - _MAX_ROWS_PER_GROUP} 笔")
        return Notification(title="记账相似金额提醒", body="\n".join(lines))

    async def run_once(self) -> int:
        """返回进程退出码：0 正常（含无可疑/未配置通知），1 失败。"""
        if not self._notifier.enabled:
            print("未配置任何通知渠道，跳过相似金额监测。")
            return 0

        try:
            report = await self.scan()
        except EzApiError as exc:
            print(f"读取 ez 交易失败：{exc}")
            return 1

        notification = self.build_notification(report)
        if notification is None:
            print(f"近 {self._lookback_days} 天无相同金额可疑交易（共 {report.total_count} 笔）。")
            return 0

        print(notification.body)
        if not await self._notifier.dispatch(notification):
            print(f"通知推送失败（渠道：{', '.join(self._notifier.channel_names)}）")
            return 1
        print(f"已推送通知（渠道：{', '.join(self._notifier.channel_names)}）。")
        return 0

    async def run_loop(self, *, daily_time: str, run_immediately: bool = False) -> None:
        channels = ", ".join(self._notifier.channel_names)
        print(
            f"相似金额监测已启动：每日 {daily_time}（{self._tz_name}）执行。"
            f"{f'通知渠道：{channels}。' if channels else '未配置通知渠道，到点仅跳过。'}"
        )
        await run_daily(
            self.run_once,
            daily_time=daily_time,
            tz_name=self._tz_name,
            run_immediately=run_immediately,
            name="duplicate-watch",
        )
