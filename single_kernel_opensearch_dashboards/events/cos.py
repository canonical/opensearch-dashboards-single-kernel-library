# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Event handler for JWT authentication configuration."""

import logging

from ops import Object, RelationCreatedEvent

from single_kernel_opensearch_dashboards.charms.charm_status import StatusHandlingCharm
from single_kernel_opensearch_dashboards.common.literals import (
    CLUSTER_MANAGER_NAME,
    COS_RELATION_NAME,
    GRAFANA_RELATION_NAME,
    LOKI_RELATION_NAME,
    PROMETHEUS_RELATION_NAME,
    Substrates,
)
from single_kernel_opensearch_dashboards.core.state import ClusterState
from single_kernel_opensearch_dashboards.core.statuses import ServerStatuses

logger = logging.getLogger(__name__)


class COSEvents(Object):
    """Handler for managing COS relations."""

    def __init__(
        self,
        charm: StatusHandlingCharm,
        state: ClusterState,
    ) -> None:
        super().__init__(charm, "cos_events")  # type: ignore[arg-type]
        self.charm = charm
        self.state = state
        self.framework.observe(
            self.charm.on[COS_RELATION_NAME].relation_created, self._on_cos_relation_created
        )
        self.framework.observe(
            self.charm.on[PROMETHEUS_RELATION_NAME].relation_created, self._on_cos_relation_created
        )
        self.framework.observe(
            self.charm.on[LOKI_RELATION_NAME].relation_created, self._on_cos_relation_created
        )
        self.framework.observe(
            self.charm.on[GRAFANA_RELATION_NAME].relation_created, self._on_cos_relation_created
        )

    def _on_cos_relation_created(self, event: RelationCreatedEvent) -> None:
        """Handle the `RelationCreatedEvent` event."""
        if self.state.substrate == Substrates.VM and (
            self.state.loki_relation
            or self.state.grafana_relation
            or self.state.prometheus_relation
        ):
            logger.warning(
                "grafana-k8s, loki-k8s, prometheus-k8s relation is not possible for vm, use grafana-agent instead"
            )
            return
        if self.state.substrate == Substrates.K8S and self.state.cos_agent_relation:
            logger.warning(
                "grafana-agent relation is not possible for k8s, use grafana-k8s, loki-k8s, prometheus-k8s instead"
            )
