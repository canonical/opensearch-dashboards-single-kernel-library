# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Event handler for JWT authentication configuration."""

import logging
from typing import cast

from dpcharmlibs.interfaces import (
    AuthenticationUpdatedEvent,
    RequirerCommonModel,
    ResourceRequirerEventHandler,
)
from ops import CharmBase, Object, RelationBrokenEvent, RelationChangedEvent
from typing_extensions import Any

from single_kernel_opensearch_dashboards.charms.charm_status import StatusHandlingCharm
from single_kernel_opensearch_dashboards.common.literals import (
    CONFIG_MANAGER_NAME,
    JWT_REL_NAME,
)
from single_kernel_opensearch_dashboards.common.statuses import ConfigStatuses
from single_kernel_opensearch_dashboards.core.relation_models import JWTAuthConfiguration
from single_kernel_opensearch_dashboards.core.state import ClusterState

logger = logging.getLogger(__name__)


class JwtEvents(Object):
    """Handler for managing JWT relations."""

    def __init__(
        self,
        charm: StatusHandlingCharm,
        state: ClusterState,
    ) -> None:
        super().__init__(charm, "jwt_events")  # type: ignore[arg-type]
        self.charm = charm
        self.state = state
        self.jwt_interface = ResourceRequirerEventHandler(
            cast(CharmBase, cast(Any, charm)),
            relation_name=JWT_REL_NAME,
            requests=[RequirerCommonModel(resource="jwt-configuration")],
            response_model=JWTAuthConfiguration,
        )
        self.framework.observe(
            self.charm.on[JWT_REL_NAME].relation_changed, self._on_jwt_relation_changed
        )
        self.framework.observe(
            self.charm.on[JWT_REL_NAME].relation_broken, self._on_jwt_relation_broken
        )
        self.framework.observe(
            self.jwt_interface.on.authentication_updated, self._on_jwt_authentication_updated
        )

    def _on_jwt_relation_changed(self, event: RelationChangedEvent) -> None:
        """Handle changed relation data."""
        if not self.state.jwt_relation:
            self.state.statuses.add(
                status=ConfigStatuses.JWT_RELATIONS_DATA_FAILED.value,
                scope="app",
                component=CONFIG_MANAGER_NAME,
            )
            logger.error(f"Cannot access relation data for {JWT_REL_NAME}")
            return

        self.charm.emit_restart(event)

    def _on_jwt_relation_broken(self, event: RelationBrokenEvent) -> None:
        """Handle broken relation data."""
        if self.charm.is_app_removal(event):
            return

        self.charm.emit_restart(event)

    def _on_jwt_authentication_updated(self, event: AuthenticationUpdatedEvent) -> None:
        """Handle a rotated JWT secret delivered via secret-changed."""
        if not self.state.jwt_relation:
            return

        if not self.state.jwt:
            logger.debug("No valid JWT configuration found in the databag yet, deferring.")
            event.defer()
            return

        self.charm.emit_restart(event)
