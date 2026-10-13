"""兼容转发：实际定义见 app.pipelines.balance（构造参数已改为 PipelineDeps）。"""

from app.pipelines.balance import BalancePipeline

BalanceService = BalancePipeline

__all__ = ["BalancePipeline", "BalanceService"]
