import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx

from app.core.errors import EzApiError
from app.ez.client import EzClient

logger = logging.getLogger(__name__)


@dataclass
class TransactionDraft:
    """待写入 ez 的交易；金额为 ez 两位小数定点整数。"""

    txn_type: int
    category_id: str
    time_unix: int
    utc_offset: int
    source_account_id: str
    source_amount: int
    comment: str
    client_session_id: str
    destination_account_id: str = "0"
    destination_amount: int = 0
    tag_ids: list[str] = field(default_factory=list)
    picture_ids: list[str] = field(default_factory=list)
    hide_amount: bool = False
    geo_location: tuple[float, float] | None = None

    def to_body(self) -> dict[str, Any]:
        body = {
            "type": self.txn_type,
            "categoryId": self.category_id,
            "time": self.time_unix,
            "utcOffset": self.utc_offset,
            "sourceAccountId": self.source_account_id,
            "destinationAccountId": self.destination_account_id,
            "sourceAmount": self.source_amount,
            "destinationAmount": self.destination_amount,
            "hideAmount": self.hide_amount,
            "tagIds": list(self.tag_ids),
            "pictureIds": list(self.picture_ids),
            "comment": self.comment[:255],
            "clientSessionId": self.client_session_id,
        }
        if self.geo_location is not None:
            latitude, longitude = self.geo_location
            body["geoLocation"] = {"latitude": latitude, "longitude": longitude}
        return body


class SubmitStatus(str, Enum):
    Ok = "ok"
    Timeout = "timeout"
    Error = "error"


@dataclass(frozen=True)
class SubmitResult:
    status: SubmitStatus
    id: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == SubmitStatus.Ok


TIMEOUT_MESSAGE = "提交 ez 超时，可能已写入。请到 ez 中核对，勿盲目重复提交。"


class EzTransactionGateway:
    """写入类 ez 调用的统一入口：超时与 EzApiError 转为 SubmitResult，不向流程层抛出。"""

    def __init__(self, ez: EzClient) -> None:
        self._ez = ez

    async def submit(self, draft: TransactionDraft) -> SubmitResult:
        try:
            result = await self._ez.add_transaction(draft.to_body())
        except httpx.TimeoutException:
            logger.warning("ez 提交超时（session=%s）", draft.client_session_id)
            return SubmitResult(SubmitStatus.Timeout, error=TIMEOUT_MESSAGE)
        except EzApiError as exc:
            return SubmitResult(SubmitStatus.Error, error=str(exc))
        return SubmitResult(SubmitStatus.Ok, id=str((result or {}).get("id", "")))

    async def upload_picture(
        self,
        jpeg_bytes: bytes,
        *,
        filename: str,
        client_session_id: str,
    ) -> SubmitResult:
        try:
            info = await self._ez.upload_transaction_picture(
                jpeg_bytes,
                filename=filename,
                client_session_id=client_session_id,
            )
        except httpx.TimeoutException:
            return SubmitResult(SubmitStatus.Timeout, error="ez 图片上传超时")
        except EzApiError as exc:
            return SubmitResult(SubmitStatus.Error, error=str(exc))
        return SubmitResult(SubmitStatus.Ok, id=str(info["pictureId"]))
