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
from single_kernel_opensearch_dashboards.core.statuses import (
    HealthStatuses,
    ServerStatuses,
)
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
from single_kernel_opensearch_dashboards.managers.cluster import ClusterManager
from single_kernel_opensearch_dashboards.managers.config import ConfigManager
from single_kernel_opensearch_dashboards.managers.cos import COSManager
from single_kernel_opensearch_dashboards.managers.health import HealthManager
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
        self.health_manager = HealthManager(self.state, self.workload, self.substrate)
        self.config_manager = ConfigManager(self.state, self.workload, self.substrate)
        self.upgrade_manager = UpgradeManager(self.state, self.workload)
        self.cluster_manager = ClusterManager(self.state, self.workload)
        self.restart_manager = RollingOpsManager(
            self, relation=RESTART_REL_NAME, callback=self.restart
        )
        self.cos_manager = COSManager(self, self.state, self.workload, self.substrate)

        # --- Event Handlers ---
        self.opensearch_events = OpenSearchDashboardsEvents(
            self,
            self.state,
            self.cluster_manager,
            self.substrate,
        )
        self.jwt_events = JwtEvents(
            self,
            self.state,
        )
        self.tls_events = TLSEvents(
            self,
            self.state,
            self.tls_manager,
        )
        self.requirer_events = RequirerEvents(self, self.state, self.tls_manager, self.substrate)
        self.oauth = OAuthEvents(
            self,
            self.state,
        )

        self.upgrade_events = UpgradeEvents(
            self,
            self.state,
            self.substrate,
            self.upgrade_manager,
            self.health_manager,
            self.tls_manager,
        )

        self.status_handler = StatusHandler(
            self,
            self.config_manager,
            self.cluster_manager,
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
        """Restart method for RollingOpsManager"""
        logger.debug(f"Setting dashboards properties for {event.framework.model.unit.name}")
        self.config_manager.set_dashboard_properties()

        self.state.delete_status_if_present(
            status=ServerStatuses.WAITING_ON_RESTART.value,
            scope="unit",
            component=self.cluster_manager.name,
        )

        if not self.state.unit_server.started:
            self.status_handler.set_running_status(
                status=ServerStatuses.STARTING_SERVER.value,
                component_name=self.cluster_manager.name,
                scope="unit",
            )
            self.cluster_manager.init_server()
            self.state.unit_server.update({"state": "started"})

            # Set ca if unit was added after opensearch relation creation
            self.tls_manager.write_tls_files()
        else:
            self.status_handler.set_running_status(
                status=ServerStatuses.RESTARTING_SERVER.value,
                component_name=self.cluster_manager.name,
                scope="unit",
            )
            self.cluster_manager.restart_server()

            # Set ca if pod was re-created
            if self.substrate == Substrates.K8S:
                self.tls_manager.write_tls_files()

        # Checking health after restart
        self.status_handler.set_running_status(
            status=HealthStatuses.AFTER_RESTART.value,
            component_name=self.health_manager.name,
            scope="unit",
        )
        self._check_osd_status()

    def _check_osd_status(self) -> None:
        """Verify the OpenSearch connection and trigger a health check.
        Returns true if OSD server is healthy otherwise false
        """
        # OPENSEARCH CONNECTION
        self.state.delete_status_if_present_both(
            status=ServerStatuses.DB_CONNECTION_MISSING.value, component=self.cluster_manager.name
        )

        if not self.state.opensearch_server:
            self.state.add_status_to_both(
                status=ServerStatuses.DB_CONNECTION_MISSING.value,
                component=self.cluster_manager.name,
            )

        # HEALTH
        self.health_manager.check_osd_health()

    def emit_restart(self, event: EventBase) -> None:
        """Evaluate conditions and emit a restart lock request if necessary."""
        if not self.config_manager.config_changed():
            if self.health_manager.check_unit_health():
                logger.debug("OpenSearch Dashboards is healthy and config is same, not restarting")
                return

        self.state.statuses.add(
            status=ServerStatuses.WAITING_ON_RESTART.value,
            scope="unit",
            component=self.cluster_manager.name,
        )

        self.on[self.restart_manager.name].acquire_lock.emit()
