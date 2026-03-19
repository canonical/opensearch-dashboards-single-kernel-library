#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for handling OpenSearch Dashboards OAuth configuration."""

import logging

from ops import EventBase, ModelError

from single_kernel_opensearch_dashboards.charms.base import (
    OpenSearchDashboardsStatusHandler,
)
from single_kernel_opensearch_dashboards.common.literals import (
    CONFIG_MANAGER_NAME,
    OAUTH_REL_NAME,
    SERVER_MANAGER_NAME,
)
from single_kernel_opensearch_dashboards.core.cluster import ClusterState
from single_kernel_opensearch_dashboards.core.statuses import (
    ConfigStatuses,
    ServerStatuses,
)
from single_kernel_opensearch_dashboards.events.base import BaseEvents
from single_kernel_opensearch_dashboards.lib.charms.rolling_ops.v0.rollingops import (
    RollingOpsManager,
)
from single_kernel_opensearch_dashboards.managers.config import ConfigManager
from single_kernel_opensearch_dashboards.managers.health import HealthManager
from single_kernel_opensearch_dashboards.managers.server import ServerManager
from single_kernel_opensearch_dashboards.managers.tls import TLSManager

logger = logging.getLogger(__name__)


class OAuthEvents(BaseEvents):
    """Handler for managing oauth relations."""

    def __init__(
        self,
        charm: OpenSearchDashboardsStatusHandler,
        state: ClusterState,
        health_manager: HealthManager,
        config_manager: ConfigManager,
        server_manager: ServerManager,
        restart_manager: RollingOpsManager,
        tls_manager: TLSManager,
    ) -> None:
        super().__init__(
            charm,
            state,
            health_manager,
            config_manager,
            server_manager,
            restart_manager,
            tls_manager,
            "oauth",
        )

        self.framework.observe(
            self.charm.on[OAUTH_REL_NAME].relation_changed, self._on_oauth_relation_changed
        )
        self.framework.observe(
            self.charm.on[OAUTH_REL_NAME].relation_broken, self._on_oauth_relation_changed
        )
        self.state.oauth_require.update_client_config(self.state.oauth_client_config())

    def _on_oauth_relation_changed(self, event: EventBase) -> None:
        """Handler for `_on_oauth_relation_changed` event."""
        if not self.state.servers:
            self.state.statuses.add(
                status=ServerStatuses.SERVERS_IS_DOWN.value,
                scope="app",
                component=SERVER_MANAGER_NAME,
            )
            event.defer()
            return

        self.delete_status_if_present(
            status=ServerStatuses.SERVERS_IS_DOWN.value, scope="app", component=SERVER_MANAGER_NAME
        )

        try:
            provider_info = self.state.oauth_require.get_provider_info()
        except ModelError as e:
            logger.error("OAuth provider info not available: %s", e)
            self.state.statuses.add(
                status=ConfigStatuses.MISSING_OAUTH_SECRET.value,
                scope="app",
                component=CONFIG_MANAGER_NAME,
            )
            event.defer()
            return
        self.state.cluster.update(
            {
                "oauth-client-secret": (
                    provider_info.client_secret
                    if provider_info and provider_info.client_secret
                    else ""
                ),
            }
        )
        self.delete_status_if_present(
            status=ConfigStatuses.MISSING_OAUTH_SECRET.value,
            scope="app",
            component=CONFIG_MANAGER_NAME,
        )

        self.emit_restart(event)
