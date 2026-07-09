# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Event handler for JWT authentication configuration."""

import logging

from ops import Object, RelationCreatedEvent

from single_kernel_opensearch_dashboards.charms.charm_status import StatusHandlingCharm
from single_kernel_opensearch_dashboards.common.literals import (
    CLUSTER_MANAGER_NAME,
    INGRESS_REL_NAME,
    Substrates,
)
from single_kernel_opensearch_dashboards.core.state import ClusterState
from single_kernel_opensearch_dashboards.core.statuses import ServerStatuses
from single_kernel_opensearch_dashboards.lib.charms.traefik_k8s.v2.ingress import (
    IngressPerAppReadyEvent,
    IngressPerAppRevokedEvent,
)
from single_kernel_opensearch_dashboards.utils.helpers import app_going_down

logger = logging.getLogger(__name__)


class IngressEvents(Object):
    """Handler for managing Ingress relations."""

    def __init__(
        self,
        charm: StatusHandlingCharm,
        state: ClusterState,
    ) -> None:
        super().__init__(charm, "ingress_events")  # type: ignore[arg-type]
        self.charm = charm
        self.state = state
        self.framework.observe(
            self.charm.on[INGRESS_REL_NAME].relation_created, self._on_relation_created
        )
        if self.state.substrate != Substrates.VM:
            self.framework.observe(
                self.charm.ingress_manager.ingress_requirer.on.ready, self._on_ingress_ready
            )
            self.framework.observe(
                self.charm.ingress_manager.ingress_requirer.on.revoked, self._on_ingress_revoked
            )

    def _on_ingress_ready(self, event: IngressPerAppReadyEvent) -> None:
        """Handle ingress ready event."""
        logger.info("Ingress ready at: %s", event.url)
        self.charm.emit_restart(event)

    def _on_ingress_revoked(self, event: IngressPerAppRevokedEvent) -> None:
        """Handle ingress revoked event."""
        if app_going_down(self.charm.base, event):
            return

        logger.warning("Ingress revoked, falling back to direct access.")
        self.charm.emit_restart(event)

    def _on_relation_created(self, event: RelationCreatedEvent) -> None:
        """Handle ingress relation created event."""
        if self.state.substrate != Substrates.VM:
            return

        logger.warning("Ingress on VM charm is not possible")
        self.state.add_status_to_both(
            ServerStatuses.INGRESS_RELATION_IN_VM.value, CLUSTER_MANAGER_NAME
        )
