#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""OpenSearch Dashboards Machine Charm."""
from single_kernel_opensearch_dashboards.charms.base import (
    OpenSearchDashboardsBaseCharm,
)
from single_kernel_opensearch_dashboards.common.literals import Substrates
from single_kernel_opensearch_dashboards.workload.base import WorkloadBase
from single_kernel_opensearch_dashboards.workload.vm import VMWorkload


class OpenSearchVMCharm(OpenSearchDashboardsBaseCharm):
    """OpenSearch Dashboards Machine Charm"""

    @property
    def workload(self) -> WorkloadBase:
        """Access current workload."""
        return VMWorkload()

    @property
    def substrate(self) -> Substrates:
        """Access current substrate."""
        return Substrates.VM
