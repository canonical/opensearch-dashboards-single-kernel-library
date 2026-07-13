#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""OpenSearch Dashboards Base Charm."""

import logging
from abc import ABC, abstractmethod
from typing import Any, cast

from data_platform_helpers.advanced_statuses import StatusHandler
from ops import EventBase

from single_kernel_opensearch_dashboards.charms.charm_status import StatusHandlingCharm
from single_kernel_opensearch_dashboards.common.exceptions import (
    OSDFileOperationError,
    OSDNotTrusted,
)
from single_kernel_opensearch_dashboards.common.literals import (
    DASHBOARDS_NAME,
    RESTART_REL_NAME,
    SERVER_PORT,
    Substrates,
)
from single_kernel_opensearch_dashboards.core.cluster import ClusterState
from single_kernel_opensearch_dashboards.core.config import CharmConfig
from single_kernel_opensearch_dashboards.core.statuses import (
    HealthStatuses,
    ServerStatuses,
)
from single_kernel_opensearch_dashboards.events.ingress import IngressEvents
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
from single_kernel_opensearch_dashboards.lib.charms.data_platform_libs.v1.data_models import (
    TypedCharmBase,
)
from single_kernel_opensearch_dashboards.lib.charms.rolling_ops.v0.rollingops import (
    Lock,
    RollingOpsManager,
)
from single_kernel_opensearch_dashboards.lib.charms.traefik_k8s.v2.ingress import (
    IngressPerAppRequirer,
)
from single_kernel_opensearch_dashboards.managers.cluster import ClusterManager
from single_kernel_opensearch_dashboards.managers.config import ConfigManager
from single_kernel_opensearch_dashboards.managers.cos import COSManager
from single_kernel_opensearch_dashboards.managers.health import HealthManager
from single_kernel_opensearch_dashboards.managers.ingress import IngressManager
from single_kernel_opensearch_dashboards.managers.tls import TLSManager
from single_kernel_opensearch_dashboards.managers.upgrade import UpgradeManager
from single_kernel_opensearch_dashboards.workload.base import WorkloadBase

logger = logging.getLogger(__name__)


class OpenSearchDashboardsBaseCharm(TypedCharmBase[CharmConfig], ABC):
    """Base OpenSearch Dashboards Charm, this will include base structure for both machine and k8s charms."""

    config_type = CharmConfig

    def __init__(self, *args):
        super().__init__(*args)

        self.name = DASHBOARDS_NAME

        # --- State ---
        self.state = ClusterState(self, self.substrate)

        # --- Managers ---
        self.tls_manager = TLSManager(self.state, self.workload)
        self.health_manager = HealthManager(self.state, self.workload)
        self.ingress = None
        if self.substrate == Substrates.K8S:
            self.ingress = IngressPerAppRequirer(
                self,
                port=SERVER_PORT,
                scheme="https" if self.state.unit_server.tls_enabled else "http",
                strip_prefix=False,
                # for the time being, while we support full load balancing
            )
        self.ingress_manager = IngressManager(self.state, self.workload, self.ingress)
        self.config_manager = ConfigManager(self.state, self.workload)
        self.upgrade_manager = UpgradeManager(self.state, self.workload)
        self.cluster_manager = ClusterManager(self.state, self.workload)
        self.restart_manager = RollingOpsManager(
            self, relation=RESTART_REL_NAME, callback=self.restart_on_lock_acquired
        )
        self.cos_manager = COSManager(self, self.state, self.workload)

        # --- Event Handlers ---
        protocol_self = cast(StatusHandlingCharm, cast(Any, self))
        self.opensearch_dashboards_events = OpenSearchDashboardsEvents(
            protocol_self, self.state, self.workload
        )
        self.jwt_events = JwtEvents(protocol_self, self.state)
        self.tls_events = TLSEvents(protocol_self, self.state)
        self.requirer_events = RequirerEvents(protocol_self, self.state)
        self.oauth = OAuthEvents(protocol_self, self.state)
        self.ingress_events = IngressEvents(protocol_self, self.state)

        try:
            self.upgrade_events = UpgradeEvents(
                self,
                self.state,
                self.workload,
                self.upgrade_manager,
                self.health_manager,
            )
        except OSDNotTrusted as e:
            logger.error(
                "OpenSearch Dashboards charm is not trusted, upgrade functionality is not possible, %s",
                e,
            )

        self.status_handler = StatusHandler(
            self,
            self.config_manager,
            self.cluster_manager,
            self.ingress_manager,
            self.health_manager,
            self.upgrade_manager,
            self.tls_manager,
            self.cos_manager,
        )

    @property
    def base(self) -> "OpenSearchDashboardsBaseCharm":
        """Return self to satisfy the StatusHandlingCharm protocol."""
        return self

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

    def restart_on_lock_acquired(self, event: EventBase) -> None:
        """RollingOpsManager callback: apply config, restart, then verify health."""
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
        else:
            self.status_handler.set_running_status(
                status=ServerStatuses.RESTARTING_SERVER.value,
                component_name=self.cluster_manager.name,
                scope="unit",
            )

        self.cluster_manager.restart_server()
        self.state.unit_server.update({"state": "started"})
        self.unit.open_port(protocol="tcp", port=SERVER_PORT)

        # Checking health after restart
        self.status_handler.set_running_status(
            status=HealthStatuses.AFTER_RESTART.value,
            component_name=self.health_manager.name,
            scope="unit",
        )
        try:
            self.health_manager.check_osd_health()
        except OSDFileOperationError as e:
            logger.warning(e)
            event.defer()
            return

    def emit_restart(self, event: EventBase) -> None:
        """Evaluate conditions and emit a restart lock request if necessary."""
        lock = Lock(self.restart_manager)

        try:
            if not self.config_manager.config_changed():
                if lock.is_pending() or lock.is_held():
                    logger.debug("Waiting for lock")
                    return
                elif self.health_manager.check_unit_health():
                    logger.debug(
                        "OpenSearch Dashboards is healthy and config is same, not restarting"
                    )
                    return
        except OSDFileOperationError as e:
            logger.warning(e)
            event.defer()
            return

        self.state.statuses.add(
            status=ServerStatuses.WAITING_ON_RESTART.value,
            scope="unit",
            component=self.cluster_manager.name,
        )

        self.on[self.restart_manager.name].acquire_lock.emit()
