"""余额对账截图只在创建调整交易时上传并关联。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.core.schemas import BalanceLlmOutput, TaskStatus
from app.ez.catalog import AccountCandidate, CategoryCandidate, EzCatalog
from app.ez.transactions import SubmitResult, SubmitStatus
from app.image.intake import ImagePayload
from app.pipelines.balance import BalancePipeline


def _pipeline(balance: int, upload: SubmitResult) -> tuple[BalancePipeline, SimpleNamespace]:
    account = AccountCandidate(
        id="1", name="现金账户", path="现金账户", currency="CNY", is_liability=False, category=1
    )
    category = CategoryCandidate(
        id="10", name="修改余额", path="调整 > 修改余额", comment="", type=1
    )
    catalog = EzCatalog(accounts=[account], categories=[category], tags=[])
    deps = SimpleNamespace(
        settings=SimpleNamespace(
            default_cash_account_name="现金账户",
            default_timezone="Asia/Shanghai",
            balance_adjust_income_category_path=None,
            balance_adjust_expense_category_path=None,
        ),
        ez=SimpleNamespace(get_account=AsyncMock(return_value={"balance": balance})),
        gateway=SimpleNamespace(
            upload_picture=AsyncMock(return_value=upload),
            submit=AsyncMock(return_value=SubmitResult(SubmitStatus.Ok, id="99")),
        ),
    )
    return BalancePipeline(deps), SimpleNamespace(catalog=catalog, gateway=deps.gateway)


def test_balance_adjustment_attaches_uploaded_screenshot() -> None:
    pipeline, context = _pipeline(10000, SubmitResult(SubmitStatus.Ok, id="pic-1"))
    image = ImagePayload(jpeg_bytes=b"jpeg", sha256="hash")

    result = asyncio.run(
        pipeline.resolve(
            "balance-test",
            BalanceLlmOutput(value="101.00", adjust_kind="cash"),
            context.catalog,
            image,
        )
    )

    assert result.status == TaskStatus.Adjusted
    assert "附件：已上传截图" in result.message
    assert context.gateway.upload_picture.await_args.args[0] == image.jpeg_bytes
    draft = context.gateway.submit.await_args.args[0]
    assert draft.picture_ids == ["pic-1"]
    assert draft.to_body()["pictureIds"] == ["pic-1"]


def test_balance_no_change_does_not_upload_or_create_transaction() -> None:
    pipeline, context = _pipeline(10000, SubmitResult(SubmitStatus.Ok, id="pic-1"))

    result = asyncio.run(
        pipeline.resolve(
            "balance-test",
            BalanceLlmOutput(value="100.00", adjust_kind="cash"),
            context.catalog,
            ImagePayload(jpeg_bytes=b"jpeg", sha256="hash"),
        )
    )

    assert result.status == TaskStatus.NoChange
    context.gateway.upload_picture.assert_not_awaited()
    context.gateway.submit.assert_not_awaited()


def test_balance_upload_failure_prevents_adjustment() -> None:
    pipeline, context = _pipeline(10000, SubmitResult(SubmitStatus.Error, error="upload failed"))

    result = asyncio.run(
        pipeline.resolve(
            "balance-test",
            BalanceLlmOutput(value="101.00", adjust_kind="cash"),
            context.catalog,
            ImagePayload(jpeg_bytes=b"jpeg", sha256="hash"),
        )
    )

    assert result.status == TaskStatus.Failed
    assert "上传失败" in result.message
    context.gateway.submit.assert_not_awaited()
