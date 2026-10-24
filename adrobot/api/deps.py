from typing import Annotated

import httpx
from fastapi import Depends, Request
from sqlalchemy import Engine

from adrobot.config import Settings


def get_keitaro(request: Request) -> httpx.AsyncClient:
    return request.app.state.keitaro


def get_engine(request: Request) -> Engine:
    return request.app.state.engine


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


KeitaroDep = Annotated[httpx.AsyncClient, Depends(get_keitaro)]
EngineDep = Annotated[Engine, Depends(get_engine)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
