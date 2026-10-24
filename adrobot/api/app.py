from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles

from adrobot.api.routes import campaigns, editor
from adrobot.api.templates import failure
from adrobot.config import load_settings
from adrobot.db.engine import make_engine
from adrobot.keitaro.client import build_client


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    settings = load_settings()
    app.state.settings = settings
    app.state.engine = make_engine()
    async with build_client(settings) as client:
        app.state.keitaro = client
        yield
    app.state.engine.dispose()


app = FastAPI(lifespan=lifespan)
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).resolve().parent.parent / "web" / "static"),
    name="static",
)
app.include_router(campaigns.router)
app.include_router(editor.router)


@app.exception_handler(RequestValidationError)
async def invalid_form(request: Request, _exc: RequestValidationError):
    if request.url.path == "/campaigns":
        message = "Check the form: a name, a two-letter geo code, and an offer are required."
    else:
        message = "That form was not accepted. Nothing was changed."
    return failure(request, message)
