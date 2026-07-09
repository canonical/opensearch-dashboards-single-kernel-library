#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Handler for General OpenSearch Dashboards charm events."""

import logging
from typing import Any, cast

import ops
from pydantic import ValidationError

from single_kernel_opensearch_dashboards.charms.charm_status import StatusHandlingCharm
from single_kernel_opensearch_dashboards.common.exceptions import OSDFileOperationError
from single_kernel_opensearch_dashboards.common.literals import (
    CLUSTER_MANAGER_NAME,
    CONFIG_MANAGER_NAME,
    COS_RELATION_NAME,
    GRAFANA_RELATION_NAME,
    LOKI_RELATION_NAME,
    PROMETHEUS_RELATION_NAME,
    UPGRADE_MANAGER_NAME,
    Substrates,
)
from single_kernel_opensearch_dashboards.core.state import ClusterState
from single_kernel_opensearch_dashboards.core.statuses import (
    ConfigStatuses,
    ServerStatuses,
    UpgradeStatuses,
)
from single_kernel_opensearch_dashboards.workload.base import WorkloadBase

logger = logging.getLogger(__name__)
from ops import (
    CharmBase,
    ConfigChangedEvent,
    EventBase,
    InstallEvent,
    Object,
    RelationChangedEvent,
    RelationCreatedEvent,
    RelationDepartedEvent,
    SecretChangedEvent,
    SecretNotFoundError,
)

from single_kernel_opensearch_dashboards.common.literals import (
    PEERS_REL_NAME,
)
from single_kernel_opensearch_dashboards.utils.helpers import (
    app_going_down,
    update_grafana_dashboards_title,
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

        self.framework.observe(
            self.charm.on[COS_RELATION_NAME].relation_created, self._on_cos_relation_created
        )
        self.framework.observe(
            self.charm.on[PROMETHEUS_RELATION_NAME].relation_created, self._on_cos_relation_created
        )
        self.framework.observe(
            self.charm.on[LOKI_RELATION_NAME].relation_created, self._on_cos_relation_created
        )
        self.framework.observe(
            self.charm.on[GRAFANA_RELATION_NAME].relation_created, self._on_cos_relation_created
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
        if not self.pre_restart_check():
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
        update_grafana_dashboards_title(cast(CharmBase, cast(Any, self.charm)))

        if not self.pre_restart_check():
            event.defer()
            return

        self.charm.emit_restart(event)

    def _on_relation_departed(self, event: RelationDepartedEvent) -> None:
        """Handle the peer `relation-departed` event."""
        # do not restart a unit that is dying, or an application going down
        if app_going_down(self.charm.base, event):
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

        self.charm.emit_restart(event)

    def _on_relation_changed(self, event: RelationChangedEvent) -> None:
        """Handle `relation-changed` and `relation-joined` events for peers."""
        if app_going_down(self.charm.base, event):
            return

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

    def _on_cos_relation_created(self, event: RelationCreatedEvent) -> None:
        """Handle the `secret-changed` event."""
        if self.state.substrate == Substrates.VM and (
            self.state.loki_relation
            or self.state.grafana_relation
            or self.state.prometheus_relation
        ):
            logger.warning(
                "grafana-k8s, loki-k8s, prometheus-k8s relation is not possible for vm, use grafana-agent instead"
            )
            self.state.add_status_to_both(
                ServerStatuses.COS_RELATION_IN_VM.value, CLUSTER_MANAGER_NAME
            )
            return
        elif self.state.substrate == Substrates.K8S and self.state.cos_agent_relation:
            logger.warning(
                "grafana-agent relation is not possible for k8s, use grafana-k8s, loki-k8s, prometheus-k8s instead"
            )
            self.state.add_status_to_both(
                ServerStatuses.COS_RELATION_IN_K8s.value, CLUSTER_MANAGER_NAME
            )

        return

    def pre_restart_check(self) -> bool:
        """Perform pre-flight checks to determine if a restart can proceed."""
        # CONTAINER CHECK
        if not self.workload.ready():
            return False

        # PEER RELATION CHECK
        if not self.state.peer_relation:
            logger.debug("Waiting for peer relations")
            self.state.add_status_to_both(
                status=ConfigStatuses.WAITING_FOR_PEER.value, component=CONFIG_MANAGER_NAME
            )
            return False

        # UPGRADE IDLE CHECK
        if not self.state.upgrade_idle:
            logger.debug("Waiting for upgrade relations to be idle")
            self.state.add_status_to_both(
                status=UpgradeStatuses.WAITING_FOR_UPGRADE.value,
                component=UPGRADE_MANAGER_NAME,
            )
            return False

        return True
