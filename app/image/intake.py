import hashlib
import io
import os
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_and_normalize_image(raw: bytes, max_bytes: int) -> tuple[bytes, str]:
    if len(raw) > max_bytes:
        raise ValueError(f"图片超过大小限制（{max_bytes} 字节）")
    if len(raw) < 32:
        raise ValueError("图片内容无效或过短")

    try:
        image = Image.open(io.BytesIO(raw))
        image.verify()
    except Exception as exc:
        raise ValueError("无法识别为有效图片") from exc

    image = Image.open(io.BytesIO(raw))
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    elif image.mode == "L":
        image = image.convert("RGB")

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90, optimize=True)
    jpeg_bytes = buffer.getvalue()
    if len(jpeg_bytes) > max_bytes:
        raise ValueError("转换 JPEG 后仍超过大小限制")
    return jpeg_bytes, sha256_bytes(jpeg_bytes)


def write_temp_jpeg(jpeg_bytes: bytes) -> Path:
    fd, path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    Path(path).write_bytes(jpeg_bytes)
    return Path(path)


@dataclass(frozen=True)
class ImagePayload:
    jpeg_bytes: bytes
    sha256: str


class ImageIntake:
    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max_bytes

    def normalize(self, raw: bytes) -> ImagePayload:
        """校验并统一转为 JPEG；非法图片抛 ValueError（由 HTTP 层转 400）。"""
        jpeg_bytes, digest = validate_and_normalize_image(raw, self._max_bytes)
        return ImagePayload(jpeg_bytes=jpeg_bytes, sha256=digest)

    def normalize_archive(self, raw: bytes) -> list[ImagePayload]:
        """Read 2–10 images from a ZIP in memory, with bounded decompression."""
        if len(raw) > 10 * self._max_bytes + 1024 * 1024:
            raise ValueError("图片归档文件过大")
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                members = [
                    item for item in archive.infolist()
                    if not item.is_dir()
                    and not item.filename.startswith("__MACOSX/")
                    and not Path(item.filename).name.startswith("._")
                ]
                if not 2 <= len(members) <= 10:
                    raise ValueError("归档文件需要包含 2 到 10 张图片")
                if sum(item.file_size for item in members) > 10 * self._max_bytes:
                    raise ValueError("归档中的图片总大小超过限制")
                payloads = []
                for index, item in enumerate(members, start=1):
                    if item.file_size > self._max_bytes or item.flag_bits & 0x1:
                        raise ValueError(f"第 {index} 张图片过大或已加密")
                    if Path(item.filename).suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                        raise ValueError(f"第 {index} 个文件不是支持的图片格式")
                    with archive.open(item) as image_file:
                        payloads.append(self.normalize(image_file.read(self._max_bytes + 1)))
                return payloads
        except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            raise ValueError("无法读取 ZIP 图片归档文件") from exc
