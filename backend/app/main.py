"""FastAPI 入口。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from .database import init_db
from .routers.api import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="材料实验室拉伸试验分析", version="0.1.0", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    # 将校验错误（如柔度系数缺少校准单位）扁平化为中文 detail，前端直接展示原因
    messages: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
        msg = err.get("msg", "参数校验失败")
        ctx_error = err.get("ctx", {}).get("error")
        if ctx_error is not None and str(ctx_error):
            # model_validator 中抛出的中文 ValueError
            msg = str(ctx_error)
        messages.append(f"{loc}：{msg}" if loc else msg)
    return JSONResponse(status_code=422, content={"detail": "；".join(messages)})


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
