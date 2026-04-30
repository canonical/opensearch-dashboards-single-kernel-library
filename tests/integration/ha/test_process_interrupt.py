#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging
from pathlib import Path

import pytest
import yaml
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_attempt, wait_fixed

from ..helpers import (
    CONFIG_OPTS,
    TLS_STABLE_CHANNEL,
    access_all_dashboards,
    get_leader_name,
)
from .helpers import (
    get_service_pid,
    is_down,
    patch_restart_delay,
    remove_restart_delay,
    send_control_signal,
)

logger = logging.getLogger(__name__)

CLIENT_TIMEOUT = 10
RESTART_DELAY = 60
UPDATE_STATUS_INTERVAL = 60

METADATA_VM = yaml.safe_load(Path("tests/charms/vm/metadata.yaml").read_text())
METADATA_K8S = yaml.safe_load(Path("tests/charms/k8s/metadata.yaml").read_text())
APP_NAME = METADATA_VM["name"]
APP_NAME_K8S = METADATA_K8S["name"]

OPENSEARCH_APP_NAME = "opensearch"
OPENSEARCH_CONFIG = {
    "logging-config": "<root>=INFO;unit=DEBUG",
    "update-status-hook-interval": f"{UPDATE_STATUS_INTERVAL}s",
    "cloudinit-userdata": """postruncmd:
        - [ 'sysctl', '-w', 'vm.max_map_count=262144' ]
        - [ 'sysctl', '-w', 'fs.file-max=1048576' ]
        - [ 'sysctl', '-w', 'vm.swappiness=0' ]
        - [ 'sysctl', '-w', 'net.ipv4.tcp_retries2=5' ]
    """,
}
OPENSEARCH_RELATION_NAME = "opensearch-client"
TLS_CERT_APP_NAME = "self-signed-certificates"
APP_AND_TLS = [APP_NAME, TLS_CERT_APP_NAME]
PEER = "dashboard_peers"
SERVER_PORT = 5601

NUM_UNITS_APP = 2
NUM_UNITS_DB = 3

LONG_TIMEOUT = 3000
LONG_WAIT = 30
TRAEFIK_APP_NAME = "traefik-k8s"
RESOURCE = {
    "opensearch-dashboards-image": METADATA_K8S["resources"]["opensearch-dashboards-image"][
        "upstream-source"
    ]
}


@pytest.fixture()
async def restart_delay(ops_test: OpsTest, ops_test_microk8s: OpsTest):
    if ops_test.model.name != ops_test_microk8s.model.name:
        return

    for unit in ops_test.model.applications[APP_NAME].units:
        await patch_restart_delay(ops_test=ops_test, unit_name=unit.name, delay=RESTART_DELAY)
    yield
    for unit in ops_test.model.applications[APP_NAME].units:
        await remove_restart_delay(ops_test=ops_test, unit_name=unit.name)


@pytest.mark.skip_if_deployed
@pytest.mark.abort_on_fail
async def test_build_and_deploy(
    ops_test: OpsTest, ops_test_microk8s: OpsTest, charmvm: str, charmk8s: str, series: str
):
    """Tests that the charm deploys safely"""
    is_cross_model = ops_test.model.name != ops_test_microk8s.model.name
    charm = charmvm
    app_name = APP_NAME
    if is_cross_model:
        charm = charmk8s
        app_name = APP_NAME_K8S

    if is_cross_model:
        await ops_test_microk8s.model.deploy(
            charm,
            application_name=app_name,
            num_units=NUM_UNITS_APP,
            series=series,
            resources=RESOURCE,
        )

    else:
        await ops_test_microk8s.model.deploy(
            charm, application_name=app_name, num_units=NUM_UNITS_APP, series=series
        )

    # Opensearch
    await ops_test.model.set_config(OPENSEARCH_CONFIG)
    await ops_test.model.deploy(
        OPENSEARCH_APP_NAME, channel="2/edge", num_units=NUM_UNITS_DB, config=CONFIG_OPTS
    )

    config = {"ca-common-name": "CN_CA"}
    await ops_test.model.deploy(TLS_CERT_APP_NAME, channel=TLS_STABLE_CHANNEL, config=config)

    await ops_test.model.wait_for_idle(
        apps=[TLS_CERT_APP_NAME], wait_for_active=True, timeout=1000
    )

    # Relate it to OpenSearch to set up TLS.
    await ops_test.model.integrate(OPENSEARCH_APP_NAME, TLS_CERT_APP_NAME)
    await ops_test.model.wait_for_idle(
        apps=[OPENSEARCH_APP_NAME, TLS_CERT_APP_NAME], wait_for_active=True, timeout=1000
    )

    # Opensearch Dashboards
    async with ops_test_microk8s.fast_forward():
        await ops_test_microk8s.model.wait_for_idle(
            apps=[app_name],
            wait_for_exact_units=NUM_UNITS_APP,
            timeout=1000,
            idle_period=30,
        )

    assert ops_test_microk8s.model.applications[app_name].status == "blocked"

    if is_cross_model:
        await ops_test.model.create_offer("opensearch-client", OPENSEARCH_APP_NAME, "opensearch")
        await ops_test_microk8s.model.consume(f"admin/{ops_test.model.name}.{OPENSEARCH_APP_NAME}")
        await ops_test_microk8s.model.deploy(TRAEFIK_APP_NAME, channel="latest/stable", trust=True)
        await ops_test_microk8s.model.wait_for_idle(
            apps=[app_name], status="blocked", timeout=1000
        )

    pytest.relation = await ops_test_microk8s.model.integrate(OPENSEARCH_APP_NAME, app_name)
    if is_cross_model:
        await ops_test_microk8s.model.integrate(app_name, TRAEFIK_APP_NAME)
    await ops_test_microk8s.model.wait_for_idle(apps=[app_name], status="active", timeout=1000)
    await ops_test.model.wait_for_idle(
        apps=[OPENSEARCH_APP_NAME], wait_for_active=True, timeout=1000
    )


##############################################################################
# Helper functions
##############################################################################


async def _recover_from_signal(
    ops_test: OpsTest,
    ops_test_microk8s: OpsTest,
    signal: str,
    units: list[str],
    app_name: str = APP_NAME,
    https: bool = False,
    verify: bool = False,
):
    is_cross_model = ops_test.model.name != ops_test_microk8s.model.name
    container = ""
    if app_name == APP_NAME or app_name == APP_NAME_K8S:
        app_name = APP_NAME
        if is_cross_model:
            container = "opensearch-dashboards"
            app_name = APP_NAME_K8S
    pid = {}
    # In attempt to prevent flaky behavior
    # The process is restarted so fast, slow pipelines may not "catch" it in time
    for attempt in Retrying(stop=stop_after_attempt(3), wait=wait_fixed(5), reraise=True):
        with attempt:
            if is_cross_model and signal != "SIGSTOP":
                for unit in units:
                    pid[unit] = await get_service_pid(ops_test_microk8s, unit)

            logger.info(f"Sending {signal} {app_name}:{units}...")
            await asyncio.gather(
                *[
                    send_control_signal(
                        ops_test_microk8s, unit, signal, app_name, True if container else False
                    )
                    for unit in units
                ]
            )

            if is_cross_model and signal != "SIGSTOP":
                logger.info(f"Asserting {app_name}:{units} service pid is changed")
                for unit in units:
                    assert await get_service_pid(ops_test_microk8s, unit) != pid[unit]
            else:
                # Check that process is down
                logger.info(f"Waiting for {app_name}:{units} to be down...")
                assert all(
                    await asyncio.gather(*[is_down(ops_test, unit, app_name) for unit in units])
                )

    logger.info("Waiting a bit, so the process could safely restart...")
    await asyncio.sleep(UPDATE_STATUS_INTERVAL + 2)

    await ops_test.model.wait_for_idle(
        apps=[OPENSEARCH_APP_NAME], wait_for_active=True, timeout=1000
    )
    await ops_test_microk8s.model.wait_for_idle(
        apps=[APP_NAME], wait_for_active=True, timeout=1000
    )

    logger.info("Checking OSD access...")
    assert await access_all_dashboards(ops_test, ops_test_microk8s, https, verify=verify)


##############################################################################
# Tests
##############################################################################


@pytest.mark.abort_on_fail
@pytest.mark.parametrize("signal", ["SIGKILL", "SIGTERM"])
async def test_signal_opensearch_process_leader(
    ops_test: OpsTest, ops_test_microk8s: OpsTest, signal
):
    """Signals OSD leader process and checks recovery + re-election."""
    db_leader_name = await get_leader_name(ops_test, app_name=OPENSEARCH_APP_NAME)
    await _recover_from_signal(
        ops_test, ops_test_microk8s, signal, [db_leader_name], app_name=OPENSEARCH_APP_NAME
    )


@pytest.mark.skip(reason="Opensearch is not possible to contact after recovery")
@pytest.mark.abort_on_fail
async def test_sigstop_opensearch_process_leader(ops_test: OpsTest, ops_test_microk8s: OpsTest):
    """Signals Opensearch leader process and checks recovery + re-election."""
    db_leader_name = await get_leader_name(ops_test, app_name=OPENSEARCH_APP_NAME)
    await _recover_from_signal(
        ops_test, ops_test_microk8s, "SIGSTOP", [db_leader_name], app_name=OPENSEARCH_APP_NAME
    )


@pytest.mark.abort_on_fail
@pytest.mark.parametrize("signal", ["SIGKILL", "SIGTERM", "SIGSTOP"])
async def test_signal_dashboard_process_leader(
    ops_test: OpsTest, ops_test_microk8s: OpsTest, signal
):
    """Signals OSD leader process and checks recovery + re-election."""
    app_name = APP_NAME
    if ops_test.model.name != ops_test_microk8s.model.name:
        app_name = APP_NAME_K8S
    leader_name = await get_leader_name(ops_test, app_name)
    await _recover_from_signal(ops_test, ops_test_microk8s, signal, [leader_name])


@pytest.mark.abort_on_fail
@pytest.mark.parametrize("signal", ["SIGKILL", "SIGTERM"])
async def test_signal_opensearch_process_cluster(
    ops_test: OpsTest, ops_test_microk8s: OpsTest, signal
):
    """Signals Opensearch leader process and checks recovery + re-election."""
    db_units = [unit.name for unit in ops_test.model.applications[OPENSEARCH_APP_NAME].units]
    await _recover_from_signal(
        ops_test, ops_test_microk8s, signal, db_units, app_name=OPENSEARCH_APP_NAME
    )


@pytest.mark.skip(reason="Opensearch is not possible to contact after recovery")
@pytest.mark.abort_on_fail
async def test_sigstop_opensearch_process_cluster(ops_test: OpsTest, ops_test_microk8s: OpsTest):
    """Signals Opensearch leader process and checks recovery + re-election."""
    db_units = [unit.name for unit in ops_test.model.applications[OPENSEARCH_APP_NAME].units]
    await _recover_from_signal(
        ops_test, ops_test_microk8s, "SIGSTOP", db_units, app_name=OPENSEARCH_APP_NAME
    )


@pytest.mark.abort_on_fail
@pytest.mark.parametrize("signal", ["SIGKILL", "SIGTERM", "SIGSTOP"])
async def test_signal_dashboard_process_cluster(
    ops_test: OpsTest, ops_test_microk8s: OpsTest, signal
):
    """Signals OSD leader process and checks recovery + re-election."""
    app_name = APP_NAME
    if ops_test.model.name != ops_test_microk8s.model.name:
        app_name = APP_NAME_K8S
    units = [unit.name for unit in ops_test.model.applications[app_name].units]
    await _recover_from_signal(ops_test, ops_test_microk8s, signal, units)


##############################################################################


@pytest.mark.abort_on_fail
async def test_set_tls(ops_test: OpsTest, ops_test_microk8s: OpsTest):
    """Not a real test but a separate stage to start TLS testing"""
    logger.info("Initializing TLS Charm connections")
    app_name = APP_NAME
    if ops_test.model.name != ops_test_microk8s.model.name:
        app_name = APP_NAME_K8S
        await ops_test.model.create_offer(
            "certificates", TLS_CERT_APP_NAME, "self-signed-certificates"
        )
        await ops_test_microk8s.model.consume(f"admin/{ops_test.model_name}.{TLS_CERT_APP_NAME}")
        await ops_test_microk8s.model.integrate(
            f"{TLS_CERT_APP_NAME}:certificates", TLS_CERT_APP_NAME
        )

    await ops_test_microk8s.model.integrate(app_name, TLS_CERT_APP_NAME)

    await ops_test.model.wait_for_idle(
        apps=[TLS_CERT_APP_NAME], wait_for_active=True, timeout=LONG_TIMEOUT
    )
    await ops_test_microk8s.model.wait_for_idle(
        apps=[app_name], wait_for_active=True, timeout=LONG_TIMEOUT
    )

    logger.info("Checking Dashboard access after TLS is configured")
    assert await access_all_dashboards(ops_test, ops_test_microk8s, https=True, verify=True)


##############################################################################


@pytest.mark.abort_on_fail
@pytest.mark.parametrize("signal", ["SIGKILL", "SIGTERM"])
async def test_signal_opensearch_process_leader_https(
    ops_test: OpsTest, ops_test_microk8s: OpsTest, signal
):
    """Signals OSD leader process and checks recovery + re-election."""
    db_leader_name = await get_leader_name(ops_test, app_name=OPENSEARCH_APP_NAME)
    await _recover_from_signal(
        ops_test,
        ops_test_microk8s,
        signal,
        [db_leader_name],
        app_name=OPENSEARCH_APP_NAME,
        https=True,
        verify=True,
    )


@pytest.mark.skip(reason="Opensearch is not possible to contact after recovery")
@pytest.mark.abort_on_fail
async def test_sigstop_opensearch_process_leader_https(
    ops_test: OpsTest, ops_test_microk8s: OpsTest
):
    """Signals Opensearch leader process and checks recovery + re-election."""
    db_leader_name = await get_leader_name(ops_test, app_name=OPENSEARCH_APP_NAME)
    await _recover_from_signal(
        ops_test,
        ops_test_microk8s,
        "SIGSTOP",
        [db_leader_name],
        app_name=OPENSEARCH_APP_NAME,
        https=True,
        verify=True,
    )


@pytest.mark.abort_on_fail
@pytest.mark.parametrize("signal", ["SIGKILL", "SIGTERM", "SIGSTOP"])
async def test_signal_dashboard_process_leader_https(
    ops_test: OpsTest, ops_test_microk8s: OpsTest, signal
):
    """Signals OSD leader process and checks recovery + re-election."""
    app_name = APP_NAME
    if ops_test.model.name != ops_test_microk8s.model.name:
        app_name = APP_NAME_K8S
    leader_name = await get_leader_name(ops_test, app_name)
    await _recover_from_signal(
        ops_test, ops_test_microk8s, signal, [leader_name], https=True, verify=True
    )


@pytest.mark.abort_on_fail
@pytest.mark.parametrize("signal", ["SIGKILL", "SIGTERM"])
async def test_signal_opensearch_process_cluster_https(
    ops_test: OpsTest, ops_test_microk8s: OpsTest, signal
):
    """Signals Opensearch leader process and checks recovery + re-election."""
    db_units = [unit.name for unit in ops_test.model.applications[OPENSEARCH_APP_NAME].units]
    await _recover_from_signal(
        ops_test,
        ops_test_microk8s,
        signal,
        db_units,
        app_name=OPENSEARCH_APP_NAME,
        https=True,
        verify=True,
    )


@pytest.mark.skip(reason="Opensearch is not possible to contact after recovery")
@pytest.mark.abort_on_fail
async def test_sigstop_opensearch_process_cluster_https(
    ops_test: OpsTest, ops_test_microk8s: OpsTest
):
    """Signals Opensearch leader process and checks recovery + re-election."""
    db_units = [unit.name for unit in ops_test.model.applications[OPENSEARCH_APP_NAME].units]
    await _recover_from_signal(
        ops_test, ops_test_microk8s, "SIGSTOP", db_units, https=True, verify=True
    )


@pytest.mark.abort_on_fail
@pytest.mark.parametrize("signal", ["SIGKILL", "SIGTERM", "SIGSTOP"])
async def test_signal_dashboard_process_cluster_https(
    ops_test: OpsTest, ops_test_microk8s: OpsTest, signal
):
    """Signals OSD leader process and checks recovery + re-election."""
    app_name = APP_NAME
    if ops_test.model.name != ops_test_microk8s.model.name:
        app_name = APP_NAME_K8S
    units = [unit.name for unit in ops_test.model.applications[app_name].units]
    await _recover_from_signal(ops_test, ops_test_microk8s, signal, units, https=True, verify=True)
