"""Compliance 152-FZ API Server entry point."""
from __future__ import annotations

import logging

import uvicorn

from src.config import API_HOST, API_PORT, LOG_LEVEL

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def main():
    uvicorn.run(
        "src.api.server:app",
        host=API_HOST,
        port=int(API_PORT),
        reload=True,
    )


if __name__ == "__main__":
    main()
