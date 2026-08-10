#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Model for the oauth relation."""

import logging
from typing import MutableMapping

import requests
from ops.model import Relation
from pydantic import BaseModel, Field

from single_kernel_opensearch_dashboards.lib.charms.hydra.v0.oauth import ClientConfig

logger = logging.getLogger(__name__)


class OAuthModel(BaseModel):
    """State collection metadata for the oauth relation."""

    issuer_url: str = Field(default="")
    client_id: str = Field(default="")
    jwks_endpoint: str = Field(default="")
    introspection_endpoint: str = Field(default="")
    jwt_access_token: bool = Field(default=False)
    # Sourced from the peer app databag, not the oauth provider.
    client_secret: str = Field(default="")

    @classmethod
    def from_relation(cls, relation: Relation | None, client_secret: str) -> "OAuthModel":
        """Build the model from the oauth provider's application databag."""
        data: MutableMapping[str, str] = (
            relation.data[relation.app] if relation and relation.app else {}
        )
        return cls.model_validate({**dict(data), "client_secret": client_secret})

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

    @property
    def uses_trusted_ca(self) -> bool:
        """A flag indicating if the IDP uses certificates signed by a trusted CA."""
        try:
            requests.get(self.issuer_url, timeout=10)
            return True
        except requests.exceptions.SSLError:
            return False
        except requests.exceptions.RequestException:
            return True
