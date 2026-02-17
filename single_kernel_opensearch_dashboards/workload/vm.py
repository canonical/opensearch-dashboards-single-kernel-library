#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Implementation of WorkloadBase for running on VMs."""
import logging
import secrets
import string
import subprocess

from charmlibs import pathops
from single_kernel_opensearch_dashboards.lib.charms.operator_libs_linux.v2 import snap
from tenacity import retry, retry_if_exception_type
from tenacity.retry import retry_any, retry_if_exception, retry_if_not_result
from tenacity.stop import stop_after_attempt
from tenacity.wait import wait_fixed
from typing_extensions import override

from single_kernel_opensearch_dashboards.workload.base import WorkloadBase
from single_kernel_opensearch_dashboards.workload.base import Paths
from single_kernel_opensearch_dashboards.common.exceptions import OSDInstallError
from single_kernel_opensearch_dashboards.utils.literals import OPENSEARCH_DASHBOARDS_SNAP_REVISION

logger = logging.getLogger(__name__)


class WorkloadVM(WorkloadBase):
    """Implementation of WorkloadBase for running on VMs."""

    SNAP_NAME = "opensearch-dashboards"
    SNAP_APP_SERVICE = "opensearch-dashboards-daemon"
    SNAP_EXPORTER_SERVICE = "exporter-daemon"

    def __init__(self):
        self.dashboards = snap.SnapCache()[self.SNAP_NAME]

    @property
    @override
    def paths(self) -> Paths:
        return Paths(pathops.LocalPath("/"))

    @override
    def start(self) -> None:
        try:
            self.dashboards.start(services=[self.SNAP_APP_SERVICE, self.SNAP_EXPORTER_SERVICE])
        except snap.SnapError as e:
            logger.exception(str(e))

    @override
    def stop(self) -> None:
        try:
            self.dashboards.stop(services=[self.SNAP_APP_SERVICE, self.SNAP_EXPORTER_SERVICE])
        except snap.SnapError as e:
            logger.exception(str(e))

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
        try:
            self.dashboards.restart(services=[self.SNAP_APP_SERVICE, self.SNAP_EXPORTER_SERVICE])
        except snap.SnapError as e:
            logger.exception(str(e))
        return self.alive()

    @override
    def configure(self, key, value) -> None:
        try:
            self.dashboards.set(config={key: value})
        except snap.SnapError as e:
            logger.exception(str(e))

    @override
    def exec(self, command: list[str], working_dir: str | None = None) -> str:
        return subprocess.check_output(
            command,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            cwd=working_dir,
        )

    @override
    @retry(
        wait=wait_fixed(1),
        stop=stop_after_attempt(5),
        retry_error_callback=lambda state: state.outcome.result(),  # type: ignore
        retry=retry_if_not_result(lambda result: True if result else False),
    )
    def alive(self) -> bool:
        """The main application is alive."""
        try:
            return bool(self.dashboards.services[self.SNAP_APP_SERVICE]["active"])
        except KeyError:
            return False

    @override
    def healthy(self) -> bool:
        return self.alive()

    @override
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_fixed(5),
        reraise=True,
        retry=retry_if_exception_type(OSDInstallError),
    )
    def install(self) -> None:
        """Loads the snap from LP, returning a StatusBase for the Charm to set."""
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

    def generate_password(self) -> str:
        """Creates randomized string for use as app passwords.

        Returns:
            String of 32 randomized letter+digit characters
        """
        return "".join([secrets.choice(string.ascii_letters + string.digits) for _ in range(32)])
