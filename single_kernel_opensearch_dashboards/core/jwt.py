#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""JWT authentication configuration model."""

import logging

from ops.model import Model, Relation
from pydantic import Field, ValidationError

from single_kernel_opensearch_dashboards.lib.charms.data_platform_libs.v1.data_interfaces import (
    DataContractV1,
    ExtraSecretStr,
    OpsRelationRepository,
    ResourceProviderModel,
    build_model,
)

logger = logging.getLogger(__name__)


class JWTAuthConfiguration(ResourceProviderModel):
    """Model for the configuration parameters published by a JWT provider.

    Subclasses ``ResourceProviderModel`` so the same instance can be parsed from both a
    data-interfaces v0 provider (flat databag) and a v1 provider (``DataContractV1``).
    """

    signing_key: ExtraSecretStr = Field(default=None)
    jwt_url_parameter: str | None = Field(default=None)

    @classmethod
    def from_relation(
        cls, model: Model, relation: Relation | None
    ) -> "JWTAuthConfiguration | None":
        """Build the configuration from the JWT provider's application databag."""
        if not relation or not relation.app:
            return None

        repository = OpsRelationRepository(model, relation, component=relation.app)
        # A v1 provider tags its response with the literal marker "v1"; any
        # other value (or none) means a v0 response.
        is_v1 = repository.get_field("version") == "v1"
        try:
            if is_v1:
                contract = build_model(repository, DataContractV1[cls])
                return contract.requests[0] if contract.requests else None
            return build_model(repository, cls)
        except ValidationError as e:
            logger.error(f"Failed to validate jwt configuration: {e}")
            return None
