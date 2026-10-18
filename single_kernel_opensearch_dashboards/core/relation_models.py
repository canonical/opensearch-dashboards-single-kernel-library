#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Models of the relation databags."""

import json
from typing import Any, Iterable, Optional

from dpcharmlibs.interfaces import (
    ExtraSecretStr,
    PeerModel,
    ResourceProviderModel,
    TlsSecretStr,
)
from pydantic import BaseModel, Field, field_validator, model_validator
from urllib3.util import parse_url

from single_kernel_opensearch_dashboards.lib.charms.hydra.v0.oauth import ClientConfig


class OSDClusterModel(PeerModel):
    """Peer model to the dashboards application state."""

    oauth_client_secret: ExtraSecretStr = Field(default="")


class OSDServerModel(PeerModel):
    """Peer model to a dashboards unit's state."""

    # True once the workload service is running.
    started: bool = Field(default=False)
    log_level: Optional[str] = Field(default=None)

    ca_cert: TlsSecretStr = Field(default="")
    csr: TlsSecretStr = Field(default="")
    certificate: TlsSecretStr = Field(default="")
    private_key: TlsSecretStr = Field(default="")

    @property
    def tls_enabled(self) -> bool:
        """Flag to check if TLS is enabled for the unit."""
        return bool(self.ca_cert) and bool(self.certificate) and bool(self.private_key)


class UpgradeUnitModel(PeerModel):
    """upgrade model to a dashboards unit's state."""

    state: str = Field(default="")

    @staticmethod
    def all_idle(states: Iterable[str]) -> bool:
        """Whether every unit upgrade state is idle (or empty), i.e. no upgrade in progress."""
        states = list(states)
        return not states or set(states) <= {"", "idle"}


class OpensearchServer(ResourceProviderModel):
    """Connection metadata for a related OpenSearch server."""

    # The opensearch publishes endpoints as a comma-separated string, we transform to a sorted list.
    endpoints: list[str] = Field(default_factory=list)

    @field_validator("endpoints", mode="before")
    @classmethod
    def _normalize_endpoints(cls, value: Any) -> list[str]:
        """Split the provider's comma-separated endpoints string into a sorted list."""
        if not value:
            return []
        if isinstance(value, str):
            return sorted(value.split(","))
        return list(value)

    # TODO: remove after opensearch is v1
    @property
    def short_uuid(self) -> str | None:
        """v0 providers set no request id, use the no-uuid v0 secret label."""
        return None


class JWTAuthConfiguration(ResourceProviderModel):
    """Model for the configuration parameters published by a JWT provider."""

    signing_key: ExtraSecretStr = Field(default=None)
    jwt_url_parameter: str | None = Field(default=None)


class OAuthModel(BaseModel):
    """State collection metadata for the oauth relation."""

    issuer_url: str = Field(default="")
    client_id: str = Field(default="")
    jwks_endpoint: str = Field(default="")
    introspection_endpoint: str = Field(default="")
    jwt_access_token: bool = Field(default=False)

    client_secret: str = Field(default="")

    @staticmethod
    def client_config(base_redirect_url: str) -> ClientConfig:
        """Build the OAuth requirer client config, redirecting through the given base URL."""
        return ClientConfig(
            audience=["opensearch"],
            redirect_uri=f"{base_redirect_url}/auth/openid/login",
            scope="openid profile email phone offline address",
            grant_types=["authorization_code"],
            token_endpoint_auth_method="client_secret_post",
        )


class IngressModel(BaseModel):
    """State of the Ingress relation."""

    # The ingress URL published by the provider
    url: str | None = Field(default=None)

    @model_validator(mode="before")
    @classmethod
    def extract_url(cls, data: Any) -> Any:
        """Get ``url`` out of the provider's ``ingress`` JSON blob"""
        if isinstance(data, dict) and "ingress" in data:
            payload = data.get("ingress") or {}
            if isinstance(payload, str):
                payload = json.loads(payload)
            return {**data, "url": payload.get("url")}
        return data

    @property
    def base_path(self) -> str | None:
        """Return the ingress base path."""
        ingress_url = self.url
        if not ingress_url:
            return None
        return parse_url(ingress_url).path
