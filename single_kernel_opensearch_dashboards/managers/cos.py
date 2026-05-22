#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for handling COS"""

from data_platform_helpers.advanced_statuses import StatusObject
from data_platform_helpers.advanced_statuses.types import Scope

from single_kernel_opensearch_dashboards.common.literals import (
    COS_MANAGER_NAME,
    COS_PORT,
    COS_RELATION_NAME,
    Substrates,
)
from single_kernel_opensearch_dashboards.core.cluster import ClusterState
from single_kernel_opensearch_dashboards.core.config import CharmConfig
from single_kernel_opensearch_dashboards.core.statuses import CharmStatuses
from single_kernel_opensearch_dashboards.lib.charms.data_platform_libs.v1.data_models import (
    TypedCharmBase,
)
from single_kernel_opensearch_dashboards.lib.charms.grafana_agent.v0.cos_agent import (
    COSAgentProvider,
)
from single_kernel_opensearch_dashboards.lib.charms.grafana_k8s.v0.grafana_dashboard import (
    GrafanaDashboardProvider,
)
from single_kernel_opensearch_dashboards.lib.charms.loki_k8s.v1.loki_push_api import (
    LogForwarder,
)
from single_kernel_opensearch_dashboards.lib.charms.prometheus_k8s.v0.prometheus_scrape import (
    MetricsEndpointProvider,
)
from single_kernel_opensearch_dashboards.managers.base import BaseManager
from single_kernel_opensearch_dashboards.workload.base import WorkloadBase


class COSManager(BaseManager):
    """Include the right cos."""

    def __init__(
        self,
        charm: TypedCharmBase[CharmConfig],
        state: ClusterState,
        workload: WorkloadBase,
    ) -> None:
        super().__init__(state, workload)
        self.charm = charm
        self.name = COS_MANAGER_NAME

        if self.state.substrate == Substrates.VM:
            self.cos_integration = COSAgentProvider(
                self.charm,
                relation_name=COS_RELATION_NAME,
                metrics_endpoints=[],
                scrape_configs=self.scrape_config(),
                refresh_events=[self.charm.on.config_changed],
                metrics_rules_dir=(self.charm.charm_dir / "src/alert_rules/prometheus").as_posix(),
                log_slots=["opensearch-dashboards:logs"],
            )

        elif self.state.substrate == Substrates.K8S:
            # 1. Metrics (Prometheus)
            self.metrics_endpoint = MetricsEndpointProvider(
                self.charm,
                relation_name="metrics-endpoint",
                jobs=self.scrape_config(),
                alert_rules_path=f"{self.charm.charm_dir}/src/alert_rules/prometheus",
            )

            # 2. Logs (Loki)
            self.log_proxy = LogForwarder(
                self.charm,
                relation_name="logging",
            )

            # 3. Dashboards (Grafana)
            self.grafana_dashboards = GrafanaDashboardProvider(
                self.charm, relation_name="grafana-dashboard"
            )

    def scrape_config(self) -> list[dict]:
        """Generates the scrape config as needed."""
        target_ip = (
            f"{self.state.unit_server.private_ip}"
            if self.state.substrate == Substrates.VM
            else "*"
        )

        return [
            {
                "metrics_path": "/metrics",
                "static_configs": [{"targets": [f"{target_ip}:{COS_PORT}"]}],
                "scheme": "http",
            }
        ]

    def get_statuses(self, scope: Scope, recompute: bool = False) -> list[StatusObject]:
        """Compute the cos manager's statuses."""
        status_list: list[StatusObject] = []

        return status_list or [CharmStatuses.ACTIVE_IDLE.value]
