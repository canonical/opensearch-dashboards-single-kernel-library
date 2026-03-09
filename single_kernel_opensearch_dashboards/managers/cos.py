#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""The COS manager.

This class is purely there for separation of purposes, it will just include the
right observability stack.
"""

from single_kernel_opensearch_dashboards.common.literals import (
    COS_PORT,
    COS_RELATION_NAME,
    Substrates,
)
from single_kernel_opensearch_dashboards.core.cluster import ClusterState
from single_kernel_opensearch_dashboards.core.config import CharmConfig
from single_kernel_opensearch_dashboards.lib.charms.data_platform_libs.v1.data_models import (
    TypedCharmBase,
)
from single_kernel_opensearch_dashboards.lib.charms.grafana_agent.v0.cos_agent import (
    COSAgentProvider,
)
from single_kernel_opensearch_dashboards.workload.base import WorkloadBase


class COSManager:
    """Include the right cos."""

    def __init__(
        self,
        charm: TypedCharmBase[CharmConfig],
        state: ClusterState,
        workload: WorkloadBase,
        substrate: Substrates,
    ) -> None:
        self.state = state
        self.workload = workload
        self.substrate = substrate
        self.charm = charm

        if self.substrate == Substrates.VM:
            self.cos_integration = COSAgentProvider(
                self.charm,
                relation_name=COS_RELATION_NAME,
                metrics_endpoints=[],
                scrape_configs=self.scrape_config,
                refresh_events=[self.charm.on.config_changed],
                metrics_rules_dir=(self.charm.charm_dir / "src/alert_rules/prometheus").as_posix(),
                log_slots=["opensearch-dashboards:logs"],
            )

    # --- CONVENIENCE METHODS ---
    def scrape_config(self) -> list[dict]:
        """Generates the scrape config as needed."""
        return [
            {
                "metrics_path": "/metrics",
                "static_configs": [
                    {"targets": [f"{self.state.unit_server.private_ip}:{COS_PORT}"]}
                ],
                # "tls_config": {"ca": self.state.unit_server.ca},
                "scheme": "http",
            }
        ]
