# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Event handler for JWT authentication configuration."""

import logging

from ops import Object

from single_kernel_opensearch_dashboards.charms.base import (
    OpenSearchDashboardsStatusHandler,
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
        charm: OpenSearchDashboardsStatusHandler,
        state: ClusterState,
    ) -> None:
        super().__init__(charm, "ingress_events")
        self.charm = charm
        self.state = state

        self.framework.observe(
            getattr(self.charm, "ingress_manager").ingress.on.ready, self._on_ingress_ready
        )
        self.framework.observe(
            getattr(self.charm, "ingress_manager").ingress.on.revoked, self._on_ingress_revoked
        )

    def _on_ingress_ready(self, event: IngressPerAppReadyEvent) -> None:
        """Handle ingress ready event."""
        logger.info("Ingress ready at: %s", event.url)
        self.charm.emit_restart(event)

    def _on_ingress_revoked(self, event: IngressPerAppRevokedEvent) -> None:
        """Handle ingress revoked event."""
        logger.warning("Ingress revoked, falling back to direct access. %s")
        self.charm.emit_restart(event)
