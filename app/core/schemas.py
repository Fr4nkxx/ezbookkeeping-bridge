from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    NeedsInput = "needs_input"
    Saved = "saved"
    Adjusted = "adjusted"
    NoChange = "no_change"
    Rejected = "rejected"
    Failed = "failed"


class ChoiceItem(BaseModel):
    id: str
    name: str
    path: str


class TransactionSummary(BaseModel):
    id: str
    type: str
    amount: str
    currency: str
    account: str
    category: str | None = None
    description: str


class BridgeResponse(BaseModel):
    """内部完整结果：message 用于通知；HTTP 对外只映射为调试摘要。"""

    status: TaskStatus
    request_id: str
    message: str
    missing_fields: list[str] | None = None
    choices: dict[str, list[ChoiceItem]] | None = None
    transaction: TransactionSummary | None = None
    recognition: dict[str, Any] | None = None


class BridgeDebugResponse(BaseModel):
    """HTTP 调试响应：用户通知走通知渠道，客户端可忽略本体。"""

    status: TaskStatus
    request_id: str
    message: str = Field(description="调试摘要（与通知正文一致）")
    bark_notified: bool = False


class ReceiptLlmOutput(BaseModel):
    type: str
    time: str | None = None
    amount: str
    currency: str | None = None
    source_account: str | None = None
    category: str | None = None
    category_reason: str | None = None
    tags: list[str] = Field(default_factory=list)
    description: str
    destination_amount: str | None = None
    destination_currency: str | None = None
    destination_account: str | None = None


class BalanceLlmOutput(BaseModel):
    account: str | None = None
    platform: str | None = None
    value: str | None = None
    currency: str | None = None
    value_meaning: str | None = None
    adjust_kind: str | None = None
