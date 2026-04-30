#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for handling OpenSearch Dashboards OAuth configuration."""

import logging

from ops import EventBase, ModelError, Object, RelationDepartedEvent

from single_kernel_opensearch_dashboards.charms.base import (
    OpenSearchDashboardsStatusHandler,
)
from single_kernel_opensearch_dashboards.common.literals import (
    CLUSTER_MANAGER_NAME,
    CONFIG_MANAGER_NAME,
    OAUTH_REL_NAME,
)
from single_kernel_opensearch_dashboards.core.cluster import ClusterState
from single_kernel_opensearch_dashboards.core.statuses import (
    ConfigStatuses,
    ServerStatuses,
)

logger = logging.getLogger(__name__)


class OAuthEvents(Object):
    """Handler for managing oauth relations."""

    def __init__(
        self,
        charm: OpenSearchDashboardsStatusHandler,
        state: ClusterState,
    ) -> None:
        super().__init__(
            charm,
            "oauth_events",
        )
        self.charm = charm
        self.state = state
        self.framework.observe(
            self.charm.on[OAUTH_REL_NAME].relation_changed, self._on_oauth_relation_changed
        )
        self.framework.observe(
            self.charm.on[OAUTH_REL_NAME].relation_broken, self._on_oauth_relation_changed
        )
        self.framework.observe(
            self.charm.on[OAUTH_REL_NAME].relation_departed, self._on_oauth_departed
        )
        self.state.oauth_require.update_client_config(self.state.oauth_client_config())

    def _on_oauth_relation_changed(self, event: EventBase) -> None:
        """Handler for `_on_oauth_relation_changed` event."""
        if self.state.unit_server.unit_dying:
            return
        if not self.state.servers:
            self.state.statuses.add(
                status=ServerStatuses.SERVERS_IS_DOWN.value,
                scope="app",
                component=CLUSTER_MANAGER_NAME,
            )
            event.defer()
            return

        self.state.delete_status_if_present(
            status=ServerStatuses.SERVERS_IS_DOWN.value,
            scope="app",
            component=CLUSTER_MANAGER_NAME,
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
        self.state.delete_status_if_present(
            status=ConfigStatuses.MISSING_OAUTH_SECRET.value,
            scope="app",
            component=CONFIG_MANAGER_NAME,
        )

        self.charm.emit_restart(event)

    def _on_oauth_departed(self, event: RelationDepartedEvent) -> None:
        """Handle unit dying."""
        if event.departing_unit == self.charm.unit:
            self.state.unit_server.unit_dying = True
