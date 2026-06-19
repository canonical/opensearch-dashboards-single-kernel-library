#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for handling OpenSearch Dashboards OAuth configuration."""

import logging

from ops import EventBase, ModelError, Object, RelationBrokenEvent

from single_kernel_opensearch_dashboards.charms.charm_status import StatusHandlingCharm
from single_kernel_opensearch_dashboards.common.literals import (
    CLUSTER_MANAGER_NAME,
    CONFIG_MANAGER_NAME,
    OAUTH_REL_NAME,
    TLS_MANAGER_NAME,
    RelDepartureReason,
)
from single_kernel_opensearch_dashboards.core.cluster import ClusterState
from single_kernel_opensearch_dashboards.core.statuses import (
    ConfigStatuses,
    ServerStatuses,
)
from single_kernel_opensearch_dashboards.utils.helpers import relation_departure_reason

logger = logging.getLogger(__name__)


class OAuthEvents(Object):
    """Handler for managing oauth relations."""

    def __init__(
        self,
        charm: StatusHandlingCharm,
        state: ClusterState,
    ) -> None:
        super().__init__(charm, "oauth_events")  # type: ignore[arg-type]
        self.charm = charm
        self.state = state
        self.framework.observe(
            self.charm.on[OAUTH_REL_NAME].relation_changed, self._on_oauth_relation_changed
        )
        self.framework.observe(
            self.charm.on[OAUTH_REL_NAME].relation_broken, self._on_oauth_relation_broken
        )
        self.state.oauth_require.update_client_config(self.state.oauth_client_config())

    def _on_oauth_relation_changed(self, event: EventBase) -> None:
        """Handler for `_on_oauth_relation_changed` event."""
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

    def _on_oauth_relation_broken(self, event: RelationBrokenEvent) -> None:
        """Handler for `_on_oauth_relation_changed` event."""
        if (
            relation_departure_reason(self.charm.base, event.relation.name, event.app.name)
            == RelDepartureReason.APP_REMOVAL
        ):
            return
        self._on_oauth_relation_changed(event)
