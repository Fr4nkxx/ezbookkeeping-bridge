import hmac

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.container import Container

_bearer = HTTPBearer(auto_error=False)


def verify_bridge_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Container:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="缺少或无效的 Authorization Bearer 令牌")
    for runtime in request.app.state.user_runtimes:
        if hmac.compare_digest(credentials.credentials.encode(), runtime.bridge_token.encode()):
            runtime.rate_limiter.check()
            return runtime.container
    raise HTTPException(status_code=403, detail="中转服务鉴权失败")


def get_app_state(request: Request):
    return request.app.state
