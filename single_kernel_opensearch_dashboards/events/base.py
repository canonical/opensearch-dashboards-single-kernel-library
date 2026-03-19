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

    def emit_restart(self, event: EventBase) -> None:
        """Evaluate conditions and emit a restart lock request if necessary."""
        # Check API only if opensearch connection exists
        api_ok = (
            self.health_manager.dashboards_status()[0] if self.state.opensearch_server else True
        )

        if (
            self.config_manager.config_changed()
            or not self.health_manager.service_healthy()
            or not api_ok
        ):
            self.charm.on[self.restart_manager.name].acquire_lock.emit()
        else:
            logger.debug("OpenSearch Dashboards is healthy and config is same, not restarting")

    def restart(self, event: EventBase) -> None:
        """Execute the restart logic for the OpenSearch Dashboards server."""
        logger.debug(f"Creating properties for {event.framework.model.unit.name}")
        self.config_manager.set_dashboard_properties()

        if not self.state.unit_server.started:
            self.charm.status_handler.set_running_status(
                status=ServerStatuses.STARTING_SERVER.value,
                component_name="server_manager",
                scope="unit",
            )
            self.server_manager.init_server()
            self.state.unit_server.update({"state": "started"})
            self.check_osd_status()
            return

        self.charm.status_handler.set_running_status(
            status=ServerStatuses.RESTARTING_SERVER.value,
            component_name="server_manager",
            scope="unit",
        )
        self.server_manager.restart_server()
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
                    component="config_manager",
                )
            self.state.statuses.add(
                status=ConfigStatuses.WAITING_FOR_PEER.value,
                scope="unit",
                component="config_manager",
            )
            return False

        self.delete_status_if_present(
            status=ConfigStatuses.WAITING_FOR_PEER.value, scope="unit", component="config_manager"
        )
        self.delete_status_if_present(
            status=ConfigStatuses.WAITING_FOR_PEER.value, scope="app", component="config_manager"
        )

        # UPGRADE IDLE CHECK
        if not self.state.upgrade_idle:
            logger.debug("Waiting for upgrade relations to be idle")
            if self.state.unit.is_leader():
                self.state.statuses.add(
                    status=UpgradeStatuses.WAITING_FOR_UPGRADE.value,
                    scope="app",
                    component="upgrade_manager",
                )
            self.state.statuses.add(
                status=UpgradeStatuses.WAITING_FOR_UPGRADE.value,
                scope="unit",
                component="upgrade_manager",
            )
            return False

        self.delete_status_if_present(
            status=UpgradeStatuses.WAITING_FOR_UPGRADE.value,
            scope="unit",
            component="upgrade_manager",
        )
        self.delete_status_if_present(
            status=UpgradeStatuses.WAITING_FOR_UPGRADE.value,
            scope="app",
            component="upgrade_manager",
        )

        return True

    def check_osd_status(self) -> None:
        """Verify the OpenSearch connection and trigger a health check."""
        # OPENSEARCH CONNECTION
        self.delete_status_if_present(
            status=ServerStatuses.DB_CONNECTION_MISSING.value,
            scope="app",
            component="server_manager",
        )
        self.delete_status_if_present(
            status=ServerStatuses.DB_CONNECTION_MISSING.value,
            scope="unit",
            component="server_manager",
        )

        if not self.state.opensearch_server:
            if self.state.unit.is_leader():
                self.state.statuses.add(
                    status=ServerStatuses.DB_CONNECTION_MISSING.value,
                    scope="app",
                    component="server_manager",
                )
            self.state.statuses.add(
                status=ServerStatuses.DB_CONNECTION_MISSING.value,
                scope="unit",
                component="server_manager",
            )

        # HEALTH
        self.charm.status_handler.set_running_status(
            HealthStatuses.WAITING_FOR_GREEN.value, scope="unit", component_name="health_manager"
        )
        self.health_manager.check_health()

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
