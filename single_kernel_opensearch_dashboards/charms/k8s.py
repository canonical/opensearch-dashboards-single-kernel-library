#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""OpenSearch Dashboards Kubernetes Charm."""
from single_kernel_opensearch_dashboards.charms.base import (
    OpenSearchDashboardsBaseCharm,
)
from single_kernel_opensearch_dashboards.utils.literals import Substrates
from single_kernel_opensearch_dashboards.workload.base import WorkloadBase
from single_kernel_opensearch_dashboards.workload.k8s import WorkloadK8s


class OpenSearchK8sCharm(OpenSearchDashboardsBaseCharm):
    """OpenSearch Dashboards kubernetes Charm"""

    def __init__(self, *args):
        super().__init__(*args)

    @property
    def workload(self) -> WorkloadBase:
        """Access current workload."""
        return WorkloadK8s()

    @property
    def substrate(self) -> Substrates:
        """Access current substrate."""
        return Substrates.K8S
