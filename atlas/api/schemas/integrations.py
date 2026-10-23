"""Safe public schemas for external integration diagnostics."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from atlas.services.cisco_support import CiscoDiagnosticState


class CiscoSupportTestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overall: Literal["READY", "PARTIAL", "UNAVAILABLE"]
    oauth: CiscoDiagnosticState
    software_suggestion: CiscoDiagnosticState
    eox: CiscoDiagnosticState


__all__ = ["CiscoSupportTestResponse"]
