#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from unittest.mock import patch

import pytest
from single_kernel_opensearch_dashboards.lib.charms.data_platform_libs.v0.upgrade import ClusterNotReadyError, DependencyModel
from ops.model import BlockedStatus

from single_kernel_opensearch_dashboards.events.upgrade import UpgradeEvents
from single_kernel_opensearch_dashboards.managers.upgrade import OpensearchDashboardsDependencyModel
from single_kernel_opensearch_dashboards.common.exceptions import OSDInstallError
from single_kernel_opensearch_dashboards.utils.literals import CHARM_KEY, DEPENDENCIES, OPENSEARCH_REL_NAME
from single_kernel_opensearch_dashboards.utils.literals import MSG_INCOMPATIBLE_UPGRADE
from single_kernel_opensearch_dashboards.workload.vm import WorkloadVM

logger = logging.getLogger(__name__)

OPENSEARCH_APP_NAME = "opensearch"

@pytest.mark.parametrize("harness", [{
    "init_peer_hostname": True,
    "inject_manager_dep_model": True
}], indirect=True)
def test_pre_upgrade_check_succeeds(harness, mocker):
    """pre_upgrade_check successful on a healthy system."""
    with patch("single_kernel_opensearch_dashboards.workload.vm.WorkloadVM.alive", return_value=True):
        assert harness.charm.upgrade_events.pre_upgrade_check() is None

@pytest.mark.parametrize("harness", [{
    "init_peer_hostname": True,
    "inject_manager_dep_model": True,
}], indirect=True)
def test_pre_upgrade_check_fails_if_workload_down(harness, mocker):
    """Simulate a workflow failure to verify pre_upgrade_check fails then."""
    with patch("single_kernel_opensearch_dashboards.workload.vm.WorkloadVM.alive", return_value=False):
        with pytest.raises(ClusterNotReadyError):
            assert harness.charm.upgrade_events.pre_upgrade_check() is None
            harness.charm.unit.status = BlockedStatus(MSG_INCOMPATIBLE_UPGRADE)

@pytest.mark.parametrize("harness", [{
    "init_peer_hostname": True,
    "inject_manager_dep_model": True
}], indirect=True)
@pytest.mark.parametrize("version", ["2.1.1", "2.12.0", "2.12.1", "2.12"])
def test_post_upgrade_check_succeeds(version, harness, mocker):
    """Verify success if no version mismatch"""
    opensearch_rel_id = harness.add_relation(OPENSEARCH_REL_NAME, OPENSEARCH_APP_NAME)
    harness.update_relation_data(opensearch_rel_id, f"{OPENSEARCH_APP_NAME}", {"version": version})
    assert harness.charm.upgrade_events.post_upgrade_check() is None
    assert harness.charm.shared_events.upgrade_manager.version_compatible() is True

@pytest.mark.parametrize("harness", [{
    "init_peer_hostname": True,
    "inject_manager_dep_model": True
}], indirect=True)
def test_post_upgrade_check_fails_major(harness, mocker):
    opensearch_rel_id = harness.add_relation(OPENSEARCH_REL_NAME, OPENSEARCH_APP_NAME)
    with pytest.raises(ClusterNotReadyError):
        harness.update_relation_data(
            opensearch_rel_id, f"{OPENSEARCH_APP_NAME}", {"version": "3.1"}
        )
        assert harness.charm.upgrade_events.post_upgrade_check() is None
        assert harness.charm.upgrade_manager.version_compatible() is False
        assert isinstance(harness.model.unit.status, BlockedStatus)

@pytest.mark.parametrize("harness", [{
    "init_peer_hostname": True,
    "inject_manager_dep_model": True
}], indirect=True)
def test_post_upgrade_check_fails_minor(harness, mocker):
    opensearch_rel_id = harness.add_relation(OPENSEARCH_REL_NAME, OPENSEARCH_APP_NAME)
    with pytest.raises(ClusterNotReadyError):
        harness.update_relation_data(
            opensearch_rel_id, f"{OPENSEARCH_APP_NAME}", {"version": "2.13.1"}
        )
        assert harness.charm.upgrade_events.post_upgrade_check() is None
        assert harness.charm.shared_events.upgrade_manager.version_compatible() is False
        assert isinstance(harness.model.unit.status, BlockedStatus)

@pytest.mark.parametrize("harness", [{
    "init_peer_hostname": True,
    "inject_manager_dep_model": True
}], indirect=True)
def test_build_upgrade_stack(harness):
    with harness.hooks_disabled():
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
    assert len(stack) == 5 # 3 here and 2 from harness init in conftest

def test_dashboards_dependency_model():
    assert sorted(OpensearchDashboardsDependencyModel.__fields__.keys()) == sorted(
        DEPENDENCIES.keys()
    )

    for value in DEPENDENCIES.values():
        assert DependencyModel(**value)

@pytest.mark.parametrize("harness", [{
    "init_peer_hostname": True,
    "inject_manager_dep_model": True
}], indirect=True)
def test_upgrade_granted_sets_failed_if_failed_snap(harness, mocker):
    mocker.patch.object(WorkloadVM, "stop")
    mocker.patch.object(WorkloadVM, "restart")
    mocker.patch.object(WorkloadVM, "install", side_effect=OSDInstallError("install failed"))
    mocker.patch.object(UpgradeEvents, "pre_upgrade_check")
    mocker.patch.object(UpgradeEvents, "set_unit_completed")
    mocker.patch.object(UpgradeEvents, "set_unit_failed")

    mock_event = mocker.MagicMock()

    harness.charm.upgrade_events._on_upgrade_granted(mock_event)

    WorkloadVM.stop.assert_called_once()
    WorkloadVM.install.assert_called_once()
    WorkloadVM.restart.assert_not_called()
    UpgradeEvents.set_unit_completed.assert_not_called()
    UpgradeEvents.set_unit_failed.assert_called_once()

@pytest.mark.parametrize("harness", [{
    "init_peer_hostname": True,
    "inject_manager_dep_model": True
}], indirect=True)
def test_upgrade_granted_sets_failed_if_failed_upgrade_check(harness, mocker):
    opensearch_rel_id = harness.add_relation(OPENSEARCH_REL_NAME, OPENSEARCH_APP_NAME)
    harness.update_relation_data(
        opensearch_rel_id, f"{OPENSEARCH_APP_NAME}", {"version": "5.12.1"}
    )

    mocker.patch.object(WorkloadVM, "stop")
    mocker.patch.object(WorkloadVM, "restart")
    mocker.patch.object(WorkloadVM, "install", return_value=True)
    mocker.patch.object(UpgradeEvents, "set_unit_completed")
    mocker.patch.object(UpgradeEvents, "set_unit_failed")

    mock_event = mocker.MagicMock()

    harness.charm.upgrade_events._on_upgrade_granted(mock_event)

    WorkloadVM.stop.assert_called_once()
    WorkloadVM.install.assert_called_once()
    UpgradeEvents.set_unit_completed.assert_not_called()
    UpgradeEvents.set_unit_failed.assert_called_once()

@pytest.mark.parametrize("harness", [{
    "init_peer_hostname": True,
    "inject_manager_dep_model": True
}], indirect=True)
def test_upgrade_granted_succeeds(harness, mocker):
    opensearch_rel_id = harness.add_relation(OPENSEARCH_REL_NAME, OPENSEARCH_APP_NAME)
    harness.update_relation_data(
        opensearch_rel_id, f"{OPENSEARCH_APP_NAME}", {"version": "2.12.1"}
    )

    mocker.patch.object(WorkloadVM, "stop")
    mocker.patch.object(WorkloadVM, "restart")
    mocker.patch.object(WorkloadVM, "install")
    mocker.patch.object(UpgradeEvents, "pre_upgrade_check")
    mocker.patch.object(UpgradeEvents, "set_unit_completed")
    mocker.patch.object(UpgradeEvents, "set_unit_failed")

    mock_event = mocker.MagicMock()

    harness.charm.upgrade_events._on_upgrade_granted(mock_event)

    WorkloadVM.stop.assert_called_once()
    WorkloadVM.install.assert_called_once()
    WorkloadVM.restart.assert_called_once()
    UpgradeEvents.set_unit_completed.assert_called_once()
    UpgradeEvents.set_unit_failed.assert_not_called()

@pytest.mark.parametrize("harness", [{
    "init_peer_hostname": True,
    "inject_manager_dep_model":  True
}], indirect=True)
def test_upgrade_granted_recurses_upgrade_changed_on_leader(harness, mocker):
    opensearch_rel_id = harness.add_relation(OPENSEARCH_REL_NAME, OPENSEARCH_APP_NAME)
    harness.update_relation_data(
        opensearch_rel_id, f"{OPENSEARCH_APP_NAME}", {"version": "2.12.1"}
    )

    mocker.patch.object(WorkloadVM, "stop")
    mocker.patch.object(WorkloadVM, "restart")
    mocker.patch.object(WorkloadVM, "install")
    mocker.patch.object(UpgradeEvents, "pre_upgrade_check")
    mocker.patch.object(UpgradeEvents, "on_upgrade_changed")

    mock_event = mocker.MagicMock()

    harness.charm.upgrade_events._on_upgrade_granted(mock_event)

    UpgradeEvents.on_upgrade_changed.assert_not_called()

    with harness.hooks_disabled():
        harness.set_leader(True)

    harness.charm.upgrade_events._on_upgrade_granted(mock_event)

    UpgradeEvents.on_upgrade_changed.assert_called_once()
