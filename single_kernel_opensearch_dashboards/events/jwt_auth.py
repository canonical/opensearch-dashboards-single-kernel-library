# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Event handler for JWT authentication configuration."""

import logging

from ops import Object, RelationBrokenEvent, RelationChangedEvent

from single_kernel_opensearch_dashboards.charms.base import (
    OpenSearchDashboardsStatusHandler,
)
from single_kernel_opensearch_dashboards.common.literals import (
    CONFIG_MANAGER_NAME,
    JWT_REL_NAME,
)
from single_kernel_opensearch_dashboards.core.cluster import ClusterState
from single_kernel_opensearch_dashboards.core.statuses import ConfigStatuses

logger = logging.getLogger(__name__)


class JwtEvents(Object):
    """Handler for managing JWT relations."""

    def __init__(
        self,
        charm: OpenSearchDashboardsStatusHandler,
        state: ClusterState,
    ) -> None:
        super().__init__(
            charm,
            "jwt_events",
        )
        self.charm = charm
        self.state = state

        self.framework.observe(
            self.charm.on[JWT_REL_NAME].relation_changed, self._on_jwt_relation_changed
        )
        self.framework.observe(
            self.charm.on[JWT_REL_NAME].relation_broken, self._on_jwt_relation_broken
        )

    def _on_jwt_relation_changed(self, event: RelationChangedEvent) -> None:
        """Handle changed relation data."""
        if not self.state.jwt_relation:
            self.state.statuses.add(
                status=ConfigStatuses.JWT_RELATIONS_DATA_FAILED.value,
                scope="app",
                component=CONFIG_MANAGER_NAME,
            )
            logger.error(f"Cannot access relation data for {JWT_REL_NAME}")
            return

        self.state.delete_status_if_present(
            status=ConfigStatuses.JWT_RELATIONS_DATA_FAILED.value,
            scope="app",
            component=CONFIG_MANAGER_NAME,
        )
        self.charm.emit_restart(event)

    def _on_jwt_relation_broken(self, event: RelationBrokenEvent) -> None:
        """Handle broken relation data."""
        self.state.delete_status_if_present(
            status=ConfigStatuses.JWT_RELATIONS_DATA_FAILED.value,
            scope="app",
            component=CONFIG_MANAGER_NAME,
        )
        self.charm.emit_restart(event)
