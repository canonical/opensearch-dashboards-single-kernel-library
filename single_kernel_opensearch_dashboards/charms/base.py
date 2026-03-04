#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""OpenSearch Dashboards Base Charm."""
import logging
from abc import ABC, abstractmethod

from ops import EventBase

from single_kernel_opensearch_dashboards.common.literals import (
    COS_RELATION_NAME,
    Substrates,
)
from single_kernel_opensearch_dashboards.core.cluster import ClusterState
from single_kernel_opensearch_dashboards.core.config import CharmConfig
from single_kernel_opensearch_dashboards.events.jwt_auth import JwtEvents
from single_kernel_opensearch_dashboards.events.oauth import OAuthEvents
from single_kernel_opensearch_dashboards.events.opensearch_dashboards import (
    OpenSearchDashboardsEvents,
)
from single_kernel_opensearch_dashboards.events.requirer import RequirerEvents
from single_kernel_opensearch_dashboards.events.shared_events import SharedEvents
from single_kernel_opensearch_dashboards.events.tls import TLSEvents
from single_kernel_opensearch_dashboards.events.upgrade import UpgradeEvents
from single_kernel_opensearch_dashboards.lib.charms.data_platform_libs.v1.data_models import (
    TypedCharmBase,
)
from single_kernel_opensearch_dashboards.lib.charms.grafana_agent.v0.cos_agent import (
    COSAgentProvider,
)
from single_kernel_opensearch_dashboards.lib.charms.rolling_ops.v0.rollingops import (
    RollingOpsManager,
)
from single_kernel_opensearch_dashboards.managers.config import ConfigManager
from single_kernel_opensearch_dashboards.managers.health import HealthManager
from single_kernel_opensearch_dashboards.managers.tls import TLSManager
from single_kernel_opensearch_dashboards.managers.upgrade import UpgradeManager
from single_kernel_opensearch_dashboards.workload.base import WorkloadBase

logger = logging.getLogger(__name__)


class OpenSearchDashboardsBaseCharm(TypedCharmBase[CharmConfig], ABC):
    """Base OpenSearch Dashboards Charm, this will include base structure for both machine and k8s charms."""

    config_type = CharmConfig

    def __init__(self, *args):
        super().__init__(*args)

        # --- State ---
        self.state = ClusterState(self, self.substrate)

        # --- Managers ---
        self.tls_manager = TLSManager(self.state, self.workload)
        self.health_manager = HealthManager(self.state, self.workload)
        self.config_manager = ConfigManager(self.state, self.workload)
        self.upgrade_manager = UpgradeManager(self.state, self.workload)
        self.restart_manager = RollingOpsManager(self, relation="restart", callback=self.restart)

        # --- Helper class ---
        self.shared_events = SharedEvents(
            self,
            self.state,
            self.workload,
            self.health_manager,
            self.restart_manager,
            self.config_manager,
            self.tls_manager,
            self.upgrade_manager,
        )

        # --- Event Handlers ---
        self.opensearch_events = OpenSearchDashboardsEvents(
            self, self.state, self.workload, self.shared_events
        )
        self.jwt_events = JwtEvents(self, self.state, self.shared_events)
        self.tls_events = TLSEvents(self, self.state, self.tls_manager)
        self.requirer_events = RequirerEvents(self, self.state, self.workload, self.shared_events)
        self.oauth = OAuthEvents(self, self.state, self.shared_events)
        self.upgrade_events = UpgradeEvents(
            self,
            self.state,
            self.workload,
            self.substrate,
            self.upgrade_manager,
            self.shared_events,
        )

        # --- COS ---
        self.cos_integration = COSAgentProvider(
            self,
            relation_name=COS_RELATION_NAME,
            metrics_endpoints=[],
            scrape_configs=self.shared_events.scrape_config,
            refresh_events=[self.on.config_changed],
            metrics_rules_dir=(self.charm_dir / "src/alert_rules/prometheus").as_posix(),
            log_slots=["opensearch-dashboards:logs"],
        )

    @property
    @abstractmethod
    def workload(self) -> WorkloadBase:
        """Access current workload."""
        ...

    @property
    @abstractmethod
    def substrate(self) -> Substrates:
        """Access current substrate."""
        ...

    def restart(self, event: EventBase):
        """
        Helper method for RollingOpsManager
        If callback method is not directly in charm class it will throw error
        """
        self.shared_events.restart(event)
