# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Event handler for JWT authentication configuration."""

import logging

from ops import Object

from single_kernel_opensearch_dashboards.charms.charm_status import StatusHandlingCharm
from single_kernel_opensearch_dashboards.common.literals import (
    RelDepartureReason,
    Substrates,
)
from single_kernel_opensearch_dashboards.core.cluster import ClusterState
from single_kernel_opensearch_dashboards.lib.charms.traefik_k8s.v2.ingress import (
    IngressPerAppReadyEvent,
    IngressPerAppRevokedEvent,
)
from single_kernel_opensearch_dashboards.utils.helpers import relation_departure_reason

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
        if (
            relation_departure_reason(self.charm.base, event.relation.name, event.app.name)
            == RelDepartureReason.APP_REMOVAL
        ):
            return
        logger.warning("Ingress revoked, falling back to direct access.")
        self.charm.emit_restart(event)
