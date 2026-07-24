#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Kubernetes Workload."""

import logging
import os
import tempfile
from typing import Any

import ops
import yaml
from charmlibs import pathops
from charmlibs.pathops import PathProtocol
from tenacity import retry, retry_if_not_result, stop_after_attempt, wait_fixed
from typing_extensions import override

from single_kernel_opensearch_dashboards.common.exceptions import OSDFileOperationError
from single_kernel_opensearch_dashboards.common.literals import (
    EXPORTER_SERVICE,
    LAYER_NAME,
    OSD_SERVICE,
    OpenSearchDashboardsPaths,
)
from single_kernel_opensearch_dashboards.workload.base import (
    Paths,
    WorkloadBase,
)

logger = logging.getLogger(__name__)


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


class K8sWorkload(WorkloadBase):
    """Implementation of WorkloadBase for running on Kubernetes via Pebble."""

    def __init__(self, container: ops.Container):
        """Initializes the K8s workload instance.

        Args:
            container (ops.Container): The Pebble container running the workload.
        """
        self.container = container

    @property
    @override
    def paths(self) -> Paths:
        """Retrieves the file system paths used by the workload.

        Returns:
            Paths: An object representing the container paths, rooted at '/'.
        """
        # Note: In K8s charms, file operations are usually done via self.container.push/pull,
        # but the logical path root is still "/"

        return K8sPaths(pathops.ContainerPath("/", container=self.container))

    @override
    def stop(self) -> None:
        """Stops the OpenSearch Dashboards and exporter daemon services via Pebble."""
        try:
            if self.container.can_connect():
                self.container.stop(OSD_SERVICE, EXPORTER_SERVICE)
        except ops.pebble.ChangeError as e:
            logger.exception(f"Failed to stop services: {e}")
            raise

    @override
    @retry(
        wait=wait_fixed(1),
        stop=stop_after_attempt(5),
        retry_error_callback=lambda state: state.outcome.result(),
        # type: ignore
        retry=retry_if_not_result(lambda result: True if result else False),
    )
    def restart(self) -> bool:
        """Restarts the workload services and verifies their status.

        Returns:
            bool: True if the services were successfully restarted and are alive, False otherwise.
        """
        try:
            if self.container.can_connect():
                self.container.restart(OSD_SERVICE, EXPORTER_SERVICE)
        except ops.pebble.ChangeError as e:
            logger.exception(f"Failed to restart services: {e}")
            raise

        return self.healthy()

    @override
    def configure(self, key: str, value: Any) -> None:
        """Sets a configuration key-value pair for the container.

        Unlike Snap, Pebble doesn't have a direct 'config set' command.
        Instead, we update the environment variables in the Pebble layer
        and replan the services.

        Args:
            key (str): The configuration key (usually an ENV var name) to set.
            value (Any): The value to assign.
        """
        if not self.container.can_connect():
            raise ops.pebble.ConnectionError("Cannot connect to Pebble.")

        try:
            plan = self.container.get_plan()
            if plan.services and OSD_SERVICE in plan.services:
                service = plan.services[OSD_SERVICE]
                env = service.environment or {}
                env[key] = value

                layer_dict = {
                    "services": {
                        OSD_SERVICE: {
                            "override": "merge",
                            "environment": env,
                        }
                    }
                }
                self.container.add_layer(LAYER_NAME, yaml.safe_dump(layer_dict), combine=True)
                self.container.replan()
        except ops.pebble.Error as e:
            logger.exception(f"Failed to configure Pebble layer: {e}")
            raise

    @override
    def exec(self, command: list[str], working_dir: str | None = None) -> str:
        """Executes a shell command inside the Kubernetes container via Pebble.

        Args:
            command (list[str]): The command and its arguments to execute.
            working_dir (str | None, optional): The directory to execute the command in.

        Returns:
            str: The standard output of the executed command.

        Raises:
            ops.pebble.ExecError: If the command returns a non-zero exit status.
        """
        if not self.container.can_connect():
            raise ops.pebble.ConnectionError("Cannot connect to Pebble.")

        process = self.container.exec(
            command,
            working_dir=working_dir,
            combine_stderr=True,
        )
        stdout, _ = process.wait_output()
        return stdout

    @override
    @retry(
        wait=wait_fixed(1),
        stop=stop_after_attempt(5),
        retry_error_callback=lambda state: state.outcome.result(),
        # type: ignore
        retry=retry_if_not_result(lambda result: True if result else False),
    )
    def healthy(self) -> bool:
        """Checks if the workload is healthy.

        Returns:
            bool: True if the workload is alive and functioning as expected.
        """
        if not self.container.can_connect():
            logger.debug("can't connect to container")
            return False

        try:
            # Safely check if the service is even defined in the plan yet
            plan = self.container.get_plan()
            if OSD_SERVICE not in plan.services:
                logger.debug("service not in plan")
                return False

            service = self.container.get_service(OSD_SERVICE)
            return service.is_running()

        except ops.pebble.ConnectionError:
            return False
        except ops.pebble.APIError as e:
            logger.debug(f"Pebble API error while checking if service is alive: {e}")
            return False

    @override
    def install(self) -> None:
        """Installs the workload.

        In Kubernetes, workloads are provided by the container image itself.
        Therefore, no explicit installation steps are required here.
        """
        return

    @override
    def ready(self) -> bool:
        """Checks if workload is ready."""
        return self.container.can_connect()

    def write_certs(self, ca: str | None) -> str | None:
        """Copies certs to another container and returns the local path"""
        # request library is run on other container than the opensearch is located so we temporarily
        # write certificates and remove them after request
        if ca is None:
            return None

        try:
            with tempfile.NamedTemporaryFile(mode="w", delete=False) as local_ca_file:
                local_ca_file.write(ca)
                return local_ca_file.name
        except (
            FileNotFoundError,
            LookupError,
            NotADirectoryError,
            PermissionError,
            pathops.PebbleConnectionError,
            ValueError,
        ) as e:
            raise OSDFileOperationError(e)

    def remove_certs(self, local_ca_path: str | None) -> None:
        """Removes certs from container"""
        if local_ca_path is None:
            return
        try:
            if os.path.exists(local_ca_path):
                os.remove(local_ca_path)
        except (
            FileNotFoundError,
            LookupError,
            NotADirectoryError,
            PermissionError,
            pathops.PebbleConnectionError,
            ValueError,
        ) as e:
            raise OSDFileOperationError(e)
