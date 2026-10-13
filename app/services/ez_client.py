"""兼容转发：实际定义见 app.ez.client / app.core.errors。"""

from app.core.errors import EzApiError
from app.ez.client import EzClient

__all__ = ["EzApiError", "EzClient"]
