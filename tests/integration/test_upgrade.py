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
    is_https_enabled,
    wait_for_dashboard_idle,
)

logger = logging.getLogger(__name__)

METADATA_VM = yaml.safe_load(Path("tests/charms/dashboards_vm_charm/metadata.yaml").read_text())
METADATA_K8S = yaml.safe_load(Path("tests/charms/dashboards_k8s_charm/metadata.yaml").read_text())

NUM_UNITS_APP = 3
NUM_UNITS_DB = 3


@pytest.mark.abort_on_fail
@pytest.mark.skip_if_deployed
async def test_build_and_deploy(
    ops_test_vm: OpsTest,
    ops_test: OpsTest,
    charmvm: str,
    charmk8s: str,
    charm_base: str,
    dashboard_tester_charm: str,
    substrate: str,
    test_flags: Flags,
):
    """Deploying all charms required for the tests, and wait for their complete setup to be done."""
    tls = test_flags.test_tls
    traefik = test_flags.traefik

    app_name = await deploy_base(
        ops_test_vm,
        ops_test,
        charmvm,
        charmk8s,
        charm_base,
        substrate,
        num_units_app=NUM_UNITS_APP,
        num_units_db=NUM_UNITS_DB,
        trust_charm=True,
    )

    async with ops_test.fast_forward():
        await ops_test.model.block_until(
            lambda: len(ops_test.model.applications[app_name].units) == NUM_UNITS_APP
        )
        await ops_test.model.wait_for_idle(apps=[app_name], timeout=1000, idle_period=30)

    assert ops_test.model.applications[app_name].status == "blocked"

    if tls:
        if substrate == "k8s":
            await ops_test_vm.model.create_offer(
                "certificates", TLS_CERTIFICATES_APP_NAME, "self-signed-certificates"
            )
            await ops_test.model.consume(
                f"admin/{ops_test_vm.model_name}.{TLS_CERTIFICATES_APP_NAME}"
            )
        await ops_test.model.integrate(app_name, TLS_CERTIFICATES_APP_NAME)
        if not traefik and substrate == "k8s":
            await ops_test.model.deploy(dashboard_tester_charm, application_name=DUMMY_CHARM)

    if traefik:
        await ops_test.model.deploy(TRAEFIK_APP_NAME, channel="latest/stable", trust=True)
        await ops_test.model.integrate(app_name, TRAEFIK_APP_NAME)
        if tls:
            await ops_test.model.integrate(
                TRAEFIK_APP_NAME, f"{TLS_CERTIFICATES_APP_NAME}:certificates"
            )

    await wait_for_dashboard_idle(ops_test, traefik)
    await ops_test_vm.model.wait_for_idle(
        apps=[OPENSEARCH_APP_NAME], status="active", timeout=1000
    )


@pytest.mark.abort_on_fail
async def test_in_place_upgrade(
    ops_test_vm: OpsTest,
    ops_test: OpsTest,
    charmvm: str,
    charmk8s: str,
    substrate: str,
    test_flags: Flags,
):
    """Test the in-place upgrade handling the appropriate protocol (HTTP/HTTPS)."""
    app_name = METADATA_K8S["name"] if substrate == "k8s" else METADATA_VM["name"]
    tls = test_flags.test_tls
    traefik = test_flags.traefik

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

    if substrate == "k8s":
        await ops_test.model.applications[app_name].refresh(path=charmk8s, resources=RESOURCE)
    else:
        await ops_test.model.applications[app_name].refresh(path=charmvm)

    await wait_for_dashboard_idle(ops_test, traefik)
    # Validate access
    assert await access_all_dashboards(
        ops_test_vm, ops_test, https=is_https_enabled(test_flags), verify=tls
    )
