#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""OpenSearch Dashboards Base Charm."""

from abc import ABC, abstractmethod
from ops import EventBase


from single_kernel_opensearch_dashboards.lib.charms.data_platform_libs.v1.data_models import TypedCharmBase

from single_kernel_opensearch_dashboards.utils.literals import Substrates

from single_kernel_opensearch_dashboards.core.config import CharmConfig
from single_kernel_opensearch_dashboards.core.cluster import ClusterState

from single_kernel_opensearch_dashboards.events.shared_events import SharedEvents
from single_kernel_opensearch_dashboards.events.opensearch_dashboards import OpenSearchDashboardsEvents

from single_kernel_opensearch_dashboards.workload.base import WorkloadBase

from single_kernel_opensearch_dashboards.events.upgrade import UpgradeEvents
from single_kernel_opensearch_dashboards.events.jwt_auth import JwtEvents
from single_kernel_opensearch_dashboards.events.oauth import OAuthEvents
from single_kernel_opensearch_dashboards.events.requirer import RequirerEvents
from single_kernel_opensearch_dashboards.events.tls import TLSEvents

class OpenSearchDashboardsBaseCharm(TypedCharmBase[CharmConfig], ABC):
    """Base OpenSearch Dashboards Charm, this will include base structure for both machine and k8s charms."""
    config_type = CharmConfig

    def __init__(self, *args):
        super().__init__(*args)

        # State
        self.state = ClusterState(self, self.substrate)

        # Event Handlers
        self.shared_events = SharedEvents(
            self,
            self.state,
            self.workload,
        )

        self.opensearch_events = OpenSearchDashboardsEvents(self.shared_events)
        self.jwt_events = JwtEvents(self.shared_events)
        self.tls_events = TLSEvents(self.shared_events)
        self.requirer_events = RequirerEvents(self.shared_events)
        self.oauth = OAuthEvents(self.shared_events)
        self.upgrade_events = UpgradeEvents(self.shared_events,self.substrate)

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

    def restart(self, event:EventBase):
        """
        Helper method for RollingOpsManager
        If callback method is not directly in charm class it will throw error
        """
        self.shared_events.restart(event)