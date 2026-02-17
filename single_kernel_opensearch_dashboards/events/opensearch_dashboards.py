#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Handler for General OpenSearch Dashboards charm events."""
import logging


logger = logging.getLogger(__name__)
from ops import (
    EventBase,
    InstallEvent,
    Object,
    SecretChangedEvent,
)
from ops.model import MaintenanceStatus, WaitingStatus
from single_kernel_opensearch_dashboards.utils.helpers import (
    clear_status,
)
from single_kernel_opensearch_dashboards.utils.literals import (
    MSG_INSTALLING,
    MSG_STARTING,
    MSG_WAITING_FOR_PEER,
    PEER,
)
from single_kernel_opensearch_dashboards.events.shared_events import SharedEvents


class OpenSearchDashboardsEvents(Object):
    """Handler for main Opensearch Dashboards charm events."""
    def __init__(
            self,
            shared_events: SharedEvents,
    ) -> None:
        super().__init__(shared_events.charm, "opensearch-dashboards-events")
        self.charm = shared_events.charm
        self.state = shared_events.state
        self.workload = shared_events.workload
        self.shared_events = shared_events

        self.framework.observe(getattr(self.charm.on, "install"), self._on_install)
        self.framework.observe(getattr(self.charm.on, "start"), self._start)
        self.framework.observe(getattr(self.charm.on, "update_status"),
                               self.shared_events.reconcile)
        self.framework.observe(getattr(self.charm.on, "leader_elected"),
                               self.shared_events.reconcile)
        self.framework.observe(getattr(self.charm.on, "config_changed"),
                               self.shared_events.reconcile)
        self.framework.observe(getattr(self.charm.on, f"{PEER}_relation_changed"),
                               self.shared_events.reconcile)
        self.framework.observe(getattr(self.charm.on, f"{PEER}_relation_joined"),
                               self.shared_events.reconcile)
        self.framework.observe(getattr(self.charm.on, f"{PEER}_relation_departed"),
                               self.shared_events.reconcile)
        self.framework.observe(getattr(self.charm.on, "secret_changed"),
                               self._on_secret_changed)


    def _on_install(self, event: InstallEvent) -> None:
        """Handler for the `on_install` event."""
        self.charm.unit.status = MaintenanceStatus(MSG_INSTALLING)

        self.workload.install()

        # don't complete install until passwords set
        if not self.state.peer_relation:
            self.charm.unit.status = WaitingStatus(MSG_WAITING_FOR_PEER)
            event.defer()
            return
        clear_status(self.charm.unit, [MSG_INSTALLING, MSG_WAITING_FOR_PEER])


    def _on_secret_changed(self, event: SecretChangedEvent):
        """Reconfigure services on a secret changed event."""
        if not event.secret.label:
            return

        if not self.state.peer_relation:
            return

        cluster_secret_label = self.state.cluster.data_interface._generate_secret_label(
            PEER,
            self.state.peer_relation.id,
            "extra",  # type:ignore noqa
        )  # Changes with the soon upcoming new version of DP-libs STILL within this POC

        server_secret_label = self.state.unit_server.data_interface._generate_secret_label(
            PEER,
            self.state.peer_relation.id,
            "extra",  # type:ignore noqa
        )  # Changes with the soon upcoming new version of DP-libs STILL within this POC

        if event.secret.label in [cluster_secret_label, server_secret_label]:
            logger.info(f"Secret {event.secret.label} changed.")
            self.shared_events.reconcile(event)

    def _start(self, event: EventBase) -> None:
        """Forces a rolling-restart event.

        Necessary for ensuring that `on_start` restarts roll.
        """
        # if not self.state.peer_relation or not self.state.stable or not self.upgrade_events.idle:
        self.charm.unit.status = MaintenanceStatus(MSG_STARTING)
        if not self.state.peer_relation or not self.state.stable:
            event.defer()
            return

        self.shared_events.reconcile(event)
        clear_status(self.charm.unit, MSG_STARTING)

