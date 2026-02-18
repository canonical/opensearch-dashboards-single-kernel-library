# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Event handler for JWT authentication configuration."""

import logging

from ops import Object, RelationChangedEvent

from single_kernel_opensearch_dashboards.events.shared_events import SharedEvents
from single_kernel_opensearch_dashboards.utils.literals import (
    JWT_REL_NAME,
)

logger = logging.getLogger(__name__)


class JwtEvents(Object):
    """Handler for managing JWT relations."""

    def __init__(
        self,
        shared_events: SharedEvents,
    ) -> None:
        super().__init__(shared_events.charm, "provider")
        self.charm = shared_events.charm
        self.state = shared_events.state
        self.shared_events = shared_events

        self.framework.observe(
            self.charm.on[JWT_REL_NAME].relation_changed, self._on_jwt_relation_changed
        )
        self.framework.observe(
            self.charm.on[JWT_REL_NAME].relation_broken, self.shared_events.reconcile
        )

    def _on_jwt_relation_changed(self, event: RelationChangedEvent) -> None:
        """Handle changed relation data."""
        if not self.state.jwt_relation:
            logger.error(f"Cannot access relation data for {JWT_REL_NAME}")
            return
        self.shared_events.reconcile(event)
