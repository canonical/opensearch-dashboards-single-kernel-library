#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Model for the dashboard_peers application relation."""

import logging

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


class OSDClusterModel(RelationModel, PeerModel):
    """Peer model mapping to the dashboards application state."""

    oauth_client_secret: ExtraSecretStr = Field(default="")
    monitor_username: ExtraSecretStr = Field(default="")
    monitor_password: ExtraSecretStr = Field(default="")

    @classmethod
    def secret_fields(cls) -> tuple[str, ...]:
        """Names of the fields stored in the ``extra`` Juju secret group."""
        return tuple(
            name
            for name, field in cls.model_fields.items()
            if field.metadata and field.metadata[-1] == "extra"
        )

    @model_validator(mode="after")
    def _coerce_missing_secrets(self) -> "OSDClusterModel":
        """Restore after creating secret groups"""
        for field in self.secret_fields():
            object.__setattr__(self, field, stripped_or_none(getattr(self, field)) or "")
        return self

    def initialize_empty_secrets(self) -> None:
        """Force creation of the app-level Juju secret for empty fields."""
        with self.update() as model:
            for field in self.secret_fields():
                if not getattr(model, field):
                    setattr(model, field, " ")
