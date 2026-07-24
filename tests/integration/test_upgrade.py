#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import logging
from pathlib import Path

import pytest
import yaml
from pytest_operator.plugin import OpsTest

from .conftest import Flags
from .helpers import (
    DUMMY_CHARM,
    OPENSEARCH_APP_NAME,
    RESOURCE,
    TLS_CERTIFICATES_APP_NAME,
    TRAEFIK_APP_NAME,
    access_all_dashboards,
    deploy_base,
    get_app_relation_data,
    get_charm_workload_version,
    get_dashboards_version,
    is_https_enabled,
    wait_for_dashboard_idle,
)

logger = logging.getLogger(__name__)

METADATA_UPGRADE_TEST_K8S = yaml.safe_load(
    Path("tests/integration/dashboards_k8s_upgrade_test_charm/metadata.yaml").read_text()
)
RESOURCE_OLD = {
    "opensearch-dashboards-image": METADATA_UPGRADE_TEST_K8S["resources"][
        "opensearch-dashboards-image"
    ]["upstream-source"]
}
NUM_UNITS_APP = 3
NUM_UNITS_DB = 3

CHANNEL_STABLE = "2/stable"
CHANNEL_EDGE = "2/edge"


async def _run_upgrade_scenario(
    ops_test_vm: OpsTest,
    ops_test: OpsTest,
    charm: str,
    charm_base: str,
    substrate: str,
    dashboard_tester_charm: str,
    test_flags: Flags,
    old_charm_channel: str | None = None,
    old_charm_local: str | None = None,
    old_charm_resource: dict | None = None,
) -> None:
    """Deploy an old dashboards release, upgrade it to the local charm, and check the workload version changed.

    The old release is pulled from Charmhub (`old_charm_channel`) or built locally
    (`old_charm_local`, e.g. the k8s upgrade test charm pinned to an old workload version).
    Handles both substrates; k8s additionally needs ingress (Traefik or the dashboard tester
    charm) and a cross-model TLS offer, since OpenSearch/TLS live in a separate VM model there.
    """
    tls = test_flags.test_tls
    traefik = test_flags.traefik
    app_name = await deploy_base(
        ops_test_vm=ops_test_vm,
        ops_test=ops_test,
        charm=old_charm_local or charm,
        charm_base=charm_base,
        substrate=substrate,
        num_units_app=NUM_UNITS_APP,
        num_units_db=NUM_UNITS_DB,
        trust_charm=True,
        resource=old_charm_resource,
        charm_channel=old_charm_channel,
    )

    if substrate == "k8s":
        async with ops_test.fast_forward():
            await ops_test.model.block_until(
                lambda: len(ops_test.model.applications[app_name].units) == NUM_UNITS_APP
            )
            await ops_test.model.wait_for_idle(apps=[app_name], timeout=1000, idle_period=30)
        assert ops_test.model.applications[app_name].status == "blocked"

        if traefik:
            await ops_test.model.deploy(TRAEFIK_APP_NAME, channel="latest/stable", trust=True)
            await ops_test.model.integrate(app_name, TRAEFIK_APP_NAME)
        else:
            await ops_test.model.deploy(dashboard_tester_charm, application_name=DUMMY_CHARM)

        if tls:
            await ops_test_vm.model.create_offer(
                "certificates", TLS_CERTIFICATES_APP_NAME, "self-signed-certificates"
            )
            await ops_test.model.consume(
                f"admin/{ops_test_vm.model_name}.{TLS_CERTIFICATES_APP_NAME}"
            )
            await ops_test.model.integrate(app_name, TLS_CERTIFICATES_APP_NAME)
            if traefik:
                await ops_test.model.integrate(
                    TRAEFIK_APP_NAME, f"{TLS_CERTIFICATES_APP_NAME}:certificates"
                )

        await wait_for_dashboard_idle(ops_test, traefik)
        await ops_test_vm.model.wait_for_idle(
            apps=[OPENSEARCH_APP_NAME], status="active", timeout=1000
        )
    else:
        if tls:
            await ops_test.model.integrate(app_name, TLS_CERTIFICATES_APP_NAME)
        await wait_for_dashboard_idle(ops_test, traefik)

    leader_unit = None
    for unit in ops_test.model.applications[app_name].units:
        if await unit.is_leader_from_status():
            leader_unit = unit
    assert leader_unit

    action = await leader_unit.run_action("pre-upgrade-check")
    await action.wait()

    # ensuring that the upgrade stack is correct
    relation_data = get_app_relation_data(
        model_full_name=ops_test.model_full_name,
        unit=f"{app_name}/0",
        endpoint="upgrade",
    )
    assert "upgrade-stack" in relation_data
    assert set(json.loads(relation_data["upgrade-stack"])) == set(
        [int(unit.name.split("/")[-1]) for unit in ops_test.model.applications[app_name].units]
    )

    old_workload_version = {
        unit.name: get_charm_workload_version(ops_test.model_full_name, unit.name, substrate)
        for unit in ops_test.model.applications[app_name].units
    }
    old_dashboards_version = {
        unit.name: get_dashboards_version(ops_test.model_full_name, unit.name, substrate)
        for unit in ops_test.model.applications[app_name].units
    }
    logger.info(f"Old Workload Version: {old_workload_version}")
    logger.info(f"Old Dashboards Version: {old_dashboards_version}")

    if substrate == "k8s":
        await ops_test.model.applications[app_name].refresh(path=charm, resources=RESOURCE)
    else:
        await ops_test.model.applications[app_name].refresh(path=charm)

    await wait_for_dashboard_idle(ops_test, traefik, idle_period=60)
    # Validate access
    assert await access_all_dashboards(
        ops_test_vm, ops_test, https=is_https_enabled(test_flags), verify=tls
    )

    new_workload_version = {
        unit.name: get_charm_workload_version(ops_test.model_full_name, unit.name, substrate)
        for unit in ops_test.model.applications[app_name].units
    }
    new_dashboards_version = {
        unit.name: get_dashboards_version(ops_test.model_full_name, unit.name, substrate)
        for unit in ops_test.model.applications[app_name].units
    }
    logger.info(f"New Workload Version: {new_workload_version}")
    logger.info(f"New Dashboards Version: {new_dashboards_version}")

    assert new_workload_version != old_workload_version
    assert new_dashboards_version != old_dashboards_version


@pytest.mark.vm_only
@pytest.mark.abort_on_fail
async def test_vm_upgrade_from_stable(
    ops_test_vm: OpsTest,
    ops_test: OpsTest,
    charmvm: str,
    charm_base: str,
    substrate: str,
    dashboard_tester_charm: str,
    test_flags: Flags,
):
    """VM: upgrade from the 2/stable Charmhub release to the locally built charm."""
    await _run_upgrade_scenario(
        ops_test_vm,
        ops_test,
        charmvm,
        charm_base,
        substrate,
        dashboard_tester_charm,
        test_flags,
        old_charm_channel=CHANNEL_STABLE,
    )


@pytest.mark.vm_only
@pytest.mark.abort_on_fail
async def test_vm_upgrade_from_edge(
    ops_test_vm: OpsTest,
    ops_test: OpsTest,
    charmvm: str,
    charm_base: str,
    substrate: str,
    dashboard_tester_charm: str,
    test_flags: Flags,
):
    """VM: upgrade from the 2/edge Charmhub release to the locally built charm."""
    await _run_upgrade_scenario(
        ops_test_vm,
        ops_test,
        charmvm,
        charm_base,
        substrate,
        dashboard_tester_charm,
        test_flags,
        old_charm_channel=CHANNEL_EDGE,
    )


@pytest.mark.k8s_only
@pytest.mark.abort_on_fail
async def test_k8s_upgrade_from_local_2_19_4_charm(
    ops_test_vm: OpsTest,
    ops_test: OpsTest,
    charmk8s: str,
    charm_base: str,
    substrate: str,
    dashboard_tester_charm: str,
    dashboard_k8s_upgrade_charm: str,
    test_flags: Flags,
):
    """K8s: upgrade from the locally built old-version test charm to the locally built current charm.

    Used in place of a Charmhub 2/stable release, which doesn't exist yet for the k8s charm.
    """
    await _run_upgrade_scenario(
        ops_test_vm,
        ops_test,
        charmk8s,
        charm_base,
        substrate,
        dashboard_tester_charm,
        test_flags,
        old_charm_local=dashboard_k8s_upgrade_charm,
        old_charm_resource=RESOURCE_OLD,
    )


@pytest.mark.skip(reason="opensearch-dashboards-k8s has not been published to 2/edge yet")
@pytest.mark.k8s_only
@pytest.mark.abort_on_fail
async def test_k8s_upgrade_from_edge(
    ops_test_vm: OpsTest,
    ops_test: OpsTest,
    charmk8s: str,
    charm_base: str,
    substrate: str,
    dashboard_tester_charm: str,
    test_flags: Flags,
):
    """K8s: upgrade from the 2/edge Charmhub release to the locally built charm.

    Disabled until opensearch-dashboards-k8s is published to 2/edge
    """
    await _run_upgrade_scenario(
        ops_test_vm,
        ops_test,
        charmk8s,
        charm_base,
        substrate,
        dashboard_tester_charm,
        test_flags,
        old_charm_channel=CHANNEL_EDGE,
    )
