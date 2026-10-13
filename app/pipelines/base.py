import logging
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, ClassVar, Generic, TypeVar

from pydantic import BaseModel

from app.core.config import Settings
from app.core.errors import EzApiError, LlmError
from app.core.schemas import BridgeResponse, ChoiceItem, TaskStatus
from app.ez.catalog import EzCatalog, load_catalog
from app.ez.client import EzClient
from app.ez.transactions import EzTransactionGateway
from app.image.intake import ImagePayload
from app.llm.client import VisionModel
from app.notify.dispatcher import NotificationDispatcher
from app.pipelines.formatter import ResultFormatter
from app.prompts.builder import PromptBuilder

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

MAX_CHOICES = 20


@dataclass
class PipelineDeps:
    settings: Settings
    ez: EzClient
    gateway: EzTransactionGateway
    vision: VisionModel
    prompts: PromptBuilder
    notifier: NotificationDispatcher
    formatter: ResultFormatter


@dataclass(frozen=True)
class PipelineOutcome:
    result: BridgeResponse
    notified: bool


def to_choices(items: Iterable[Any]) -> list[ChoiceItem]:
    return [
        ChoiceItem(id=item.id, name=item.name, path=getattr(item, "path", item.name))
        for item in list(items)[:MAX_CHOICES]
    ]


class VisionPipeline(ABC, Generic[T]):
    """截图识别类流程模板：catalog -> prompt -> 视觉模型 -> resolve -> 通知。"""

    kind: ClassVar[str]
    prompt_file: ClassVar[str]
    instruction: ClassVar[str]
    output_schema: ClassVar[type[BaseModel]]

    def __init__(self, deps: PipelineDeps) -> None:
        self._deps = deps

    @property
    def settings(self) -> Settings:
        return self._deps.settings

    async def run(
        self,
        *,
        request_id: str,
        image: ImagePayload,
        geo_location: tuple[float, float] | None = None,
    ) -> PipelineOutcome:
        try:
            result = await self._analyze_and_resolve(request_id, image, geo_location)
        except Exception as exc:
            logger.exception("%s 流程异常（request_id=%s）", self.kind, request_id)
            result = BridgeResponse(
                status=TaskStatus.Failed,
                request_id=request_id,
                message=f"处理异常：{exc}",
            )
        notified = await self._deps.notifier.dispatch(
            self._deps.formatter.format(kind=self.kind, result=result)
        )
        return PipelineOutcome(result=result, notified=notified)

    async def _analyze_and_resolve(
        self,
        request_id: str,
        image: ImagePayload,
        geo_location: tuple[float, float] | None,
    ) -> BridgeResponse:
        try:
            catalog = await load_catalog(self._deps.ez)
            prompt = self._deps.prompts.build(self.prompt_file, catalog, self.instruction)
            parsed = await self._deps.vision.parse_image(
                system_prompt=prompt.system,
                user_prompt=prompt.user,
                jpeg_bytes=image.jpeg_bytes,
                schema=self.output_schema,
            )
        except (LlmError, EzApiError, FileNotFoundError) as exc:
            return BridgeResponse(
                status=TaskStatus.Failed,
                request_id=request_id,
                message=self.failure_message(exc),
            )
        return await self.resolve(request_id, parsed, catalog, image, geo_location)  # type: ignore[arg-type]

    def failure_message(self, exc: Exception) -> str:
        if isinstance(exc, LlmError):
            return f"模型识别失败：{exc}"
        if isinstance(exc, EzApiError):
            return f"读取 ez 账户/分类失败：{exc}"
        return str(exc)

    @abstractmethod
    async def resolve(
        self,
        request_id: str,
        parsed: T,
        catalog: EzCatalog,
        image: ImagePayload,
        geo_location: tuple[float, float] | None,
    ) -> BridgeResponse: ...
