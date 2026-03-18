#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Handler for General OpenSearch Dashboards charm events."""
import logging

from pydantic import ValidationError

from single_kernel_opensearch_dashboards.charms.base import (
    OpenSearchDashboardsStatusHandler,
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

logger = logging.getLogger(__name__)
from ops import (
    ConfigChangedEvent,
    EventBase,
    InstallEvent,
    RelationDepartedEvent,
    SecretChangedEvent,
)

from single_kernel_opensearch_dashboards.common.literals import (
    PEERS_REL_NAME,
)
from single_kernel_opensearch_dashboards.utils.helpers import (
    update_grafana_dashboards_title,
)


class OpenSearchDashboardsEvents(BaseEvents):
    """Handler for main Opensearch Dashboards charm events."""

    def __init__(
        self,
        charm: OpenSearchDashboardsStatusHandler,
        state: ClusterState,
        health_manager: HealthManager,
        config_manager: ConfigManager,
        server_manager: ServerManager,
        restart_manager: RollingOpsManager,
    ) -> None:
        super().__init__(
            charm,
            state,
            health_manager,
            config_manager,
            server_manager,
            restart_manager,
            "opensearch-dashboards-events",
        )
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
        """Handler for the `install` event."""
        self.charm.status_handler.set_running_status(
            status=ServerStatuses.INSTALLING_SERVER.value,
            scope="unit",
            component_name="server_manager",
        )

        self.server_manager.install_osd_server()

    def _on_start(self, event: EventBase) -> None:
        """Handler for the `start` event."""
        if not self.pre_restart_check:
            event.defer()
            return

        # We are doing it through restart because of RollingOps lib
        # and their locks on start/restart operations
        self.emit_restart(event)

    def _on_update_status(self, event: EventBase) -> None:
        update_grafana_dashboards_title(self.charm)

        if not self.pre_restart_check:
            event.defer()
            return

        self.emit_restart(event)
        self.check_osd_status()

    def _on_relation_departed(self, event: RelationDepartedEvent):
        # do not restart unit that is dying
        if event.departing_unit == self.charm.unit:
            return

        if not self.pre_restart_check:
            event.defer()
            return

        self.emit_restart(event)

    def _on_leader_elected(self, event: EventBase):
        if not self.pre_restart_check:
            event.defer()
            return

        self.emit_restart(event)

    def _on_config_changed(self, event: ConfigChangedEvent):
        if not self.pre_restart_check:
            event.defer()
            return

        try:
            self.state.unit_server.log_level = self.state.config.log_level
        except ValidationError:
            self.state.statuses.add(
                ConfigStatuses.INVALID_CONFIG.value, scope="app", component="config_manager"
            )
            # no point in deferring, the hook will be called another time after config update
            return
        self.delete_status_if_present(
            status=ConfigStatuses.INVALID_CONFIG.value, scope="app", component="server_manager"
        )

        self.emit_restart(event)

    def _on_relation_changed(self, event: EventBase):
        if not self.pre_restart_check:
            event.defer()
            return

        self.emit_restart(event)

    def _on_secret_changed(self, event: SecretChangedEvent):
        """Reconfigure services on a secret changed event."""
        if not self.pre_restart_check:
            event.defer()
            return

        if not event.secret.label:
            return

        if self.state.cluster.data_interface.secrets.get(
            event.secret.label
        ) or self.state.unit_server.data_interface.secrets.get(event.secret.label):
            logger.info(f"Secret {event.secret.label} changed.")
            self.emit_restart(event)
