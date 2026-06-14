"""FastAPI application entrypoint (MVP skeleton).

Routes are added as each layer comes online. For now this gives a running server
and DB initialization so the foundation is verifiable end-to-end.
"""
from __future__ import annotations

from fastapi import FastAPI

from app.db import init_db

app = FastAPI(title="Freight IQ", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "freight-iq", "version": "0.1.0"}
