#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml
from ops.model import BlockedStatus
from ops.testing import Harness

from single_kernel_opensearch_dashboards.charms.k8s import OpenSearchDashboardsK8sCharm
from single_kernel_opensearch_dashboards.common.exceptions import OSDInstallError
from single_kernel_opensearch_dashboards.common.literals import (
    CHARM_KEY,
    DEPENDENCIES,
    OPENSEARCH_REL_NAME,
    UPGRADE_MANAGER_NAME,
    Substrates,
)
from single_kernel_opensearch_dashboards.core.statuses import UpgradeStatuses
from single_kernel_opensearch_dashboards.events.upgrade import UpgradeEvents
from single_kernel_opensearch_dashboards.lib.charms.data_platform_libs.v1.upgrade import (
    ClusterNotReadyError,
    DataUpgrade,
    DependencyModel,
    UpgradeState,
)
from single_kernel_opensearch_dashboards.managers.upgrade import (
    OpensearchDashboardsDependencyModel,
)
from single_kernel_opensearch_dashboards.workload.vm import VMWorkload

logger = logging.getLogger(__name__)

OPENSEARCH_APP_NAME = "opensearch"

CONFIG = str(yaml.safe_load(Path("tests/unit/charm/config.yaml").read_text()))
ACTIONS = str(yaml.safe_load(Path("tests/unit/charm/actions.yaml").read_text()))
METADATA = str(yaml.safe_load(Path("tests/unit/charm/metadata.yaml").read_text()))


def _begin_k8s_harness(mocker):
    mocker.patch.object(UpgradeEvents, "is_charm_trusted", return_value=True)
    harness = Harness(
        OpenSearchDashboardsK8sCharm,
        meta=METADATA,
        config=CONFIG,
        actions=ACTIONS,
    )
    harness.begin()
    return harness


def test_k8s_upgrade_charm_without_stack_skips_generic_upgrade_handler(mocker, caplog):
    """An idle k8s upgrade-charm event must not enter the generic upgrade flow."""
    generic_upgrade_handler = mocker.patch.object(DataUpgrade, "_on_upgrade_charm")
    set_unit_failed = mocker.patch.object(UpgradeEvents, "set_unit_failed")
    harness = _begin_k8s_harness(mocker)

    with caplog.at_level(logging.INFO):
        harness.charm.on.upgrade_charm.emit()

    generic_upgrade_handler.assert_not_called()
    set_unit_failed.assert_not_called()
    assert "Pod restarted, but no upgrade is in progress. Skipping upgrade logic." in caplog.text


def test_k8s_upgrade_charm_with_stack_runs_k8s_upgrade_flow(mocker):
    """A prepared k8s upgrade must still enter the base k8s upgrade path."""
    post_upgrade_check = mocker.patch.object(UpgradeEvents, "post_upgrade_check")
    set_unit_completed = mocker.patch.object(UpgradeEvents, "set_unit_completed")
    harness = _begin_k8s_harness(mocker)

    harness.add_relation("upgrade", CHARM_KEY)
    harness.charm.upgrade_events.upgrade_stack = [0]

    harness.charm.on.upgrade_charm.emit()

    assert harness.charm.upgrade_events.state is UpgradeState.UPGRADING
    post_upgrade_check.assert_called_once_with()
    set_unit_completed.assert_called_once_with()


def test_vm_upgrade_charm_delegates_to_data_upgrade(mocker):
    """VM upgrade-charm handling stays with DataUpgrade."""
    handler = object.__new__(UpgradeEvents)
    handler.osd_state = SimpleNamespace(substrate=Substrates.VM)
    base_upgrade_handler = mocker.patch.object(DataUpgrade, "_on_upgrade_charm")
    event = mocker.MagicMock()

    handler._on_upgrade_charm(event)

    base_upgrade_handler.assert_called_once_with(event)


@pytest.mark.parametrize(
    "harness", [{"init_peer_hostname": True, "inject_manager_dep_model": True}], indirect=True
)
def test_pre_upgrade_check_succeeds(harness, mocker):
    """pre_upgrade_check successful on a healthy system."""
    with patch(
        "single_kernel_opensearch_dashboards.workload.vm.VMWorkload.healthy", return_value=True
    ):
        assert harness.charm.upgrade_events.pre_upgrade_check() is None


@pytest.mark.parametrize(
    "harness",
    [
        {
            "init_peer_hostname": True,
            "inject_manager_dep_model": True,
        }
    ],
    indirect=True,
)
def test_pre_upgrade_check_fails_if_workload_down(harness, mocker):
    """Simulate a workflow failure to verify pre_upgrade_check fails then."""
    with patch(
        "single_kernel_opensearch_dashboards.workload.vm.VMWorkload.healthy", return_value=False
    ):
        with pytest.raises(ClusterNotReadyError):
            assert harness.charm.upgrade_events.pre_upgrade_check() is None
            statuses = harness.state.statuses.get("unit", UPGRADE_MANAGER_NAME).root
            assert UpgradeStatuses.DB_INCOMPATIBLE_VERSION in statuses


@pytest.mark.parametrize(
    "harness", [{"init_peer_hostname": True, "inject_manager_dep_model": True}], indirect=True
)
@pytest.mark.parametrize("version", ["2.1.1", "2.12.0", "2.12.1", "2.12"])
def test_post_upgrade_check_succeeds(version, harness, mocker):
    """Verify success if no version mismatch"""
    with (
        patch(
            "single_kernel_opensearch_dashboards.managers.config.ConfigManager.config_changed",
            return_value=False,
        ),
        patch(
            "single_kernel_opensearch_dashboards.managers.health.HealthManager.check_unit_health",
            return_value=True,
        ),
    ):
        opensearch_rel_id = harness.add_relation(OPENSEARCH_REL_NAME, OPENSEARCH_APP_NAME)
        harness.update_relation_data(
            opensearch_rel_id, f"{OPENSEARCH_APP_NAME}", {"version": version}
        )
        assert harness.charm.upgrade_events.post_upgrade_check() is None
        assert harness.charm.upgrade_manager.version_compatible() is True


@pytest.mark.parametrize(
    "harness", [{"init_peer_hostname": True, "inject_manager_dep_model": True}], indirect=True
)
def test_post_upgrade_check_fails_major(harness, mocker):
    opensearch_rel_id = harness.add_relation(OPENSEARCH_REL_NAME, OPENSEARCH_APP_NAME)
    harness.update_relation_data(opensearch_rel_id, "opensearch", {"password": "test"})
    with (
        pytest.raises(ClusterNotReadyError),
        patch(
            "single_kernel_opensearch_dashboards.managers.config.ConfigManager.config_changed",
            return_value=False,
        ),
        patch(
            "single_kernel_opensearch_dashboards.managers.health.HealthManager.check_unit_health",
            return_value=True,
        ),
    ):
        harness.update_relation_data(
            opensearch_rel_id, f"{OPENSEARCH_APP_NAME}", {"version": "3.1"}
        )
        assert harness.charm.upgrade_events.post_upgrade_check() is None
        assert harness.charm.upgrade_manager.version_compatible() is False
        assert isinstance(harness.model.unit.status, BlockedStatus)


@pytest.mark.parametrize(
    "harness", [{"init_peer_hostname": True, "inject_manager_dep_model": True}], indirect=True
)
def test_post_upgrade_check_fails_minor(harness, mocker):
    opensearch_rel_id = harness.add_relation(OPENSEARCH_REL_NAME, OPENSEARCH_APP_NAME)
    harness.update_relation_data(opensearch_rel_id, "opensearch", {"password": "test"})
    with (
        pytest.raises(ClusterNotReadyError),
        patch(
            "single_kernel_opensearch_dashboards.managers.config.ConfigManager.config_changed",
            return_value=False,
        ),
        patch(
            "single_kernel_opensearch_dashboards.managers.health.HealthManager.check_unit_health",
            return_value=True,
        ),
    ):
        harness.update_relation_data(
            opensearch_rel_id, f"{OPENSEARCH_APP_NAME}", {"version": "2.13.1"}
        )
        assert harness.charm.upgrade_events.post_upgrade_check() is None
        assert harness.charm.upgrade_manager.version_compatible() is False
        assert isinstance(harness.model.unit.status, BlockedStatus)


@pytest.mark.parametrize(
    "harness", [{"init_peer_hostname": True, "inject_manager_dep_model": True}], indirect=True
)
def test_build_upgrade_stack(harness):
    with (
        harness.hooks_disabled(),
        patch(
            "single_kernel_opensearch_dashboards.managers.config.ConfigManager.config_changed",
            return_value=False,
        ),
        patch(
            "single_kernel_opensearch_dashboards.managers.health.HealthManager.check_unit_health",
            return_value=True,
        ),
    ):
        harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/1")
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, f"{CHARM_KEY}/1", {"hostname": "111.111.111"}
        )
        harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/2")
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, f"{CHARM_KEY}/2", {"hostname": "222.222.222"}
        )
        harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/3")
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, f"{CHARM_KEY}/3", {"hostname": "333.333.333"}
        )

    stack = harness.charm.upgrade_events.build_upgrade_stack()

    assert stack[0] == 0
    assert len(stack) == 5  # 3 here and 2 from harness init in conftest


def test_dashboards_dependency_model():
    assert sorted(OpensearchDashboardsDependencyModel.model_fields.keys()) == sorted(
        DEPENDENCIES.keys()
    )

    for value in DEPENDENCIES.values():
        assert DependencyModel(**value)


@pytest.mark.parametrize(
    "harness", [{"init_peer_hostname": True, "inject_manager_dep_model": True}], indirect=True
)
def test_upgrade_granted_sets_failed_if_failed_snap(harness, mocker):
    mocker.patch.object(VMWorkload, "stop")
    mocker.patch.object(VMWorkload, "restart")
    mocker.patch.object(VMWorkload, "install", side_effect=OSDInstallError("install failed"))
    mocker.patch.object(UpgradeEvents, "pre_upgrade_check")
    mocker.patch.object(UpgradeEvents, "set_unit_completed")
    mocker.patch.object(UpgradeEvents, "set_unit_failed")

    mock_event = mocker.MagicMock()

    harness.charm.upgrade_events._on_upgrade_granted(mock_event)

    VMWorkload.stop.assert_called_once()
    VMWorkload.install.assert_called_once()
    VMWorkload.restart.assert_not_called()
    UpgradeEvents.set_unit_completed.assert_not_called()
    UpgradeEvents.set_unit_failed.assert_called_once()


@pytest.mark.parametrize(
    "harness", [{"init_peer_hostname": True, "inject_manager_dep_model": True}], indirect=True
)
def test_upgrade_granted_sets_failed_if_failed_upgrade_check(harness, mocker):
    opensearch_rel_id = harness.add_relation(OPENSEARCH_REL_NAME, OPENSEARCH_APP_NAME)
    harness.update_relation_data(opensearch_rel_id, "opensearch", {"password": "test"})
    with (
        patch(
            "single_kernel_opensearch_dashboards.managers.config.ConfigManager.config_changed",
            return_value=False,
        ),
        patch(
            "single_kernel_opensearch_dashboards.managers.health.HealthManager.check_unit_health",
            return_value=True,
        ),
    ):
        harness.update_relation_data(
            opensearch_rel_id, f"{OPENSEARCH_APP_NAME}", {"version": "5.12.1"}
        )

    mocker.patch.object(VMWorkload, "stop")
    mocker.patch.object(VMWorkload, "restart")
    mocker.patch.object(VMWorkload, "install", return_value=True)
    mocker.patch.object(UpgradeEvents, "set_unit_completed")
    mocker.patch.object(UpgradeEvents, "set_unit_failed")

    mock_event = mocker.MagicMock()

    harness.charm.upgrade_events._on_upgrade_granted(mock_event)

    VMWorkload.stop.assert_called_once()
    VMWorkload.install.assert_called_once()
    UpgradeEvents.set_unit_completed.assert_not_called()
    UpgradeEvents.set_unit_failed.assert_called_once()


@pytest.mark.parametrize(
    "harness", [{"init_peer_hostname": True, "inject_manager_dep_model": True}], indirect=True
)
def test_upgrade_granted_succeeds(harness, mocker):
    opensearch_rel_id = harness.add_relation(OPENSEARCH_REL_NAME, OPENSEARCH_APP_NAME)
    with (
        patch(
            "single_kernel_opensearch_dashboards.managers.config.ConfigManager.config_changed",
            return_value=False,
        ),
        patch(
            "single_kernel_opensearch_dashboards.managers.health.HealthManager.check_unit_health",
            return_value=True,
        ),
    ):
        harness.update_relation_data(
            opensearch_rel_id, f"{OPENSEARCH_APP_NAME}", {"version": "2.12.1"}
        )

    mocker.patch.object(VMWorkload, "stop")
    mocker.patch.object(VMWorkload, "restart")
    mocker.patch.object(VMWorkload, "install")
    mocker.patch.object(UpgradeEvents, "pre_upgrade_check")
    mocker.patch.object(UpgradeEvents, "set_unit_completed")
    mocker.patch.object(UpgradeEvents, "set_unit_failed")

    mock_event = mocker.MagicMock()

    harness.charm.upgrade_events._on_upgrade_granted(mock_event)

    VMWorkload.stop.assert_called_once()
    VMWorkload.install.assert_called_once()
    VMWorkload.restart.assert_called_once()
    UpgradeEvents.set_unit_completed.assert_called_once()
    UpgradeEvents.set_unit_failed.assert_not_called()


@pytest.mark.parametrize(
    "harness", [{"init_peer_hostname": True, "inject_manager_dep_model": True}], indirect=True
)
def test_upgrade_granted_recurses_upgrade_changed_on_leader(harness, mocker):
    opensearch_rel_id = harness.add_relation(OPENSEARCH_REL_NAME, OPENSEARCH_APP_NAME)
    with (
        patch(
            "single_kernel_opensearch_dashboards.managers.config.ConfigManager.config_changed",
            return_value=False,
        ),
        patch(
            "single_kernel_opensearch_dashboards.managers.health.HealthManager.check_unit_health",
            return_value=True,
        ),
    ):
        harness.update_relation_data(
            opensearch_rel_id, f"{OPENSEARCH_APP_NAME}", {"version": "2.12.1"}
        )

    mocker.patch.object(VMWorkload, "stop")
    mocker.patch.object(VMWorkload, "restart")
    mocker.patch.object(VMWorkload, "install")
    mocker.patch.object(UpgradeEvents, "pre_upgrade_check")
    mocker.patch.object(UpgradeEvents, "on_upgrade_changed")

    mock_event = mocker.MagicMock()

    harness.charm.upgrade_events._on_upgrade_granted(mock_event)

    UpgradeEvents.on_upgrade_changed.assert_not_called()

    with harness.hooks_disabled():
        harness.set_leader(True)

    harness.charm.upgrade_events._on_upgrade_granted(mock_event)

    UpgradeEvents.on_upgrade_changed.assert_called_once()
