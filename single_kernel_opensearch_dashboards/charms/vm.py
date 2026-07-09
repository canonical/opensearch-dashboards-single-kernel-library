#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""OpenSearch Dashboards Machine Charm."""

from pathlib import Path

from single_kernel_opensearch_dashboards.charms.base import (
    OpenSearchDashboardsBaseCharm,
)
from single_kernel_opensearch_dashboards.common.literals import Substrates
from single_kernel_opensearch_dashboards.workload.base import WorkloadBase
from single_kernel_opensearch_dashboards.workload.vm import VMWorkload


class OpenSearchDashboardsVMCharm(OpenSearchDashboardsBaseCharm):
    """OpenSearch Dashboards Machine Charm"""

    def __init__(self, *args):
        super().__init__(*args)
        try:
            self.unit.set_workload_version(Path("workload_version").read_text().strip())
        except FileNotFoundError:
            self.unit.set_workload_version("2.19.2")

    @property
    def workload(self) -> WorkloadBase:
        """Access current workload."""
        return VMWorkload()

    @property
    def substrate(self) -> Substrates:
        """Access current substrate."""
        return Substrates.VM
