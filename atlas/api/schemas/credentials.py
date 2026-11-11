"""Secret-safe request contracts for device credential onboarding."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SSHSecretInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=255, repr=False)
    password: str = Field(min_length=1, max_length=4096, repr=False)
    enable_password: str | None = Field(default=None, max_length=4096, repr=False)


class APITokenInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_token: str = Field(min_length=1, max_length=8192, repr=False)


class SNMPv2Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    community: str = Field(min_length=1, max_length=1024, repr=False)


class CredentialOnboardingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_ids: list[Annotated[int, Field(gt=0)]] = Field(
        min_length=1,
        max_length=100,
    )
    platform: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    preserve_device_platforms: bool = False
    credential_profile: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$",
    )
    credential_mode: Literal["store", "reuse"] = "store"
    prophylaxis_enabled: bool = False
    profylaxia_checks: list[Literal["identity", "full_collection"]] | None = Field(
        default=None,
        max_length=2,
    )
    prophylaxis_auth: Literal["ssh", "api_token"] | None = None
    prophylaxis_ssh: SSHSecretInput | None = Field(default=None, repr=False)
    prophylaxis_api: APITokenInput | None = Field(default=None, repr=False)
    fortios_api_port: int | None = Field(default=None, ge=1, le=65535)
    oxidized_enabled: bool = False
    oxidized: SSHSecretInput | None = Field(default=None, repr=False)
    monitoring_enabled: bool = False
    snmp: SNMPv2Input | None = Field(default=None, repr=False)
    replace_existing: bool = False
    plan_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_selected_credentials(self):
        if len(set(self.device_ids)) != len(self.device_ids):
            raise ValueError("Device selection contains duplicates")
        if self.preserve_device_platforms and self.platform is not None:
            raise ValueError(
                "Platform must be omitted when preserving device platforms"
            )
        if not self.preserve_device_platforms and self.platform is None:
            raise ValueError("Platform is required")
        if not any((self.prophylaxis_enabled, self.oxidized_enabled, self.monitoring_enabled)):
            raise ValueError("Select at least one credential purpose")
        if self.profylaxia_checks is not None:
            if len(set(self.profylaxia_checks)) != len(self.profylaxia_checks):
                raise ValueError("Profylaxia selection contains duplicates")
            if self.prophylaxis_enabled and not self.profylaxia_checks:
                raise ValueError("Select at least one Profylaxia mode")
            if not self.prophylaxis_enabled and self.profylaxia_checks:
                raise ValueError("Profylaxia modes require the Prophylaxia purpose")
        if self.prophylaxis_enabled:
            if (
                self.credential_mode == "store"
                and self.prophylaxis_auth == "ssh"
                and self.prophylaxis_ssh is None
            ):
                raise ValueError("SSH credentials are required for Prophylaxia")
            if (
                self.credential_mode == "store"
                and self.prophylaxis_auth == "api_token"
                and self.prophylaxis_api is None
            ):
                raise ValueError("API token is required for Prophylaxia")
            if self.prophylaxis_auth is None:
                raise ValueError("Prophylaxia authentication is required")
            if self.prophylaxis_auth == "ssh" and (
                self.prophylaxis_api is not None or self.fortios_api_port is not None
            ):
                raise ValueError("API fields are not allowed for SSH Prophylaxia")
            if self.prophylaxis_auth == "api_token" and self.prophylaxis_ssh is not None:
                raise ValueError("SSH fields are not allowed for API Prophylaxia")
        elif any((
            self.prophylaxis_auth is not None,
            self.prophylaxis_ssh is not None,
            self.prophylaxis_api is not None,
            self.fortios_api_port is not None,
        )):
            raise ValueError("Prophylaxia fields require the Prophylaxia purpose")
        if (
            self.credential_mode == "store"
            and self.oxidized_enabled
            and self.oxidized is None
        ):
            raise ValueError("SSH credentials are required for Oxidized")
        if not self.oxidized_enabled and self.oxidized is not None:
            raise ValueError("Oxidized credentials require the Oxidized purpose")
        if self.monitoring_enabled and self.snmp is None:
            if self.credential_mode == "store":
                raise ValueError("SNMP credentials are required for monitoring")
        if not self.monitoring_enabled and self.snmp is not None:
            raise ValueError("SNMP credentials require the monitoring purpose")
        supplied_secrets = any((
            self.prophylaxis_ssh is not None,
            self.prophylaxis_api is not None,
            self.oxidized is not None,
            self.snmp is not None,
        ))
        if self.credential_mode == "reuse":
            if supplied_secrets:
                raise ValueError("Secret values are not allowed when reusing a profile")
            if self.replace_existing:
                raise ValueError("Replace is not allowed when reusing a profile")
        return self


__all__ = [
    "APITokenInput",
    "CredentialOnboardingRequest",
    "SNMPv2Input",
    "SSHSecretInput",
]
