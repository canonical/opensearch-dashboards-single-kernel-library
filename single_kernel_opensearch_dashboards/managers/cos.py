#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for handling COS"""

import json
import logging

from data_platform_helpers.advanced_statuses import StatusObject
from data_platform_helpers.advanced_statuses.types import Scope
from data_platform_helpers.version_check import get_charm_revision

from single_kernel_opensearch_dashboards.common.literals import (
    COS_MANAGER_NAME,
    COS_PORT,
    COS_RELATION_NAME,
    Substrates,
)
from single_kernel_opensearch_dashboards.core.config import CharmConfig
from single_kernel_opensearch_dashboards.core.state import ClusterState
from single_kernel_opensearch_dashboards.core.statuses import CharmStatuses, ServerStatuses
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

logger = logging.getLogger(__name__)


class COSManager(BaseManager):
    """Include the right cos."""

    GRAFANA_DASHBOARD_PATH = "src/grafana_dashboards/dashboard.json"

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

    def load_grafana_dashboard(self) -> dict | None:
        """Read and parse the charm's Grafana dashboard file.

        Returns None if the file cannot be read or does not contain a JSON object.
        """
        dashboard_path = self.charm.charm_dir / self.GRAFANA_DASHBOARD_PATH

        try:
            dashboard = json.loads(dashboard_path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            logger.error("Failed to read dashboard %s: %s", dashboard_path.name, e)
            return None

        if not isinstance(dashboard, dict):
            logger.error("Dashboard %s is not a JSON object", dashboard_path.name)
            return None

        return dashboard

    def update_grafana_dashboards_title(self) -> None:
        """Update the title of the Grafana dashboard file to include the charm revision."""
        dashboard = self.load_grafana_dashboard()
        if dashboard is None:
            return

        revision = get_charm_revision(self.charm.model.unit)
        dashboard_path = self.charm.charm_dir / self.GRAFANA_DASHBOARD_PATH

        old_title = dashboard.get("title", "Charmed OpenSearch Dashboards")
        if not isinstance(old_title, str):
            old_title = "Charmed OpenSearch Dashboards"
        title_prefix = old_title.split(" - Rev")[0]
        new_title = f"{title_prefix} - Rev {revision}"
        dashboard["title"] = new_title

        logger.info(
            "Changing the title of dashboard %s from %s to %s",
            dashboard_path.name,
            old_title,
            new_title,
        )

        try:
            dashboard_path.write_text(json.dumps(dashboard, indent=4))
        except OSError as e:
            logger.error("Failed to update the title of dashboard %s: %s", dashboard_path.name, e)

    def get_statuses(self, scope: Scope, recompute: bool = False) -> list[StatusObject]:
        """Compute the cos manager's statuses."""
        status_list: list[StatusObject] = []

        if self.state.unit_stopping or self.state.app_removal:
            return status_list

        if self.state.substrate == Substrates.VM and (
            self.state.loki_relation
            or self.state.grafana_relation
            or self.state.prometheus_relation
        ):
            status_list.append(ServerStatuses.COS_RELATION_IN_VM.value)
        elif self.state.substrate == Substrates.K8S and self.state.cos_agent_relation:
            status_list.append(ServerStatuses.COS_RELATION_IN_K8s.value)

        return status_list or [CharmStatuses.ACTIVE_IDLE.value]
