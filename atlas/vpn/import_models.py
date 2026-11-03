"""Request models for FortiClient onboarding; secrets must never be echoed."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class ProfileInspectRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    format: Literal['macos-plist', 'windows-xml', 'openvpn-config']
    profile_base64: SecretStr = Field(repr=False)
    source_name: str | None = Field(default=None, max_length=255)


class ProfileImportRequest(ProfileInspectRequest):
    profile_name: str = Field(min_length=1, max_length=200)
    export_password: SecretStr | None = Field(default=None, repr=False)
    username: SecretStr = Field(repr=False)
    password: SecretStr = Field(repr=False)
    connection_id: str = Field(min_length=1, max_length=100, pattern=r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
    name: str = Field(min_length=1, max_length=200)
    tenant_id: int = Field(gt=0)
    management_vrf_id: int = Field(gt=0)
    allowed_prefixes: list[str] = Field(default_factory=list, max_length=128)
    mfa_mode: Literal['none', 'push', 'otp'] = 'none'
    # Compatibility for API clients created before explicit MFA modes existed.
    external_mfa: bool = False
    replace_existing: bool = False
    plan_hash: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')

    @field_validator('username', 'password', 'export_password')
    @classmethod
    def limit_secret_length(cls, value):
        if value is not None and not 1 <= len(value.get_secret_value()) <= 4096:
            raise ValueError('invalid secret length')
        return value
