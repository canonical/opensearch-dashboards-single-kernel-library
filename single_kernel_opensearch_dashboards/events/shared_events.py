#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Methods that can be used by others OpenSearch Dashboards events."""
import logging
import time

from ops import (
    BlockedStatus,
    EventBase,
    MaintenanceStatus,
    Object,
    WaitingStatus,
)
from pydantic import ValidationError

from single_kernel_opensearch_dashboards.common.literals import (
    MSG_APP_STATUS,
    MSG_INCOMPATIBLE_UPGRADE,
    MSG_INVALID_CONFIG,
    MSG_STARTING,
    MSG_STARTING_SERVER,
    MSG_STATUS_DB_MISSING,
    MSG_STATUS_HANGING,
    MSG_STATUS_OAUTH_INFO_FAILED,
    MSG_TLS_CONFIG,
    MSG_UNIT_STATUS,
    MSG_WAITING_FOR_PEER,
    OAUTH_REL_NAME,
    RESTART_TIMEOUT,
    SERVER_PORT,
    SERVICE_AVAILABLE_TIMEOUT,
)
from single_kernel_opensearch_dashboards.core.cluster import ClusterState
from single_kernel_opensearch_dashboards.core.config import CharmConfig
from single_kernel_opensearch_dashboards.lib.charms.data_platform_libs.v1.data_models import (
    TypedCharmBase,
)
from single_kernel_opensearch_dashboards.lib.charms.rolling_ops.v0.rollingops import (
    RollingOpsManager,
)
from single_kernel_opensearch_dashboards.managers.config import ConfigManager
from single_kernel_opensearch_dashboards.managers.health import HealthManager
from single_kernel_opensearch_dashboards.managers.tls import TLSManager
from single_kernel_opensearch_dashboards.managers.upgrade import UpgradeManager
from single_kernel_opensearch_dashboards.utils.helpers import (
    clear_global_status,
    clear_status,
    set_global_status,
    update_grafana_dashboards_title,
)
from single_kernel_opensearch_dashboards.workload.base import WorkloadBase

logger = logging.getLogger(__name__)


class SharedEvents(Object):
    """Base class for methods and properties to be used by OpenSearch Dashboards events."""

    def __init__(
        self,
        charm: TypedCharmBase[CharmConfig],
        state: ClusterState,
        workload: WorkloadBase,
        health_manager: HealthManager,
        restart_manager: RollingOpsManager,
        config_manager: ConfigManager,
        tls_manager: TLSManager,
        upgrade_manager: UpgradeManager,
    ):
        super().__init__(charm, "shared-events")
        self.charm = charm
        self.workload = workload
        self.state = state

        # Managers
        self.tls_manager = tls_manager
        self.health_manager = health_manager
        self.config_manager = config_manager
        self.upgrade_manager = upgrade_manager
        self.restart_manager = restart_manager

    def restart(self, event: EventBase) -> None:
        """Handler for emitted restart events."""
        if not self.state.unit_server.started:
            self.reconcile(event)
            return

        logger.info(f"{self.charm.unit.name} restarting...")
        self.workload.restart()

        # Allow the service to start up safely on the snap level
        start_time = time.time()
        while not self.workload.alive() and time.time() - start_time < RESTART_TIMEOUT:
            time.sleep(5)

        # Allow the service to establish
        # Reason: we are emitting an 'update-status' right after
        # If the service is not yet functional, the status is set as
        # 'Service unavailable' until the next 'update-status' hook execution
        start_time = time.time()
        unit_healthy, _ = self.health_manager.unit_healthy()
        while not unit_healthy and time.time() - start_time < SERVICE_AVAILABLE_TIMEOUT:
            time.sleep(5)
            unit_healthy, _ = self.health_manager.unit_healthy()

        clear_status(self.charm.unit, [MSG_STARTING, MSG_STARTING_SERVER])

    def reconcile(self, event: EventBase) -> None:
        """Generic handler for all 'something changed, update' events across all relations."""
        # 1. Block until peer relation is set
        if not self.state.peer_relation:
            self.charm.unit.status = WaitingStatus(MSG_WAITING_FOR_PEER)
            return

        update_grafana_dashboards_title(self.charm)

        outdated_status = [MSG_WAITING_FOR_PEER]

        # attempt startup of server
        if not self.state.unit_server.started:
            self.init_server()

        # don't delay scale-down leader ops by restarting dying unit
        if getattr(event, "departing_unit", None) == self.charm.unit:
            return

        # 2. Restart if the service is down or on config change

        # Evaluate unit health at this point (as it may trigger a restart)
        unit_healthy, unit_msg = self.health_manager.unit_healthy()

        # Validate configs values
        try:
            self.state.unit_server.log_level = self.charm.config.log_level
        except ValidationError as e:
            logger.debug(f"Deferring reconcile due to config not passing validation yet: {e}")
            self.charm.unit.status = BlockedStatus(f"{MSG_INVALID_CONFIG} {str(e)}")
            event.defer()
            return

        if (
            (not unit_healthy and unit_msg == MSG_STATUS_HANGING)
            or self.config_manager.update_config()
            and self.state.unit_server.started
            and self.state.upgrade_idle
        ):
            self.charm.on[f"{self.restart_manager.name}"].acquire_lock.emit()
            # No point in setting any status -- would be wiped out by rollingops after the restart
            return

        # 3. Maintain the correct app status
        # No further actions below but only status settings

        # Block until Opensearch is available and it's a compatible version
        if self.state.opensearch_server:
            outdated_status.append(MSG_STATUS_DB_MISSING)
        else:
            set_global_status(self.charm, BlockedStatus(MSG_STATUS_DB_MISSING))
            return

        if self.upgrade_manager.version_compatible():
            outdated_status.append(MSG_INCOMPATIBLE_UPGRADE)
        else:
            set_global_status(self.charm, BlockedStatus(MSG_INCOMPATIBLE_UPGRADE))
            return

        # Maintain the correct unit status

        # Request new certificates if IP changed
        if self.state.cluster.tls:
            if self.state.unit_server.tls and self.tls_manager.certificate_valid():
                outdated_status.append(MSG_TLS_CONFIG)
            else:
                self.charm.unit.status = MaintenanceStatus(MSG_TLS_CONFIG)
                return
        else:
            outdated_status.append(MSG_TLS_CONFIG)

        # Regular health check
        # Checks that may modify the 'app' state as well
        opensearch_healthy, opensearch_msg = self.health_manager.opensearch_ok()
        if not opensearch_healthy:
            set_global_status(self.charm, BlockedStatus(opensearch_msg))
            return
        else:
            outdated_status += MSG_APP_STATUS

        # Checks purely on unit level
        if not unit_healthy:
            self.charm.unit.status = BlockedStatus(unit_msg)
            return

        if unit_msg:
            self.charm.unit.status = WaitingStatus(unit_msg)
            return
        else:
            outdated_status += MSG_UNIT_STATUS

        # check oauth status and make sure we have received the oauth_client_secret
        if (
            self.charm.model.get_relation(OAUTH_REL_NAME)
            and not self.state.cluster.oauth_client_secret
        ):
            set_global_status(self.charm, BlockedStatus(MSG_STATUS_OAUTH_INFO_FAILED))
            return

        # Clear all possible irrelevant statuses
        for status in outdated_status:
            clear_global_status(self.charm, status)

    # TODO: PORT TO MANAGERS
    def init_server(self):
        """Calls startup functions for server start."""
        self.charm.unit.status = MaintenanceStatus(MSG_STARTING_SERVER)
        logger.info(f"{self.charm.unit.name} initializing...")

        logger.debug("setting properties")
        self.config_manager.set_dashboard_properties()

        logger.debug("starting Opensearch Dashboards service")

        self.workload.start()

        # open port
        self.charm.unit.open_port("tcp", port=SERVER_PORT)

        # unit flags itself as 'started' so it can be retrieved by the leader
        logger.info(f"{self.charm.unit.name} started")

        # added here in case a `restart` was missed
        self.state.unit_server.update({"state": "started"})
        clear_status(self.charm.unit, MSG_STARTING_SERVER)

        if self.charm.unit.is_leader() and not self.state.opensearch_server:
            self.charm.app.status = BlockedStatus(MSG_STATUS_DB_MISSING)
