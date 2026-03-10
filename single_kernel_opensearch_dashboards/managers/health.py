#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for handling service health."""

import logging

import requests
from requests.exceptions import ConnectionError, HTTPError

from single_kernel_opensearch_dashboards.common.exceptions import OSDAPIError
from single_kernel_opensearch_dashboards.common.literals import (
    MSG_STATUS_APP_REMOVED,
    MSG_STATUS_DB_DOWN,
    MSG_STATUS_DB_MISSING,
    MSG_STATUS_DB_UNHEALTHY,
    MSG_STATUS_ERROR,
    MSG_STATUS_HANGING,
    MSG_STATUS_UNAVAIL,
    MSG_STATUS_UNHEALTHY,
    MSG_STATUS_UNKNOWN,
    MSG_STATUS_WORKLOAD_DOWN,
)
from single_kernel_opensearch_dashboards.core.cluster import ClusterState
from single_kernel_opensearch_dashboards.managers.base import BaseManager
from single_kernel_opensearch_dashboards.workload.base import WorkloadBase

logger = logging.getLogger(__name__)


class HealthManager(BaseManager):
    """Manager for handling Opensearch DashBoards machine health."""

    def __init__(self, state: ClusterState, workload: WorkloadBase):
        super().__init__(state, workload)
        self.state = state
        self.workload = workload

    def status_ok(self) -> tuple[bool, str]:
        """Health status"""
        try:
            status, body = self.request_opensearch_dashboards(endpoint="/api/status")
        except HTTPError as err:
            if err.response.status_code == 503:
                return False, MSG_STATUS_UNAVAIL
            return False, MSG_STATUS_UNKNOWN
        except (ConnectionError, OSDAPIError):
            return False, MSG_STATUS_UNAVAIL
        except requests.ReadTimeout:
            return False, MSG_STATUS_HANGING

        if body["status"]["overall"]["state"] == "green":
            return True, ""
        elif body["status"]["overall"]["state"] == "yellow":
            return True, MSG_STATUS_UNHEALTHY
        elif body["status"]["overall"]["state"] != "green":
            return False, MSG_STATUS_ERROR
        return True, MSG_STATUS_UNKNOWN

    def opensearch_ok(self) -> tuple[bool, str]:
        """Verify if associated Opensearch service is up and running."""

        if not self.state.url:
            return False, MSG_STATUS_APP_REMOVED

        if not self.state.opensearch_server or not (
            self.workload.paths.opensearch_ca.exists()
            and self.workload.paths.opensearch_ca.read_text()
        ):
            return False, MSG_STATUS_DB_MISSING

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
                    return False, MSG_STATUS_DB_UNHEALTHY

                if state in {"green", "yellow"}:
                    return True, ""

        return False, MSG_STATUS_DB_DOWN

    def unit_healthy(self) -> tuple[bool, str]:
        """Unit-level global health check."""
        if not self.workload.alive():
            return False, MSG_STATUS_WORKLOAD_DOWN

        return self.status_ok()
