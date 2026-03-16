#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Handler for General OpenSearch Dashboards charm events."""
import logging

from single_kernel_opensearch_dashboards.core.cluster import ClusterState
from single_kernel_opensearch_dashboards.core.config import CharmConfig
from single_kernel_opensearch_dashboards.events.shared_events import SharedEvents
from single_kernel_opensearch_dashboards.lib.charms.data_platform_libs.v1.data_models import (
    TypedCharmBase,
)
from single_kernel_opensearch_dashboards.workload.base import WorkloadBase

logger = logging.getLogger(__name__)
from ops import (
    EventBase,
    InstallEvent,
    Object,
    SecretChangedEvent,
)
from ops.model import MaintenanceStatus

from single_kernel_opensearch_dashboards.common.literals import (
    MSG_INSTALLING,
    MSG_STARTING,
    MSG_WAITING_FOR_PEER,
    PEERS_REL_NAME,
)
from single_kernel_opensearch_dashboards.utils.helpers import (
    clear_status,
)


class OpenSearchDashboardsEvents(Object):
    """Handler for main Opensearch Dashboards charm events."""

    def __init__(
        self,
        charm: TypedCharmBase[CharmConfig],
        state: ClusterState,
        workload: WorkloadBase,
        shared_events: SharedEvents,
    ) -> None:
        super().__init__(charm, "opensearch-dashboards-events")
        self.charm = charm
        self.state = state
        self.workload = workload
        self.shared_events = shared_events

        self.framework.observe(self.charm.on.install, self._on_install)

        self.framework.observe(self.charm.on.start, self._start)
        self.framework.observe(self.charm.on.update_status, self.shared_events.reconcile)
        self.framework.observe(self.charm.on.leader_elected, self.shared_events.reconcile)
        self.framework.observe(self.charm.on.config_changed, self.shared_events.reconcile)
        self.framework.observe(
            self.charm.on[PEERS_REL_NAME].relation_changed, self.shared_events.reconcile
        )
        self.framework.observe(
            self.charm.on[PEERS_REL_NAME].relation_joined, self.shared_events.reconcile
        )
        self.framework.observe(
            self.charm.on[PEERS_REL_NAME].relation_departed, self.shared_events.reconcile
        )
        self.framework.observe(self.charm.on.secret_changed, self._on_secret_changed)

    def _on_install(self, event: InstallEvent) -> None:
        """Handler for the `on_install` event."""
        self.charm.unit.status = MaintenanceStatus(MSG_INSTALLING)

        self.workload.install()

        clear_status(self.charm.unit, [MSG_INSTALLING, MSG_WAITING_FOR_PEER])

    def _on_secret_changed(self, event: SecretChangedEvent):
        """Reconfigure services on a secret changed event."""
        if not event.secret.label:
            return

        if not self.state.peer_relation:
            return

        if self.state.cluster.data_interface.secrets.get(
            event.secret.label
        ) or self.state.unit_server.data_interface.secrets.get(event.secret.label):
            logger.info(f"Secret {event.secret.label} changed.")
            self.shared_events.reconcile(event)

    def _start(self, event: EventBase) -> None:
        """Forces a rolling-restart event.

        Necessary for ensuring that `on_start` restarts roll.
        """
        self.charm.unit.status = MaintenanceStatus(MSG_STARTING)
        self.shared_events.reconcile(event)
        clear_status(self.charm.unit, MSG_STARTING)
