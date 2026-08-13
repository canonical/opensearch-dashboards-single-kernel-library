#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""State object for the opensearch-client relation"""

import logging
from typing import Any

from ops.model import Model, Relation
from pydantic import Field, ValidationError, field_validator

from single_kernel_opensearch_dashboards.lib.charms.data_platform_libs.v1.data_interfaces import (
    DataContractV1,
    OpsRelationRepository,
    ResourceProviderModel,
    build_model,
)

logger = logging.getLogger(__name__)


class OpensearchServer(ResourceProviderModel):
    """Connection metadata for a related OpenSearch server."""

    # The provider publishes endpoints as a comma-separated string; expose a sorted list.
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

    @classmethod
    def from_relation(cls, model: Model, relation: Relation | None) -> "OpensearchServer | None":
        """Build the model from the OpenSearch provider's application databag."""
        if not relation or not relation.app:
            return None

        repository = OpsRelationRepository(model, relation, component=relation.app)
        # A v1 provider tags its response with the literal marker "v1"; any
        # other value means a v0 response.
        is_v1 = repository.get_field("version") == "v1"
        try:
            if is_v1:
                contract = build_model(repository, DataContractV1[cls])
                return contract.requests[0] if contract.requests else None
            return build_model(repository, cls)
        except ValidationError as e:
            logger.error(f"Failed to validate opensearch response: {e}")
            return None
