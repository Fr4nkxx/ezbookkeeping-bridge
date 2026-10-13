"""
近 3 天相同金额交易监测（命令行入口）。

- 未配置任何通知渠道（如 BARK_PUSH_URL）：不执行监测逻辑。
- 与 FastAPI 同进程：应用 lifespan 内按 DUPLICATE_WATCH_DAILY_TIME 每日提醒。
- 也可单独：python -m scripts.duplicate_amount_watcher [--loop]

业务逻辑在 app.pipelines.duplicate_watch，定时在 app.scheduler.daily。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.core.config import get_settings
from app.ez.client import EzClient
from app.notify.factory import build_dispatcher
from app.pipelines.duplicate_watch import DuplicateWatchPipeline


def _build_pipeline() -> tuple[DuplicateWatchPipeline, str]:
    get_settings.cache_clear()
    settings = get_settings()
    pipeline = DuplicateWatchPipeline(
        EzClient(settings),
        build_dispatcher(settings),
        timezone_name=settings.default_timezone,
    )
    return pipeline, (settings.duplicate_watch_daily_time or "09:00").strip()


async def run_once() -> int:
    pipeline, _daily_time = _build_pipeline()
    return await pipeline.run_once()


async def run_loop(*, run_immediately: bool) -> None:
    pipeline, daily_time = _build_pipeline()
    await pipeline.run_loop(daily_time=daily_time, run_immediately=run_immediately)


def main() -> None:
    parser = argparse.ArgumentParser(description="近 3 天相同金额交易提醒")
    parser.add_argument(
        "--loop",
        action="store_true",
        help="按 DUPLICATE_WATCH_DAILY_TIME 每日循环",
    )
    parser.add_argument(
        "--run-immediately",
        action="store_true",
        help="循环模式下启动时先跑一轮，再等到每日时刻",
    )
    args = parser.parse_args()
    if args.loop:
        asyncio.run(run_loop(run_immediately=args.run_immediately))
    else:
        raise SystemExit(asyncio.run(run_once()))


if __name__ == "__main__":
    main()
