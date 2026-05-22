# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Event handler for JWT authentication configuration."""

import logging

from ops import Object, RelationDepartedEvent

from single_kernel_opensearch_dashboards.charms.charm_status import StatusHandlingCharm
from single_kernel_opensearch_dashboards.common.literals import (
    INGRESS_REL_NAME,
    Substrates,
)
from single_kernel_opensearch_dashboards.core.cluster import ClusterState
from single_kernel_opensearch_dashboards.lib.charms.traefik_k8s.v2.ingress import (
    IngressPerAppReadyEvent,
    IngressPerAppRevokedEvent,
)

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
        if self.state.substrate == Substrates.K8S:
            self.framework.observe(self.state.ingress_requirer.on.ready, self._on_ingress_ready)
            self.framework.observe(
                self.state.ingress_requirer.on.revoked, self._on_ingress_revoked
            )
            self.framework.observe(
                self.charm.on[INGRESS_REL_NAME].relation_departed, self._on_ingress_departed
            )

    def _on_ingress_ready(self, event: IngressPerAppReadyEvent) -> None:
        """Handle ingress ready event."""
        self.charm.emit_restart(event)

    def _on_ingress_revoked(self, event: IngressPerAppRevokedEvent) -> None:
        """Handle ingress revoked event."""
        if self.state.unit_server.unit_dying:
            return
        logger.warning("Ingress revoked, falling back to direct access.")
        self.charm.emit_restart(event)

    def _on_ingress_departed(self, event: RelationDepartedEvent) -> None:
        """Handle unit dying."""
        if event.departing_unit == self.charm.unit:
            self.state.unit_server.unit_dying = True
