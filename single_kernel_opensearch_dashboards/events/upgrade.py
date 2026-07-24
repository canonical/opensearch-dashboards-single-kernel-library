#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Event handler for handling OpensearchDashboards in-place upgrades."""

import logging

from lightkube import ApiError, Client
from lightkube.resources.apps_v1 import StatefulSet
from typing_extensions import override

from single_kernel_opensearch_dashboards.common.exceptions import (
    OSDInstallError,
    OSDNotTrusted,
)
from single_kernel_opensearch_dashboards.common.literals import (
    DEPENDENCIES,
    Substrates,
)
from single_kernel_opensearch_dashboards.core.config import CharmConfig
from single_kernel_opensearch_dashboards.core.state import ClusterState
from single_kernel_opensearch_dashboards.core.statuses import UpgradeStatuses
from single_kernel_opensearch_dashboards.lib.charms.data_platform_libs.v1.data_models import (
    TypedCharmBase,
)
from single_kernel_opensearch_dashboards.lib.charms.data_platform_libs.v1.upgrade import (
    ClusterNotReadyError,
    DataUpgrade,
    KubernetesClientError,
    UpgradeGrantedEvent,
    UpgradeState,
)
from single_kernel_opensearch_dashboards.managers.health import HealthManager
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
        osd_state: ClusterState,
        workload: WorkloadBase,
        upgrade_manager: UpgradeManager,
        health_manager: HealthManager,
    ) -> None:
        if osd_state.substrate == Substrates.K8S and not upgrade_manager.is_charm_trusted(
            charm.model.name
        ):
            raise OSDNotTrusted

        DataUpgrade.__init__(
            self,
            charm,
            OpensearchDashboardsDependencyModel(**DEPENDENCIES),
            "upgrade",
            "vm" if osd_state.substrate == Substrates.VM else "k8s",
        )
        self.osd_state = osd_state
        self.workload = workload
        self.upgrade_manager = upgrade_manager
        self.health_manager = health_manager

    @override
    def _on_upgrade_charm(self, event) -> None:
        """Handle the K8s-specific upgrade flow."""
        if self.osd_state.substrate == Substrates.VM:
            return super()._on_upgrade_charm(event)

        if self.osd_state.upgrade_idle and not self.upgrade_stack:
            logger.info("Pod restarted, but no upgrade is in progress. Skipping upgrade logic.")
            return None

        super()._on_upgrade_charm(event)
        if self.state != UpgradeState.UPGRADING:
            return None

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

    def post_upgrade_check(self) -> None:
        """Runs necessary checks validating the unit is in a healthy state after upgrade."""
        if not self.upgrade_manager.version_compatible():
            self.osd_state.statuses.add(
                status=UpgradeStatuses.DB_INCOMPATIBLE_VERSION.value,
                scope="unit",
                component=self.upgrade_manager.name,
            )
            raise ClusterNotReadyError(
                message="Post-upgrade check failed and cannot safely upgrade",
                cause="Opensearch version mismatch",
            )

    @override
    def pre_upgrade_check(self) -> None:
        """Perform validation before the upgrade process starts.

        Ensures the workload is currently alive and responsive.

        Raises:
            ClusterNotReadyError: If the workload is not running.
        """

        if not self.workload.healthy():
            raise ClusterNotReadyError(
                message="Pre-upgrade check failed and cannot safely upgrade",
                cause="Unit workload is not running",
            )

    @override
    def build_upgrade_stack(self) -> list[int]:
        """Compute the order in which units should be upgraded.

        Returns:
            A list of unit IDs representing the upgrade sequence
        """
        return self.upgrade_manager.build_upgrade_stack()

    @override
    def log_rollback_instructions(self) -> None:
        """Log critical manual recovery steps in the event of an upgrade failure."""
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
        """Handle the event triggered when the unit is permitted to upgrade."""
        try:
            self.upgrade_manager.upgrade_osd()
        except OSDInstallError:
            logger.error("Unable to install OpensearchDashboards...")
            self.set_unit_failed(cause="Workload install failed")
            return

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

    @override
    def _set_rolling_update_partition(self, partition: int) -> None:
        """Patch the StatefulSet's `spec.updateStrategy.rollingUpdate.partition`."""
        if self.substrate == Substrates.VM:
            return

        try:
            patch = {"spec": {"updateStrategy": {"rollingUpdate": {"partition": partition}}}}
            Client().patch(
                res=StatefulSet,
                name=self.charm.model.app.name,
                namespace=self.charm.model.name,
                obj=patch,
            )
            logger.debug(f"Kubernetes StatefulSet partition set to {partition}")

        except ApiError as e:
            if e.status.code == 403:
                cause = "`juju trust` needed to patch StatefulSet"
            else:
                cause = str(e)
            raise KubernetesClientError(message="Kubernetes StatefulSet patch failed", cause=cause)
