"""Preview or run flagged, one-time initialization from the default ez user."""

import argparse
import asyncio

from app.core.config import get_settings
from app.ez.catalog_initialization import initialize_marked_users


async def run(target_id: str | None, apply: bool) -> None:
    reports = await initialize_marked_users(get_settings(), target_id=target_id, apply=apply)
    if not reports:
        print("没有标记为 initialize_from_default=true 的用户。")
        return
    for user_id, report in reports.items():
        print(
            f"{user_id}：待删除目标分类 {report.categories_to_delete}；"
            f"待新增账户 {report.accounts_to_create}、已有账户 {report.accounts_existing}；"
            f"待新增分类 {report.categories_to_create}。"
        )
        if report.delete_category_names:
            print("  删除一级分类：" + "、".join(report.delete_category_names))
        if report.account_names:
            print("  账户：" + "、".join(report.account_names))
        if report.category_names:
            print("  分类：" + "、".join(report.category_names))
        for conflict in report.conflicts:
            print(f"  冲突：{conflict}")
        if apply and not report.conflicts:
            print("  初始化完成，标记已改为 false。")


def main() -> None:
    parser = argparse.ArgumentParser(description="预览或运行标记用户的一次性初始化")
    parser.add_argument("--target", help="只处理指定用户 id")
    parser.add_argument("--apply", action="store_true", help="立即执行；默认只预览")
    args = parser.parse_args()
    asyncio.run(run(args.target, args.apply))


if __name__ == "__main__":
    main()
