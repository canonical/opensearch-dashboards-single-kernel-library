#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Event handler for handling OpensearchDashboards in-place upgrades."""
import logging

from ops.model import BlockedStatus
from typing_extensions import override

from single_kernel_opensearch_dashboards.common.exceptions import OSDInstallError
from single_kernel_opensearch_dashboards.common.literals import (
    DEPENDENCIES,
    MSG_INCOMPATIBLE_UPGRADE,
    Substrates,
)
from single_kernel_opensearch_dashboards.core.cluster import ClusterState
from single_kernel_opensearch_dashboards.core.config import CharmConfig
from single_kernel_opensearch_dashboards.events.shared_events import SharedEvents
from single_kernel_opensearch_dashboards.lib.charms.data_platform_libs.v0.upgrade import (
    ClusterNotReadyError,
    DataUpgrade,
    UpgradeGrantedEvent,
)
from single_kernel_opensearch_dashboards.lib.charms.data_platform_libs.v1.data_models import (
    TypedCharmBase,
)
from single_kernel_opensearch_dashboards.managers.upgrade import (
    OpensearchDashboardsDependencyModel,
    UpgradeManager,
)
from single_kernel_opensearch_dashboards.workload.base import WorkloadBase

logger = logging.getLogger(__name__)


class UpgradeEvents(DataUpgrade):
    """Implementation of :class:`DataUpgrade` overrides for in-place upgrades."""

    def __init__(
        self,
        charm: TypedCharmBase[CharmConfig],
        state: ClusterState,
        workload: WorkloadBase,
        substrate: Substrates,
        upgrade_manager: UpgradeManager,
        shared_events: SharedEvents,
    ) -> None:
        DataUpgrade.__init__(
            self,
            charm,
            OpensearchDashboardsDependencyModel(**DEPENDENCIES),
            "upgrade",
            "vm" if substrate == Substrates.VM else "k8s",
        )
        self.charm = charm
        # Because DataUpgrade already have property state and cluster_state
        self.od_state = state
        self.workload = workload
        self.shared_events = shared_events
        self.upgrade_manager = upgrade_manager

    def post_upgrade_check(self) -> None:
        """Runs necessary checks validating the unit is in a healthy state after upgrade."""
        if not self.upgrade_manager.version_compatible():
            self.charm.unit.status = BlockedStatus(MSG_INCOMPATIBLE_UPGRADE)
            raise ClusterNotReadyError(
                message="Post-upgrade check failed and cannot safely upgrade",
                cause="Opensearch version mismatch",
            )

    @override
    def pre_upgrade_check(self) -> None:
        if not self.workload.alive():
            raise ClusterNotReadyError(
                message="Pre-upgrade check failed and cannot safely upgrade",
                cause="Unit workload is not running",
            )

    @override
    def build_upgrade_stack(self) -> list[int]:
        upgrade_stack = []

        units = [self.charm.unit]
        if self.od_state.peer_relation:
            units.extend(list(self.od_state.peer_relation.units))

        for unit in units:
            upgrade_stack.append(int(unit.name.split("/")[-1]))

        return upgrade_stack

    @override
    def log_rollback_instructions(self) -> None:
        logger.critical(
            "\n".join(
                [
                    "Unit failed to upgrade and requires manual rollback to previous stable version.",
                    "    1. Re-run `pre-upgrade-check` action on the leader unit to enter 'recovery' state",
                    "    2. Run `juju refresh` to the previously deployed charm revision",
                ]
            )
        )
        return

    @override
    def _on_upgrade_granted(self, event: UpgradeGrantedEvent) -> None:
        self.workload.stop()

        try:
            self.workload.install()
        except OSDInstallError:
            logger.error("Unable to install OpensearchDashboards...")
            self.set_unit_failed(cause="Workload install failed")
            return

        logger.info(f"{self.charm.unit.name} upgrading workload...")
        self.workload.restart()

        try:
            logger.debug("Running post-upgrade check...")
            self.post_upgrade_check()

            logger.debug("Marking unit completed...")
            self.set_unit_completed()

            # ensures leader gets its own relation-changed when it upgrades
            if self.charm.unit.is_leader():
                logger.debug("Re-emitting upgrade-changed on leader...")
                self.on_upgrade_changed(event)

        except ClusterNotReadyError as e:
            logger.error(e.cause)
            self.set_unit_failed(cause=e.cause)
