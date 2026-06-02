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

from tests.integration.conftest import Flags, test_flags

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
HANGING_TIMEOUT = 90

METADATA_VM = yaml.safe_load(Path("tests/charms/vm/metadata.yaml").read_text())
METADATA_K8S = yaml.safe_load(Path("tests/charms/k8s/metadata.yaml").read_text())
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

K8s_CONFIG = {
    "logging-config": "<root>=INFO;unit=DEBUG",
    "update-status-hook-interval": f"{UPDATE_STATUS_INTERVAL}s",
}

OPENSEARCH_RELATION_NAME = "opensearch-client"
TLS_CERT_APP_NAME = "self-signed-certificates"
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
async def restart_delay(ops_test: OpsTest, ops_test_microk8s: OpsTest, substrate: str):
    if substrate == "k8s":
        return
    for unit in ops_test.model.applications[METADATA_VM["name"]].units:
        await patch_restart_delay(ops_test=ops_test, unit_name=unit.name, delay=RESTART_DELAY)
    yield
    for unit in ops_test.model.applications[METADATA_VM["name"]].units:
        await remove_restart_delay(ops_test=ops_test, unit_name=unit.name)


@pytest.mark.skip_if_deployed
@pytest.mark.abort_on_fail
async def test_build_and_deploy(
    ops_test: OpsTest,
    ops_test_microk8s: OpsTest,
    charmvm: str,
    charmk8s: str,
    charm_base: str,
    substrate: str,
    test_flags: Flags,
):
    """Tests that the charm deploys safely"""
    app_name = METADATA_K8S["name"] if substrate == "k8s" else METADATA_VM["name"]
    tls = test_flags.tls
    if substrate == "k8s":
        await ops_test_microk8s.model.set_config(K8s_CONFIG)
        await ops_test_microk8s.model.deploy(
            charmk8s,
            application_name=app_name,
            num_units=NUM_UNITS_APP,
            base=charm_base,
            resources=RESOURCE,
        )

    else:
        await ops_test_microk8s.model.deploy(
            charmvm, application_name=app_name, num_units=NUM_UNITS_APP, base=charm_base
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

    if substrate == "k8s":
        await ops_test.model.create_offer("opensearch-client", OPENSEARCH_APP_NAME, "opensearch")
        await ops_test_microk8s.model.consume(f"admin/{ops_test.model.name}.{OPENSEARCH_APP_NAME}")
        await ops_test_microk8s.model.deploy(TRAEFIK_APP_NAME, channel="latest/stable", trust=True)
        await ops_test_microk8s.model.wait_for_idle(
            apps=[app_name], status="blocked", timeout=1000
        )

    pytest.relation = await ops_test_microk8s.model.integrate(OPENSEARCH_APP_NAME, app_name)
    if substrate == "k8s":
        await ops_test_microk8s.model.integrate(app_name, TRAEFIK_APP_NAME)
    await ops_test_microk8s.model.wait_for_idle(apps=[app_name], status="active", timeout=1000)
    await ops_test.model.wait_for_idle(
        apps=[OPENSEARCH_APP_NAME], wait_for_active=True, timeout=1000
    )

    if tls:
        logger.info("Initializing TLS Charm connections")
        if substrate == "k8s":
            await ops_test.model.create_offer(
                "certificates", TLS_CERT_APP_NAME, "self-signed-certificates"
            )
            await ops_test_microk8s.model.consume(
                f"admin/{ops_test.model_name}.{TLS_CERT_APP_NAME}"
            )
            await ops_test_microk8s.model.integrate(
                f"{TLS_CERT_APP_NAME}:certificates", TRAEFIK_APP_NAME
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
# Helper functions
##############################################################################


async def _recover_from_signal(
    ops_test: OpsTest,
    ops_test_microk8s: OpsTest,
    signal: str,
    units: list[str],
    app_name: str,
    substrate: str,
    test_flags: Flags,
    pid: bool = False,
    https: bool = False,
    verify: bool = False,
):
    dash_name = METADATA_K8S["name"] if substrate == "k8s" else METADATA_VM["name"]
    container = ""
    if app_name == dash_name and substrate == "k8s":
        container = "opensearch-dashboards"
    pid_list = {}
    if substrate == "k8s" and signal != "SIGSTOP" and pid:
        for unit in units:
            pid_list[unit] = await get_service_pid(ops_test_microk8s, unit)

    # In attempt to prevent flaky behavior
    # The process is restarted so fast, slow pipelines may not "catch" it in time
    for attempt in Retrying(stop=stop_after_attempt(3), wait=wait_fixed(5), reraise=True):
        with attempt:
            logger.info(f"Sending {signal} {app_name}:{units}...")
            await asyncio.gather(
                *[
                    send_control_signal(
                        ops_test_microk8s, unit, signal, app_name, True if container else False
                    )
                    for unit in units
                ]
            )

            if substrate == "k8s" and signal != "SIGSTOP" and pid:
                logger.info(f"Asserting {app_name}:{units} service pid is changed")
                for unit in units:
                    current_pid = await get_service_pid(ops_test_microk8s, unit)
                    assert current_pid != pid_list[unit]
            else:
                # Check that process is down
                logger.info(f"Waiting for {app_name}:{units} to be down...")
                assert all(
                    await asyncio.gather(*[is_down(ops_test, unit, app_name) for unit in units])
                )

    logger.info("Waiting a bit, so the process could safely restart...")
    await asyncio.sleep(UPDATE_STATUS_INTERVAL + 2)
    if signal == "SIGSTOP":
        await asyncio.sleep(HANGING_TIMEOUT + 2)
    await ops_test.model.wait_for_idle(
        apps=[OPENSEARCH_APP_NAME], wait_for_active=True, timeout=1000
    )
    if app_name == dash_name:
        await ops_test_microk8s.model.wait_for_idle(
            apps=[app_name], wait_for_active=True, timeout=1000
        )

    logger.info("Checking OSD access...")
    assert await access_all_dashboards(ops_test, ops_test_microk8s, https, verify=verify)


##############################################################################
# Tests
##############################################################################


@pytest.mark.abort_on_fail
@pytest.mark.parametrize("signal", ["SIGKILL", "SIGTERM"])
async def test_signal_opensearch_process_leader_https(
    ops_test: OpsTest,
    ops_test_microk8s: OpsTest,
    signal,
    substrate: str,
    test_flags: Flags,
):
    """Signals OSD leader process and checks recovery + re-election."""
    db_leader_name = await get_leader_name(ops_test, app_name=OPENSEARCH_APP_NAME)
    await _recover_from_signal(
        ops_test=ops_test,
        ops_test_microk8s=ops_test_microk8s,
        signal=signal,
        units=[db_leader_name],
        app_name=OPENSEARCH_APP_NAME,
        https=test_flags.tls,
        verify=test_flags.tls,
        substrate=substrate,
        test_flags=test_flags,
    )


@pytest.mark.skip(reason="Opensearch is not possible to contact after recovery")
@pytest.mark.abort_on_fail
async def test_sigstop_opensearch_process_leader_https(
    ops_test: OpsTest,
    ops_test_microk8s: OpsTest,
    substrate: str,
    test_flags: Flags,
):
    """Signals Opensearch leader process and checks recovery + re-election."""
    db_leader_name = await get_leader_name(ops_test, app_name=OPENSEARCH_APP_NAME)
    await _recover_from_signal(
        ops_test=ops_test,
        ops_test_microk8s=ops_test_microk8s,
        signal="SIGSTOP",
        units=[db_leader_name],
        app_name=OPENSEARCH_APP_NAME,
        https=test_flags.tls,
        verify=test_flags.tls,
        substrate=substrate,
        test_flags=test_flags,
    )


@pytest.mark.abort_on_fail
@pytest.mark.parametrize("signal", ["SIGKILL", "SIGTERM", "SIGSTOP"])
async def test_signal_dashboard_process_leader_https(
    ops_test: OpsTest,
    ops_test_microk8s: OpsTest,
    signal,
    substrate: str,
    test_flags: Flags,
):
    """Signals OSD leader process and checks recovery + re-election."""
    app_name = METADATA_K8S["name"] if substrate == "k8s" else METADATA_VM["name"]
    leader_name = await get_leader_name(ops_test_microk8s, app_name)
    await _recover_from_signal(
        ops_test=ops_test,
        ops_test_microk8s=ops_test_microk8s,
        signal=signal,
        units=[leader_name],
        app_name=app_name,
        https=test_flags.tls,
        verify=test_flags.tls,
        substrate=substrate,
        test_flags=test_flags,
    )


@pytest.mark.abort_on_fail
@pytest.mark.parametrize("signal", ["SIGKILL", "SIGTERM"])
async def test_signal_opensearch_process_cluster_https(
    ops_test: OpsTest,
    ops_test_microk8s: OpsTest,
    signal,
    substrate: str,
    test_flags: Flags,
):
    """Signals Opensearch leader process and checks recovery + re-election."""
    db_units = [unit.name for unit in ops_test.model.applications[OPENSEARCH_APP_NAME].units]
    await _recover_from_signal(
        ops_test=ops_test,
        ops_test_microk8s=ops_test_microk8s,
        signal=signal,
        units=db_units,
        app_name=OPENSEARCH_APP_NAME,
        https=test_flags.tls,
        verify=test_flags.tls,
        substrate=substrate,
        test_flags=test_flags,
    )


@pytest.mark.skip(reason="Opensearch is not possible to contact after recovery")
@pytest.mark.abort_on_fail
async def test_sigstop_opensearch_process_cluster_https(
    ops_test: OpsTest,
    ops_test_microk8s: OpsTest,
    substrate: str,
    test_flags: Flags,
):
    """Signals Opensearch leader process and checks recovery + re-election."""
    db_units = [unit.name for unit in ops_test.model.applications[OPENSEARCH_APP_NAME].units]
    app_name = METADATA_K8S["name"] if substrate == "k8s" else METADATA_VM["name"]
    await _recover_from_signal(
        ops_test=ops_test,
        ops_test_microk8s=ops_test_microk8s,
        signal="SIGSTOP",
        units=db_units,
        app_name=app_name,
        https=test_flags.tls,
        verify=test_flags.tls,
        substrate=substrate,
        test_flags=test_flags,
    )


@pytest.mark.abort_on_fail
@pytest.mark.parametrize("signal", ["SIGKILL", "SIGTERM", "SIGSTOP"])
async def test_signal_dashboard_process_cluster_https(
    ops_test: OpsTest,
    ops_test_microk8s: OpsTest,
    signal,
    substrate: str,
    test_flags: Flags,
):
    """Signals OSD leader process and checks recovery + re-election."""
    app_name = METADATA_K8S["name"] if substrate == "k8s" else METADATA_VM["name"]
    units = [unit.name for unit in ops_test_microk8s.model.applications[app_name].units]
    await _recover_from_signal(
        ops_test=ops_test,
        ops_test_microk8s=ops_test_microk8s,
        signal=signal,
        units=units,
        app_name=app_name,
        https=test_flags.tls,
        verify=test_flags.tls,
        substrate=substrate,
        test_flags=test_flags,
    )
