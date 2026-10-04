"""Public Operations UI platform-status schemas."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


HealthState = Literal["healthy", "degraded", "unhealthy"]


class ComponentStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: HealthState


class PlatformComponentsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    atlas: ComponentStatusResponse
    openbao: ComponentStatusResponse
    netbox: ComponentStatusResponse
    oxidized: ComponentStatusResponse


class PlatformStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: HealthState
    components: PlatformComponentsResponse
