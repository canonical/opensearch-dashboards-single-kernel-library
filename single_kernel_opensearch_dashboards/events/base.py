#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Methods that can be used by other OpenSearch Dashboards events."""

import logging
from typing import Literal

from data_platform_helpers.advanced_statuses import StatusObject
from ops import (
    EventBase,
    Object,
)

from single_kernel_opensearch_dashboards.charms.base import (
    OpenSearchDashboardsStatusHandler,
)
from single_kernel_opensearch_dashboards.common.literals import (
    CONFIG_MANAGER_NAME,
    HEALTH_MANAGER_NAME,
    SERVER_MANAGER_NAME,
    UPGRADE_MANAGER_NAME,
)
from single_kernel_opensearch_dashboards.core.cluster import ClusterState
from single_kernel_opensearch_dashboards.core.statuses import (
    ConfigStatuses,
    HealthStatuses,
    ServerStatuses,
    UpgradeStatuses,
)
from single_kernel_opensearch_dashboards.lib.charms.rolling_ops.v0.rollingops import (
    RollingOpsManager,
)
from single_kernel_opensearch_dashboards.managers.config import ConfigManager
from single_kernel_opensearch_dashboards.managers.health import HealthManager
from single_kernel_opensearch_dashboards.managers.server import ServerManager
from single_kernel_opensearch_dashboards.managers.tls import TLSManager

logger = logging.getLogger(__name__)


class BaseEvents(Object):
    """Base class providing shared methods and properties for OpenSearch Dashboards events."""

    def __init__(
        self,
        charm: OpenSearchDashboardsStatusHandler,
        state: ClusterState,
        health_manager: HealthManager,
        config_manager: ConfigManager,
        server_manager: ServerManager,
        restart_manager: RollingOpsManager,
        tls_manager: TLSManager,
        key: str,
    ) -> None:
        """Initialize the BaseEvents object."""
        super().__init__(charm, key)
        self.state = state
        self.charm = charm
        self.health_manager = health_manager
        self.config_manager = config_manager
        self.server_manager = server_manager
        self.restart_manager = restart_manager
        self.tls_manager = tls_manager

    def emit_restart(self, event: EventBase) -> None:
        """Evaluate conditions and emit a restart lock request if necessary."""
        if not self.config_manager.config_changed():
            if self.health_manager.check_unit_health():
                logger.debug("OpenSearch Dashboards is healthy and config is same, not restarting")
                return

        self.state.statuses.add(
            status=ServerStatuses.WAITING_ON_RESTART.value,
            scope="unit",
            component=SERVER_MANAGER_NAME,
        )

        self.charm.on[self.restart_manager.name].acquire_lock.emit()

    def restart(self, event: EventBase) -> None:
        """Execute the restart logic for the OpenSearch Dashboards server."""
        logger.debug(f"Creating properties for {event.framework.model.unit.name}")
        self.config_manager.set_dashboard_properties()

        self.delete_status_if_present(
            status=ServerStatuses.WAITING_ON_RESTART.value,
            scope="unit",
            component=SERVER_MANAGER_NAME,
        )

        if not self.state.unit_server.started:
            self.charm.status_handler.set_running_status(
                status=ServerStatuses.STARTING_SERVER.value,
                component_name=SERVER_MANAGER_NAME,
                scope="unit",
            )
            self.server_manager.init_server()
            self.state.unit_server.update({"state": "started"})

            # Set ca if unit was added after opensearch relation creation
            self.tls_manager.set_ca_opensearch()
        else:
            self.charm.status_handler.set_running_status(
                status=ServerStatuses.RESTARTING_SERVER.value,
                component_name=SERVER_MANAGER_NAME,
                scope="unit",
            )
            self.server_manager.restart_server()

        # Checking health after restart
        self.charm.status_handler.set_running_status(
            status=HealthStatuses.AFTER_RESTART.value,
            component_name=HEALTH_MANAGER_NAME,
            scope="unit",
        )
        self.check_osd_status()

    def pre_restart_check(self) -> bool:
        """Perform pre-flight checks to determine if a restart can proceed."""
        # PEER RELATION CHECK
        if not self.state.peer_relation:
            logger.debug("Waiting for peer relations")
            if self.state.unit.is_leader():
                self.state.statuses.add(
                    status=ConfigStatuses.WAITING_FOR_PEER.value,
                    scope="app",
                    component=CONFIG_MANAGER_NAME,
                )
            self.state.statuses.add(
                status=ConfigStatuses.WAITING_FOR_PEER.value,
                scope="unit",
                component=CONFIG_MANAGER_NAME,
            )
            return False

        self.delete_status_if_present(
            status=ConfigStatuses.WAITING_FOR_PEER.value,
            scope="unit",
            component=CONFIG_MANAGER_NAME,
        )
        self.delete_status_if_present(
            status=ConfigStatuses.WAITING_FOR_PEER.value,
            scope="app",
            component=CONFIG_MANAGER_NAME,
        )

        # UPGRADE IDLE CHECK
        if not self.state.upgrade_idle:
            logger.debug("Waiting for upgrade relations to be idle")
            if self.state.unit.is_leader():
                self.state.statuses.add(
                    status=UpgradeStatuses.WAITING_FOR_UPGRADE.value,
                    scope="app",
                    component=UPGRADE_MANAGER_NAME,
                )
            self.state.statuses.add(
                status=UpgradeStatuses.WAITING_FOR_UPGRADE.value,
                scope="unit",
                component=UPGRADE_MANAGER_NAME,
            )
            return False

        self.delete_status_if_present(
            status=UpgradeStatuses.WAITING_FOR_UPGRADE.value,
            scope="unit",
            component=UPGRADE_MANAGER_NAME,
        )
        self.delete_status_if_present(
            status=UpgradeStatuses.WAITING_FOR_UPGRADE.value,
            scope="app",
            component=UPGRADE_MANAGER_NAME,
        )

        return True

    def check_osd_status(self) -> None:
        """Verify the OpenSearch connection and trigger a health check.
        Returns true if OSD server is healthy otherwise false
        """
        # OPENSEARCH CONNECTION
        self.delete_status_if_present(
            status=ServerStatuses.DB_CONNECTION_MISSING.value,
            scope="app",
            component=SERVER_MANAGER_NAME,
        )
        self.delete_status_if_present(
            status=ServerStatuses.DB_CONNECTION_MISSING.value,
            scope="unit",
            component=SERVER_MANAGER_NAME,
        )

        if not self.state.opensearch_server:
            if self.state.unit.is_leader():
                self.state.statuses.add(
                    status=ServerStatuses.DB_CONNECTION_MISSING.value,
                    scope="app",
                    component=SERVER_MANAGER_NAME,
                )
            self.state.statuses.add(
                status=ServerStatuses.DB_CONNECTION_MISSING.value,
                scope="unit",
                component=SERVER_MANAGER_NAME,
            )

        # HEALTH
        self.health_manager.check_osd_health()

    def delete_status_if_present(
        self, status: StatusObject, scope: Literal["unit", "app"], component: str
    ) -> None:
        """Delete a status from a specific component safely.

        Checks if the status actually exists in the current state to avoid
        logging unnecessary warnings when attempting to delete a non-existent status.

        Args:
            status (StatusObject): The status object to remove.
            scope (Literal["unit", "app"]): The scope from which to remove the status.
            component (str): The name of the component holding the status.
        """
        if scope == "app" and not self.state.unit.is_leader():
            return

        current_statuses = self.state.statuses.get(scope=scope, component=component)

        if status in current_statuses:
            self.state.statuses.delete(
                status=status,
                scope=scope,
                component=component,
            )
