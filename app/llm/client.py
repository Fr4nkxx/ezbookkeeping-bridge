import base64
import json
import re
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.core.errors import LlmError

T = TypeVar("T", bound=BaseModel)


class VisionModel(Protocol):
    """视觉模型接口：输入提示词与 JPEG，输出按 schema 校验后的结构。失败抛 LlmError。"""

    async def parse_image(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        jpeg_bytes: bytes,
        schema: type[T],
    ) -> T: ...


class OpenAICompatVisionModel:
    """OpenAI 兼容 /chat/completions 视觉模型实现。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._vision_checked = False
        self._vision_ok = False

    async def ensure_vision_capable(self) -> None:
        if self._vision_checked:
            if not self._vision_ok:
                raise LlmError(
                    f"模型 {self._settings.llm_model} 未通过视觉能力检查，请更换支持图像输入的模型。"
                )
            return
        self._vision_checked = True
        base = self._settings.llm_base_url.rstrip("/")
        url = f"{base}/models"
        headers = {"Authorization": f"Bearer {self._settings.llm_api_key}"}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, headers=headers)
            if response.status_code == 404:
                self._vision_ok = True
                return
            response.raise_for_status()
            payload = response.json()
            models = payload.get("data") or []
            for item in models:
                if item.get("id") == self._settings.llm_model:
                    if item.get("vision") is False:
                        self._vision_ok = False
                        raise LlmError(f"模型 {self._settings.llm_model} 不支持图像输入。")
            self._vision_ok = True
        except LlmError:
            raise
        except Exception:
            self._vision_ok = True

    def _extract_json(self, text: str) -> dict[str, Any]:
        text = text.strip()
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if fence:
            text = fence.group(1).strip()
        return json.loads(text)

    async def parse_image(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        jpeg_bytes: bytes,
        schema: type[T],
    ) -> T:
        await self.ensure_vision_capable()
        b64 = base64.standard_b64encode(jpeg_bytes).decode("ascii")
        url = f"{self._settings.llm_base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self._settings.llm_model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                        },
                    ],
                },
            ],
        }
        try:
            async with httpx.AsyncClient(timeout=self._settings.llm_request_timeout_seconds) as client:
                response = await client.post(url, headers=headers, json=body)
        except httpx.TimeoutException as exc:
            raise LlmError("模型请求超时") from exc
        except httpx.HTTPError as exc:
            raise LlmError(f"模型请求网络错误：{exc}") from exc
        if response.status_code >= 400:
            detail = response.text[:500]
            if "vision" in detail.lower() or "image" in detail.lower():
                self._vision_ok = False
                raise LlmError(f"模型不支持图像或请求无效：{response.status_code}")
            raise LlmError(f"模型请求失败（HTTP {response.status_code}）")
        payload = response.json()
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmError("模型响应格式异常") from exc
        if not content:
            raise LlmError("模型返回空内容")
        try:
            data = self._extract_json(content)
            return schema.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise LlmError("模型输出 JSON 校验失败") from exc


LlmClient = OpenAICompatVisionModel
