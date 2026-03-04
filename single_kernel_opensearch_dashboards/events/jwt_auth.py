# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Event handler for JWT authentication configuration."""

import logging

from ops import Object, RelationChangedEvent

from single_kernel_opensearch_dashboards.common.literals import (
    JWT_REL_NAME,
)
from single_kernel_opensearch_dashboards.core.cluster import ClusterState
from single_kernel_opensearch_dashboards.core.config import CharmConfig
from single_kernel_opensearch_dashboards.events.shared_events import SharedEvents
from single_kernel_opensearch_dashboards.lib.charms.data_platform_libs.v1.data_models import (
    TypedCharmBase,
)

logger = logging.getLogger(__name__)


class JwtEvents(Object):
    """Handler for managing JWT relations."""

    def __init__(
        self, charm: TypedCharmBase[CharmConfig], state: ClusterState, shared_events: SharedEvents
    ) -> None:
        super().__init__(charm, "provider")
        self.charm = charm
        self.state = state
        self.shared_events = shared_events

        self.framework.observe(
            self.charm.on[JWT_REL_NAME].relation_changed, self._on_jwt_relation_changed
        )
        self.framework.observe(
            self.charm.on[JWT_REL_NAME].relation_broken, self.shared_events.reconcile
        )

    def _on_jwt_relation_changed(self, event: RelationChangedEvent) -> None:
        """Handle changed relation data."""
        if not self.state.jwt_relation:
            logger.error(f"Cannot access relation data for {JWT_REL_NAME}")
            return
        self.shared_events.reconcile(event)
