#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Handler for General OpenSearch Dashboards charm events."""
import logging

from pydantic import ValidationError

from single_kernel_opensearch_dashboards.charms.base import (
    OpenSearchDashboardsStatusHandler,
)
from single_kernel_opensearch_dashboards.common.literals import (
    CONFIG_MANAGER_NAME,
    UPGRADE_MANAGER_NAME,
)
from single_kernel_opensearch_dashboards.core.cluster import ClusterState
from single_kernel_opensearch_dashboards.core.statuses import (
    ConfigStatuses,
    ServerStatuses,
    UpgradeStatuses,
)
from single_kernel_opensearch_dashboards.managers.cluster import ClusterManager

logger = logging.getLogger(__name__)
from ops import (
    ConfigChangedEvent,
    EventBase,
    InstallEvent,
    Object,
    RelationDepartedEvent,
    SecretChangedEvent,
)

from single_kernel_opensearch_dashboards.common.literals import (
    PEERS_REL_NAME,
)
from single_kernel_opensearch_dashboards.utils.helpers import (
    update_grafana_dashboards_title,
)


class OpenSearchDashboardsEvents(Object):
    """Handler for main Opensearch Dashboards charm events."""

    def __init__(
        self,
        charm: OpenSearchDashboardsStatusHandler,
        state: ClusterState,
        cluster_manager: ClusterManager,
    ) -> None:
        """Initialize the OpenSearchDashboardsEvents handler."""
        super().__init__(
            charm,
            "opensearch-dashboards-events",
        )
        self.charm = charm
        self.state = state
        self.cluster_manager = cluster_manager

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
        if not self.pre_restart_check():
            event.defer()
            return

        # We are doing it through restart because of RollingOps lib
        # and their locks on start/restart operations
        self.charm.emit_restart(event)

    def _on_update_status(self, event: EventBase) -> None:
        """Handle the `update-status` event."""
        update_grafana_dashboards_title(self.charm)

        if not self.pre_restart_check():
            event.defer()
            return

        self.charm.emit_restart(event)

    def _on_relation_departed(self, event: RelationDepartedEvent) -> None:
        """Handle the peer `relation-departed` event."""
        # do not restart unit that is dying
        if event.departing_unit == self.charm.unit:
            return

        if not self.pre_restart_check():
            event.defer()
            return

        self.charm.emit_restart(event)

    def _on_leader_elected(self, event: EventBase) -> None:
        """Handle the `leader-elected` event."""
        if not self.pre_restart_check():
            event.defer()
            return

        self.charm.emit_restart(event)

    def _on_config_changed(self, event: ConfigChangedEvent) -> None:
        """Handle the `config-changed` event."""
        if not self.pre_restart_check():
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

        self.state.delete_status_if_present(
            status=ConfigStatuses.INVALID_CONFIG.value, scope="app", component=CONFIG_MANAGER_NAME
        )

        self.charm.emit_restart(event)

    def _on_relation_changed(self, event: EventBase) -> None:
        """Handle `relation-changed` and `relation-joined` events for peers."""
        if not self.pre_restart_check():
            event.defer()
            return

        self.charm.emit_restart(event)

    def _on_secret_changed(self, event: SecretChangedEvent) -> None:
        """Handle the `secret-changed` event."""
        if not self.pre_restart_check():
            event.defer()
            return

        if not event.secret.label:
            return

        if self.state.cluster.data_interface.secrets.get(
            event.secret.label
        ) or self.state.unit_server.data_interface.secrets.get(event.secret.label):
            logger.info(f"Secret {event.secret.label} changed.")
            self.charm.emit_restart(event)

    def pre_restart_check(self) -> bool:
        """Perform pre-flight checks to determine if a restart can proceed."""
        # PEER RELATION CHECK
        if not self.state.peer_relation:
            logger.debug("Waiting for peer relations")
            if self.state.unit.is_leader():
                self.state.statuses.add(
                    status=ConfigStatuses.WAITING_FOR_PEER.value,
                    scope="app",
                    component=CONFIG_MANAGER_NAME,
                )
            self.state.statuses.add(
                status=ConfigStatuses.WAITING_FOR_PEER.value,
                scope="unit",
                component=CONFIG_MANAGER_NAME,
            )
            return False

        self.state.delete_status_if_present(
            status=ConfigStatuses.WAITING_FOR_PEER.value,
            scope="unit",
            component=CONFIG_MANAGER_NAME,
        )
        self.state.delete_status_if_present(
            status=ConfigStatuses.WAITING_FOR_PEER.value,
            scope="app",
            component=CONFIG_MANAGER_NAME,
        )

        # UPGRADE IDLE CHECK
        if not self.state.upgrade_idle:
            logger.debug("Waiting for upgrade relations to be idle")
            if self.state.unit.is_leader():
                self.state.statuses.add(
                    status=UpgradeStatuses.WAITING_FOR_UPGRADE.value,
                    scope="app",
                    component=UPGRADE_MANAGER_NAME,
                )
            self.state.statuses.add(
                status=UpgradeStatuses.WAITING_FOR_UPGRADE.value,
                scope="unit",
                component=UPGRADE_MANAGER_NAME,
            )
            return False

        self.state.delete_status_if_present(
            status=UpgradeStatuses.WAITING_FOR_UPGRADE.value,
            scope="unit",
            component=UPGRADE_MANAGER_NAME,
        )
        self.state.delete_status_if_present(
            status=UpgradeStatuses.WAITING_FOR_UPGRADE.value,
            scope="app",
            component=UPGRADE_MANAGER_NAME,
        )

        return True
