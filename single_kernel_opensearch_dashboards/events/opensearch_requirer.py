#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Event handler for related applications on the `opensearch-client` relation interface."""
import logging

from ops.charm import RelationBrokenEvent, RelationEvent
from ops.framework import Object

from single_kernel_opensearch_dashboards.common.literals import OPENSEARCH_REL_NAME
from single_kernel_opensearch_dashboards.core.cluster import ClusterState
from single_kernel_opensearch_dashboards.core.config import CharmConfig
from single_kernel_opensearch_dashboards.events.shared_events import SharedEvents
from single_kernel_opensearch_dashboards.lib.charms.data_platform_libs.v0.data_interfaces import (
    OpenSearchRequiresEventHandlers,
)
from single_kernel_opensearch_dashboards.lib.charms.data_platform_libs.v1.data_models import (
    TypedCharmBase,
)
from single_kernel_opensearch_dashboards.workload.base import WorkloadBase

logger = logging.getLogger(__name__)


class RequirerEvents(Object):
    """Event handlers for related applications on the `opensearch-client` relation interface."""

    def __init__(
        self,
        charm: TypedCharmBase[CharmConfig],
        state: ClusterState,
        workload: WorkloadBase,
        shared_events: SharedEvents,
    ) -> None:
        super().__init__(charm, "provider")
        self.charm = charm
        self.state = state
        self.workload = workload
        self.shared_events = shared_events

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
            self.workload.write_text(
                self.state.opensearch_server.tls_ca, self.workload.paths.opensearch_ca
            )
            self.shared_events.reconcile(event)

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
