#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from pathlib import Path

import pytest
import yaml
from pytest_operator.plugin import OpsTest

from tests.integration.conftest import Flags

from ..helpers import (
    DUMMY_CHARM,
    TLS_CERTIFICATES_APP_NAME,
    TRAEFIK_APP_NAME,
    access_all_dashboards,
    deploy_base,
    is_https_enabled,
    wait_for_ingress_blocked,
)

logger = logging.getLogger(__name__)

METADATA_VM = yaml.safe_load(Path("tests/charms/dashboards_vm_charm/metadata.yaml").read_text())
METADATA_K8S = yaml.safe_load(Path("tests/charms/dashboards_k8s_charm/metadata.yaml").read_text())


@pytest.mark.skip_if_deployed
@pytest.mark.abort_on_fail
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
    """Deploying all charms required for the tests, and wait for complete setup."""
    tls = test_flags.test_tls
    traefik = test_flags.traefik

    app_name = await deploy_base(
        ops_test_vm,
        ops_test,
        charmvm,
        charmk8s,
        charm_base,
        substrate,
        num_units_app=1,
        num_units_db=2,
    )

    if substrate == "k8s":
        await ops_test_vm.model.create_offer(
            endpoint=f"{TLS_CERTIFICATES_APP_NAME}:certificates,send-ca-cert",
            offer_name="self-signed-certificates",
        )
        await ops_test.model.consume(f"admin/{ops_test_vm.model_name}.{TLS_CERTIFICATES_APP_NAME}")

    if traefik:
        await ops_test.model.deploy(TRAEFIK_APP_NAME, channel="latest/stable", trust=True)
        await ops_test.model.integrate(app_name, TRAEFIK_APP_NAME)
        await ops_test.model.wait_for_idle(
            apps=[app_name, TRAEFIK_APP_NAME], status="active", timeout=1000
        )

    if tls:
        await ops_test.model.integrate(app_name, TLS_CERTIFICATES_APP_NAME)
        if traefik:
            await ops_test.model.integrate(
                TRAEFIK_APP_NAME, f"{TLS_CERTIFICATES_APP_NAME}:certificates"
            )
            await ops_test.model.wait_for_idle(
                apps=[app_name, TRAEFIK_APP_NAME], status="active", timeout=1000
            )
        elif not substrate == "k8s" or traefik:
            await ops_test.model.wait_for_idle(apps=[app_name], status="active", timeout=1000)
        else:
            await ops_test.model.deploy(dashboard_tester_charm, application_name=DUMMY_CHARM)
            await wait_for_ingress_blocked(ops_test, app_name, timeout=1000)
    elif substrate == "k8s" and not traefik:
        await wait_for_ingress_blocked(ops_test, app_name, timeout=1000)
    else:
        await ops_test.model.wait_for_idle(apps=[app_name], wait_for_active=True, timeout=1000)


##############################################################################
# Helper functions
##############################################################################


async def scale_up(
    ops_test_vm: OpsTest,
    ops_test: OpsTest,
    amount: int,
    substrate: str,
    test_flags: Flags,
) -> None:
    """Testing that newly added units are functional."""
    app_name = METADATA_K8S["name"] if substrate == "k8s" else METADATA_VM["name"]
    traefik = test_flags.traefik
    init_units_count = len(ops_test.model.applications[app_name].units)
    expected = init_units_count + amount
    https = is_https_enabled(test_flags)

    # scale up
    if substrate == "k8s":
        logger.info(f"Adding units to {expected}")
        await ops_test.model.applications[app_name].scale(expected)
    else:
        logger.info(f"Adding {amount} units")
        await ops_test.model.applications[app_name].add_unit(count=amount)

    logger.info(f"Waiting for {amount} units to be added and stable")
    if substrate == "k8s" and not traefik:
        await wait_for_ingress_blocked(
            ops_test, app_name, timeout=1000, idle_period=30, wait_for_exact_units=expected
        )
    else:
        await ops_test.model.wait_for_idle(
            apps=[app_name],
            status="active",
            wait_for_exact_units=expected,
            timeout=1000,
            idle_period=30,
        )

    num_units = len(ops_test.model.applications[app_name].units)
    assert num_units == expected

    logger.info("Checking the functionality of the new units")
    assert await access_all_dashboards(ops_test_vm, ops_test, https=https, verify=https)


async def scale_down(
    ops_test_vm: OpsTest,
    ops_test: OpsTest,
    unit_ids: list[int],
    substrate: str,
    test_flags: Flags,
) -> None:
    """Testing that decreasing units keeps functionality."""
    app_name = METADATA_K8S["name"] if substrate == "k8s" else METADATA_VM["name"]
    traefik = test_flags.traefik
    https = is_https_enabled(test_flags)
    init_units_count = len(ops_test.model.applications[app_name].units)
    amount = len(unit_ids)
    expected = init_units_count - amount

    # scale down
    if substrate == "k8s":
        logger.info(f"Removing units to {expected}")
        await ops_test.model.applications[app_name].scale(expected)
    else:
        logger.info(f"Removing units {unit_ids}")
        await ops_test.model.applications[app_name].destroy_unit(
            *[f"{app_name}/{cnt}" for cnt in unit_ids]
        )

    logger.info(f"Waiting for units {unit_ids} to be removed safely")
    if substrate == "k8s" and not traefik and expected > 0:
        await wait_for_ingress_blocked(
            ops_test, app_name, timeout=1000, idle_period=30, wait_for_exact_units=expected
        )
    else:
        await ops_test.model.wait_for_idle(
            apps=[app_name],
            wait_for_exact_units=expected,
            timeout=1000,
            idle_period=30,
        )

    num_units = len(ops_test.model.applications[app_name].units)
    assert num_units == expected

    logger.info("Checking the functionality of the remaining units")
    if expected > 0:
        assert await access_all_dashboards(ops_test_vm, ops_test, https=https, verify=https)


##############################################################################
# Tests
##############################################################################


@pytest.mark.abort_on_fail
async def test_horizontal_scale_up(
    ops_test_vm: OpsTest,
    ops_test: OpsTest,
    substrate: str,
    test_flags: Flags,
) -> None:
    """Testing that newly added units are functional."""
    await scale_up(
        ops_test_vm=ops_test_vm,
        ops_test=ops_test,
        amount=2,
        substrate=substrate,
        test_flags=test_flags,
    )


@pytest.mark.abort_on_fail
async def test_horizontal_scale_down(
    ops_test_vm: OpsTest,
    ops_test: OpsTest,
    substrate: str,
    test_flags: Flags,
) -> None:
    """Testing that decreasing units keeps functionality."""
    await scale_down(
        ops_test_vm=ops_test_vm,
        ops_test=ops_test,
        unit_ids=[1, 2],
        substrate=substrate,
        test_flags=test_flags,
    )


@pytest.mark.abort_on_fail
async def test_horizontal_scale_down_to_zero(
    ops_test_vm: OpsTest,
    ops_test: OpsTest,
    substrate: str,
    test_flags: Flags,
) -> None:
    """Testing that scaling down to 0 units is possible."""
    await scale_down(
        ops_test_vm=ops_test_vm,
        ops_test=ops_test,
        unit_ids=[0],
        substrate=substrate,
        test_flags=test_flags,
    )


@pytest.mark.abort_on_fail
async def test_horizontal_scale_up_from_zero(
    ops_test_vm: OpsTest,
    ops_test: OpsTest,
    substrate: str,
    test_flags: Flags,
) -> None:
    """Testing that scaling up from zero units works."""
    await scale_up(
        ops_test_vm=ops_test_vm,
        ops_test=ops_test,
        amount=3,
        substrate=substrate,
        test_flags=test_flags,
    )
