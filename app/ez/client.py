import time
import uuid
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.core.config import Settings
from app.core.errors import EzApiError


class EzClient:
    """ezBookkeeping REST 客户端（源码核对 commit 9cb3a08e）。"""

    TRANSACTION_TYPE_MODIFY_BALANCE = 1
    TRANSACTION_TYPE_INCOME = 2
    TRANSACTION_TYPE_EXPENSE = 3
    TRANSACTION_TYPE_TRANSFER = 4

    ACCOUNT_TYPE_SINGLE = 1
    ACCOUNT_TYPE_MULTI = 2

    def __init__(self, settings: Settings) -> None:
        base = settings.ez_base_url.rstrip("/")
        self._base_url = f"{base}/api/v1"
        self._token = settings.ez_api_token
        self._timeout = settings.ez_request_timeout_seconds
        self._timezone_name = settings.default_timezone

    def _headers(self) -> dict[str, str]:
        # ez 前端在 services.ts 里对每个请求附加时区头；缺失会报 client timezone offset is invalid
        now = int(time.time())
        dt = datetime.fromtimestamp(now, tz=ZoneInfo(self._timezone_name))
        utc_offset = dt.utcoffset()
        offset_minutes = int(utc_offset.total_seconds() // 60) if utc_offset else 0
        return {
            "Authorization": f"Bearer {self._token}",
            "X-Timezone-Name": self._timezone_name,
            "X-Timezone-Offset": str(offset_minutes),
        }

    @staticmethod
    def _parse_envelope(
        response: httpx.Response,
        path: str,
        *,
        label: str = "ez",
        fallback_error: str = "ez 请求失败",
    ) -> Any:
        try:
            payload = response.json()
        except Exception as exc:
            raise EzApiError(f"{label} 返回非 JSON（HTTP {response.status_code}）") from exc

        if not isinstance(payload, dict):
            raise EzApiError(f"{label} 响应格式异常")

        if response.status_code >= 400 or not payload.get("success"):
            raise EzApiError(
                str(payload.get("errorMessage") or fallback_error),
                error_code=payload.get("errorCode"),
                path=str(payload.get("path") or path),
            )
        return payload.get("result")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self._base_url}/{path.lstrip('/')}"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.request(
                method, url, headers=self._headers(), params=params, json=json_body
            )
        return self._parse_envelope(response, path)

    async def list_accounts(self, visible_only: bool = False) -> list[dict[str, Any]]:
        result = await self._request(
            "GET", "accounts/list.json", params={"visible_only": str(visible_only).lower()}
        )
        return list(result or [])

    async def add_account(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "accounts/add.json", json_body=body)

    async def get_account(self, account_id: str) -> dict[str, Any]:
        return await self._request("GET", "accounts/get.json", params={"id": account_id})

    async def list_categories(self) -> dict[str, list[dict[str, Any]]]:
        result = await self._request("GET", "transaction/categories/list.json")
        return result or {}

    async def add_category(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "transaction/categories/add.json", json_body=body)

    async def delete_category(self, category_id: str) -> bool:
        return bool(await self._request(
            "POST", "transaction/categories/delete.json", json_body={"id": category_id}
        ))

    async def get_category(self, category_id: str) -> dict[str, Any]:
        return await self._request("GET", "transaction/categories/get.json", params={"id": category_id})

    async def list_tags(self) -> list[dict[str, Any]]:
        result = await self._request("GET", "transaction/tags/list.json")
        return list(result or [])

    async def upload_transaction_picture(
        self,
        jpeg_bytes: bytes,
        *,
        filename: str = "receipt.jpg",
        client_session_id: str | None = None,
    ) -> dict[str, Any]:
        """
        POST /transaction/pictures/upload.json
        表单字段 picture（文件）、可选 clientSessionId。
        返回含 pictureId（string）的结果。
        """
        path = "transaction/pictures/upload.json"
        url = f"{self._base_url}/{path}"
        data: dict[str, str] = {}
        if client_session_id:
            data["clientSessionId"] = client_session_id
        files = {"picture": (filename, jpeg_bytes, "image/jpeg")}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(url, headers=self._headers(), data=data, files=files)
        result = (
            self._parse_envelope(response, path, label="ez 图片上传", fallback_error="ez 图片上传失败") or {}
        )
        if not isinstance(result, dict) or not result.get("pictureId"):
            raise EzApiError("ez 图片上传成功但未返回 pictureId")
        return result

    async def add_transaction(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "transactions/add.json", json_body=body)

    async def modify_transaction(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "transactions/modify.json", json_body=body)

    async def count_transactions(self, account_id: str) -> int:
        result = await self._request(
            "GET",
            "transactions/count.json",
            params={"account_ids": account_id, "type": "0"},
        )
        if isinstance(result, dict) and "totalCount" in result:
            return int(result["totalCount"])
        return 0

    async def list_transactions_all(
        self,
        *,
        start_time: int,
        end_time: int,
        txn_type: int = 0,
    ) -> list[dict[str, Any]]:
        """
        GET /transactions/list/all.json
        start_time / end_time 为 Unix 秒。
        """
        result = await self._request(
            "GET",
            "transactions/list/all.json",
            params={
                "start_time": start_time,
                "end_time": end_time,
                "type": txn_type,
                "trim_account": "false",
                "trim_category": "false",
                "trim_tag": "true",
                "with_pictures": "false",
            },
        )
        return list(result or [])

    @staticmethod
    def new_client_session_id(prefix: str) -> str:
        return f"bridge-{prefix}-{uuid.uuid4().hex}"
