#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Implementation of WorkloadBase for running on VMs."""
import logging
import subprocess

from charmlibs import pathops
from tenacity import retry, retry_if_exception_type
from tenacity.retry import retry_any, retry_if_exception, retry_if_not_result
from tenacity.stop import stop_after_attempt
from tenacity.wait import wait_fixed
from typing_extensions import override

from single_kernel_opensearch_dashboards.common.exceptions import OSDInstallError
from single_kernel_opensearch_dashboards.common.literals import (
    OPENSEARCH_DASHBOARDS_SNAP_REVISION,
    Substrates,
)
from single_kernel_opensearch_dashboards.lib.charms.operator_libs_linux.v2 import snap
from single_kernel_opensearch_dashboards.workload.base import Paths, WorkloadBase

logger = logging.getLogger(__name__)


class VMWorkload(WorkloadBase):
    """Implementation of WorkloadBase for running on VMs."""

    SNAP_NAME = "opensearch-dashboards"
    SNAP_APP_SERVICE = "opensearch-dashboards-daemon"
    SNAP_EXPORTER_SERVICE = "exporter-daemon"

    def __init__(self):
        """Initializes the VM workload instance and loads the snap into the cache."""
        self.dashboards = snap.SnapCache()[self.SNAP_NAME]

    @property
    @override
    def paths(self) -> Paths:
        """Retrieves the file system paths used by the workload.

        Returns:
            Paths: An object representing the local paths, rooted at '/'.
        """
        return Paths(pathops.LocalPath("/"), substrate=Substrates.VM)

    @override
    def start(self) -> None:
        """Starts the OpenSearch Dashboards and exporter daemon services."""
        try:
            self.dashboards.start(services=[self.SNAP_APP_SERVICE, self.SNAP_EXPORTER_SERVICE])
        except snap.SnapError as e:
            logger.exception(str(e))
            raise

    @override
    def stop(self) -> None:
        """Stops the OpenSearch Dashboards and exporter daemon services."""
        try:
            self.dashboards.stop(services=[self.SNAP_APP_SERVICE, self.SNAP_EXPORTER_SERVICE])
        except snap.SnapError as e:
            logger.exception(str(e))
            raise

    @override
    @retry(
        wait=wait_fixed(1),
        stop=stop_after_attempt(5),
        retry_error_callback=lambda state: state.outcome.result(),  # type: ignore
        retry=retry_any(
            retry_if_not_result(lambda result: True if result else False),
            retry_if_exception(snap.SnapError),
        ),
    )
    def restart(self) -> bool:
        """Restarts the workload services and verifies their status.

        This method will retry up to 5 times if the snap errors out or if
        the service fails to report as alive.

        Returns:
            bool: True if the services were successfully restarted and are alive, False otherwise.
        """
        try:
            self.dashboards.restart(services=[self.SNAP_APP_SERVICE, self.SNAP_EXPORTER_SERVICE])
        except snap.SnapError as e:
            logger.exception(str(e))
            raise
        return self.alive()

    @override
    def configure(self, key, value) -> None:
        """Sets a configuration key-value pair for the snap.

        Args:
            key (str): The configuration key to set.
            value (Any): The value to assign to the configuration key.
        """
        try:
            self.dashboards.set(config={key: value})
        except snap.SnapError as e:
            logger.exception(str(e))
            raise

    @override
    def exec(self, command: list[str], working_dir: str | None = None) -> str:
        """Executes a shell command locally on the VM.

        Args:
            command (list[str]): The command and its arguments to execute.
            working_dir (str | None, optional): The directory to execute the command in. Defaults to None.

        Returns:
            str: The standard output of the executed command.

        Raises:
            subprocess.CalledProcessError: If the command returns a non-zero exit status.
        """
        return subprocess.check_output(
            command,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            cwd=working_dir,
        )

    @override
    @retry(
        wait=wait_fixed(3),
        stop=stop_after_attempt(5),
        retry_error_callback=lambda state: state.outcome.result(),  # type: ignore
        retry=retry_if_not_result(lambda result: True if result else False),
    )
    def alive(self) -> bool:
        """Checks if the main application service is active.

        This method retries up to 5 times to confirm the service is running.

        Returns:
            bool: True if the main snap application service is active, False otherwise.
        """
        """The main application is alive."""
        try:
            return bool(self.dashboards.services[self.SNAP_APP_SERVICE]["active"])
        except KeyError:
            return False

    @override
    def healthy(self) -> bool:
        """Checks if the workload is healthy.

        Returns:
            bool: True if the workload is alive and functioning as expected, False otherwise.
        """
        return self.alive()

    @override
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_fixed(5),
        reraise=True,
        retry=retry_if_exception_type(OSDInstallError),
    )
    def install(self) -> None:
        """Installs the OpenSearch Dashboards snap.

        Loads the snap from the cache, ensures it is installed at the pinned revision,
        creates necessary directories for certificates, and places a hold on the snap.

        Raises:
            OSDInstallError: If the snap fails to install after 3 attempts.
        """
        try:
            cache = snap.SnapCache()
            dashboards = cache[self.SNAP_NAME]

            dashboards.ensure(snap.SnapState.Present, revision=OPENSEARCH_DASHBOARDS_SNAP_REVISION)

            self.dashboards = dashboards
            self.paths.certificate_dir.mkdir(exist_ok=True)
            self.dashboards.hold()
        except snap.SnapError as e:
            logger.error(str(e))
            raise OSDInstallError(
                "failed to install the Opensearch Dashboards snap. check logs for more details"
            )
