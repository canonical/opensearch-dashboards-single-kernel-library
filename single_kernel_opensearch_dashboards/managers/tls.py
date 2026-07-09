#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for building necessary files for Java TLS auth."""

import logging
import subprocess
from subprocess import STDOUT, CalledProcessError

import ops.pebble
from data_platform_helpers.advanced_statuses import StatusObject
from data_platform_helpers.advanced_statuses.types import Scope

from single_kernel_opensearch_dashboards.common.exceptions import (
    OSDTLSMissingDataError,
)
from single_kernel_opensearch_dashboards.common.literals import TLS_MANAGER_NAME
from single_kernel_opensearch_dashboards.core.state import ClusterState
from single_kernel_opensearch_dashboards.core.statuses import (
    CharmStatuses,
    ServerStatuses,
)
from single_kernel_opensearch_dashboards.lib.charms.tls_certificates_interface.v3.tls_certificates import (
    generate_csr,
    generate_private_key,
)
from single_kernel_opensearch_dashboards.managers.base import BaseManager
from single_kernel_opensearch_dashboards.workload.base import WorkloadBase

logger = logging.getLogger(__name__)


class TLSManager(BaseManager):
    """Manager for building necessary files for Java TLS auth."""

    def __init__(
        self,
        state: ClusterState,
        workload: WorkloadBase,
    ):
        super().__init__(state, workload)
        self.name = TLS_MANAGER_NAME

    def set_private_key(self) -> None:
        """Sets the unit private-key."""
        if not self.state.unit_server.private_key:
            raise OSDTLSMissingDataError(
                "Can't set private-key to unit, missing private-key in relation data"
            )

        self.workload.write_text(
            self.state.unit_server.private_key, self.workload.paths.server_key
        )

    def set_ca(self) -> None:
        """Sets the unit CA."""
        if not self.state.unit_server.ca:
            raise OSDTLSMissingDataError("Can't set CA to unit, missing CA in relation data")

        self.workload.write_text(self.state.unit_server.ca, self.workload.paths.ca)

    def set_ca_opensearch(self) -> None:
        """Sets the unit CA."""
        if (
            self.state.opensearch_server
            and self.state.opensearch_server.password
            and self.state.opensearch_server.endpoints
            and self.state.opensearch_server.tls_ca
        ):
            self.workload.write_text(
                self.state.opensearch_server.tls_ca, self.workload.paths.opensearch_ca
            )

    def remove_ca_opensearch(self) -> None:
        """Removes the stored OpenSearch CA, if present."""
        if self.workload.exists(self.workload.paths.opensearch_ca):
            self.workload.unlink(self.workload.paths.opensearch_ca)

    def set_certificate(self) -> None:
        """Sets the unit certificate."""
        if not self.state.unit_server.certificate:
            raise OSDTLSMissingDataError(
                "Can't set certificate to unit, missing certificate in relation data"
            )

        self.workload.write_text(
            self.state.unit_server.certificate, self.workload.paths.certificate
        )
        self.workload.configure("scheme", "https")

    def remove_cert_files(self) -> None:
        """Removes all certs, keys, stores from the unit."""
        try:
            self.workload.exec(
                command=[
                    "rm",
                    "-rf",
                    f"{self.workload.paths.config_dir}/*.pem",
                    f"*{self.workload.paths.config_dir}/*.key",
                    f"*{self.workload.paths.config_dir}/*.p12",
                    f"*{self.workload.paths.config_dir}/*.jks",
                ],
                working_dir=self.workload.paths.config_dir.as_posix(),
            )
        except (subprocess.CalledProcessError, ops.pebble.ExecError) as e:
            logger.error(f"Failed to remove cert files. Output: {e.stdout}")
            raise
        self.workload.configure("scheme", "http")

    def certificate_valid(self) -> bool:
        """Check if server certificate is valid."""
        cmd = f"openssl x509 -in {self.workload.paths.certificate} -subject -noout"
        try:
            response = subprocess.check_output(
                cmd, stderr=STDOUT, shell=True, universal_newlines=True
            )
        except CalledProcessError as error:
            logging.error(f"Checking certificate failed: {error.output}")
            return False

        logger.debug(f"Response of openssl cert decode: {response}")
        logger.debug(
            f"Currently recognized IP using 'gethostbyname': {self.state.unit_server.private_ip}"
        )
        return str(self.state.bind_address) in response

    def generate_csr(self) -> bytes:
        if not self.state.unit_server.private_key:
            self.state.unit_server.update({"private-key": generate_private_key().decode("utf-8")})

        csr = generate_csr(
            private_key=self.state.unit_server.private_key.encode("utf-8"),
            subject=self.state.unit_server.host,
            sans_ip=self.state.unit_server.sans.get("sans_ip"),
            sans_dns=self.state.unit_server.sans.get("sans_dns"),
        )
        logger.debug(
            "Requesting certificate for: host: %s, with sans_ip: %s, sans_dns: %s",
            self.state.unit_server.host,
            self.state.unit_server.sans.get("sans_ip"),
            self.state.unit_server.sans.get("sans_dns"),
        )

        return csr

    def write_tls_files(self) -> None:
        """Writes necessary data from databag to files.
        Used for when k8s pod is recreated
        Or a unit added after relation with OpenSearch was created
        Raises:
            OSDFileOperationError: If there was an error when writing/reading files.
        """
        if (
            self.state.opensearch_server
            and self.state.opensearch_server.password
            and not self.workload.exists(self.workload.paths.opensearch_ca)
        ):
            self.set_ca_opensearch()

        if self.state.unit_server.private_key and not self.workload.exists(
            self.workload.paths.server_key
        ):
            self.set_private_key()

        if self.state.unit_server.ca and not self.workload.exists(self.workload.paths.ca):
            self.set_ca()

        if self.state.unit_server.certificate and not self.workload.exists(
            self.workload.paths.certificate
        ):
            self.set_certificate()
        logger.debug("Updated all TLS resources")

    def get_statuses(self, scope: Scope, recompute: bool = False) -> list[StatusObject]:
        """Compute the tls manager's statuses."""
        status_list: list[StatusObject] = []

        if self.state.unit_server:
            if not self.state.unit_server.tls_enabled and self.state.oauth_relation:
                status_list.append(ServerStatuses.NO_TLS.value)

        return status_list or [CharmStatuses.ACTIVE_IDLE.value]
