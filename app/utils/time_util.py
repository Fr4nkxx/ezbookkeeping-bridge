from datetime import datetime
from zoneinfo import ZoneInfo


def utc_offset_minutes_for_zone(tz_name: str, unix_time: int) -> int:
    tz = ZoneInfo(tz_name)
    dt = datetime.fromtimestamp(unix_time, tz=tz)
    offset = dt.utcoffset()
    if offset is None:
        raise ValueError(f"无法获取时区偏移: {tz_name}")
    return int(offset.total_seconds() // 60)


def parse_local_datetime_to_unix(text: str, tz_name: str) -> int:
    """解析 YYYY-MM-DD 或 YYYY-MM-DD HH:MM[:SS] 为 Unix 秒（指定时区）。"""
    text = text.strip()
    tz = ZoneInfo(tz_name)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text, fmt)
            break
        except ValueError:
            dt = None
    if dt is None:
        raise ValueError(f"无法解析时间: {text}")
    if fmt == "%Y-%m-%d":
        raise ValueError("缺少具体时间")
    localized = dt.replace(tzinfo=tz)
    return int(localized.timestamp())
