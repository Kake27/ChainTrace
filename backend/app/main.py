from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes.health import router as health_router
from app.api.routes.wallet import router as wallet_router
from app.blockchain.mempool_client import MempoolBlockchainClient


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    client = MempoolBlockchainClient()
    app.state.blockchain_client = client
    try:
        yield
    finally:
        client.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="ChainTrace",
        description="Local-first Bitcoin privacy analysis",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(health_router)
    app.include_router(wallet_router)
    return app


app = create_app()
