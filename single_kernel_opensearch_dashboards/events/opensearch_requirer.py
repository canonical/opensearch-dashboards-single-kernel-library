#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Event handler for related applications on the `opensearch-client` relation interface."""
import logging

from ops import Object
from ops.charm import RelationBrokenEvent, RelationDepartedEvent, RelationEvent

from single_kernel_opensearch_dashboards.charms.base import (
    OpenSearchDashboardsStatusHandler,
)
from single_kernel_opensearch_dashboards.common.exceptions import OSDFileOperationError
from single_kernel_opensearch_dashboards.common.literals import (
    CLUSTER_MANAGER_NAME,
    OPENSEARCH_REL_NAME,
)
from single_kernel_opensearch_dashboards.core.cluster import ClusterState
from single_kernel_opensearch_dashboards.core.statuses import ServerStatuses
from single_kernel_opensearch_dashboards.lib.charms.data_platform_libs.v0.data_interfaces import (
    OpenSearchRequiresEventHandlers,
)
from single_kernel_opensearch_dashboards.managers.tls import TLSManager

logger = logging.getLogger(__name__)


class RequirerEvents(Object):
    """Event handlers for related applications on the `opensearch-client` relation interface."""

    def __init__(
        self,
        charm: OpenSearchDashboardsStatusHandler,
        state: ClusterState,
        tls_manager: TLSManager,
    ) -> None:
        super().__init__(
            charm,
            "provider",
        )
        self.charm = charm
        self.state = state
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
        self.framework.observe(
            self.charm.on[OPENSEARCH_REL_NAME].relation_departed, self._on_client_departed
        )

    def _on_client_relation_changed(self, event: RelationEvent) -> None:
        """Updates ACLs while handling `client_relation_changed` events."""
        if not self.state.stable:
            event.defer()
            return
        try:
            self.tls_manager.set_ca_opensearch()
            self.charm.emit_restart(event)
        except OSDFileOperationError as e:
            logger.error(f"Operation with files is failed: {e}. Deferring event.")
            event.defer()
            return

    def _on_client_relation_broken(self, event: RelationBrokenEvent) -> None:
        """Restoring config to defaults if the relation is gone.

        Args:
            event: used for passing `RelationBrokenEvent` to subsequent methods
        """
        if self.state.unit_server.unit_dying:
            return

        self.state.add_status_to_both(
            status=ServerStatuses.DB_CONNECTION_MISSING.value,
            component=CLUSTER_MANAGER_NAME,
        )

        # call normal updated handler
        self._on_client_relation_changed(event=event)

    def _on_client_departed(self, event: RelationDepartedEvent) -> None:
        """Handle unit dying."""
        if event.departing_unit == self.charm.unit:
            self.state.unit_server.unit_dying = True
