import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = ROOT / "tests" / "fixtures"
DEFAULT_SAMPLE_JPEG = FIXTURES_DIR / "sample.jpg"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: 真实调用 ez 与 LLM（由测试文件内 RUN_LIVE_INTEGRATION 控制）",
    )


@pytest.fixture(scope="session")
def project_root() -> Path:
    return ROOT


@pytest.fixture
def reset_settings_cache():
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def print_json(label: str, obj: Any) -> None:
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    try:
        print(f"\n--- {label} ---\n{text}")
    except UnicodeEncodeError:
        print(f"\n--- {label} ---\n{json.dumps(obj, ensure_ascii=True, indent=2)}")


def ensure_sample_jpeg(path: Path | None = None) -> Path:
    target = path or DEFAULT_SAMPLE_JPEG
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file():
        from PIL import Image

        image = Image.new("RGB", (128, 128), color=(235, 235, 235))
        image.save(target, format="JPEG", quality=85)
    return target
