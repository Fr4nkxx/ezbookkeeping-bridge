import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI

from app.api.routes import router, secured
from app.container import Container, build_container
from app.core.config import get_settings
from app.core.users import load_user_settings
from app.ez.catalog_initialization import initialize_marked_users
from app.middleware.rate_limit import RateLimiter


@dataclass
class UserRuntime:
    id: str
    bridge_token: str
    container: Container
    rate_limiter: RateLimiter


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    await initialize_marked_users(settings, apply=True)
    runtimes = [
        UserRuntime(
            id=user_id,
            bridge_token=bridge_token,
            container=build_container(user_settings),
            rate_limiter=RateLimiter(settings.rate_limit_per_minute),
        )
        for user_id, bridge_token, user_settings in load_user_settings(settings)
    ]
    app.state.settings = settings
    app.state.user_runtimes = runtimes
    watch_tasks = [
        asyncio.create_task(
            runtime.container.duplicate_watch.run_loop(
                daily_time=settings.duplicate_watch_daily_time
            ),
            name=f"duplicate-amount-watcher-{runtime.id}",
        )
        for runtime in runtimes
    ]
    try:
        yield
    finally:
        for task in watch_tasks:
            task.cancel()
        await asyncio.gather(*watch_tasks, return_exceptions=True)


app = FastAPI(
    title="ezBookkeeping Bridge",
    description="个人记账截图中转服务",
    version="0.4.0",
    lifespan=lifespan,
)
app.include_router(router)
app.include_router(secured)
