import math
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.api.deps import verify_bridge_token
from app.container import Container
from app.core.schemas import BridgeDebugResponse
from app.pipelines.base import VisionPipeline

router = APIRouter()
secured = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "ez-bookkeeping-bridge"}


async def _run_vision_pipeline(
    container: Container,
    pipeline: VisionPipeline,
    image: UploadFile,
    request_id: str | None,
    geo_location: tuple[float, float] | None = None,
) -> BridgeDebugResponse:
    raw = await image.read()
    try:
        payload = container.intake.normalize(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    outcome = await pipeline.run(
        request_id=request_id or str(uuid.uuid4()), image=payload, geo_location=geo_location
    )
    return BridgeDebugResponse(
        status=outcome.result.status,
        request_id=outcome.result.request_id,
        message=outcome.result.message,
        bark_notified=outcome.notified,
    )


@secured.post("/receipt", response_model=BridgeDebugResponse)
async def receipt(
    image: UploadFile = File(...),
    request_id: str | None = Form(default=None),
    latitude: float | None = Form(default=None),
    longitude: float | None = Form(default=None),
    container: Container = Depends(verify_bridge_token),
) -> BridgeDebugResponse:
    if (latitude is None) != (longitude is None):
        raise HTTPException(status_code=400, detail="纬度和经度必须同时提供")
    if latitude is not None and longitude is not None:
        if not math.isfinite(latitude) or not -90 <= latitude <= 90:
            raise HTTPException(status_code=400, detail="纬度必须在 -90 到 90 之间")
        if not math.isfinite(longitude) or not -180 <= longitude <= 180:
            raise HTTPException(status_code=400, detail="经度必须在 -180 到 180 之间")
        geo_location = (latitude, longitude)
    else:
        geo_location = None
    return await _run_vision_pipeline(container, container.receipt, image, request_id, geo_location)


@secured.post("/balance", response_model=BridgeDebugResponse)
async def balance(
    image: UploadFile = File(...),
    request_id: str | None = Form(default=None),
    container: Container = Depends(verify_bridge_token),
) -> BridgeDebugResponse:
    return await _run_vision_pipeline(container, container.balance, image, request_id)


@secured.post("/balance/aggregate", response_model=BridgeDebugResponse)
async def balance_aggregate(
    archive: UploadFile | None = File(default=None),
    images: list[UploadFile] | None = File(default=None),
    image1: UploadFile | None = File(default=None),
    image2: UploadFile | None = File(default=None),
    image3: UploadFile | None = File(default=None),
    image4: UploadFile | None = File(default=None),
    image5: UploadFile | None = File(default=None),
    image6: UploadFile | None = File(default=None),
    image7: UploadFile | None = File(default=None),
    image8: UploadFile | None = File(default=None),
    image9: UploadFile | None = File(default=None),
    image10: UploadFile | None = File(default=None),
    request_id: str | None = Form(default=None),
    container: Container = Depends(verify_bridge_token),
) -> BridgeDebugResponse:
    numbered = [image1, image2, image3, image4, image5, image6, image7, image8, image9, image10]
    if archive is not None and (images or any(numbered)):
        raise HTTPException(status_code=400, detail="图片归档 archive 不能与其他图片字段同时使用")
    if archive is not None:
        try:
            payloads = container.intake.normalize_archive(await archive.read())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        outcome = await container.balance_aggregate.run_images(
            request_id=request_id or str(uuid.uuid4()), images=payloads
        )
        return BridgeDebugResponse(
            status=outcome.result.status,
            request_id=outcome.result.request_id,
            message=outcome.result.message,
            bark_notified=outcome.notified,
        )
    if images and any(numbered):
        raise HTTPException(status_code=400, detail="请只使用 images 或 image1、image2 等编号字段")
    if not images and any(numbered):
        count = sum(image is not None for image in numbered)
        if any(image is None for image in numbered[:count]):
            raise HTTPException(status_code=400, detail="图片字段须从 image1 连续编号")
    uploads = images or [image for image in numbered if image is not None]
    if not 2 <= len(uploads) <= 10:
        raise HTTPException(status_code=400, detail="请上传 2 到 10 张不同平台的余额截图")
    payloads = []
    for index, image in enumerate(uploads, start=1):
        try:
            payloads.append(container.intake.normalize(await image.read()))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"第 {index} 张图片无效：{exc}") from exc
    outcome = await container.balance_aggregate.run_images(
        request_id=request_id or str(uuid.uuid4()), images=payloads
    )
    return BridgeDebugResponse(
        status=outcome.result.status,
        request_id=outcome.result.request_id,
        message=outcome.result.message,
        bark_notified=outcome.notified,
    )
