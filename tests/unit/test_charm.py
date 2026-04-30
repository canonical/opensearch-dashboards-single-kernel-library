#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import logging
import time
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, call, mock_open, patch

import pytest
import responses

from single_kernel_opensearch_dashboards.common.exceptions import OSDInstallError
from single_kernel_opensearch_dashboards.common.literals import (
    CHARM_KEY,
    CLUSTER_MANAGER_NAME,
    HEALTH_MANAGER_NAME,
    INGRESS_MANAGER_NAME,
    OPENSEARCH_REL_NAME,
    UPGRADE_MANAGER_NAME,
)
from single_kernel_opensearch_dashboards.core.statuses import (
    HealthStatuses,
    ServerStatuses,
    UpgradeStatuses,
)
from single_kernel_opensearch_dashboards.lib.charms.data_platform_libs.v1.upgrade import (
    ClusterNotReadyError,
)
from single_kernel_opensearch_dashboards.utils.helpers import (
    update_grafana_dashboards_title,
)

logger = logging.getLogger(__name__)

OPENSEARCH_APP_NAME = "opensearch"


@pytest.fixture
def mocked_dashboards():
    mock_charm = MagicMock()
    mock_charm.model.unit = MagicMock()
    type(mock_charm).charm_dir = PropertyMock(return_value=Path("/fake/charm/dir"))

    yield mock_charm


@pytest.fixture(autouse=True)
def patch_get_charm_revision():
    with patch(
        "single_kernel_opensearch_dashboards.utils.helpers.get_charm_revision", return_value=167
    ) as mock_func:
        yield mock_func


@pytest.fixture(autouse=True)
def patch_test_relation_changed_starts_units():
    with patch(
        "single_kernel_opensearch_dashboards.utils.helpers.update_grafana_dashboards_title"
    ) as mock_func:
        yield mock_func


def set_healthy_opensearch_connection(harness):
    """Set up a functional opensearch mock."""
    opensearch_rel_id = harness.charm.state.opensearch_relation.id
    harness.update_relation_data(
        opensearch_rel_id,
        "opensearch",
        {"endpoints": "111.222.333.444:9200,555.666.777.888:9200"},
    )
    harness.update_relation_data(opensearch_rel_id, "opensearch", {"tls-ca": "<cert_data_here>"})
    harness.update_relation_data(
        opensearch_rel_id, f"{OPENSEARCH_APP_NAME}", {"version": "2.12.1"}
    )

    responses.add(
        method="GET",
        url="https://111.222.333.444:9200/_cluster/health",
        status=200,
        json={"status": "green"},
    )
    return opensearch_rel_id


def test_install_blocks_snap_install_failure(harness):
    with harness.hooks_disabled():
        harness.set_leader(True)

    with (
        patch(
            "single_kernel_opensearch_dashboards.workload.vm.VMWorkload.install",
            side_effect=OSDInstallError("install failed"),
        ),
        pytest.raises(OSDInstallError),
    ):
        assert harness.charm.on.install.emit()


def test_install_sets_ip_hostname_fqdn(harness):
    with harness.hooks_disabled():
        harness.set_leader(True)

    with patch(
        "single_kernel_opensearch_dashboards.workload.vm.VMWorkload.install", return_value=True
    ):
        harness.charm.on.install.emit()
        assert harness.charm.state.bind_address


def test_relation_changed_emitted_for_leader_elected(harness):
    with (
        patch(
            "single_kernel_opensearch_dashboards.charms.base.OpenSearchDashboardsBaseCharm.emit_restart"
        ) as patched,
        patch(
            "single_kernel_opensearch_dashboards.core.models.OSDServer.started", return_value=True
        ),
    ):
        harness.set_leader(True)
        patched.assert_called_once()


def test_relation_changed_emitted_for_config_changed(harness):
    with harness.hooks_disabled():
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, f"{CHARM_KEY}/0", {"state": "started"}
        )

    with patch(
        "single_kernel_opensearch_dashboards.charms.base.OpenSearchDashboardsBaseCharm.emit_restart"
    ) as patched:
        harness.charm.on.config_changed.emit()
        patched.assert_called_once()


def test_relation_changed_emitted_for_relation_changed(harness):
    with patch(
        "single_kernel_opensearch_dashboards.charms.base.OpenSearchDashboardsBaseCharm.emit_restart"
    ) as patched:
        harness.charm.on.dashboard_peers_relation_changed.emit(harness.charm.state.peer_relation)
        patched.assert_called_once()


def test_relation_changed_emitted_for_relation_joined(harness):
    with patch(
        "single_kernel_opensearch_dashboards.charms.base.OpenSearchDashboardsBaseCharm.emit_restart"
    ) as patched:
        harness.charm.on.dashboard_peers_relation_joined.emit(harness.charm.state.peer_relation)
        patched.assert_called_once()


def test_relation_changed_emitted_for_relation_departed(harness):
    with patch(
        "single_kernel_opensearch_dashboards.charms.base.OpenSearchDashboardsBaseCharm.emit_restart"
    ) as patched:
        harness.charm.on.dashboard_peers_relation_departed.emit(harness.charm.state.peer_relation)
        patched.assert_called_once()


def test_config_changed_event_emits_restart(harness):
    with harness.hooks_disabled():
        harness.set_planned_units(1)
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, f"{CHARM_KEY}/0", {"state": "started"}
        )

    with (
        patch(
            "single_kernel_opensearch_dashboards.managers.config.ConfigManager.config_changed",
            return_value=True,
        ),
        patch(
            "single_kernel_opensearch_dashboards.core.cluster.ClusterState.all_units_related",
            return_value=True,
        ),
        patch(
            "single_kernel_opensearch_dashboards.lib.charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock"
        ) as patched,
    ):

        harness.charm.on.config_changed.emit()
        patched.assert_called_once()


def test_restart_initializes_unstarted_server(harness):
    with harness.hooks_disabled():
        harness.set_planned_units(1)

    handler = harness.charm

    mock_event = MagicMock()
    mock_event.framework.model.unit.name = "opensearch-dashboards/0"

    with (
        patch(
            "single_kernel_opensearch_dashboards.managers.config.ConfigManager.set_dashboard_properties"
        ) as mock_set_props,
        patch(
            "single_kernel_opensearch_dashboards.managers.cluster.ClusterManager.init_server"
        ) as mock_init_server,
        patch("single_kernel_opensearch_dashboards.managers.tls.TLSManager.write_tls_files"),
        patch(
            "single_kernel_opensearch_dashboards.charms.base.OpenSearchDashboardsBaseCharm._check_osd_status"
        ) as mock_check_status,
    ):
        handler.restart(mock_event)

        mock_set_props.assert_called_once()
        mock_init_server.assert_called_once()
        mock_check_status.assert_called_once()

        assert handler.state.unit_server.started is True


@pytest.mark.parametrize("harness", [{"add_opensearch": True}], indirect=True)
def test_relation_changed_emitted_for_opensearch_relation_changed(harness):
    with harness.hooks_disabled():
        opensearch_rel_id = harness.add_relation(OPENSEARCH_REL_NAME, "opensearch")
        harness.add_relation_unit(opensearch_rel_id, "opensearch/0")

    with patch(
        "single_kernel_opensearch_dashboards.events.opensearch_requirer.RequirerEvents._on_client_relation_changed"
    ) as patched:
        harness.update_relation_data(opensearch_rel_id, "opensearch", {"data": "{}"})
        patched.assert_called_once()


def test_relation_changed_does_not_start_units_again(harness):
    harness.update_relation_data(
        harness.charm.state.peer_relation.id, f"{CHARM_KEY}/0", {"state": "started"}
    )

    with (
        patch(
            "single_kernel_opensearch_dashboards.managers.cluster.ClusterManager.init_server"
        ) as patched,
        patch("single_kernel_opensearch_dashboards.managers.config.ConfigManager.config_changed"),
        patch(
            "single_kernel_opensearch_dashboards.managers.config.ConfigManager.set_dashboard_properties"
        ),
    ):
        harness.charm.on.config_changed.emit()
        patched.assert_not_called()


def test_relation_changed_does_not_restart_on_departing(harness):
    with (
        patch(
            "single_kernel_opensearch_dashboards.lib.charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock"
        ) as patched,
    ):
        harness.remove_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/0")
        patched.assert_not_called()


def test_relation_changed_restarts(harness):
    with harness.hooks_disabled():
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, f"{CHARM_KEY}/0", {"state": "started"}
        )

    with (
        patch(
            "single_kernel_opensearch_dashboards.lib.charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock"
        ) as patched_restart,
        patch(
            "single_kernel_opensearch_dashboards.managers.config.ConfigManager.config_changed",
            return_value=True,
        ),
        patch(
            "single_kernel_opensearch_dashboards.managers.config.ConfigManager.set_dashboard_properties",
            return_value=True,
        ),
        patch(
            "single_kernel_opensearch_dashboards.core.cluster.ClusterState.all_units_related",
            return_value=True,
        ),
    ):
        harness.charm.on.config_changed.emit()
        patched_restart.assert_called_once()


def test_restart_fails_not_started(harness):
    with harness.hooks_disabled():
        harness.set_planned_units(1)

    mock_event = MagicMock()
    mock_event.framework.model.unit.name = "unit/0"

    with (
        patch(
            "single_kernel_opensearch_dashboards.workload.vm.VMWorkload.restart"
        ) as patched_restart,
        patch("single_kernel_opensearch_dashboards.workload.vm.VMWorkload.start") as patched_start,
        patch(
            "single_kernel_opensearch_dashboards.managers.config.ConfigManager.set_dashboard_properties"
        ),
        patch(
            "single_kernel_opensearch_dashboards.managers.config.ConfigManager.load_dashboard_properties"
        ),
        patch("single_kernel_opensearch_dashboards.managers.tls.TLSManager.write_tls_files"),
    ):
        harness.charm.restart(mock_event)
        patched_restart.assert_not_called()
        patched_start.assert_called_once()


@pytest.mark.parametrize("harness", [{"add_opensearch": True}], indirect=True)
@responses.activate
def test_restart_sleep_no_wait_once_service_up(harness):
    """We are giving a "grace period" for the service to establish after a restart.

    Reason: to avoid unhealthy charm state set by 'update-status' premature run.
    """

    # Let's assume that the service has started already, and has a healthy DB connection
    with harness.hooks_disabled():
        harness.set_planned_units(1)
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, f"{CHARM_KEY}/0", {"state": "started"}
        )

    expected_response = {
        "status": {
            "overall": {
                "state": "green",
            },
        }
    }

    responses.add(
        method="GET",
        url=f"{harness.charm.state.url}/api/status",
        status=200,
        json=expected_response,
    )

    mock_event = MagicMock()
    mock_event.framework.model.unit.name = "unit/0"

    # Let's assume that we don't need to wait for workload to come up
    # to reduce the scope of the test to the service availability delay
    with (
        patch(
            "single_kernel_opensearch_dashboards.workload.vm.VMWorkload.alive", return_value=True
        ),
        patch(
            "single_kernel_opensearch_dashboards.workload.vm.VMWorkload.restart"
        ) as patched_restart,
        patch(
            "single_kernel_opensearch_dashboards.managers.config.ConfigManager.set_dashboard_properties"
        ),
        patch("time.sleep") as patched_sleep,
    ):
        harness.charm.restart(mock_event)
        patched_restart.assert_called_once()

        # sleep() was only called to allow the service to establish
        assert patched_sleep.call_count == 0


@pytest.mark.parametrize("harness", [{"add_opensearch": True}], indirect=True)
@responses.activate
def test_restart_sleep_with_timeout_if_service_down(harness):
    """We are giving a "grace period" for the service to establish after a restart.

    Reason: to avoid unhealthy charm state set by 'update-status' premature run.
    """

    # Let's assume that the service has started already, and has a healthy DB connection
    with harness.hooks_disabled():
        harness.set_planned_units(1)
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, f"{CHARM_KEY}/0", {"state": "started"}
        )

    expected_response = {
        "status": {
            "overall": {
                "state": "red",
            },
        }
    }

    responses.add(
        method="GET",
        url=f"{harness.charm.state.url}/api/status",
        status=200,
        json=expected_response,
    )

    mock_event = MagicMock()
    mock_event.framework.model.unit.name = "unit/0"

    # Let's assume that we don't need to wait for workload to come up
    # to reduce the scope of the test to the service availability delay
    # Also decreasing timeout for faster run
    patched_timeout = 5
    with (
        patch(
            "single_kernel_opensearch_dashboards.workload.vm.VMWorkload.alive", return_value=False
        ),
        patch(
            "single_kernel_opensearch_dashboards.managers.health.SERVICE_AVAILABLE_TIMEOUT",
            patched_timeout,
        ),
        patch(
            "single_kernel_opensearch_dashboards.managers.cluster.RESTART_TIMEOUT",
            patched_timeout,
        ),
        patch(
            "single_kernel_opensearch_dashboards.workload.vm.VMWorkload.restart"
        ) as patched_restart,
        patch(
            "single_kernel_opensearch_dashboards.managers.config.ConfigManager.set_dashboard_properties"
        ),
        patch("time.sleep") as patched_sleep,
        patch("single_kernel_opensearch_dashboards.managers.tls.TLSManager.write_tls_files"),
    ):
        start_time = time.time()
        harness.charm.restart(mock_event)
        end_time = time.time()
        patched_restart.assert_called_once()

        assert patched_sleep.call_count > 2
        assert end_time - start_time >= patched_timeout


def test_restart_restarts_with_sleep(harness):
    with harness.hooks_disabled():
        harness.set_planned_units(1)
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, f"{CHARM_KEY}/0", {"state": "started"}
        )
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, f"{CHARM_KEY}", {"0": "added"}
        )
    mock_event = MagicMock()
    mock_event.framework.model.unit.name = "unit/0"

    with (
        # Harmlessly decreasing timeouts for faster test run
        patch("single_kernel_opensearch_dashboards.managers.cluster.RESTART_TIMEOUT", 3),
        patch("single_kernel_opensearch_dashboards.managers.health.SERVICE_AVAILABLE_TIMEOUT", 3),
        patch(
            "single_kernel_opensearch_dashboards.workload.vm.VMWorkload.restart"
        ) as patched_restart,
        patch(
            "single_kernel_opensearch_dashboards.managers.config.ConfigManager.set_dashboard_properties"
        ),
        patch(
            "single_kernel_opensearch_dashboards.workload.vm.VMWorkload.alive", return_value=False
        ),
        patch("time.sleep") as patched_sleep,
    ):
        harness.charm.restart(mock_event)
        patched_restart.assert_called_once()
        assert patched_sleep.call_count >= 1


def test_init_server_calls_necessary_methods_non_leader(harness):
    with harness.hooks_disabled():
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, f"{CHARM_KEY}", {"monitor-password": "bla"}
        )
    mock_event = MagicMock()
    mock_event.framework.model.unit.name = "unit/0"

    with (
        patch(
            "single_kernel_opensearch_dashboards.managers.config.ConfigManager.set_dashboard_properties"
        ) as dashboard_properties,
        patch(
            "single_kernel_opensearch_dashboards.managers.health.HealthManager.check_osd_health"
        ) as check_health,
        patch(
            "single_kernel_opensearch_dashboards.managers.cluster.ClusterManager.init_server"
        ) as init_server,
        patch(
            "single_kernel_opensearch_dashboards.charms.base.StatusHandler.set_running_status"
        ) as running,
        patch("single_kernel_opensearch_dashboards.core.cluster.StatusesState.add") as add,
        patch("single_kernel_opensearch_dashboards.managers.tls.TLSManager.write_tls_files"),
    ):
        harness.charm.restart(mock_event)
        running.assert_called_with(
            status=HealthStatuses.AFTER_RESTART.value,
            component_name=HEALTH_MANAGER_NAME,
            scope="unit",
        )
        add.assert_called_with(
            status=ServerStatuses.DB_CONNECTION_MISSING.value,
            scope="unit",
            component=CLUSTER_MANAGER_NAME,
        )
        check_health.assert_called_once()
        dashboard_properties.assert_called_once()
        init_server.assert_called_once()

        assert harness.charm.state.unit_server.started


def test_init_server_calls_necessary_methods_leader(harness):
    with harness.hooks_disabled():
        harness.set_leader(True)
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, f"{CHARM_KEY}", {"monitor-password": "bla"}
        )

    mock_event = MagicMock()
    mock_event.framework.model.unit.name = "unit/0"

    with (
        patch(
            "single_kernel_opensearch_dashboards.managers.config.ConfigManager.set_dashboard_properties"
        ) as dashboard_properties,
        patch(
            "single_kernel_opensearch_dashboards.managers.health.HealthManager.check_osd_health"
        ) as check_health,
        patch(
            "single_kernel_opensearch_dashboards.managers.cluster.ClusterManager.init_server"
        ) as init_server,
        patch(
            "single_kernel_opensearch_dashboards.charms.base.StatusHandler.set_running_status"
        ) as running,
        patch("single_kernel_opensearch_dashboards.core.cluster.StatusesState.add") as add,
        patch("single_kernel_opensearch_dashboards.managers.tls.TLSManager.write_tls_files"),
    ):
        harness.charm.restart(mock_event)

        check_health.assert_called_once()
        dashboard_properties.assert_called_once()
        init_server.assert_called_once()
        expected_calls = [
            call(
                status=ServerStatuses.DB_CONNECTION_MISSING.value,
                scope="unit",
                component=CLUSTER_MANAGER_NAME,
            ),
            call(
                status=ServerStatuses.DB_CONNECTION_MISSING.value,
                scope="app",
                component=CLUSTER_MANAGER_NAME,
            ),
        ]
        running.assert_called_with(
            status=HealthStatuses.AFTER_RESTART.value,
            component_name=HEALTH_MANAGER_NAME,
            scope="unit",
        )
        add.assert_has_calls(expected_calls, any_order=False)
        assert harness.charm.state.unit_server.started


def test_config_changed_applies_relation_data(harness):
    with harness.hooks_disabled():
        harness.set_leader(True)
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, f"{CHARM_KEY}/0", {"state": "started"}
        )

    with (
        patch(
            "single_kernel_opensearch_dashboards.workload.vm.VMWorkload.alive", return_value=True
        ),
        patch(
            "single_kernel_opensearch_dashboards.managers.config.ConfigManager.config_changed"
        ) as patched,
        patch(
            "single_kernel_opensearch_dashboards.core.cluster.ClusterState.stable",
            return_value=True,
        ),
        patch(
            "single_kernel_opensearch_dashboards.core.cluster.ClusterState.all_units_related",
            return_value=True,
        ),
        patch(
            "single_kernel_opensearch_dashboards.managers.config.ConfigManager.set_dashboard_properties"
        ),
        patch("single_kernel_opensearch_dashboards.workload.vm.VMWorkload.start"),
        patch(
            "single_kernel_opensearch_dashboards.lib.charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock"
        ),
    ):
        harness.charm.on.config_changed.emit()

        patched.assert_called_once()


# Setting the correct status
def test_workload_down_blocked_status(harness):
    with harness.hooks_disabled():
        harness.set_leader(True)

    with (
        # Harmlessly decreasing timeouts for faster test run
        patch("single_kernel_opensearch_dashboards.managers.cluster.RESTART_TIMEOUT", 3),
        patch("single_kernel_opensearch_dashboards.managers.health.SERVICE_AVAILABLE_TIMEOUT", 3),
        patch(
            "single_kernel_opensearch_dashboards.workload.vm.VMWorkload.alive", return_value=False
        ),
        patch(
            "single_kernel_opensearch_dashboards.workload.vm.VMWorkload.start", return_value=True
        ),
        patch(
            "single_kernel_opensearch_dashboards.workload.vm.VMWorkload.restart",
            return_value=False,
        ),
        patch(
            "single_kernel_opensearch_dashboards.managers.config.ConfigManager.config_changed",
            return_value=False,
        ),
        patch(
            "single_kernel_opensearch_dashboards.managers.config.ConfigManager.set_dashboard_properties"
        ),
        patch(
            "single_kernel_opensearch_dashboards.events.opensearch_dashboards.update_grafana_dashboards_title"
        ),
        patch("single_kernel_opensearch_dashboards.charms.base.StatusHandler.set_running_status"),
        patch("single_kernel_opensearch_dashboards.core.cluster.StatusesState.add") as add,
        patch("single_kernel_opensearch_dashboards.managers.tls.TLSManager.write_tls_files"),
    ):
        mock_event = MagicMock()
        mock_event.framework.model.unit.name = "unit/0"
        harness.charm.restart(mock_event)
        expected_calls = [
            call(
                status=HealthStatuses.WORKLOAD_IS_DOWN.value,
                scope="unit",
                component=HEALTH_MANAGER_NAME,
            ),
            call(
                status=HealthStatuses.WORKLOAD_IS_DOWN.value,
                scope="app",
                component=HEALTH_MANAGER_NAME,
            ),
        ]
        add.assert_has_calls(expected_calls, any_order=False)


@pytest.mark.parametrize("harness", [{"add_opensearch": True}], indirect=True)
@responses.activate
def test_service_unavailable_blocked_status(harness):
    responses.add(
        method="GET",
        url=f"{harness.charm.state.url}/api/status",
        status=503,
        body="OpenSearch Dashboards server is not ready yet",
    )

    with harness.hooks_disabled():
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, f"{CHARM_KEY}", {"monitor-password": "bla"}
        )
        harness.set_leader(True)
    with (
        patch(
            "single_kernel_opensearch_dashboards.workload.vm.VMWorkload.alive", return_value=True
        ),
        patch(
            "single_kernel_opensearch_dashboards.workload.vm.VMWorkload.start", return_value=True
        ),
        patch(
            "single_kernel_opensearch_dashboards.managers.config.ConfigManager.config_changed",
            return_value=False,
        ),
        patch(
            "single_kernel_opensearch_dashboards.managers.config.ConfigManager.set_dashboard_properties"
        ),
        patch("single_kernel_opensearch_dashboards.managers.health.SERVICE_AVAILABLE_TIMEOUT", 3),
        patch("single_kernel_opensearch_dashboards.charms.base.StatusHandler.set_running_status"),
        patch("single_kernel_opensearch_dashboards.core.cluster.StatusesState.add") as add,
        patch(
            "single_kernel_opensearch_dashboards.core.models.OpensearchServer.password",
            return_value="1",
        ),
        patch("single_kernel_opensearch_dashboards.managers.tls.TLSManager.write_tls_files"),
    ):
        mock_event = MagicMock()
        mock_event.framework.model.unit.name = "unit/0"
        harness.charm.restart(mock_event)
        expected_calls = [
            call(
                status=HealthStatuses.STATUS_UNAVAILABLE.value,
                scope="unit",
                component=HEALTH_MANAGER_NAME,
            ),
        ]
        add.assert_has_calls(expected_calls, any_order=False)


@pytest.mark.parametrize("harness", [{"add_opensearch": True}], indirect=True)
@responses.activate
def test_service_unhealthy(harness):
    expected_response = {
        "status": {
            "overall": {
                "state": "yellow",
            },
        }
    }

    responses.add(
        method="GET",
        url=f"{harness.charm.state.url}/api/status",
        status=200,
        json=expected_response,
    )

    with harness.hooks_disabled():
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, f"{CHARM_KEY}", {"monitor-password": "bla"}
        )
        harness.set_leader(True)
        set_healthy_opensearch_connection(harness)

    opensearch_ca = MagicMock()
    opensearch_ca.read_text.return_value = "1"
    opensearch_ca.exist.return_value = True
    with (
        patch(
            "single_kernel_opensearch_dashboards.workload.base.Paths.opensearch_ca",
            new_callable=PropertyMock,
            return_value=opensearch_ca,
        ),
        patch(
            "single_kernel_opensearch_dashboards.workload.vm.VMWorkload.alive", return_value=True
        ),
        patch(
            "single_kernel_opensearch_dashboards.workload.vm.VMWorkload.start", return_value=True
        ),
        patch(
            "single_kernel_opensearch_dashboards.managers.config.ConfigManager.config_changed",
            return_value=False,
        ),
        patch(
            "single_kernel_opensearch_dashboards.managers.config.ConfigManager.set_dashboard_properties"
        ),
        patch(
            "single_kernel_opensearch_dashboards.core.models.OSDServer.hostname",
            new_callable=PropertyMock,
            return_value="opensearch-dashboards",
        ),
        patch(
            "single_kernel_opensearch_dashboards.core.models.OSDServer.fqdn",
            new_callable=PropertyMock,
            return_value="opensearch-dashboards",
        ),
        patch(
            "single_kernel_opensearch_dashboards.managers.base.BaseManager.request_opensearch_dashboards",
            return_value=(
                200,
                {
                    "status": {
                        "overall": {
                            "state": "yellow",
                        },
                    },
                },
            ),
        ),
        patch("single_kernel_opensearch_dashboards.managers.health.SERVICE_AVAILABLE_TIMEOUT", 3),
        patch("single_kernel_opensearch_dashboards.charms.base.StatusHandler.set_running_status"),
        patch("single_kernel_opensearch_dashboards.core.cluster.StatusesState.add") as add,
        patch(
            "single_kernel_opensearch_dashboards.core.models.OpensearchServer.password",
            return_value="1",
        ),
        patch("single_kernel_opensearch_dashboards.managers.tls.TLSManager.write_tls_files"),
    ):
        mock_event = MagicMock()
        mock_event.framework.model.unit.name = "unit/0"
        harness.charm.restart(mock_event)
        expected_calls = [
            call(
                status=HealthStatuses.STATUS_UNHEALTHY.value,
                scope="unit",
                component=HEALTH_MANAGER_NAME,
            ),
        ]
        add.assert_has_calls(expected_calls, any_order=False)


@pytest.mark.parametrize("harness", [{"add_opensearch": True}], indirect=True)
@responses.activate
def test_service_error(harness):
    expected_response = {
        "status": {
            "overall": {
                "state": "red",
            },
        }
    }

    responses.add(
        method="GET",
        url=f"{harness.charm.state.url}/api/status",
        status=200,
        json=expected_response,
    )

    with harness.hooks_disabled():
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, f"{CHARM_KEY}", {"monitor-password": "bla"}
        )
        harness.set_leader(True)
        set_healthy_opensearch_connection(harness)

    opensearch_ca = MagicMock()
    opensearch_ca.read_text.return_value = "1"
    opensearch_ca.exist.return_value = True
    with (
        patch(
            "single_kernel_opensearch_dashboards.workload.vm.VMWorkload.alive", return_value=True
        ),
        patch(
            "single_kernel_opensearch_dashboards.workload.vm.VMWorkload.start", return_value=True
        ),
        patch(
            "single_kernel_opensearch_dashboards.managers.config.ConfigManager.config_changed",
            return_value=False,
        ),
        patch(
            "single_kernel_opensearch_dashboards.managers.config.ConfigManager.set_dashboard_properties"
        ),
        patch(
            "single_kernel_opensearch_dashboards.workload.base.Paths.opensearch_ca",
            new_callable=PropertyMock,
            return_value=opensearch_ca,
        ),
        patch(
            "single_kernel_opensearch_dashboards.core.models.OSDServer.hostname",
            new_callable=PropertyMock,
            return_value="opensearch-dashboards",
        ),
        patch(
            "single_kernel_opensearch_dashboards.core.models.OSDServer.fqdn",
            new_callable=PropertyMock,
            return_value="opensearch-dashboards",
        ),
        patch(
            "single_kernel_opensearch_dashboards.managers.base.BaseManager.request_opensearch_dashboards",
            return_value=(
                200,
                {
                    "status": {
                        "overall": {
                            "state": "red",
                        },
                    }
                },
            ),
        ),
        patch("single_kernel_opensearch_dashboards.managers.health.SERVICE_AVAILABLE_TIMEOUT", 3),
        patch("single_kernel_opensearch_dashboards.charms.base.StatusHandler.set_running_status"),
        patch("single_kernel_opensearch_dashboards.core.cluster.StatusesState.add") as add,
        patch(
            "single_kernel_opensearch_dashboards.core.models.OpensearchServer.password",
            return_value="1",
        ),
        patch("single_kernel_opensearch_dashboards.managers.tls.TLSManager.write_tls_files"),
    ):
        mock_event = MagicMock()
        mock_event.framework.model.unit.name = "unit/0"
        harness.charm.restart(mock_event)
        expected_calls = [
            call(
                status=HealthStatuses.STATUS_ERROR.value,
                scope="unit",
                component=HEALTH_MANAGER_NAME,
            ),
        ]
        add.assert_has_calls(expected_calls, any_order=False)


@pytest.mark.parametrize("harness", [{"add_opensearch": True}], indirect=True)
@responses.activate
def test_service_available(harness):
    expected_response = {
        "status": {
            "overall": {
                "state": "green",
            },
        }
    }

    responses.add(
        method="GET",
        url=f"{harness.charm.state.url}/api/status",
        status=200,
        json=expected_response,
    )

    with harness.hooks_disabled():
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, f"{CHARM_KEY}", {"monitor-password": "bla"}
        )
        harness.set_leader(True)
        set_healthy_opensearch_connection(harness)

    opensearch_ca = MagicMock()
    opensearch_ca.read_text.return_value = "1"
    opensearch_ca.exist.return_value = True
    with (
        patch(
            "single_kernel_opensearch_dashboards.workload.vm.VMWorkload.alive", return_value=True
        ),
        patch(
            "single_kernel_opensearch_dashboards.workload.vm.VMWorkload.start", return_value=True
        ),
        patch(
            "single_kernel_opensearch_dashboards.managers.config.ConfigManager.config_changed",
            return_value=False,
        ),
        patch(
            "single_kernel_opensearch_dashboards.managers.config.ConfigManager.set_dashboard_properties"
        ),
        patch("single_kernel_opensearch_dashboards.workload.base.Paths.opensearch_ca"),
        patch(
            "single_kernel_opensearch_dashboards.core.models.OSDServer.hostname",
            new_callable=PropertyMock,
            return_value="opensearch-dashboards",
        ),
        patch(
            "single_kernel_opensearch_dashboards.core.models.OSDServer.fqdn",
            new_callable=PropertyMock,
            return_value="opensearch-dashboards",
        ),
        patch(
            "single_kernel_opensearch_dashboards.managers.base.BaseManager.request_opensearch_dashboards",
            return_value=(
                200,
                {
                    "status": {
                        "overall": {
                            "state": "green",
                        },
                    },
                },
            ),
        ),
        patch("single_kernel_opensearch_dashboards.managers.health.SERVICE_AVAILABLE_TIMEOUT", 3),
        patch("single_kernel_opensearch_dashboards.charms.base.StatusHandler.set_running_status"),
        patch("single_kernel_opensearch_dashboards.core.cluster.StatusesState.add") as add,
        patch(
            "single_kernel_opensearch_dashboards.core.models.OpensearchServer.password",
            return_value="1",
        ),
        patch("single_kernel_opensearch_dashboards.managers.tls.TLSManager.write_tls_files"),
    ):
        mock_event = MagicMock()
        mock_event.framework.model.unit.name = "unit/0"
        harness.charm.restart(mock_event)
        add.assert_not_called()


@pytest.mark.parametrize("harness", [{"add_opensearch": True}], indirect=True)
@responses.activate
def test_wrong_opensearch_version(harness):
    expected_response = {
        "status": {
            "overall": {
                "state": "green",
            },
        }
    }

    responses.add(
        method="GET",
        url=f"{harness.charm.state.url}/api/status",
        status=200,
        json=expected_response,
    )

    with harness.hooks_disabled():
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, f"{CHARM_KEY}", {"monitor-password": "bla"}
        )
        harness.set_leader(True)
        set_healthy_opensearch_connection(harness)
        harness.update_relation_data(
            harness.charm.state.opensearch_relation.id,
            f"{OPENSEARCH_APP_NAME}",
            {"version": "20.12.1"},
        )

    with (
        patch(
            "single_kernel_opensearch_dashboards.workload.vm.VMWorkload.alive", return_value=True
        ),
        patch(
            "single_kernel_opensearch_dashboards.workload.vm.VMWorkload.start", return_value=True
        ),
        patch(
            "single_kernel_opensearch_dashboards.managers.config.ConfigManager.config_changed",
            return_value=False,
        ),
        patch(
            "single_kernel_opensearch_dashboards.managers.config.ConfigManager.set_dashboard_properties"
        ),
        patch("single_kernel_opensearch_dashboards.managers.health.SERVICE_AVAILABLE_TIMEOUT", 3),
        patch("single_kernel_opensearch_dashboards.charms.base.StatusHandler.set_running_status"),
        patch("single_kernel_opensearch_dashboards.core.cluster.StatusesState.add") as add,
    ):
        with pytest.raises(ClusterNotReadyError):
            harness.charm.upgrade_events.post_upgrade_check()

        expected_calls = [
            call(
                status=UpgradeStatuses.DB_INCOMPATIBLE_VERSION.value,
                scope="unit",
                component=UPGRADE_MANAGER_NAME,
            ),
        ]
        add.assert_has_calls(expected_calls, any_order=False)


@patch(
    "builtins.open",
    new_callable=mock_open,
    read_data=json.dumps({"title": "Charmed OpenSearch Dashboards"}),
)
@patch("json.dump")
def test_update_grafana_dashboards_title_no_prior_revision(
    mock_json_dump, mock_open_func, mocked_dashboards
):

    update_grafana_dashboards_title(mocked_dashboards)

    expected_updated_dashboard = {"title": "Charmed OpenSearch Dashboards - Rev 167"}
    mock_json_dump.assert_called_once_with(expected_updated_dashboard, mock_open_func(), indent=4)


@patch(
    "builtins.open",
    new_callable=mock_open,
    read_data=json.dumps({"title": "Charmed OpenSearch - Rev 166"}),
)
@patch("json.dump")
def test_update_grafana_dashboards_title_prior_revision(
    mock_json_dump, mock_open_func, mocked_dashboards
):
    update_grafana_dashboards_title(mocked_dashboards)

    expected_updated_dashboard = {"title": "Charmed OpenSearch - Rev 167"}
    mock_json_dump.assert_called_once_with(expected_updated_dashboard, mock_open_func(), indent=4)


# def test_port_updates_if_tls(harness):
#     with harness.hooks_disabled():
#         harness.add_relation(PEER, CHARM_KEY)
#         app_id = harness.add_relation(REL_NAME, "application")
#         harness.set_leader(True)
#         harness.update_relation_data(app_id, "application", {"chroot": "app"})
#
#         # checking if ssl port and ssl flag are passed
#         harness.update_relation_data(
#             harness.charm.state.peer_relation.id,
#             f"{CHARM_KEY}/0",
#             {"private-address": "treebeard", "state": "started"},
#         )
#         harness.update_relation_data(
#             harness.charm.state.peer_relation.id,
#             CHARM_KEY,
#             {"quorum": "ssl", "relation-0": "mellon", "tls": "enabled"},
#         )
#         harness.charm.update_client_data()
#
#     uris = ""
#
#     for client in harness.charm.state.clients:
#         assert client.tls
#         uris = client.uris
#
#     with harness.hooks_disabled():
#         # checking if normal port and non-ssl flag are passed
#         harness.update_relation_data(
#             harness.charm.state.peer_relation.id,
#             f"{CHARM_KEY}/0",
#             {"private-address": "treebeard", "state": "started", "quorum": "non-ssl"},
#         )
#         harness.update_relation_data(
#             harness.charm.state.peer_relation.id,
#             CHARM_KEY,
#             {"quorum": "non-ssl", "relation-0": "mellon", "tls": ""},
#         )
#         harness.charm.update_client_data()
#
#     for client in harness.charm.state.clients:
#         assert not client.tls
#         assert client.uris != uris
#
#
# def test_update_relation_data(harness):
#     with harness.hooks_disabled():
#         harness.add_relation(PEER, CHARM_KEY)
#         harness.set_leader(True)
#         app_1_id = harness.add_relation(REL_NAME, "application")
#         app_2_id = harness.add_relation(REL_NAME, "new_application")
#         harness.update_relation_data(
#             app_1_id,
#             "application",
#             {"chroot": "app", "requested-secrets": json.dumps(["username", "password"])},
#         )
#         harness.update_relation_data(
#             app_2_id,
#             "new_application",
#             {
#                 "chroot": "new_app",
#                 "chroot-acl": "rw",
#                 "requested-secrets": json.dumps(["username", "password"]),
#             },
#         )
#         harness.update_relation_data(
#             harness.charm.state.peer_relation.id,
#             f"{CHARM_KEY}/0",
#             {
#                 "ip": "treebeard",
#                 "state": "started",
#                 "private-address": "glamdring",
#                 "hostname": "frodo",
#             },
#         )
#         harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/1")
#         harness.update_relation_data(
#             harness.charm.state.peer_relation.id,
#             f"{CHARM_KEY}/1",
#             {"ip": "shelob", "state": "ready", "private-address": "narsil", "hostname": "sam"},
#         )
#         harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/2")
#         harness.update_relation_data(
#             harness.charm.state.peer_relation.id,
#             f"{CHARM_KEY}/2",
#             {
#                 "ip": "balrog",
#                 "state": "started",
#                 "private-address": "anduril",
#                 "hostname": "merry",
#             },
#         )
#         harness.charm.peer_app_interface.update_relation_data(
#             harness.charm.state.peer_relation.id,
#             {f"relation-{app_1_id}": "mellon", f"relation-{app_2_id}": "friend"},
#         )
#
#     with (
#         patch("core.cluster.ClusterState.ready", new_callable=PropertyMock, return_value=True),
#     ):
#         harness.charm.update_client_data()
#
#     # building bare clients for validation
#     usernames = []
#     passwords = []
#
#     for relation in harness.charm.state.client_relations:
#         myclient = None
#         for client in harness.charm.state.clients:
#             if client.relation == relation:
#                 myclient = client
#         client = ODClient(
#             relation=relation,
#             data_interface=harness.charm.client_provider_interface,
#             substrate=SUBSTRATE,
#             component=relation.app,
#             local_app=harness.charm.app,
#             password=myclient.relation_data.get("password", ""),
#             endpoints=myclient.relation_data.get("endpoints", ""),
#             uris=myclient.relation_data.get("uris", ""),
#             tls=myclient.relation_data.get("tls", ""),
#         )
#
#         assert client.username, (
#             client.password in harness.charm.state.cluster.client_passwords.items()
#         )
#         assert client.username not in usernames
#         assert client.password not in passwords
#
#         logger.info(client.endpoints)
#
#         assert len(client.endpoints.split(",")) == 3
#         assert len(client.uris.split(",")) == 3, client.uris
#
#         if SUBSTRATE == "vm":
#             # checking ips are used
#             for ip in ["treebeard", "shelob", "balrog"]:
#                 assert ip in client.endpoints
#                 assert ip in client.uris
#
#             # checking private-address or hostnames are NOT used
#             for hostname_address in ["glamdring", "narsil", "anduril", "sam", "frodo", "merry"]:
#                 assert hostname_address not in client.endpoints
#                 assert hostname_address not in client.uris
#
#         if SUBSTRATE == "k8s":
#             assert "endpoints" in client.endpoints
#             assert "endpoints" in client.uris
#
#         for uri in client.uris.split(","):
#             # checking client_port in uri
#             assert re.search(r":[\d]+", uri)
#
#         assert client.uris.endswith(client.chroot)
#
#         usernames.append(client.username)
#         passwords.append(client.password)
