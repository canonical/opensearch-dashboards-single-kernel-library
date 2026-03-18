# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Event handler for JWT authentication configuration."""

import logging

from ops import RelationBrokenEvent, RelationChangedEvent

from single_kernel_opensearch_dashboards.charms.base import (
    OpenSearchDashboardsStatusHandler,
)
from single_kernel_opensearch_dashboards.common.literals import (
    JWT_REL_NAME,
)
from single_kernel_opensearch_dashboards.core.cluster import ClusterState
from single_kernel_opensearch_dashboards.core.statuses import ConfigStatuses
from single_kernel_opensearch_dashboards.events.base import BaseEvents
from single_kernel_opensearch_dashboards.lib.charms.rolling_ops.v0.rollingops import (
    RollingOpsManager,
)
from single_kernel_opensearch_dashboards.managers.config import ConfigManager
from single_kernel_opensearch_dashboards.managers.health import HealthManager
from single_kernel_opensearch_dashboards.managers.server import ServerManager

logger = logging.getLogger(__name__)


class JwtEvents(BaseEvents):
    """Handler for managing JWT relations."""

    def __init__(
        self,
        charm: OpenSearchDashboardsStatusHandler,
        state: ClusterState,
        health_manager: HealthManager,
        config_manager: ConfigManager,
        server_manager: ServerManager,
        restart_manager: RollingOpsManager,
    ) -> None:
        super().__init__(
            charm,
            state,
            health_manager,
            config_manager,
            server_manager,
            restart_manager,
            "provider",
        )

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
                component="config_manager",
            )
            logger.error(f"Cannot access relation data for {JWT_REL_NAME}")
            return

        self.delete_status_if_present(
            status=ConfigStatuses.JWT_RELATIONS_DATA_FAILED.value,
            scope="app",
            component="config_manager",
        )
        self.charm.on[f"{self.restart_manager.name}"].acquire_lock.emit()

    def _on_jwt_relation_broken(self, event: RelationBrokenEvent) -> None:
        """Handle broken relation data."""
        self.delete_status_if_present(
            status=ConfigStatuses.JWT_RELATIONS_DATA_FAILED.value,
            scope="app",
            component="config_manager",
        )
        self.charm.on[f"{self.restart_manager.name}"].acquire_lock.emit()
