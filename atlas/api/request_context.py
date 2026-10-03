"""Request correlation helpers shared by middleware and error handlers."""

import re
from uuid import uuid4

from fastapi import Request


_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,128}")


def assign_request_id(request: Request) -> str:
    candidate = request.headers.get("X-Request-ID", "")
    request_id = candidate if _REQUEST_ID_PATTERN.fullmatch(candidate) else uuid4().hex
    request.state.request_id = request_id
    return request_id


def get_request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    return request_id if isinstance(request_id, str) else assign_request_id(request)
