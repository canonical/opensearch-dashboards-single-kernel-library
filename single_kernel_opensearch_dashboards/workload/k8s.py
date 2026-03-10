#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Kubernetes Workload."""
from charmlibs import pathops

from single_kernel_opensearch_dashboards.workload.base import Paths, WorkloadBase


class K8sWorkload(WorkloadBase):
    """Kubernetes OpenSearch Dashboards Workload."""

    def __init__(self) -> None:
        self._paths = Paths(pathops.LocalPath("/"))

    def start(self) -> None:
        """Starts the workload service."""
        ...

    def stop(self) -> None:
        """Stops the workload service."""
        ...

    def restart(self) -> None:
        """Restarts the workload service."""
        ...

    def configure(self, key: str, value: str) -> None:
        """Set workload parameters"""
        ...

    def exec(self, command: list[str], working_dir: str | None = None) -> None:
        """Runs a command on the workload substrate."""
        ...

    def alive(self) -> bool:
        """Checks that the workload is alive."""
        ...

    @property
    def healthy(self) -> bool:
        """Checks that the workload is healthy."""
        ...

    def install(self) -> None:
        """Install OSD."""
        ...

    @property
    def paths(self) -> Paths:
        """Checks that the workload is healthy."""
        return self._paths
