#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import logging

import pytest
from pytest_operator.plugin import OpsTest

from .conftest import Flags
from .helpers import (
    DB_CLIENT_APP_NAME,
    NUM_UNITS_APP,
    NUM_UNITS_DB,
    OLD_K8S_RESOURCE,
    OPENSEARCH_APP_NAME,
    RESOURCE,
    TLS_CERTIFICATES_APP_NAME,
    TRAEFIK_APP_NAME,
    access_all_dashboards,
    assert_no_downgrade,
    assert_upgraded,
    deploy_opensearch_and_dashboards,
    get_app_relation_data,
    get_charm_workload_version,
    get_dashboards_version,
    is_https_enabled,
    wait_for_dashboard_idle,
)

logger = logging.getLogger(__name__)

CHANNEL_STABLE = "2/stable"
CHANNEL_EDGE = "2/edge"


async def _run_upgrade_scenario(
    ops_test: OpsTest,
    substrate: str,
    application_charm: str,
    test_flags: Flags,
    charm_base: str,
    charm: str,
    opensearch_deploy_args: tuple[str, bool],
    old_charm_channel: str | None = None,
    old_resource: dict | None = None,
    expect_dashboards_upgrade: bool = False,
    expect_workload_upgrade: bool = False,
) -> None:
    """Deploy an old dashboards release, upgrade it to the local charm, and check the workload version changed."""
    tls = test_flags.test_tls
    traefik = test_flags.traefik
    app_name = await deploy_opensearch_and_dashboards(
        ops_test,
        charm=charm,
        charm_base=charm_base,
        substrate=substrate,
        opensearch_deploy_args=opensearch_deploy_args,
        num_units_app=NUM_UNITS_APP,
        num_units_db=NUM_UNITS_DB,
        trust_charm=True,
        charm_channel=old_charm_channel,
        resource=old_resource,
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
            await ops_test.model.deploy(application_charm, application_name=DB_CLIENT_APP_NAME)

        if tls:
            await ops_test.model.integrate(app_name, TLS_CERTIFICATES_APP_NAME)
            if traefik:
                await ops_test.model.integrate(
                    TRAEFIK_APP_NAME, f"{TLS_CERTIFICATES_APP_NAME}:certificates"
                )

        await wait_for_dashboard_idle(ops_test, traefik)
        await ops_test.model.wait_for_idle(
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

    old_charm_url = ops_test.model.applications[app_name].safe_data["charm-url"]
    logger.info(f"Old Charm URL: {old_charm_url}")

    if substrate == "k8s":
        await ops_test.model.applications[app_name].refresh(path=charm, resources=RESOURCE)
    else:
        await ops_test.model.applications[app_name].refresh(path=charm)

    await wait_for_dashboard_idle(ops_test, traefik, idle_period=60)
    # Validate access
    assert await access_all_dashboards(ops_test, https=is_https_enabled(test_flags), verify=tls)

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

    new_charm_url = ops_test.model.applications[app_name].safe_data["charm-url"]
    logger.info(f"New Charm URL: {new_charm_url}")

    assert new_charm_url != old_charm_url
    if all(old_workload_version.values()):
        if expect_workload_upgrade:
            assert_upgraded(old_workload_version, new_workload_version)
        else:
            assert_no_downgrade(old_workload_version, new_workload_version)
    else:
        logger.info("Old release has no workload_version file; skipping workload comparison")
    if expect_dashboards_upgrade:
        assert_upgraded(old_dashboards_version, new_dashboards_version)
    else:
        assert_no_downgrade(old_dashboards_version, new_dashboards_version)


@pytest.mark.vm_only
@pytest.mark.abort_on_fail
async def test_vm_upgrade_from_stable(
    ops_test: OpsTest,
    substrate: str,
    application_charm: str,
    test_flags: Flags,
    charm_base: str,
    charm: str,
    opensearch_deploy_args: tuple[str, bool],
):
    """VM: upgrade from the 2/stable Charmhub release to the locally built charm."""
    await _run_upgrade_scenario(
        ops_test,
        substrate,
        application_charm,
        test_flags,
        charm_base,
        charm,
        opensearch_deploy_args,
        old_charm_channel=CHANNEL_STABLE,
        expect_dashboards_upgrade=True,
        expect_workload_upgrade=True,
    )


@pytest.mark.vm_only
@pytest.mark.abort_on_fail
async def test_vm_upgrade_from_edge(
    ops_test: OpsTest,
    substrate: str,
    application_charm: str,
    test_flags: Flags,
    charm_base: str,
    charm: str,
    opensearch_deploy_args: tuple[str, bool],
):
    """VM: upgrade from the 2/edge Charmhub release to the locally built charm."""
    await _run_upgrade_scenario(
        ops_test,
        substrate,
        application_charm,
        test_flags,
        charm_base,
        charm,
        opensearch_deploy_args,
        old_charm_channel=CHANNEL_EDGE,
    )


@pytest.mark.k8s_only
@pytest.mark.abort_on_fail
async def test_k8s_upgrade_from_edge(
    ops_test: OpsTest,
    substrate: str,
    application_charm: str,
    test_flags: Flags,
    charm_base: str,
    charm: str,
    opensearch_deploy_args: tuple[str, bool],
):
    """K8s: upgrade from the 2/edge Charmhub release to the locally built charm."""
    await _run_upgrade_scenario(
        ops_test,
        substrate,
        application_charm,
        test_flags,
        charm_base,
        charm,
        opensearch_deploy_args,
        old_charm_channel=CHANNEL_EDGE,
        old_resource=OLD_K8S_RESOURCE,
        expect_dashboards_upgrade=True,
    )
