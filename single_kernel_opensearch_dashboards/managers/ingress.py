#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for handling ingress."""

from data_platform_helpers.advanced_statuses import StatusObject
from data_platform_helpers.advanced_statuses.types import Scope

from single_kernel_opensearch_dashboards.common.literals import (
    INGRESS_MANAGER_NAME,
    Substrates,
)
from single_kernel_opensearch_dashboards.core.state import ClusterState
from single_kernel_opensearch_dashboards.core.statuses import (
    CharmStatuses,
    ServerStatuses,
)
from single_kernel_opensearch_dashboards.lib.charms.traefik_k8s.v2.ingress import (
    IngressPerAppRequirer,
)
from single_kernel_opensearch_dashboards.managers.base import BaseManager
from single_kernel_opensearch_dashboards.workload.base import WorkloadBase


class IngressManager(BaseManager):
    """Ingress manager for k8s."""

    def __init__(
        self,
        state: ClusterState,
        workload: WorkloadBase,
        ingress_requirer: IngressPerAppRequirer | None,
    ) -> None:
        super().__init__(state, workload)
        self.name = INGRESS_MANAGER_NAME

        if self.state.substrate == Substrates.VM:
            return

        self.ingress_requirer = ingress_requirer
        # not really a manager, but we'll see how to best adjust that once we add HAProxy

    def get_statuses(self, scope: Scope, recompute: bool = False) -> list[StatusObject]:
        """Compute the ingress manager's statuses."""
        if self.state.unit_stopping or self.state.app_removal:
            return []

        if self.state.substrate == Substrates.VM:
            return [CharmStatuses.ACTIVE_IDLE.value]

        if not self.ingress_requirer.relation:
            return [ServerStatuses.INGRESS_RELATION_MISSING.value]

        if not self.ingress_requirer.is_ready():
            return [ServerStatuses.INGRESS_RELATION_NOT_READY.value]

        return [CharmStatuses.ACTIVE_IDLE.value]
