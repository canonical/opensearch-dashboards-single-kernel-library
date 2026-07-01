#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""OpenSearch Dashboards Status Handler"""
from abc import abstractmethod
from typing import Protocol

from data_platform_helpers.advanced_statuses import StatusHandler
from ops import CharmBase, CharmEvents, EventBase, Unit

from single_kernel_opensearch_dashboards.managers.cluster import ClusterManager
from single_kernel_opensearch_dashboards.managers.health import HealthManager
from single_kernel_opensearch_dashboards.managers.ingress import IngressManager
from single_kernel_opensearch_dashboards.managers.tls import TLSManager
from single_kernel_opensearch_dashboards.managers.upgrade import UpgradeManager


class StatusHandlingCharm(Protocol):
    # Abstract solution to not create a circular dependency with events.
    # To set running statuses we need to access charm.status_handler
    # which we can only get by passing OpenSearchDashboardsBaseCharm as argument
    # so to not create dependencies we are using separate class
    status_handler: StatusHandler
    on: CharmEvents
    unit: Unit
    tls_manager: TLSManager
    ingress_manager: IngressManager
    cluster_manager: ClusterManager
    upgrade_manager: UpgradeManager
    health_manager: HealthManager
    base: CharmBase

    def __init__(self, *args):
        super().__init__(*args)

    @abstractmethod
    def emit_restart(self, event: EventBase) -> None: ...
