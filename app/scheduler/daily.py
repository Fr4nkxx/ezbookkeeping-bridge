import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


def parse_hh_mm(text: str) -> tuple[int, int]:
    parts = text.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"每日时刻格式应为 HH:MM，当前：{text}")
    hour = int(parts[0])
    minute = int(parts[1])
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError(f"每日时刻非法：{text}")
    return hour, minute


def seconds_until_next_daily_time(daily_time: str, tz_name: str) -> int:
    hour, minute = parse_hh_mm(daily_time)
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz=tz)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return max(1, int((target - now).total_seconds()))


async def run_daily(
    job: Callable[[], Awaitable[Any]],
    *,
    daily_time: str,
    tz_name: str,
    run_immediately: bool = False,
    name: str = "daily-job",
) -> None:
    """每日 daily_time（tz_name 时区）执行一次 job；单轮异常只打印，不中断循环。"""
    if run_immediately:
        try:
            await job()
        except Exception as exc:
            print(f"[{name}] 启动即跑一轮失败：{exc}")

    while True:
        try:
            sleep_seconds = seconds_until_next_daily_time(daily_time, tz_name)
        except ValueError as exc:
            print(f"[{name}] {exc}；60 秒后重试。")
            await asyncio.sleep(60)
            continue

        next_at = datetime.now(tz=ZoneInfo(tz_name)) + timedelta(seconds=sleep_seconds)
        print(f"[{name}] 下次执行时间：{next_at.strftime('%Y-%m-%d %H:%M:%S %Z')}（约 {sleep_seconds} 秒后）")
        await asyncio.sleep(sleep_seconds)
        try:
            await job()
        except Exception as exc:
            print(f"[{name}] 本轮执行异常：{exc}")
