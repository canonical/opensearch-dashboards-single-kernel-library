#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for building necessary files for Java TLS auth."""
import logging
import subprocess
from subprocess import STDOUT, CalledProcessError

import ops.pebble

from single_kernel_opensearch_dashboards.core.cluster import ClusterState
from single_kernel_opensearch_dashboards.workload.base import WorkloadBase

logger = logging.getLogger(__name__)


class TLSManager:
    """Manager for building necessary files for Java TLS auth."""

    def __init__(self,
                 state: ClusterState,
                 workload: WorkloadBase,
                 ):
        self.state = state
        self.workload = workload

    def set_private_key(self) -> None:
        """Sets the unit private-key."""
        if not self.state.unit_server.private_key:
            logger.error("Can't set private-key to unit, missing private-key in relation data")
            return


        self.workload.paths.server_key.write_text(self.state.unit_server.private_key)

    def set_ca(self) -> None:
        """Sets the unit CA."""
        if not self.state.unit_server.ca:
            logger.error("Can't set CA to unit, missing CA in relation data")
            return

        self.workload.paths.ca.write_text(self.state.unit_server.ca)

    def set_certificate(self) -> None:
        """Sets the unit certificate."""
        if not self.state.unit_server.certificate:
            logger.error("Can't set certificate to unit, missing certificate in relation data")
            return

        self.workload.paths.certificate.write_text(self.state.unit_server.certificate)
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
            logger.error(str(e.stdout))
            raise e
        self.workload.configure("scheme", "http")

    def certificate_valid(self) -> bool:
        """Check if server certificate is valid"""
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
