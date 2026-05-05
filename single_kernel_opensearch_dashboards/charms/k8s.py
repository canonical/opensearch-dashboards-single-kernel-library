#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""OpenSearch Dashboards Kubernetes Charm."""

from single_kernel_opensearch_dashboards.charms.base import (
    OpenSearchDashboardsBaseCharm,
)
from single_kernel_opensearch_dashboards.common.literals import Substrates
from single_kernel_opensearch_dashboards.workload.base import WorkloadBase
from single_kernel_opensearch_dashboards.workload.k8s import K8sWorkload


class OpenSearchDashboardsK8sCharm(OpenSearchDashboardsBaseCharm):
    """OpenSearch Dashboards kubernetes Charm"""

    @property
    def workload(self) -> WorkloadBase:
        """Access current workload."""
        return K8sWorkload()

    @property
    def substrate(self) -> Substrates:
        """Access current substrate."""
        return Substrates.K8S
