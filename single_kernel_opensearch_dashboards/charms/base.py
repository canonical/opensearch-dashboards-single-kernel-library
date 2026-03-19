#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""OpenSearch Dashboards Base Charm."""
import logging
from abc import abstractmethod

from data_platform_helpers.advanced_statuses import StatusHandler
from ops import EventBase

from single_kernel_opensearch_dashboards.charms.charm_status import (
    OpenSearchDashboardsStatusHandler,
)
from single_kernel_opensearch_dashboards.common.literals import (
    RESTART_REL_NAME,
    Substrates,
)
from single_kernel_opensearch_dashboards.core.cluster import ClusterState
from single_kernel_opensearch_dashboards.core.config import CharmConfig
from single_kernel_opensearch_dashboards.events.jwt_auth import JwtEvents
from single_kernel_opensearch_dashboards.events.oauth import OAuthEvents
from single_kernel_opensearch_dashboards.events.opensearch_dashboards import (
    OpenSearchDashboardsEvents,
)
from single_kernel_opensearch_dashboards.events.opensearch_requirer import (
    RequirerEvents,
)
from single_kernel_opensearch_dashboards.events.tls import TLSEvents
from single_kernel_opensearch_dashboards.events.upgrade import UpgradeEvents
from single_kernel_opensearch_dashboards.lib.charms.rolling_ops.v0.rollingops import (
    RollingOpsManager,
)
from single_kernel_opensearch_dashboards.managers.config import ConfigManager
from single_kernel_opensearch_dashboards.managers.cos import COSManager
from single_kernel_opensearch_dashboards.managers.health import HealthManager
from single_kernel_opensearch_dashboards.managers.server import ServerManager
from single_kernel_opensearch_dashboards.managers.tls import TLSManager
from single_kernel_opensearch_dashboards.managers.upgrade import UpgradeManager
from single_kernel_opensearch_dashboards.workload.base import WorkloadBase

logger = logging.getLogger(__name__)


class OpenSearchDashboardsBaseCharm(OpenSearchDashboardsStatusHandler):
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
        self.server_manager = ServerManager(self.state, self.workload)
        self.restart_manager = RollingOpsManager(
            self, relation=RESTART_REL_NAME, callback=self.restart
        )
        self.cos_manager = COSManager(self, self.state, self.workload, self.substrate)

        # --- Event Handlers ---
        self.opensearch_events = OpenSearchDashboardsEvents(
            self,
            self.state,
            self.health_manager,
            self.config_manager,
            self.server_manager,
            self.restart_manager,
            self.tls_manager,
        )
        self.jwt_events = JwtEvents(
            self,
            self.state,
            self.health_manager,
            self.config_manager,
            self.server_manager,
            self.restart_manager,
            self.tls_manager,
        )
        self.tls_events = TLSEvents(
            self,
            self.state,
            self.health_manager,
            self.config_manager,
            self.server_manager,
            self.restart_manager,
            self.tls_manager,
        )
        self.requirer_events = RequirerEvents(
            self,
            self.state,
            self.health_manager,
            self.config_manager,
            self.server_manager,
            self.restart_manager,
            self.tls_manager,
        )
        self.oauth = OAuthEvents(
            self,
            self.state,
            self.health_manager,
            self.config_manager,
            self.server_manager,
            self.restart_manager,
            self.tls_manager,
        )

        self.upgrade_events = UpgradeEvents(
            self, self.state, self.substrate, self.upgrade_manager, self.health_manager
        )

        self.status_handler = StatusHandler(
            self,
            self.config_manager,
            self.server_manager,
            self.health_manager,
            self.upgrade_manager,
            self.tls_manager,
            self.cos_manager,
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
        self.opensearch_events.restart(event)
