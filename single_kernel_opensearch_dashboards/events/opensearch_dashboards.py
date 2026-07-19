#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Handler for General OpenSearch Dashboards charm events."""

import logging

import ops
from pydantic import ValidationError

from single_kernel_opensearch_dashboards.charms.charm_status import StatusHandlingCharm
from single_kernel_opensearch_dashboards.common.exceptions import OSDFileOperationError
from single_kernel_opensearch_dashboards.common.literals import (
    CONFIG_MANAGER_NAME,
    Substrates,
)
from single_kernel_opensearch_dashboards.core.state import ClusterState
from single_kernel_opensearch_dashboards.core.statuses import (
    ConfigStatuses,
    ServerStatuses,
)
from single_kernel_opensearch_dashboards.workload.base import WorkloadBase

logger = logging.getLogger(__name__)
from ops import (
    ConfigChangedEvent,
    EventBase,
    InstallEvent,
    Object,
    RelationChangedEvent,
    RelationDepartedEvent,
    SecretChangedEvent,
    SecretNotFoundError,
)

from single_kernel_opensearch_dashboards.common.literals import (
    PEERS_REL_NAME,
)


class OpenSearchDashboardsEvents(Object):
    """Handler for main Opensearch Dashboards charm events."""

    def __init__(
        self,
        charm: StatusHandlingCharm,
        state: ClusterState,
        workload: WorkloadBase,
    ) -> None:
        """Initialize the OpenSearchDashboardsEvents handler."""
        super().__init__(charm, "opensearch-dashboards-events")  # type: ignore[arg-type]
        self.charm = charm
        self.state = state
        self.workload = workload
        self.cluster_manager = self.charm.cluster_manager
        self.tls_manager = self.charm.tls_manager
        self.framework.observe(self.charm.on.install, self._on_install)
        self.framework.observe(self.charm.on.start, self._on_start)
        self.framework.observe(self.charm.on.update_status, self._on_update_status)
        self.framework.observe(self.charm.on.leader_elected, self._on_leader_elected)
        self.framework.observe(self.charm.on.config_changed, self._on_config_changed)
        self.framework.observe(
            self.charm.on[PEERS_REL_NAME].relation_changed, self._on_relation_changed
        )
        self.framework.observe(
            self.charm.on[PEERS_REL_NAME].relation_joined, self._on_relation_changed
        )
        self.framework.observe(
            self.charm.on[PEERS_REL_NAME].relation_departed, self._on_relation_departed
        )

        self.framework.observe(self.charm.on.secret_changed, self._on_secret_changed)
        if self.state.substrate == Substrates.K8S:
            self.framework.observe(
                self.charm.on.opensearch_dashboards_pebble_ready, self._on_pebble_ready
            )

    def _on_pebble_ready(self, event: ops.PebbleReadyEvent):
        """Define the initial Pebble layer and start the service."""
        if not self.workload.ready():
            self.state.statuses.add(
                status=ServerStatuses.CONTAINER_IS_NOT_ACCESSIBLE.value,
                scope="unit",
                component=self.cluster_manager.name,
            )
            event.defer()
            return

    def _on_install(self, event: InstallEvent) -> None:
        """Handle the `install` event."""
        self.charm.status_handler.set_running_status(
            status=ServerStatuses.INSTALLING_SERVER.value,
            scope="unit",
            component_name=self.cluster_manager.name,
        )

        self.cluster_manager.install_osd_server()

    def _on_start(self, event: EventBase) -> None:
        """Handle the `start` event."""
        if not self.charm.pre_restart_check():
            event.defer()
            return

        # Restore certs from databag if restarted pod / added new unit
        try:
            self.tls_manager.write_tls_files()
        except (OSDFileOperationError, SecretNotFoundError) as e:
            logger.error("%s", e)
            event.defer()
            return

        # We are doing it through restart because of RollingOps lib
        # and their locks on start/restart operations
        self.charm.emit_restart(event)

    def _on_update_status(self, event: EventBase) -> None:
        """Handle the `update-status` event."""
        self.charm.cos_manager.update_grafana_dashboards_title()

        if not self.charm.pre_restart_check():
            event.defer()
            return

        self.charm.emit_restart(event)

    def _on_relation_departed(self, event: RelationDepartedEvent) -> None:
        """Handle the peer `relation-departed` event."""
        # do not restart a unit that is dying, or an application going down
        if self.charm.is_app_removal(event):
            return

        if not self.charm.pre_restart_check():
            event.defer()
            return

        self.charm.emit_restart(event)

    def _on_leader_elected(self, event: EventBase) -> None:
        """Handle the `leader-elected` event."""
        if not self.charm.pre_restart_check():
            event.defer()
            return

        self.charm.emit_restart(event)

    def _on_config_changed(self, event: ConfigChangedEvent) -> None:
        """Handle the `config-changed` event."""
        if not self.charm.pre_restart_check():
            event.defer()
            return

        try:
            self.state.unit_server.log_level = self.state.config.log_level
        except ValidationError:
            self.state.statuses.add(
                ConfigStatuses.INVALID_CONFIG.value, scope="app", component=CONFIG_MANAGER_NAME
            )
            # no point in deferring, the hook will be called another time after config update
            return

        self.charm.emit_restart(event)

    def _on_relation_changed(self, event: RelationChangedEvent) -> None:
        """Handle `relation-changed` and `relation-joined` events for peers."""
        if self.charm.is_app_removal(event):
            return

        if not self.charm.pre_restart_check():
            event.defer()
            return

        self.charm.emit_restart(event)

    def _on_secret_changed(self, event: SecretChangedEvent) -> None:
        """Handle the `secret-changed` event."""
        if not self.charm.pre_restart_check():
            event.defer()
            return

        if not event.secret.label:
            return

        if self.state.cluster.data_interface.secrets.get(
            event.secret.label
        ) or self.state.unit_server.data_interface.secrets.get(event.secret.label):
            logger.info(f"Secret {event.secret.label} changed.")
            self.charm.emit_restart(event)
