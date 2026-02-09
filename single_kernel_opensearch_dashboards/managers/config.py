#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for handling configuration building + writing."""
import logging
from typing import Any
import yaml

from single_kernel_opensearch_dashboards.core.cluster import ClusterState
from single_kernel_opensearch_dashboards.workload.base import WorkloadBase

logger = logging.getLogger(__name__)

DEFAULT_PROPERTIES = {
    "opensearch.requestHeadersWhitelist": ["authorization", "securitytenant"],
    "opensearch_security.multitenancy.enabled": True,
    "opensearch_security.multitenancy.tenants.preferred": ["Private", "Global"],
    "opensearch_security.readonly_mode.roles": ["kibana_read_only"],
    "server.ssl.enabled": False,
    "opensearch_security.cookie.secure": False,
}

# Overrides the DEFAULT_PROPERTIES if we have TLS enabled
TLS_PROPERTIES = {
    "server.ssl.enabled": True,
    "opensearch.ssl.verificationMode": "full",
    "opensearch_security.cookie.secure": True,
}

LOG_PROPERTIES = {
    "logging.verbose": True,
}


class ConfigManager:
    """Manager for handling configuration building + writing."""
    def __init__(
        self,
        state: ClusterState,
        workload: WorkloadBase,
    ):
        self.state = state
        self.workload = workload

    def update_config(self) -> bool:
        """Compares expected vs actual config that would require a restart to apply."""
        if self._load_dashboard_properties() == self._dashboard_properties():
            return False
        self._set_dashboard_properties()
        return True

    def _set_dashboard_properties(self) -> None:
        """Writes built config file."""
        self.workload.paths.properties.write_text(yaml.dump(self._dashboard_properties()))

    def _load_dashboard_properties(self) -> dict[str, Any]:
        """Reads built config file."""
        return yaml.load(self.workload.paths.properties.read_text(), yaml.UnsafeLoader)

    def _dashboard_properties(self) -> dict[str, Any]:
        """Build the opensearch_dashboards.yml content.

        As we are building on top of the known templates above, we do not need to care about
        merging lists, for example. We will override the default properties if needed.

        Returns:
            List of properties to be set to opensearch_dashboards.yml config file
        """

        properties = DEFAULT_PROPERTIES.copy()

        opensearch_user = (
            self.state.opensearch_server.username if self.state.opensearch_server else ""
        )
        opensearch_password = (
            self.state.opensearch_server.password if self.state.opensearch_server else ""
        )

        if self.state.opensearch_server and len(self.state.opensearch_server.endpoints) > 0:
            properties["opensearch.hosts"] = [
                f"https://{endpoint}" for endpoint in self.state.opensearch_server.endpoints
            ]

        opensearch_ca = self.workload.paths.opensearch_ca if self.state.opensearch_server else None

        # We are using the address exposed by Juju as service address
        properties |= {
            "server.host": str(self.state.bind_address)
        }
        if opensearch_user and opensearch_password:
            properties |= {
                "opensearch.username": opensearch_user,
                "opensearch.password": opensearch_password,
            }

        if opensearch_ca:
            properties |= {
                "path.data": self.workload.paths.data_dir.as_posix()
            }
            properties["opensearch.ssl.certificateAuthorities"] = [opensearch_ca.as_posix()]

        if self.state.unit_server.tls:
            properties |= TLS_PROPERTIES
            properties |= {
                "server.ssl.certificate": self.workload.paths.certificate.as_posix(),
                "server.ssl.key": self.workload.paths.server_key.as_posix(),
            }

        if self.state.oauth_relation:
            properties |= {
                "opensearch_security.auth.type": ["basicauth", "openid"],
                "opensearch_security.auth.multiple_auth_enabled": True,
                "opensearch_security.openid.connect_url": f"{self.state.oauth.issuer_url}/.well-known/openid-configuration",
                "opensearch_security.openid.client_id": self.state.oauth.client_id,
                "opensearch_security.openid.client_secret": self.state.oauth.client_secret,
                "opensearch_security.openid.verify_hostnames": False,
                "opensearch_security.openid.root_ca": opensearch_ca,
                "opensearch_security.openid.base_redirect_url": self.state.url,
            }

        if self.state.jwt_relation:
            if self.state.oauth_relation:
                properties["opensearch_security.auth.type"] = ["basicauth", "openid", "jwt"]
            else:
                properties["opensearch_security.auth.type"] = ["basicauth", "jwt"]

            properties["opensearch_security.auth.multiple_auth_enabled"] = True

            jwt_relation_data = self.state.jwt_requires.fetch_relation_data(
                [self.state.jwt_relation.id]
            )
            if url_param := jwt_relation_data[self.state.jwt_relation.id].get("jwt-url-parameter"):
                properties["opensearch_security.jwt.url_param"] = url_param

        # Log-level
        config_log_level = self.state.unit_server.log_level

        if config_log_level == "WARNING":
            properties["logging.quiet"] = True
        elif config_log_level == "INFO":
            properties["logging.verbose"] = True
        elif config_log_level == "ERROR":
            properties["logging.silent"] = True

        # Paths
        properties |= {
            "path.data": self.workload.paths.data.as_posix()
        }

        return properties
