#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for handling service health."""

import logging
import time
from typing import Optional

import requests
from data_platform_helpers.advanced_statuses import StatusObject
from data_platform_helpers.advanced_statuses.types import Scope
from requests.exceptions import ConnectionError, HTTPError

from single_kernel_opensearch_dashboards.common.exceptions import OSDAPIError
from single_kernel_opensearch_dashboards.common.literals import (
    SERVICE_AVAILABLE_TIMEOUT,
)
from single_kernel_opensearch_dashboards.core.cluster import ClusterState
from single_kernel_opensearch_dashboards.core.statuses import (
    CharmStatuses,
    HealthStatuses,
)
from single_kernel_opensearch_dashboards.managers.base import BaseManager
from single_kernel_opensearch_dashboards.workload.base import WorkloadBase

logger = logging.getLogger(__name__)


class HealthManager(BaseManager):
    """Manager responsible for handling Opensearch Dashboards service health."""

    def __init__(self, state: ClusterState, workload: WorkloadBase):
        """Initialize the HealthManager."""
        super().__init__(state, workload)
        self.name = "health_manager"

    def dashboards_status(self) -> tuple[bool, Optional[StatusObject]]:
        """Fetch and evaluate the local OpenSearch Dashboards health status.

        Queries the `/api/status` endpoint of the local Dashboards instance.

        Returns:
            tuple[bool, Optional[StatusObject]]: A tuple where the first element is a
                boolean indicating if the service is reachable/functioning, and the
                second is a specific StatusObject (or None if the state is perfectly 'green').
        """
        try:
            status, body = self.request_opensearch_dashboards(endpoint="/api/status")
        except HTTPError as err:
            if err.response.status_code == 503:
                return False, HealthStatuses.STATUS_UNAVAILABLE.value
            return False, HealthStatuses.STATUS_UNKNOWN.value
        except (ConnectionError, OSDAPIError):
            return False, HealthStatuses.STATUS_UNAVAILABLE.value
        except requests.ReadTimeout:
            return False, HealthStatuses.STATUS_HANGING.value

        state = body.get("status", {}).get("overall", {}).get("state")

        if state == "green":
            return True, None
        if state == "yellow":
            return True, HealthStatuses.STATUS_UNHEALTHY.value
        if state:
            return False, HealthStatuses.STATUS_ERROR.value

        return False, HealthStatuses.STATUS_UNKNOWN.value

    def opensearch_status(self) -> tuple[bool, Optional[StatusObject]]:
        """Verify if the associated remote OpenSearch service is up and healthy.

        Iterates through known OpenSearch endpoints and attempts to fetch the
        `/_cluster/health` status. Stops at the first successful connection.

        Returns:
            tuple[bool, Optional[StatusObject]]: A tuple where the boolean indicates
                if a healthy/accessible connection exists, and the StatusObject provides
                specific error states (or None if 'green' or 'yellow').
        """
        for endpoint in self.state.opensearch_server.endpoints:
            full_url = f"https://{endpoint}/_cluster/health"
            try:
                code, body = self.request_opensearch(full_url)
            except requests.RequestException:
                logger.error(f"Failed to connect to {full_url}")
                continue

            if code == 200:
                state = body.get("status")
                if state == "red":
                    return False, HealthStatuses.DB_UNHEALTHY.value
                if state in {"green", "yellow"}:
                    return True, None

        return False, HealthStatuses.DB_DOWN.value

    def service_healthy(self) -> bool:
        """Perform a unit-level global health check.

        Returns:
            bool: True if the underlying workload process is alive, False otherwise.
        """
        return self.workload.alive()

    def check_unit_health(self) -> None:
        """Monitor the unit's health and wait for it to reach a 'green' state.

        Polls the dashboards status for up to SERVICE_AVAILABLE_TIMEOUT seconds.
        Updates the state statuses if the unit fails to become healthy.
        """
        logger.info(f"{self.state.unit.name} waiting for green health")

        start_time = time.time()
        unit_healthy, unit_message = self.dashboards_status()

        while unit_message is not None and time.time() - start_time < SERVICE_AVAILABLE_TIMEOUT:
            time.sleep(5)
            unit_healthy, unit_message = self.dashboards_status()

        if unit_message:
            if self.state.unit.is_leader():
                self.state.statuses.add(
                    status=unit_message, scope="app", component="health_manager"
                )
            self.state.statuses.add(status=unit_message, scope="unit", component="health_manager")
            logger.info(f"{self.state.unit.name} is not healthy")
            return

        logger.info(f"{self.state.unit.name} is in green health")

    def check_opensearch_health(self) -> None:
        """Check the health of the remote OpenSearch cluster and update statuses.

        Only executes if an OpenSearch server connection exists and valid CA
        certificates are present on disk.
        """
        if self.state.opensearch_server and (
            self.workload.paths.opensearch_ca.exists()
            and self.workload.paths.opensearch_ca.read_text()
        ):
            opensearch_healthy, status = self.opensearch_status()
            if not opensearch_healthy and status:
                self.state.statuses.add(status=status, scope="app", component="health_manager")

    def check_health(self) -> None:
        """Coordinate and execute all comprehensive health checks.

        Validates the workload process, local dashboards state, and remote OpenSearch
        health, clearing and updating the state's status records accordingly.
        """
        if not self.service_healthy():
            if self.state.unit.is_leader():
                self.state.statuses.add(
                    status=HealthStatuses.WORKLOAD_IS_DOWN.value,
                    scope="app",
                    component="health_manager",
                )
            self.state.statuses.add(
                status=HealthStatuses.WORKLOAD_IS_DOWN.value,
                scope="unit",
                component="health_manager",
            )
            return

        # Do not check health if not connected to opensearch (no credentials)
        if not self.state.opensearch_server:
            return

        # Clear the statuses for health manager because they will be recomputed
        if self.state.unit.is_leader():
            self.state.statuses.clear(scope="app", component="health_manager")
        self.state.statuses.clear(scope="unit", component="health_manager")

        self.check_unit_health()
        self.check_opensearch_health()

    def get_statuses(self, scope: Scope, recompute: bool = False) -> list[StatusObject]:
        """Compute the health manager's statuses."""
        if not recompute:
            statuses = self.state.statuses.get(scope, "health_manager").root
            return statuses or [CharmStatuses.ACTIVE_IDLE.value]

        status_list: list[StatusObject] = []

        if not self.service_healthy():
            status_list.append(HealthStatuses.WORKLOAD_IS_DOWN.value)
            # Return immediately because we cannot access the dashboards API if the service is down
            return status_list

        # Do not check opensearch health if it's not connected or missing certificates
        if self.state.opensearch_server and (
            self.workload.paths.opensearch_ca.exists()
            and self.workload.paths.opensearch_ca.read_text()
        ):
            opensearch_healthy, status = self.opensearch_status()
            if not opensearch_healthy and status:
                status_list.append(status)

            dashboards_healthy, status = self.dashboards_status()
            if not dashboards_healthy and status:
                status_list.append(status)

        return status_list or [CharmStatuses.ACTIVE_IDLE.value]
