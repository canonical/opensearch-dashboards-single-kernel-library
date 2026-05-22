#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Base objects for workload operations across VM + K8s charms."""
from abc import ABC, abstractmethod

from charmlibs import pathops
from charmlibs.pathops import PathProtocol

from single_kernel_opensearch_dashboards.common.exceptions import OSDFileOperationError
from single_kernel_opensearch_dashboards.common.literals import (
    BASE_SNAP_DIR,
    SNAP,
    SNAP_COMMON,
    SNAP_DATA,
    OpenSearchDashboardsPaths,
)


class Paths(ABC):
    """Collection of expected paths for the Opensearch Dashboards workload."""

    def __init__(self, root: PathProtocol):
        self.root = root

    # DYNAMIC BASE PATHS
    @property
    @abstractmethod
    def data(self) -> PathProtocol:
        """The base directory where Opensearch Dashboards will store data."""
        ...

    @property
    @abstractmethod
    def config_dir(self) -> PathProtocol:
        """The directory where Opensearch Dashboards will store configs."""
        ...

    @property
    @abstractmethod
    def bin_dir(self) -> PathProtocol:
        """The directory containing Opensearch Dashboards binaries."""
        ...

    @property
    @abstractmethod
    def log_dir(self) -> PathProtocol:
        """The directory where Opensearch Dashboards will store logs."""
        ...

    # RELATIVE PATHS
    @property
    def data_dir(self) -> PathProtocol:
        """The directory where Opensearch Dashboards will store the in-memory database snapshots."""
        return self.data / "data"

    @property
    def properties(self) -> PathProtocol:
        """The main properties filepath. Contains all the main configuration for the service."""
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
        """The certificate for Opensearch to identify itself with for TLS auth."""
        return self.certificate_dir / "opensearch_ca.pem"


class VMPaths(Paths):
    """VM (Snap) specific paths for Opensearch Dashboards."""

    # SNAP-SPECIFIC PATHS
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

    # DYNAMIC BASE PATHS
    @property
    def data(self) -> PathProtocol:
        """The base directory where Opensearch Dashboards will store data."""
        return self.snap_common / OpenSearchDashboardsPaths.DATA

    @property
    def config_dir(self) -> PathProtocol:
        """The directory where Opensearch Dashboards will store configs."""
        return self.snap_current / OpenSearchDashboardsPaths.CONF

    @property
    def bin_dir(self) -> PathProtocol:
        """The directory containing Opensearch Dashboards binaries."""
        return self.snap / OpenSearchDashboardsPaths.BIN

    @property
    def log_dir(self) -> PathProtocol:
        """The directory where Opensearch Dashboards will store logs."""
        return self.snap_common / OpenSearchDashboardsPaths.LOGS


class K8sPaths(Paths):
    """K8s specific paths for Opensearch Dashboards."""

    # DYNAMIC BASE PATHS
    @property
    def data(self) -> PathProtocol:
        """The base directory where Opensearch Dashboards will store data."""
        return self.root / OpenSearchDashboardsPaths.DATA

    @property
    def config_dir(self) -> PathProtocol:
        """The directory where Opensearch Dashboards will store configs."""
        return self.root / OpenSearchDashboardsPaths.CONF

    @property
    def bin_dir(self) -> PathProtocol:
        """The directory containing Opensearch Dashboards binaries."""
        return self.root / OpenSearchDashboardsPaths.BIN

    @property
    def log_dir(self) -> PathProtocol:
        """The directory where Opensearch Dashboards will store logs."""
        return self.root / OpenSearchDashboardsPaths.LOGS


class WorkloadBase(ABC):
    """Base interface for common workload operations."""

    @property
    @abstractmethod
    def paths(self) -> Paths:
        """"""
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

    @abstractmethod
    def healthy(self) -> bool:
        """Checks that the workload is healthy."""
        ...

    @abstractmethod
    def install(self) -> None:
        """Install OSD."""
        ...

    @abstractmethod
    def ready(self) -> bool:
        """Checks if workload is ready."""
        ...

    def write_text(self, content: str, path: PathProtocol) -> None:
        """Write content to a file on disk.

        Args:
            content (str): The content to be written.
            path (str): The file path where the content should be written.
        """
        try:
            path.write_text(content)
        except (
            FileNotFoundError,
            LookupError,
            NotADirectoryError,
            PermissionError,
            pathops.PebbleConnectionError,
            ValueError,
        ) as e:
            raise OSDFileOperationError(e)

    def read_text(self, path: pathops.PathProtocol) -> str:
        """Read content from a file on disk.

        Args:
            path (str): The file path to read from.

        Returns:
            str: The content read from the file.

        Raises:
            OSDFileOperationError: If there was an error when reading file.
        """
        try:
            return path.read_text()
        except (
            FileNotFoundError,
            LookupError,
            NotADirectoryError,
            PermissionError,
            pathops.PebbleConnectionError,
            ValueError,
        ) as e:
            raise OSDFileOperationError(e)

    def exists(self, path: pathops.PathProtocol) -> bool:
        """Checks if folder exists.

        Args:
            path (str): The file path to read from.

        Returns:
            bool: File exists.

        Raises:
            OSDFileOperationError: If there was an error when checking existence of file.
        """
        try:
            return path.exists()
        except (
            FileNotFoundError,
            LookupError,
            NotADirectoryError,
            PermissionError,
            pathops.PebbleConnectionError,
            ValueError,
        ) as e:
            raise OSDFileOperationError(e)

    def create_folder(self, path: pathops.PathProtocol) -> None:
        """Creates folder at path

        Args:
            path (str): The file path to read from.
        Raises:
            OSDFileOperationError: If there was an error creating file.
        """
        try:
            return path.mkdir()
        except (
            FileNotFoundError,
            LookupError,
            NotADirectoryError,
            PermissionError,
            pathops.PebbleConnectionError,
            ValueError,
        ) as e:
            raise OSDFileOperationError(e)

    def unlink(self, path: pathops.PathProtocol) -> None:
        """Unlinks file if it's not a directory

        Args:
            path (str): The file path to read from.\
        Raises:
            OSDFileOperationError: If there was an error when deleting file.
        """
        try:
            path.unlink()
        except (
            FileNotFoundError,
            LookupError,
            NotADirectoryError,
            PermissionError,
            pathops.PebbleConnectionError,
            ValueError,
        ) as e:
            raise OSDFileOperationError(e)
