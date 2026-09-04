# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

from pathlib import Path
from unittest.mock import MagicMock, PropertyMock

import pytest
import yaml
from ops import JujuVersion
from ops.testing import Harness

from single_kernel_opensearch_dashboards.common.literals import (
    CHARM_KEY,
    OPENSEARCH_REL_NAME,
    PEERS_REL_NAME,
)
from single_kernel_opensearch_dashboards.managers.upgrade import (
    OpensearchDashboardsDependencyModel,
)
from tests.charms.dashboards_vm_charm.src.charm import (
    OpenSearchDashboardsVMCharm as TestCharm,
)

CONFIG = str(yaml.safe_load(Path("tests/charms/dashboards_vm_charm/config.yaml").read_text()))
ACTIONS = str(yaml.safe_load(Path("tests/charms/dashboards_vm_charm/actions.yaml").read_text()))
METADATA = str(yaml.safe_load(Path("tests/charms/dashboards_vm_charm/metadata.yaml").read_text()))

CONFIG_K8s = str(yaml.safe_load(Path("tests/charms/dashboards_k8s_charm/config.yaml").read_text()))
ACTIONS_K8s = str(
    yaml.safe_load(Path("tests/charms/dashboards_k8s_charm/actions.yaml").read_text())
)
METADATA_K8s = str(
    yaml.safe_load(Path("tests/charms/dashboards_k8s_charm/metadata.yaml").read_text())
)


@pytest.fixture(autouse=True)
def patched_wait(mocker):
    mocker.patch("tenacity.nap.time")


@pytest.fixture(autouse=True)
def patched_pebble_restart(mocker):
    mocker.patch("ops.model.Container.restart")


@pytest.fixture(autouse=True)
def patched_snap(mocker):
    mocker.patch(
        "single_kernel_opensearch_dashboards.workload.vm.VMWorkload.dashboards",
        new_callable=PropertyMock,
        return_value=MagicMock(),
    )


@pytest.fixture(autouse=True)
def patched_healthy(mocker):
    mocker.patch(
        "single_kernel_opensearch_dashboards.workload.vm.VMWorkload.healthy",
        return_value=True,
    )


@pytest.fixture(autouse=True)
def patched_ready(mocker):
    """`ready()` checks the on-disk config dir, which does not exist under the harness."""
    mocker.patch(
        "single_kernel_opensearch_dashboards.workload.vm.VMWorkload.ready",
        return_value=True,
    )


@pytest.fixture(autouse=True)
def patched_workload_version(mocker):
    """The charm reads the workload_version file from its own directory at runtime;
    unit tests run from the repo root where that file does not exist."""
    mocker.patch(
        "single_kernel_opensearch_dashboards.charms.base.Path"
    ).return_value.read_text.return_value = "2.19.4"


@pytest.fixture(autouse=True)
def juju_has_secrets(mocker):
    """Using Juju3 we should always have secrets available."""
    mocker.patch.object(JujuVersion, "has_secrets", new_callable=PropertyMock).return_value = True


@pytest.fixture
def harness(request):
    """
    A unified harness fixture that can be parameterized for different test scenarios.

    Usage:
    @pytest.mark.parametrize("harness", [{"log_level": "debug", "add_opensearch": True}], indirect=True)
    def test_something(harness):
        ...
    """

    options = {
        "log_level": "INFO",
        "add_peer": True,  # Most tests seem to want a peer relation
        "add_upgrade": True,  # Add the upgrade relation
        "upgrade_state": "idle",  # Set upgrade state to idle
        "add_opensearch": False,  # specific to api/health tests
        "opensearch_data": None,  # Dict of data to push to opensearch relation
        "mock_network": False,  # specific to test_api
        "inject_events_dep_model": False,  # For test_charm (upgrade_events)
        "inject_manager_dep_model": False,  # For test_upgrade (upgrade_manager)
        "init_peer_hostname": False,  # specific to test_upgrade
    }

    if hasattr(request, "param"):
        options.update(request.param)

    harness = Harness(TestCharm, meta=METADATA, config=CONFIG, actions=ACTIONS)

    harness.add_relation("restart", CHARM_KEY)

    if options["add_peer"]:
        peer_rel_id = harness.add_relation(PEERS_REL_NAME, CHARM_KEY)
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")

    if options["add_upgrade"]:
        upgrade_rel_id = harness.add_relation("upgrade", CHARM_KEY)
        if options["upgrade_state"]:
            harness.update_relation_data(
                upgrade_rel_id, f"{CHARM_KEY}/0", {"state": options["upgrade_state"]}
            )

    if options["add_opensearch"]:
        opensearch_rel_id = harness.add_relation(OPENSEARCH_REL_NAME, "opensearch")
        harness.add_relation_unit(opensearch_rel_id, "opensearch/0")
        harness.update_relation_data(opensearch_rel_id, "opensearch", {"password": "test"})
        if options["opensearch_data"]:
            for key, value in options["opensearch_data"].items():
                harness.update_relation_data(opensearch_rel_id, "opensearch", {key: value})

    harness._update_config({"log_level": options["log_level"]})

    harness.begin()

    if options["init_peer_hostname"]:
        with harness.hooks_disabled():
            harness.update_relation_data(
                peer_rel_id, f"{CHARM_KEY}/0", {"hostname": "000.000.000"}
            )

    if options["mock_network"]:
        harness.charm.model.get_binding = MagicMock()
        harness.charm.model.get_binding.network.bind_address = MagicMock(
            return_value="10.10.10.10"
        )

    # Logic for test_charm: Target upgrade_events
    if options["inject_events_dep_model"]:
        harness.charm.upgrade_events.dependency_model.osd_upstream = (
            OpensearchDashboardsDependencyModel(
                **{
                    "osd_upstream": {
                        "dependencies": {"opensearch": "2.19.4"},
                        "name": "opensearch-dashboards",
                        "upgrade_supported": ">=2",
                        "version": "2.19.4",
                    }
                }
            )
        )

    # Logic for test_upgrade: Target upgrade_manager
    if options["inject_manager_dep_model"]:
        harness.charm.upgrade_manager.dependency_model = OpensearchDashboardsDependencyModel(
            **{
                "osd_upstream": {
                    "dependencies": {"opensearch": "2.12"},
                    "name": "opensearch-dashboards",
                    "upgrade_supported": ">=2",
                    "version": "2.12",
                }
            }
        )

    return harness


# @pytest.fixture(autouse=True)
# def patched_idle(mocker):
#     mocker.patch(
#         "events.upgrade.ODUpgradeEvents.idle", new_callable=PropertyMock, return_value=True
#     )

# @pytest.fixture(autouse=True)
# def patched_set_rolling_update_partition(mocker):
#     mocker.patch("events.upgrade.ODUpgradeEvents._set_rolling_update_partition")
