#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for building necessary files for TLS auth."""
import logging

from data_platform_helpers.advanced_statuses import StatusObject
from data_platform_helpers.advanced_statuses.types import Scope

from single_kernel_opensearch_dashboards.common.literals import DEPENDENCIES
from single_kernel_opensearch_dashboards.core.cluster import ClusterState
from single_kernel_opensearch_dashboards.core.statuses import (
    CharmStatuses,
    UpgradeStatuses,
)
from single_kernel_opensearch_dashboards.lib.charms.data_platform_libs.v1.upgrade import (
    BaseModel,
    DependencyModel,
)
from single_kernel_opensearch_dashboards.managers.base import BaseManager
from single_kernel_opensearch_dashboards.workload.base import WorkloadBase

logger = logging.getLogger(__name__)


class OpensearchDashboardsDependencyModel(BaseModel):
    """Model for OpensearchDashboards Operator dependencies."""

    osd_upstream: DependencyModel


class UpgradeManager(BaseManager):
    """Logic relating to Rolling Upgrades."""

    def __init__(
        self,
        state: ClusterState,
        workload: WorkloadBase,
    ):
        super().__init__(state, workload)
        self.dependency_model = OpensearchDashboardsDependencyModel(**DEPENDENCIES)
        self.name = "upgrade_manager"

    def version_compatible(self) -> bool:
        """Verify version compatibility with Opensearch."""
        # When there's no Opensearch connection, we shouldn't report version mismatch
        if not self.state.opensearch_server:
            return True

        if not (srv_version_actual := self.state.opensearch_server.version):
            return False

        srv_version_required = self.dependency_model.osd_upstream.dependencies["opensearch"]
        major_actual, minor_actual = srv_version_actual.split(".")[:2]
        major_required, minor_required = srv_version_required.split(".")[:2]
        return major_actual <= major_required and minor_actual <= minor_required

    def build_upgrade_stack(self) -> list[int]:
        """Compute the order in which units should be upgraded.

        Returns:
            A list of unit IDs representing the upgrade sequence
        """
        upgrade_stack = []

        units = [self.state.unit]
        if self.state.peer_relation:
            units.extend(list(self.state.peer_relation.units))

        for unit in units:
            upgrade_stack.append(int(unit.name.split("/")[-1]))

        return upgrade_stack

    def upgrade_osd(self) -> None:
        self.workload.stop()

        self.workload.install()

        logger.info(f"{self.state.unit.name} upgrading workload...")
        self.workload.restart()

    def get_statuses(self, scope: Scope, recompute: bool = False) -> list[StatusObject]:
        """Compute the upgrade manager's statuses."""
        if not recompute:
            statuses = self.state.statuses.get(scope, "upgrade_manager").root
            return statuses or [CharmStatuses.ACTIVE_IDLE.value]

        status_list: list[StatusObject] = []

        if not self.version_compatible():
            status_list.append(UpgradeStatuses.DB_INCOMPATIBLE_VERSION.value)
        if not self.state.upgrade_idle:
            status_list.append(UpgradeStatuses.WAITING_FOR_UPGRADE.value)

        return status_list or [CharmStatuses.ACTIVE_IDLE.value]
