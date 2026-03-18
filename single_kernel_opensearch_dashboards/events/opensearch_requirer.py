#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Event handler for related applications on the `opensearch-client` relation interface."""
import logging

from ops.charm import RelationBrokenEvent, RelationEvent

from single_kernel_opensearch_dashboards.charms.base import (
    OpenSearchDashboardsStatusHandler,
)
from single_kernel_opensearch_dashboards.common.literals import OPENSEARCH_REL_NAME
from single_kernel_opensearch_dashboards.core.cluster import ClusterState
from single_kernel_opensearch_dashboards.events.base import BaseEvents
from single_kernel_opensearch_dashboards.lib.charms.data_platform_libs.v0.data_interfaces import (
    OpenSearchRequiresEventHandlers,
)
from single_kernel_opensearch_dashboards.lib.charms.rolling_ops.v0.rollingops import (
    RollingOpsManager,
)
from single_kernel_opensearch_dashboards.managers.config import ConfigManager
from single_kernel_opensearch_dashboards.managers.health import HealthManager
from single_kernel_opensearch_dashboards.managers.server import ServerManager
from single_kernel_opensearch_dashboards.managers.tls import TLSManager

logger = logging.getLogger(__name__)


class RequirerEvents(BaseEvents):
    """Event handlers for related applications on the `opensearch-client` relation interface."""

    def __init__(
        self,
        charm: OpenSearchDashboardsStatusHandler,
        state: ClusterState,
        health_manager: HealthManager,
        config_manager: ConfigManager,
        server_manager: ServerManager,
        restart_manager: RollingOpsManager,
        tls_manager: TLSManager,
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
        self.tls_manager = tls_manager

        self.requirer_events = OpenSearchRequiresEventHandlers(
            self.charm, self.state.client_requires_data
        )
        self.framework.observe(
            self.charm.on[OPENSEARCH_REL_NAME].relation_changed, self._on_client_relation_changed
        )
        self.framework.observe(
            self.charm.on[OPENSEARCH_REL_NAME].relation_broken, self._on_client_relation_broken
        )

    def _on_client_relation_changed(self, event: RelationEvent) -> None:
        """Updates ACLs while handling `client_relation_changed` events."""
        if not self.state.stable:
            event.defer()
            return

        if (
            self.state.opensearch_server
            and self.state.opensearch_server.password
            and self.state.opensearch_server.endpoints
            and self.state.opensearch_server.tls_ca
        ):
            self.tls_manager.set_ca_opensearch()
            self.charm.on[f"{self.restart_manager.name}"].acquire_lock.emit()

    def _on_client_relation_broken(self, event: RelationBrokenEvent) -> None:
        """Restoring config to defaults if the relation is gone.

        Args:
            event: used for passing `RelationBrokenEvent` to subsequent methods
        """
        # Don't remove anything if the service is going down
        if self.charm.app.planned_units == 0 or not self.charm.unit.is_leader():
            return

        # call normal updated handler
        self._on_client_relation_changed(event=event)
