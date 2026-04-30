#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for building necessary files for TLS auth."""
import logging

from data_platform_helpers.advanced_statuses import StatusObject
from data_platform_helpers.advanced_statuses.types import Scope

from single_kernel_opensearch_dashboards.common.literals import (
    DEPENDENCIES,
    UPGRADE_MANAGER_NAME,
)
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
    """Model representing the dependencies for the OpensearchDashboards Operator."""

    osd_upstream: DependencyModel


class UpgradeManager(BaseManager):
    """Manager class responsible for handling Rolling Upgrades logic."""

    def __init__(
        self,
        state: ClusterState,
        workload: WorkloadBase,
    ):
        super().__init__(state, workload)
        self.dependency_model = OpensearchDashboardsDependencyModel(**DEPENDENCIES)
        self.name = UPGRADE_MANAGER_NAME

    def version_compatible(self) -> bool:
        """Verify version compatibility between OpenSearch Dashboards and the OpenSearch server.

        Returns:
            bool: True if the versions are compatible or if no server connection
                exists; False if there is a version mismatch.
        """

        # When there's no Opensearch connection, we shouldn't report version mismatch
        if not self.state.opensearch_server or not self.state.opensearch_server.password:
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
            list[int]: A list of numeric unit IDs representing the sequence
                in which the units should be upgraded.
        """
        upgrade_stack = []

        units = [self.state.unit]
        if self.state.peer_relation:
            units.extend(list(self.state.peer_relation.units))

        for unit in units:
            upgrade_stack.append(int(unit.name.split("/")[-1]))

        return upgrade_stack

    def upgrade_osd(self) -> None:
        """Execute the upgrade process for the OpenSearch Dashboards.

        This method stops the current workload, installs the upgraded version
        of the software, and restarts the service.
        """
        self.workload.stop()

        self.workload.install()

        logger.info(f"{self.state.unit.name} upgrading workload...")
        self.workload.restart()

    def get_statuses(self, scope: Scope, recompute: bool = False) -> list[StatusObject]:
        """Compute the upgrade manager's statuses."""
        if not recompute:
            statuses = self.state.statuses.get(scope, self.name).root
            return statuses or [CharmStatuses.ACTIVE_IDLE.value]

        status_list: list[StatusObject] = []

        if not self.version_compatible() and scope == "unit":
            status_list.append(UpgradeStatuses.DB_INCOMPATIBLE_VERSION.value)
        if not self.state.upgrade_idle:
            status_list.append(UpgradeStatuses.WAITING_FOR_UPGRADE.value)

        return status_list or [CharmStatuses.ACTIVE_IDLE.value]
