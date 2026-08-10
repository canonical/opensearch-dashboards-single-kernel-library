#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Model for the dashboard_peers relation."""

import logging
from typing import Optional

from pydantic import Field, model_validator

from single_kernel_opensearch_dashboards.core.relation_base import (
    RelationModel,
    stripped_or_none,
)
from single_kernel_opensearch_dashboards.lib.charms.data_platform_libs.v1.data_interfaces import (
    ExtraSecretStr,
    PeerModel,
)

logger = logging.getLogger(__name__)


class OSDServerModel(RelationModel, PeerModel):
    """Peer model mapping to a dashboards unit's databag."""

    # True once the workload service is running.
    started: bool = Field(default=False)
    # Whether the unit has rotated its internal passwords.
    password_rotated: bool = Field(default=False, alias="password-rotated")
    log_level: Optional[str] = Field(default=None, alias="log_level")

    ca_cert: ExtraSecretStr = Field(default="")
    csr: ExtraSecretStr = Field(default="")
    certificate: ExtraSecretStr = Field(default="")
    private_key: ExtraSecretStr = Field(default="")

    @classmethod
    def _secret_fields(cls) -> tuple[str, ...]:
        """Names of the fields stored in the ``extra`` Juju secret group."""
        return tuple(
            name
            for name, field in cls.model_fields.items()
            if field.metadata and field.metadata[-1] == "extra"
        )

    @model_validator(mode="after")
    def _coerce_missing_secrets(self) -> "OSDServerModel":
        """Restore after creating secret groups"""
        for field in self._secret_fields():
            object.__setattr__(self, field, stripped_or_none(getattr(self, field)) or "")
        return self

    def initialize_empty_secrets(self) -> None:
        """Force creation of the unit-level Juju secret for empty fields."""
        with self.update() as model:
            for field in self._secret_fields():
                if not getattr(model, field):
                    setattr(model, field, " ")

    # --- TLS ---

    @property
    def ca(self) -> str:
        """The root CA contents for the unit to use for TLS (alias of ca_cert)."""
        return self.ca_cert or ""

    @property
    def tls_enabled(self) -> bool:
        """Flag to check if TLS is enabled for the unit."""
        return bool(self.ca) and bool(self.certificate) and bool(self.private_key)
