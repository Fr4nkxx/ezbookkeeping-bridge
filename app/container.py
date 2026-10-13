from dataclasses import dataclass

from app.core.config import Settings
from app.ez.client import EzClient
from app.ez.transactions import EzTransactionGateway
from app.image.intake import ImageIntake
from app.llm.client import OpenAICompatVisionModel, VisionModel
from app.notify.dispatcher import NotificationDispatcher
from app.notify.factory import build_dispatcher
from app.pipelines.balance import BalancePipeline
from app.pipelines.balance_aggregate import AggregateBalancePipeline
from app.pipelines.base import PipelineDeps
from app.pipelines.duplicate_watch import DuplicateWatchPipeline
from app.pipelines.formatter import ResultFormatter
from app.pipelines.receipt import ReceiptPipeline
from app.prompts.builder import PromptBuilder


@dataclass
class Container:
    """单个用户的依赖装配；替换实现（模型、通知渠道等）只需改 build_container。"""

    settings: Settings
    ez: EzClient
    gateway: EzTransactionGateway
    vision: VisionModel
    prompts: PromptBuilder
    intake: ImageIntake
    notifier: NotificationDispatcher
    receipt: ReceiptPipeline
    balance: BalancePipeline
    balance_aggregate: AggregateBalancePipeline
    duplicate_watch: DuplicateWatchPipeline


def build_container(settings: Settings) -> Container:
    ez = EzClient(settings)
    notifier = build_dispatcher(settings)
    deps = PipelineDeps(
        settings=settings,
        ez=ez,
        gateway=EzTransactionGateway(ez),
        vision=OpenAICompatVisionModel(settings),
        prompts=PromptBuilder(settings.prompts_dir),
        notifier=notifier,
        formatter=ResultFormatter(),
    )
    account_locks = {}
    return Container(
        settings=settings,
        ez=ez,
        gateway=deps.gateway,
        vision=deps.vision,
        prompts=deps.prompts,
        intake=ImageIntake(settings.max_image_bytes),
        notifier=notifier,
        receipt=ReceiptPipeline(deps),
        balance=BalancePipeline(deps, account_locks),
        balance_aggregate=AggregateBalancePipeline(deps, account_locks),
        duplicate_watch=DuplicateWatchPipeline(ez, notifier, timezone_name=settings.default_timezone),
    )
