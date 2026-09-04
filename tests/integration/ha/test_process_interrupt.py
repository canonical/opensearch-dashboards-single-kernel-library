#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging

import pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_attempt, wait_fixed

from tests.integration.conftest import Flags, test_flags

from ..helpers import (
    APP_NAME,
    OPENSEARCH_APP_NAME,
    TLS_CERTIFICATES_APP_NAME,
    TRAEFIK_APP_NAME,
    access_all_dashboards,
    deploy_opensearch_and_dashboards,
    get_leader_name,
    wait_for_ingress_blocked,
)
from .helpers import (
    LONG_TIMEOUT,
    get_service_pid,
    is_down,
    patch_restart_delay,
    remove_restart_delay,
    send_control_signal,
)

logger = logging.getLogger(__name__)

RESTART_DELAY = 60
UPDATE_STATUS_INTERVAL = 60
HANGING_TIMEOUT = 90

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

NUM_UNITS_APP = 2
NUM_UNITS_DB = 3


@pytest.fixture()
async def restart_delay(ops_test: OpsTest, substrate: str):
    if substrate == "k8s":
        yield
        return
    for unit in ops_test.model.applications[APP_NAME].units:
        await patch_restart_delay(ops_test=ops_test, unit_name=unit.name, delay=RESTART_DELAY)
    yield
    for unit in ops_test.model.applications[APP_NAME].units:
        await remove_restart_delay(ops_test=ops_test, unit_name=unit.name)


@pytest.mark.skip_if_deployed
@pytest.mark.abort_on_fail
async def test_build_and_deploy(
    ops_test: OpsTest,
    substrate: str,
    test_flags: Flags,
    charm: str,
    charm_base: str,
    opensearch_deploy_args: tuple[str, bool],
):
    """Tests that the charm deploys safely"""
    tls = test_flags.test_tls
    if substrate == "k8s":
        await ops_test.model.set_config(K8s_CONFIG)

    app_name = await deploy_opensearch_and_dashboards(
        ops_test,
        charm,
        charm_base,
        substrate,
        opensearch_deploy_args,
        num_units_app=NUM_UNITS_APP,
        num_units_db=NUM_UNITS_DB,
        opensearch_config=OPENSEARCH_CONFIG,
    )

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[app_name],
            wait_for_exact_units=NUM_UNITS_APP,
            timeout=1000,
            idle_period=30,
        )

    if substrate == "k8s":
        assert ops_test.model.applications[app_name].status == "blocked"
        await ops_test.model.deploy(TRAEFIK_APP_NAME, channel="latest/stable", trust=True)
        await wait_for_ingress_blocked(ops_test, app_name, timeout=1000)
        await ops_test.model.integrate(app_name, TRAEFIK_APP_NAME)
    else:
        assert ops_test.model.applications[app_name].status == "active"
    await ops_test.model.wait_for_idle(apps=[app_name], status="active", timeout=1000)
    await ops_test.model.wait_for_idle(
        apps=[OPENSEARCH_APP_NAME], wait_for_active=True, timeout=1000
    )

    if tls:
        logger.info("Initializing TLS Charm connections")
        if substrate == "k8s":
            await ops_test.model.integrate(
                f"{TLS_CERTIFICATES_APP_NAME}:certificates", TRAEFIK_APP_NAME
            )

        await ops_test.model.integrate(app_name, TLS_CERTIFICATES_APP_NAME)

        await ops_test.model.wait_for_idle(
            apps=[TLS_CERTIFICATES_APP_NAME], wait_for_active=True, timeout=LONG_TIMEOUT
        )
        await ops_test.model.wait_for_idle(
            apps=[app_name], wait_for_active=True, timeout=LONG_TIMEOUT
        )

        logger.info("Checking Dashboard access after TLS is configured")
        assert await access_all_dashboards(ops_test, https=True, verify=True)


##############################################################################
# Helper functions
##############################################################################


async def _recover_from_signal(
    ops_test: OpsTest,
    signal: str,
    units: list[str],
    app_name: str,
    substrate: str,
    pid: bool = False,
    https: bool = False,
    verify: bool = False,
):
    is_dashboards = app_name == APP_NAME
    container = ""
    if is_dashboards and substrate == "k8s":
        container = "opensearch-dashboards"
    pid_list = {}
    if substrate == "k8s" and signal != "SIGSTOP" and pid:
        for unit in units:
            pid_list[unit] = await get_service_pid(ops_test, unit)

    # In attempt to prevent flaky behavior
    # The process is restarted so fast, slow pipelines may not "catch" it in time
    for attempt in Retrying(stop=stop_after_attempt(3), wait=wait_fixed(5), reraise=True):
        with attempt:
            logger.info(f"Sending {signal} {app_name}:{units}...")
            await asyncio.gather(
                *[
                    send_control_signal(
                        ops_test, unit, signal, app_name, True if container else False
                    )
                    for unit in units
                ]
            )

            if substrate == "k8s" and signal != "SIGSTOP" and pid:
                logger.info(f"Asserting {app_name}:{units} service pid is changed")
                for unit in units:
                    current_pid = await get_service_pid(ops_test, unit)
                    assert current_pid != pid_list[unit]
            else:
                # Check that process is down
                logger.info(f"Waiting for {app_name}:{units} to be down...")
                assert all(
                    await asyncio.gather(*[is_down(ops_test, unit, app_name) for unit in units])
                )

    # Opensearch does not restart a SIGSTOP-frozen process,
    # so it never recovers on its own.
    # un-freeze it and let the system recover, so we can verify
    # the dashboards reconnect once the node is responsive again.
    if signal == "SIGSTOP" and not is_dashboards:
        logger.info(f"Holding {app_name}:{units} frozen so the outage is registered...")
        await asyncio.sleep(UPDATE_STATUS_INTERVAL + 2)
        logger.info(f"Sending SIGCONT to un-freeze {app_name}:{units}...")
        await asyncio.gather(
            *[
                send_control_signal(
                    ops_test, unit, "SIGCONT", app_name, True if container else False
                )
                for unit in units
            ]
        )

    logger.info("Waiting a bit, so the process could safely restart...")
    await asyncio.sleep(UPDATE_STATUS_INTERVAL + 2)
    if signal == "SIGSTOP":
        await asyncio.sleep(HANGING_TIMEOUT + 2)
    await ops_test.model.wait_for_idle(
        apps=[OPENSEARCH_APP_NAME], wait_for_active=True, timeout=1000
    )
    # Always wait for dashboards to reconnect, not just when we were testing dashboards directly.
    await ops_test.model.wait_for_idle(apps=[APP_NAME], wait_for_active=True, timeout=1000)

    logger.info("Checking OSD access...")
    assert await access_all_dashboards(ops_test, https, verify=verify)


##############################################################################
# Tests
##############################################################################


@pytest.mark.abort_on_fail
@pytest.mark.parametrize("signal", ["SIGKILL", "SIGTERM"])
async def test_signal_opensearch_process_leader_https(
    ops_test: OpsTest,
    signal,
    substrate: str,
    test_flags: Flags,
):
    """Signals OSD leader process and checks recovery + re-election."""
    db_leader_name = await get_leader_name(ops_test, app_name=OPENSEARCH_APP_NAME)
    await _recover_from_signal(
        ops_test=ops_test,
        signal=signal,
        units=[db_leader_name],
        app_name=OPENSEARCH_APP_NAME,
        https=test_flags.test_tls,
        verify=test_flags.test_tls,
        substrate=substrate,
    )


@pytest.mark.abort_on_fail
async def test_sigstop_opensearch_process_leader(
    ops_test: OpsTest,
    substrate: str,
    test_flags: Flags,
):
    """Freezes the Opensearch leader with SIGSTOP, un-freezes it, and checks recovery."""
    db_leader_name = await get_leader_name(ops_test, app_name=OPENSEARCH_APP_NAME)
    await _recover_from_signal(
        ops_test=ops_test,
        signal="SIGSTOP",
        units=[db_leader_name],
        app_name=OPENSEARCH_APP_NAME,
        https=test_flags.test_tls,
        verify=test_flags.test_tls,
        substrate=substrate,
    )


@pytest.mark.abort_on_fail
@pytest.mark.parametrize("signal", ["SIGKILL", "SIGTERM", "SIGSTOP"])
async def test_signal_dashboard_process_leader(
    ops_test: OpsTest,
    signal,
    substrate: str,
    test_flags: Flags,
    restart_delay,
):
    """Signals OSD leader process and checks recovery + re-election."""
    leader_name = await get_leader_name(ops_test, APP_NAME)
    await _recover_from_signal(
        ops_test=ops_test,
        signal=signal,
        units=[leader_name],
        app_name=APP_NAME,
        https=test_flags.test_tls,
        verify=test_flags.test_tls,
        substrate=substrate,
    )


@pytest.mark.abort_on_fail
@pytest.mark.parametrize("signal", ["SIGKILL", "SIGTERM"])
async def test_signal_opensearch_process_cluster(
    ops_test: OpsTest,
    signal,
    substrate: str,
    test_flags: Flags,
):
    """Signals Opensearch leader process and checks recovery + re-election."""
    db_units = [unit.name for unit in ops_test.model.applications[OPENSEARCH_APP_NAME].units]
    await _recover_from_signal(
        ops_test=ops_test,
        signal=signal,
        units=db_units,
        app_name=OPENSEARCH_APP_NAME,
        https=test_flags.test_tls,
        verify=test_flags.test_tls,
        substrate=substrate,
    )


@pytest.mark.abort_on_fail
async def test_sigstop_opensearch_process_cluster(
    ops_test: OpsTest,
    substrate: str,
    test_flags: Flags,
):
    """Freezes the whole Opensearch cluster with SIGSTOP, un-freezes it, and checks recovery."""
    db_units = [unit.name for unit in ops_test.model.applications[OPENSEARCH_APP_NAME].units]
    await _recover_from_signal(
        ops_test=ops_test,
        signal="SIGSTOP",
        units=db_units,
        app_name=OPENSEARCH_APP_NAME,
        https=test_flags.test_tls,
        verify=test_flags.test_tls,
        substrate=substrate,
    )


@pytest.mark.abort_on_fail
@pytest.mark.parametrize("signal", ["SIGKILL", "SIGTERM", "SIGSTOP"])
async def test_signal_dashboard_process_cluster(
    ops_test: OpsTest,
    signal,
    substrate: str,
    test_flags: Flags,
    restart_delay,
):
    """Signals OSD leader process and checks recovery + re-election."""
    units = [unit.name for unit in ops_test.model.applications[APP_NAME].units]
    await _recover_from_signal(
        ops_test=ops_test,
        signal=signal,
        units=units,
        app_name=APP_NAME,
        https=test_flags.test_tls,
        verify=test_flags.test_tls,
        substrate=substrate,
    )
