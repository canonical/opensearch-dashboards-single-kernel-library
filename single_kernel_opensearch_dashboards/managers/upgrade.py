#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for building necessary files for TLS auth."""
import logging

from single_kernel_opensearch_dashboards.core.cluster import ClusterState
from single_kernel_opensearch_dashboards.lib.charms.data_platform_libs.v0.upgrade import (
    BaseModel,
    DependencyModel,
)
from single_kernel_opensearch_dashboards.utils.literals import DEPENDENCIES
from single_kernel_opensearch_dashboards.workload.base import WorkloadBase

logger = logging.getLogger(__name__)


class OpensearchDashboardsDependencyModel(BaseModel):
    """Model for OpensearchDashboards Operator dependencies."""

    osd_upstream: DependencyModel


class UpgradeManager:
    """Logic relating to Rolling Upgrades."""

    def __init__(
        self,
        state: ClusterState,
        workload: WorkloadBase,
    ):
        self.state = state
        self.workload = workload
        self.dependency_model = OpensearchDashboardsDependencyModel(**DEPENDENCIES)

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
