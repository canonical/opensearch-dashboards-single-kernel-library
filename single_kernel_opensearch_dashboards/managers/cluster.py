#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""The COS manager.

This class is purely there for separation of purposes, it will just include the
right observability stack.
"""
import logging
import time

from data_platform_helpers.advanced_statuses import StatusObject
from data_platform_helpers.advanced_statuses.types import Scope

from single_kernel_opensearch_dashboards.common.literals import (
    CLUSTER_MANAGER_NAME,
    RESTART_TIMEOUT,
)
from single_kernel_opensearch_dashboards.core.cluster import ClusterState
from single_kernel_opensearch_dashboards.core.statuses import (
    CharmStatuses,
    ServerStatuses,
)
from single_kernel_opensearch_dashboards.managers.base import BaseManager
from single_kernel_opensearch_dashboards.workload.base import WorkloadBase

logger = logging.getLogger(__name__)


class ClusterManager(BaseManager):
    def __init__(self, state: ClusterState, workload: WorkloadBase):
        super().__init__(state, workload)
        self.name = CLUSTER_MANAGER_NAME

    def restart_server(self) -> None:
        """Calls restart functions for server restart."""

        logger.info("restarting Opensearch Dashboards service")
        self.workload.restart()
        start_time = time.time()
        while not self.workload.healthy() and time.time() - start_time < RESTART_TIMEOUT:
            time.sleep(5)
        logger.info("Opensearch Dashboards service restarted")

    def install_osd_server(self) -> None:
        """Calls install functions for server installation."""
        logger.info("installing Opensearch Dashboards")
        self.workload.install()
        logger.info("Opensearch Dashboards installed")

    def get_statuses(self, scope: Scope, recompute: bool = False) -> list[StatusObject]:
        """Compute the server manager's statuses."""
        if not recompute:
            statuses = self.state.statuses.get(scope, self.name).root
            return statuses or [CharmStatuses.ACTIVE_IDLE.value]

        status_list: list[StatusObject] = []

        if not self.state.servers:
            status_list.append(ServerStatuses.SERVERS_IS_DOWN.value)

        if scope == "unit" and not self.workload.ready():
            status_list.append(ServerStatuses.CONTAINER_IS_NOT_ACCESSIBLE.value)

        if not self.state.opensearch_server or not self.state.opensearch_server.password:
            status_list.append(
                ServerStatuses.DB_CONNECTION_MISSING.value,
            )

        return status_list or [CharmStatuses.ACTIVE_IDLE.value]
