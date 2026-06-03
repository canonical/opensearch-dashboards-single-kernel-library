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
    CONFIG_OPTS,
    DUMMY_CHARM,
    TLS_CERTIFICATES_APP_NAME,
    TLS_STABLE_CHANNEL,
    access_all_dashboards,
    is_https_enabled,
)

logger = logging.getLogger(__name__)

METADATA_VM = yaml.safe_load(Path("tests/charms/vm/metadata.yaml").read_text())
METADATA_K8S = yaml.safe_load(Path("tests/charms/k8s/metadata.yaml").read_text())

OPENSEARCH_APP_NAME = "opensearch"
TRAEFIK_APP_NAME = "traefik-k8s"
OPENSEARCH_CONFIG = {
    "logging-config": "<root>=INFO;unit=DEBUG",
    "cloudinit-userdata": """postruncmd:
        - [ 'sysctl', '-w', 'vm.max_map_count=262144' ]
        - [ 'sysctl', '-w', 'fs.file-max=1048576' ]
        - [ 'sysctl', '-w', 'vm.swappiness=0' ]
        - [ 'sysctl', '-w', 'net.ipv4.tcp_retries2=5' ]
    """,
}

RESOURCE = {
    "opensearch-dashboards-image": METADATA_K8S["resources"]["opensearch-dashboards-image"][
        "upstream-source"
    ]
}


@pytest.mark.skip_if_deployed
@pytest.mark.abort_on_fail
async def test_build_and_deploy(
    ops_test: OpsTest,
    ops_test_microk8s: OpsTest,
    charmvm: str,
    charmk8s: str,
    charm_base: str,
    dashboard_tester_charm: str,
    substrate: str,
    test_flags: Flags,
):
    """Deploying all charms required for the tests, and wait for complete setup."""
    app_name = METADATA_K8S["name"] if substrate == "k8s" else METADATA_VM["name"]
    tls = test_flags.tls
    traefik = test_flags.traefik

    deploy_kwargs = {"application_name": app_name, "num_units": 1, "base": charm_base}
    if substrate == "k8s":
        deploy_kwargs["resources"] = RESOURCE
    else:
        deploy_kwargs["base"] = charm_base

    # 1. Deploy OpenSearch and Certificates
    await ops_test.model.set_config(OPENSEARCH_CONFIG)
    await ops_test.model.deploy(
        OPENSEARCH_APP_NAME, channel="2/edge", num_units=2, config=CONFIG_OPTS
    )

    config = {"ca-common-name": "CN_CA"}
    await ops_test.model.deploy(
        TLS_CERTIFICATES_APP_NAME, channel=TLS_STABLE_CHANNEL, config=config
    )

    await ops_test.model.wait_for_idle(
        apps=[TLS_CERTIFICATES_APP_NAME], status="active", timeout=1000
    )

    await ops_test.model.integrate(OPENSEARCH_APP_NAME, TLS_CERTIFICATES_APP_NAME)
    await ops_test.model.wait_for_idle(
        apps=[OPENSEARCH_APP_NAME, TLS_CERTIFICATES_APP_NAME], status="active", timeout=1000
    )

    # 2. Deploy Dashboards Charm
    if substrate == "k8s":
        await ops_test.model.create_offer(
            "opensearch-client", OPENSEARCH_APP_NAME, "opensearch"
        )
        await ops_test_microk8s.model.consume(
            f"admin/{ops_test.model.name}.{OPENSEARCH_APP_NAME}"
        )
        await ops_test.model.create_offer(
            endpoint=f"{TLS_CERTIFICATES_APP_NAME}:certificates,send-ca-cert",
            offer_name="self-signed-certificates",
        )
        await ops_test_microk8s.model.consume(
            f"admin/{ops_test.model_name}.{TLS_CERTIFICATES_APP_NAME}"
        )

        await ops_test_microk8s.model.deploy(charmk8s, **deploy_kwargs)
    else:
        await ops_test_microk8s.model.deploy(charmvm, **deploy_kwargs)

    if traefik:
        await ops_test_microk8s.model.deploy(
            TRAEFIK_APP_NAME, channel="latest/stable", trust=True
        )
    await ops_test_microk8s.model.wait_for_idle(
        apps=[app_name], status="blocked", timeout=1000, idle_period=30
    )
    pytest.relation = await ops_test_microk8s.model.integrate(OPENSEARCH_APP_NAME, app_name)
    await ops_test.model.wait_for_idle(
        apps=[OPENSEARCH_APP_NAME], wait_for_active=True, timeout=1000
    )

    if traefik:
        await ops_test_microk8s.model.integrate(app_name, TRAEFIK_APP_NAME)
        await ops_test_microk8s.model.wait_for_idle(
            apps=[app_name, TRAEFIK_APP_NAME], status="active", timeout=1000
        )

    if tls:
        await ops_test_microk8s.model.integrate(app_name, TLS_CERTIFICATES_APP_NAME)
        if traefik:
            await ops_test_microk8s.model.integrate(
                TRAEFIK_APP_NAME, f"{TLS_CERTIFICATES_APP_NAME}:certificates"
            )
            await ops_test_microk8s.model.wait_for_idle(
                apps=[app_name, TRAEFIK_APP_NAME], status="active", timeout=1000
            )
        elif not substrate == "k8s" or traefik:
            await ops_test_microk8s.model.wait_for_idle(
                apps=[app_name], status="active", timeout=1000
            )
        else:
            await ops_test_microk8s.model.deploy(
                dashboard_tester_charm, application_name=DUMMY_CHARM
            )
            await ops_test_microk8s.model.wait_for_idle(
                apps=[app_name], status="blocked", timeout=1000
            )
    elif substrate == "k8s" and not traefik:
        await ops_test_microk8s.model.wait_for_idle(
            apps=[app_name], status="blocked", timeout=1000
        )
    else:
        await ops_test_microk8s.model.wait_for_idle(
            apps=[app_name], wait_for_active=True, timeout=1000
        )

##############################################################################
# Helper functions
##############################################################################

async def scale_up(
    ops_test: OpsTest,
    ops_test_microk8s: OpsTest,
    amount: int,
    substrate: str,
    test_flags: Flags,
) -> None:
    """Testing that newly added units are functional."""
    app_name = METADATA_K8S["name"] if substrate == "k8s" else METADATA_VM["name"]
    traefik = test_flags.traefik
    init_units_count = len(ops_test_microk8s.model.applications[app_name].units)
    expected = init_units_count + amount
    https = is_https_enabled(test_flags)

    # scale up
    if substrate == "k8s":
        logger.info(f"Adding units to {expected}")
        await ops_test_microk8s.model.applications[app_name].scale(expected)
    else:
        logger.info(f"Adding {amount} units")
        await ops_test_microk8s.model.applications[app_name].add_unit(count=amount)

    logger.info(f"Waiting for {amount} units to be added and stable")
    if substrate == "k8s" and not traefik:
        await ops_test_microk8s.model.wait_for_idle(
            apps=[app_name],
            status="blocked",
            wait_for_exact_units=expected,
            timeout=1000,
            idle_period=30,
        )
    else:
        await ops_test_microk8s.model.wait_for_idle(
            apps=[app_name],
            status="active",
            wait_for_exact_units=expected,
            timeout=1000,
            idle_period=30,
        )

    num_units = len(ops_test_microk8s.model.applications[app_name].units)
    assert num_units == expected

    logger.info("Checking the functionality of the new units")
    verify = True if https else False
    assert await access_all_dashboards(ops_test, ops_test_microk8s, https=https, verify=verify)

async def scale_down(
    ops_test: OpsTest,
    ops_test_microk8s: OpsTest,
    unit_ids: list[int],
    substrate: str,
    test_flags: Flags,
) -> None:
    """Testing that decreasing units keeps functionality."""
    app_name = METADATA_K8S["name"] if substrate == "k8s" else METADATA_VM["name"]
    traefik = test_flags.traefik
    https = is_https_enabled(test_flags)
    init_units_count = len(ops_test_microk8s.model.applications[app_name].units)
    amount = len(unit_ids)
    expected = init_units_count - amount

    # scale down
    if substrate == "k8s":
        logger.info(f"Removing units to {expected}")
        await ops_test_microk8s.model.applications[app_name].scale(expected)
    else:
        logger.info(f"Removing units {unit_ids}")
        await ops_test_microk8s.model.applications[app_name].destroy_unit(
            *[f"{app_name}/{cnt}" for cnt in unit_ids]
        )

    logger.info(f"Waiting for units {unit_ids} to be removed safely")
    if substrate == "k8s" and not traefik:
        await ops_test_microk8s.model.wait_for_idle(
            apps=[app_name],
            status="blocked",
            wait_for_exact_units=expected,
            timeout=1000,
            idle_period=30,
        )
    else:
        await ops_test_microk8s.model.wait_for_idle(
            apps=[app_name],
            status="active",
            wait_for_exact_units=expected,
            timeout=1000,
            idle_period=30,
        )

    num_units = len(ops_test_microk8s.model.applications[app_name].units)
    assert num_units == expected

    logger.info("Checking the functionality of the remaining units")
    if expected > 0:
        verify = True if https else False
        assert await access_all_dashboards(
            ops_test, ops_test_microk8s, https=https, verify=verify
        )

##############################################################################
# Tests
##############################################################################

@pytest.mark.abort_on_fail
async def test_horizontal_scale_up(
    ops_test: OpsTest,
    ops_test_microk8s: OpsTest,
    substrate: str,
    test_flags: Flags,
) -> None:
    """Testing that newly added units are functional."""
    await scale_up(
        ops_test=ops_test,
        ops_test_microk8s=ops_test_microk8s,
        amount=2,
        substrate=substrate,
        test_flags=test_flags,
    )

@pytest.mark.abort_on_fail
async def test_horizontal_scale_down(
    ops_test: OpsTest,
    ops_test_microk8s: OpsTest,
    substrate: str,
    test_flags: Flags,
) -> None:
    """Testing that decreasing units keeps functionality."""
    await scale_down(
        ops_test=ops_test,
        ops_test_microk8s=ops_test_microk8s,
        unit_ids=[1, 2],
        substrate=substrate,
        test_flags=test_flags,
    )

@pytest.mark.abort_on_fail
async def test_horizontal_scale_down_to_zero(
    ops_test: OpsTest,
    ops_test_microk8s: OpsTest,
    substrate: str,
    test_flags: Flags,
) -> None:
    """Testing that scaling down to 0 units is possible."""
    await scale_down(
        ops_test=ops_test,
        ops_test_microk8s=ops_test_microk8s,
        unit_ids=[0],
        substrate=substrate,
        test_flags=test_flags,
    )

@pytest.mark.abort_on_fail
async def test_horizontal_scale_up_from_zero(
    ops_test: OpsTest,
    ops_test_microk8s: OpsTest,
    substrate: str,
    test_flags: Flags,
) -> None:
    """Testing that scaling up from zero units works."""
    await scale_up(
        ops_test=ops_test,
        ops_test_microk8s=ops_test_microk8s,
        amount=3,
        substrate=substrate,
        test_flags=test_flags,
    )
