from app.core.schemas import BridgeResponse, TaskStatus
from app.notify.base import Notification

_STATUS_TITLE_SUFFIX: dict[TaskStatus, str] = {
    TaskStatus.Saved: "成功",
    TaskStatus.Adjusted: "已调整",
    TaskStatus.NoChange: "无需变更",
    TaskStatus.NeedsInput: "需补充",
    TaskStatus.Rejected: "已拒绝",
    TaskStatus.Failed: "失败",
}

_KIND_LABEL = {
    "receipt": "记账",
    "balance": "对账",
    "balance_aggregate": "合计对账",
}


def result_title(*, kind: str, status: TaskStatus) -> str:
    prefix = _KIND_LABEL.get(kind, kind)
    suffix = _STATUS_TITLE_SUFFIX.get(status, status.value)
    return f"{prefix}{suffix}"


class ResultFormatter:
    """把流程结果转换成渠道无关的 Notification。"""

    def format(self, *, kind: str, result: BridgeResponse) -> Notification:
        return Notification(
            title=result_title(kind=kind, status=result.status),
            body=result.message.strip() or f"status={result.status.value}",
        )
