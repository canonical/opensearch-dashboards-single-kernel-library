#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Host/network address resolution for the local dashboards unit."""

import logging
import socket

from ops.model import Unit

from single_kernel_opensearch_dashboards.common.literals import Substrates

logger = logging.getLogger(__name__)


class Network:
    """Resolves host, FQDN, IPs and TLS SANs for the local Juju unit."""

    def __init__(
        self,
        unit: Unit,
        substrate: Substrates,
        bind_address: str | None = None,
    ):
        self.unit = unit
        self.substrate = substrate
        self.bind_address = bind_address

    @property
    def unit_id(self) -> int:
        """The id of the unit from the unit name, e.g. opensearch-dashboards/2 --> 2."""
        return int(self.unit.name.split("/")[1])

    @property
    def hostname(self) -> str:
        """The hostname for the unit."""
        if self.substrate == Substrates.VM:
            return socket.gethostname()
        app = self.unit.name.split("/")[0]
        return f"{app}-{self.unit_id}.{app}-endpoints"

    @property
    def fqdn(self) -> str:
        """The Fully Qualified Domain Name for the unit."""
        if self.substrate == Substrates.VM:
            return socket.getfqdn(self.private_ip)

        try:
            info = socket.getaddrinfo(
                self.host,
                None,
                family=socket.AF_UNSPEC,
                flags=socket.AI_CANONNAME,
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as e:
            logger.warning(
                "Failed to resolve canonical name for %s: %s. \nFalling back on default fqdn.",
                self.host,
                e,
            )
            return socket.getfqdn(self.host)

        for entry in info:
            if canonname := entry[3]:
                return canonname

        logger.warning(
            "Failed to resolve canonical name for %s. \nFalling back on default fqdn.", self.host
        )
        return socket.getfqdn(self.host)

    @property
    def private_ip(self) -> str:
        """The IP for the unit recovered using socket."""
        return socket.gethostbyname(self.hostname)

    @property
    def public_ip(self) -> str:
        """The public IP for the unit."""
        return socket.gethostbyname(self.hostname)

    @property
    def host(self) -> str:
        """The host address for the unit."""
        if self.substrate == Substrates.VM and self.bind_address:
            return self.bind_address
        return self.hostname

    @property
    def sans(self) -> dict[str, list[str]]:
        """The Subject Alternative Name for the unit's TLS certificates."""
        sans_dns = {self.hostname, self.fqdn}
        sans_ip: set[str] = set()
        if self.substrate == Substrates.K8S:
            sans_dns.add(self.host)
        else:
            sans_ip = {self.private_ip, self.public_ip}
            if self.bind_address:
                sans_ip.add(self.bind_address)

        return {
            "sans_ip": list(sans_ip),
            "sans_dns": list(sans_dns),
        }
