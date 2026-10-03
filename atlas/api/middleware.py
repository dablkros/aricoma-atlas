"""Cross-cutting HTTP request behavior."""

import logging
import time
from typing import Callable

from fastapi import FastAPI, Request, Response

from atlas.api.request_context import assign_request_id


logger = logging.getLogger("atlas.api")


def install_request_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_logging(request: Request, call_next: Callable) -> Response:
        request_id = assign_request_id(request)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            # Exception messages can contain upstream payloads or credentials.
            # Log the type for correlation, while keeping the response and log
            # event free of arbitrary exception text.
            logger.error(
                "request.failed",
                extra={
                    "event": "request.failed",
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "error_type": type(exc).__name__,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                },
            )
            raise

        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request.completed",
            extra={
                "event": "request.completed",
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            },
        )
        return response
