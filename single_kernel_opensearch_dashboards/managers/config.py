#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for handling configuration building + writing."""

import logging
from typing import Any

import yaml
from data_platform_helpers.advanced_statuses import StatusObject
from data_platform_helpers.advanced_statuses.types import Scope
from ops import ModelError
from pydantic import ValidationError

from single_kernel_opensearch_dashboards.common.literals import (
    CONFIG_MANAGER_NAME,
    DASHBOARD_USER,
    Substrates,
)
from single_kernel_opensearch_dashboards.core.cluster import ClusterState
from single_kernel_opensearch_dashboards.core.statuses import (
    CharmStatuses,
    ConfigStatuses,
)
from single_kernel_opensearch_dashboards.managers.base import BaseManager
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


class ConfigManager(BaseManager):
    """Manager for handling configuration building + writing."""

    def __init__(self, state: ClusterState, workload: WorkloadBase):
        super().__init__(state, workload)
        self.name = CONFIG_MANAGER_NAME

    def config_changed(self) -> bool:
        """Compares expected vs actual config that would require a restart to apply."""
        return self.load_dashboard_properties() != self.dashboard_properties()

    def set_dashboard_properties(self) -> None:
        """Writes built config file."""
        self.workload.write_text(
            yaml.dump(self.dashboard_properties()), self.workload.paths.properties
        )

    def load_dashboard_properties(self) -> dict[str, Any]:
        """
        Reads built config file.
        Raises:
            OSDFileOperationError: If there was an error when reading config.
        """
        return yaml.load(
            self.workload.read_text(self.workload.paths.properties), yaml.UnsafeLoader
        )

    def dashboard_properties(self) -> dict[str, Any]:
        """Build the opensearch_dashboards.yml content.

        As we are building on top of the known templates above, we do not need to care about
        merging lists, for example. We will override the default properties if needed.

        Returns:
            List of properties to be set to opensearch_dashboards.yml config file
        """

        properties = DEFAULT_PROPERTIES.copy()

        opensearch_user = DASHBOARD_USER if self.state.opensearch_server else ""
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
            "server.host": (
                self.state.unit_server.host if self.state.substrate == Substrates.VM else "0.0.0.0"
            )
        }

        if opensearch_user and opensearch_password:
            properties |= {
                "opensearch.username": opensearch_user,
                "opensearch.password": opensearch_password,
            }

        if opensearch_ca:
            properties |= {"path.data": self.workload.paths.data_dir.as_posix()}
            properties["opensearch.ssl.certificateAuthorities"] = [opensearch_ca.as_posix()]

        if self.state.unit_server.tls_enabled:
            properties |= TLS_PROPERTIES
            properties |= {
                "server.ssl.certificate": self.workload.paths.certificate.as_posix(),
                "server.ssl.key": self.workload.paths.server_key.as_posix(),
            }

        if (
            self.state.substrate == Substrates.K8S
            and self.state.ingress_relation
            and self.state.ingress.url
        ):
            properties |= {
                "server.rewriteBasePath": True,
                "server.basePath": f"{self.state.ingress.base_path}",
            }

        if self.state.oauth_relation and self.state.unit_server.tls_enabled:
            properties |= {
                "opensearch_security.auth.type": ["basicauth", "openid"],
                "opensearch_security.auth.multiple_auth_enabled": True,
                "opensearch_security.openid.connect_url": f"{self.state.oauth.issuer_url}/.well-known/openid-configuration",
                "opensearch_security.openid.client_id": self.state.oauth.client_id,
                "opensearch_security.openid.client_secret": self.state.oauth.client_secret,
                "opensearch_security.openid.verify_hostnames": False,
                "opensearch_security.openid.base_redirect_url": self.state.oauth_url,
            }
            if opensearch_ca:
                properties |= {"opensearch_security.openid.root_ca": opensearch_ca.as_posix()}

        if self.state.jwt_relation:
            if self.state.oauth_relation and self.state.unit_server.tls_enabled:
                properties["opensearch_security.auth.type"] = ["basicauth", "openid", "jwt"]
            else:
                properties["opensearch_security.auth.type"] = ["basicauth", "jwt"]

            properties["opensearch_security.auth.multiple_auth_enabled"] = True
            properties |= {"opensearch_security.jwt.url_param": self.state.jwt.get_jwt_url()}

        # Log-level
        config_log_level = self.state.unit_server.log_level

        if config_log_level == "WARNING":
            properties["logging.quiet"] = True
        elif config_log_level == "INFO":
            properties["logging.verbose"] = True
        elif config_log_level == "ERROR":
            properties["logging.silent"] = True

        # Paths
        properties |= {"path.data": self.workload.paths.data.as_posix()}

        return properties

    def get_statuses(self, scope: Scope, recompute: bool = False) -> list[StatusObject]:
        """Compute the config manager's statuses."""
        status_list: list[StatusObject] = []

        if not self.state.peer_relation:
            status_list.append(ConfigStatuses.WAITING_FOR_PEER.value)

        if self.state.jwt_relation:
            if self.state.jwt.get_jwt_url() is None:
                status_list.append(ConfigStatuses.JWT_RELATIONS_DATA_FAILED.value)

        try:
            self.state.config.log_level
        except ValidationError:
            status_list.append(ConfigStatuses.INVALID_CONFIG.value)

        try:
            self.state.oauth_require.get_provider_info()
        except ModelError:
            status_list.append(ConfigStatuses.MISSING_OAUTH_SECRET.value)

        return status_list or [CharmStatuses.ACTIVE_IDLE.value]
