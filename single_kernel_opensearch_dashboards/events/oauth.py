#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for handling OpenSearch Dashboards OAuth configuration."""

import logging

from single_kernel_opensearch_dashboards.events.shared_events import SharedEvents
from ops import BlockedStatus, EventBase, ModelError, Object
from single_kernel_opensearch_dashboards.utils.helpers import set_global_status
from single_kernel_opensearch_dashboards.utils.literals import MSG_STATUS_OAUTH_INFO_FAILED, OAUTH_REL_NAME
from single_kernel_opensearch_dashboards.lib.charms.hydra.v0.oauth import \
    OAuthRequirer, ClientConfig

logger = logging.getLogger(__name__)


class OAuthEvents(Object):
    """Handler for managing oauth relations."""

    def __init__(
            self,
            shared_events: SharedEvents,
    ) -> None:
        super().__init__(shared_events.charm, "oauth")
        self.charm = shared_events.charm
        self.state = shared_events.state
        self.shared_events = shared_events

        self.framework.observe(
            self.charm.on[OAUTH_REL_NAME].relation_changed, self._on_oauth_relation_changed
        )
        self.framework.observe(
            self.charm.on[OAUTH_REL_NAME].relation_broken, self._on_oauth_relation_changed
        )

        self.oauth = OAuthRequirer(self.charm, self._oauth_client_config(),
                                   relation_name=OAUTH_REL_NAME)
        self.oauth.update_client_config(self._oauth_client_config())

    def _on_oauth_relation_changed(self, event: EventBase) -> None:
        """Handler for `_on_oauth_relation_changed` event."""
        if not self.state.servers:
            event.defer()
            return
        try:
            provider_info = self.oauth.get_provider_info()
        except ModelError as e:
            logger.error("OAuth provider info not available: %s", e)
            set_global_status(self.charm, BlockedStatus(MSG_STATUS_OAUTH_INFO_FAILED))
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

        self.shared_events.reconcile(event)

    def _oauth_client_config(self) -> ClientConfig:
        """Generates actual client config for the OAuth."""
        return ClientConfig(
            audience=["opensearch"],
            redirect_uri=f"{self.state.url}/auth/openid/login",
            scope="openid profile email phone offline address",
            grant_types=["authorization_code"],
            token_endpoint_auth_method="client_secret_post",
        )
