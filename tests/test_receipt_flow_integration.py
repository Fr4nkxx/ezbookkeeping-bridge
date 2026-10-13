"""
主流程集成测试：multipart 上传图片 → /receipt → 查看调试 JSON（用户通知走 Bark）。

配置只读 .env，不读 .env.example。

用法：
  # Mock
  pytest tests/test_receipt_flow_integration.py::test_receipt_main_flow_mocked -s -v

  # 真实流程：把下方 RUN_LIVE_INTEGRATION 改为 True，并填写 TEST_IMAGE_PATH
  pytest tests/test_receipt_flow_integration.py::test_receipt_main_flow_live -s -v
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from tests.conftest import (
    DEFAULT_SAMPLE_JPEG,
    ROOT,
    ensure_sample_jpeg,
    print_json,
)

# 真实调用 LLM + ez 的开关（改这里，不再依赖环境变量）
RUN_LIVE_INTEGRATION = True
# 真实测试的图片路径；仅本地手动填写，不将私人截图放进项目
TEST_IMAGE_PATH = r""


def _apply_local_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ROOT)
    monkeypatch.setenv("EZ_BASE_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("EZ_API_TOKEN", "test-ez-token")
    monkeypatch.setenv("BRIDGE_API_TOKEN", "test-bridge-token")
    monkeypatch.setenv("LLM_BASE_URL", "http://llm.test/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-llm-key")
    monkeypatch.setenv("LLM_MODEL", "test-vision-model")
    monkeypatch.setenv("PROMPTS_DIR", str(ROOT / "prompts"))
    monkeypatch.setenv("BARK_PUSH_URL", "")


def _build_fake_catalog():
    from app.services.ez_catalog import AccountCandidate, CategoryCandidate, EzCatalog

    accounts = [
        AccountCandidate(
            id="1",
            name="现金账户",
            path="现金账户",
            currency="CNY",
            is_liability=False,
            category=1,
        )
    ]
    categories = [
        CategoryCandidate(
            id="10",
            name="医疗健康",
            path="临时性必要开支 > 医疗健康",
            comment="医疗",
            type=2,
        )
    ]
    catalog = EzCatalog(accounts=accounts, categories=categories, tags=[])
    catalog.accounts_by_id = {a.id: a for a in accounts}
    catalog.categories_by_id = {c.id: c for c in categories}
    catalog.tags_by_id = {}
    catalog.tags_by_name = {}
    return catalog


def test_receipt_main_flow_mocked(
    monkeypatch: pytest.MonkeyPatch,
    reset_settings_cache,
) -> None:
    from app.config import get_settings
    from app.ez.client import EzClient
    from app.llm import client as llm_module
    from app.pipelines import base as pipeline_base_module
    from app.schemas import ReceiptLlmOutput

    _apply_local_test_env(monkeypatch)
    get_settings.cache_clear()

    fake_llm = ReceiptLlmOutput(
        type="expense",
        time="2025-10-13 14:30:00",
        amount="19.90",
        currency="CNY",
        source_account="现金账户",
        category="临时性必要开支 > 医疗健康",
        category_reason="商品为保健品，匹配医疗健康",
        tags=[],
        description="集成测试 mock 消费",
    )

    async def fake_load_catalog(_ez: EzClient):
        return _build_fake_catalog()

    monkeypatch.setattr(pipeline_base_module, "load_catalog", fake_load_catalog)
    monkeypatch.setattr(llm_module.OpenAICompatVisionModel, "parse_image", AsyncMock(return_value=fake_llm))
    monkeypatch.setattr(
        EzClient,
        "upload_transaction_picture",
        AsyncMock(return_value={"pictureId": "888001"}),
    )
    add_transaction = AsyncMock(
        return_value={
            "id": "999001",
            "type": 3,
            "sourceAmount": 1990,
            "comment": "集成测试 mock 消费",
        }
    )
    monkeypatch.setattr(EzClient, "add_transaction", add_transaction)
    monkeypatch.setattr(
        llm_module.OpenAICompatVisionModel, "ensure_vision_capable", AsyncMock(return_value=None)
    )

    from app.main import app

    settings = get_settings()
    image_path = ensure_sample_jpeg()
    request_id = f"mock-flow-{uuid.uuid4().hex[:8]}"

    with TestClient(app) as client:
        with image_path.open("rb") as image_file:
            response = client.post(
                "/receipt",
                headers={"Authorization": f"Bearer {settings.bridge_api_token}"},
                data={"request_id": request_id, "latitude": "0", "longitude": "0"},
                files={"image": ("sample.jpg", image_file, "image/jpeg")},
            )
        for location_fields in (
            {"latitude": "0"},
            {"latitude": "91", "longitude": "0"},
            {"latitude": "0", "longitude": "181"},
        ):
            with image_path.open("rb") as image_file:
                invalid_response = client.post(
                    "/receipt",
                    headers={"Authorization": f"Bearer {settings.bridge_api_token}"},
                    data=location_fields,
                    files={"image": ("sample.jpg", image_file, "image/jpeg")},
                )
            assert invalid_response.status_code == 400

    assert response.status_code == 200, response.text
    body = response.json()
    print_json("/receipt mock 主流程返回", body)
    assert body["request_id"] == request_id
    assert body["status"] == "saved"
    assert "message" in body
    assert "bark_notified" in body
    assert add_transaction.await_args.args[0]["geoLocation"] == {
        "latitude": 0,
        "longitude": 0,
    }
    assert add_transaction.await_count == 1


@pytest.mark.integration
def test_receipt_main_flow_live(
    monkeypatch: pytest.MonkeyPatch,
    reset_settings_cache,
) -> None:
    if not RUN_LIVE_INTEGRATION:
        pytest.skip("RUN_LIVE_INTEGRATION=False，跳过真实主流程（在本文件顶部改为 True）")

    env_file = ROOT / ".env"
    if not env_file.is_file():
        pytest.skip("未找到 .env（请从 .env.example 复制并填写）")

    from app.config import get_settings

    monkeypatch.chdir(ROOT)
    monkeypatch.setenv("PROMPTS_DIR", str(ROOT / "prompts"))
    get_settings.cache_clear()

    from app.main import app

    settings = get_settings()
    if not TEST_IMAGE_PATH.strip():
        pytest.skip("真实测试需显式填写 TEST_IMAGE_PATH")
    image_path = Path(TEST_IMAGE_PATH)
    if not image_path.is_file():
        pytest.skip(f"测试图片不存在: {image_path}")

    request_id = f"live-flow-{uuid.uuid4().hex[:8]}"

    with TestClient(app, raise_server_exceptions=False) as client:
        with image_path.open("rb") as image_file:
            response = client.post(
                "/receipt",
                headers={"Authorization": f"Bearer {settings.bridge_api_token}"},
                data={"request_id": request_id},
                files={"image": (image_path.name, image_file, "image/jpeg")},
            )

        print(f"\n图片: {image_path}\nHTTP {response.status_code}")
        try:
            body = response.json()
            print_json("/receipt 真实主流程返回", body)
        except Exception:
            print(response.text)
            raise

        assert response.status_code == 200
        assert "status" in body
        assert "message" in body
        assert body["request_id"] == request_id
