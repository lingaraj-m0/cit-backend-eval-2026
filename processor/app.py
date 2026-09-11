from contextlib import asynccontextmanager
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from .engine import Engine
from .models import ProcessRequest
from .settings import Settings


class LimitBodyMiddleware:
    """Reject large request bodies, including chunked requests, before JSON parsing."""
    def __init__(self, app, limit=65536):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        body = bytearray()
        while True:
            msg = await receive()
            if msg["type"] == "http.disconnect":
                return
            body.extend(msg.get("body", b""))
            if len(body) > self.limit:
                response = JSONResponse({"error": "REQUEST_TOO_LARGE"}, status_code=413)
                return await response(scope, receive, send)
            if not msg.get("more_body", False):
                break
        sent = False
        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()
        await self.app(scope, replay, send)


def create_app(settings: Settings | None = None):
    cfg = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        app.state.engine = Engine(cfg)
        yield
        await app.state.engine.close()

    app = FastAPI(title="Batch Processing Simulator", version="1.1.0", lifespan=lifespan)
    app.add_middleware(LimitBodyMiddleware)

    def admin(x_admin_token: str = Header(default="")):
        if not secrets.compare_digest(x_admin_token.encode("utf-8"), cfg.admin_token.encode("utf-8")):
            raise HTTPException(401, detail="Invalid admin token")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post("/v1/process")
    async def process(body: ProcessRequest, request: Request):
        code, content, headers = await request.app.state.engine.process(body)
        return JSONResponse(content, status_code=code, headers=headers)

    @app.get("/admin/metrics", dependencies=[Depends(admin)], include_in_schema=False)
    async def metrics(request: Request, run_id: str = Query(min_length=1, max_length=128)):
        return request.app.state.engine.store.metrics(run_id)

    @app.get("/admin/audit", dependencies=[Depends(admin)], include_in_schema=False)
    async def audit(request: Request, run_id: str = Query(min_length=1, max_length=128),
                    job_id: str | None = Query(default=None, max_length=128),
                    after: int = Query(default=0, ge=0), limit: int = Query(default=1000, ge=1, le=1000)):
        rows = request.app.state.engine.store.audit(run_id, job_id, after, limit)
        return {"items": rows, "next_after": rows[-1]["id"] if rows else after}

    return app
