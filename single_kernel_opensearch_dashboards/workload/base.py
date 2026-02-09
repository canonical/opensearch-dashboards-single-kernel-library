#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Base objects for workload operations across VM + K8s charms."""
from abc import ABC, abstractmethod

from single_kernel_opensearch_dashboards.utils.literals import (
    BASE_SNAP_DIR,
    SNAP,
    SNAP_COMMON,
    SNAP_DATA,
    OpenSearchDashboardsPaths,
)
from charmlibs.pathops import PathProtocol

class Paths:
    """Collection of expected paths for the Opensearch Dashboards workload."""

    def __init__(self, root: PathProtocol):
        self.root = root

    @property
    def base_snap_dir(self) -> PathProtocol:
        """Return path to the Base snap directory."""
        return self.root / BASE_SNAP_DIR

    @property
    def snap_current(self) -> PathProtocol:
        """Return path to the snap data directory."""
        return self.base_snap_dir / SNAP_DATA

    @property
    def snap_common(self) -> PathProtocol:
        """Return path to the snap common directory."""
        return self.base_snap_dir / SNAP_COMMON

    @property
    def snap(self) -> PathProtocol:
        """Return path to the snap directory."""
        return self.root / SNAP

    @property
    def data_dir(self) -> PathProtocol:
        """The directory where Opensearch Dashboards will store the in-memory database snapshots."""
        return self.snap_common / OpenSearchDashboardsPaths.DATA / "data"

    @property
    def data(self) -> PathProtocol:
        """The directory where Opensearch Dashboards will store the in-memory database snapshots."""
        return self.snap_common / OpenSearchDashboardsPaths.DATA

    @property
    def config_dir(self) -> PathProtocol:
        """The directory where Opensearch Dashboards will store configs"""
        return self.snap_current / OpenSearchDashboardsPaths.CONF

    @property
    def properties(self) -> PathProtocol:
        """The main properties filepath.

        Contains all the main configuration for the service.
        """
        return self.config_dir / "opensearch_dashboards.yml"

    @property
    def certificate_dir(self) -> PathProtocol:
        """The directory for the certificates."""
        return self.config_dir / "certificates"

    @property
    def server_key(self) -> PathProtocol:
        """The private-key for the service to identify itself with for TLS auth."""
        return self.certificate_dir / "server.key"

    @property
    def ca(self) -> PathProtocol:
        """The shared cluster CA."""
        return self.certificate_dir / "ca.pem"

    @property
    def certificate(self) -> PathProtocol:
        """The certificate for the service to identify itself with for TLS auth."""
        return self.certificate_dir / "server.pem"

    @property
    def opensearch_ca(self) -> PathProtocol:
        """The certificate for the service to identify itself with for TLS auth."""
        return self.certificate_dir / "opensearch_ca.pem"

class WorkloadBase(ABC):
    """Base interface for common workload operations."""

    @property
    @abstractmethod
    def paths(self) -> Paths:
        """"""
        ...

    @abstractmethod
    def start(self) -> None:
        """Starts the workload service."""
        ...

    @abstractmethod
    def stop(self) -> None:
        """Stops the workload service."""
        ...

    @abstractmethod
    def restart(self) -> None:
        """Restarts the workload service."""
        ...

    @abstractmethod
    def configure(self, key: str, value: str) -> None:
        """Set workload parameters"""
        ...

    @abstractmethod
    def exec(self, command: list[str], working_dir: str | None = None) -> None:
        """Runs a command on the workload substrate."""
        ...

    @property
    @abstractmethod
    def alive(self) -> bool:
        """Checks that the workload is alive."""
        ...

    @property
    @abstractmethod
    def healthy(self) -> bool:
        """Checks that the workload is healthy."""
        ...

    @abstractmethod
    def install(self) -> None:
        """Install OD."""
        ...
