#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""OpenSearch Dashboards Machine Charm."""
from single_kernel_opensearch_dashboards.charms.base import OpenSearchDashboardsBaseCharm
from single_kernel_opensearch_dashboards.workload.base import WorkloadBase
from single_kernel_opensearch_dashboards.workload.vm import WorkloadVM
from single_kernel_opensearch_dashboards.utils.literals import Substrates

class OpenSearchVMCharm(OpenSearchDashboardsBaseCharm):
    """OpenSearch Dashboards Machine Charm"""

    def __init__(self, *args):
        super().__init__(*args)

    @property
    def workload(self) -> WorkloadBase:
        """Access current workload."""
        return WorkloadVM()

    @property
    def substrate(self) -> Substrates:
        """Access current substrate."""
        return Substrates.VM
