"""Public, secret-free schemas for Profylaxia operations."""

from pydantic import BaseModel, ConfigDict


class ProphylaxisDeviceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    ip: str
    platform: str


__all__ = ["ProphylaxisDeviceResponse"]
