"""兼容转发：实际定义见 app.pipelines.receipt（构造参数已改为 PipelineDeps）。"""

from app.pipelines.receipt import ReceiptPipeline

ReceiptService = ReceiptPipeline

__all__ = ["ReceiptPipeline", "ReceiptService"]
