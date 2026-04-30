#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
import asyncio
import logging
import os
import subprocess
import tempfile
import textwrap
from pathlib import Path
from subprocess import CalledProcessError

import pytest
import yaml
from lightkube import Client
from lightkube.models.meta_v1 import LabelSelector, ObjectMeta
from lightkube.models.networking_v1 import NetworkPolicySpec
from lightkube.resources.core_v1 import Pod
from lightkube.resources.networking_v1 import NetworkPolicy
from pytest_operator.plugin import OpsTest

import tests.integration.ha.helpers as ha_helpers
from tests.integration.helpers import (
    CONFIG_OPTS,
    TLS_STABLE_CHANNEL,
    access_all_dashboards,
    all_dashboards_unavailable,
    get_address,
    get_leader_name,
)

logger = logging.getLogger(__name__)


CLIENT_TIMEOUT = 10
RESTART_DELAY = 60

METADATA_VM = yaml.safe_load(Path("tests/charms/vm/metadata.yaml").read_text())
METADATA_K8S = yaml.safe_load(Path("tests/charms/k8s/metadata.yaml").read_text())
APP_NAME = METADATA_VM["name"]
APP_NAME_K8S = METADATA_K8S["name"]

OPENSEARCH_APP_NAME = "opensearch"
OPENSEARCH_CONFIG = {
    "logging-config": "<root>=INFO;unit=DEBUG",
    "cloudinit-userdata": """postruncmd:
        - [ 'sysctl', '-w', 'vm.max_map_count=262144' ]
        - [ 'sysctl', '-w', 'fs.file-max=1048576' ]
        - [ 'sysctl', '-w', 'vm.swappiness=0' ]
        - [ 'sysctl', '-w', 'net.ipv4.tcp_retries2=5' ]
    """,
}
TLS_CERT_APP_NAME = "self-signed-certificates"

ALL_APPS = [APP_NAME, TLS_CERT_APP_NAME, OPENSEARCH_APP_NAME]
APP_AND_TLS = [APP_NAME, TLS_CERT_APP_NAME]
PEER = "dashboard_peers"
SERVER_PORT = 5601

NUM_UNITS_APP = 2
NUM_UNITS_DB = 2

LONG_TIMEOUT = 3000
LONG_WAIT = 30

RESOURCE = {
    "opensearch-dashboards-image": "ghcr.io/canonical/charmed-opensearch-dashboards:2.19.4-24.04-edge"
}


@pytest.fixture(scope="module", autouse=True)
async def chaos_mesh(ops_test: OpsTest, ops_test_microk8s: OpsTest):
    if ops_test.model.name == ops_test_microk8s.model.name:
        yield
        return

    env = os.environ.copy()
    subprocess.check_call(
        " ".join(
            [
                "tests/integration/scripts/install_chaos_mesh.sh",
                ops_test_microk8s.model.info.name,
            ]
        ),
        shell=True,
        env=env,
    )

    yield

    subprocess.check_call(
        " ".join(
            [
                "tests/integration/scripts/destroy_chaos_mesh.sh",
                ops_test_microk8s.model.info.name,
            ]
        ),
        shell=True,
        env=env,
    )


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
        OPENSEARCH_APP_NAME,
        channel="2/edge",
        num_units=NUM_UNITS_DB,
        config=CONFIG_OPTS,
    )

    config = {"ca-common-name": "CN_CA"}
    await ops_test.model.deploy(TLS_CERT_APP_NAME, channel=TLS_STABLE_CHANNEL, config=config)

    # Relate it to OpenSearch to set up TLS.
    await ops_test.model.integrate(OPENSEARCH_APP_NAME, TLS_CERT_APP_NAME)
    await ops_test.model.wait_for_idle(
        apps=[OPENSEARCH_APP_NAME, TLS_CERT_APP_NAME],
        wait_for_active=True,
        timeout=LONG_TIMEOUT,
    )

    # Opensearch Dashboards
    async with ops_test_microk8s.fast_forward():
        await ops_test_microk8s.model.wait_for_idle(
            apps=[app_name],
            wait_for_exact_units=NUM_UNITS_APP,
            timeout=LONG_TIMEOUT,
            idle_period=30,
        )

    assert ops_test_microk8s.model.applications[app_name].status == "blocked"

    if is_cross_model:
        await ops_test.model.create_offer("opensearch-client", OPENSEARCH_APP_NAME, "opensearch")
        await ops_test_microk8s.model.consume(f"admin/{ops_test.model.name}.{OPENSEARCH_APP_NAME}")

    pytest.relation = await ops_test_microk8s.model.integrate(OPENSEARCH_APP_NAME, app_name)
    await ops_test.model.wait_for_idle(
        apps=[OPENSEARCH_APP_NAME], wait_for_active=True, timeout=1000
    )

    await ops_test_microk8s.model.wait_for_idle(
        apps=[app_name], wait_for_active=True, timeout=1000
    )


##############################################################################
# Helper functions
##############################################################################


async def network_cut_leader(ops_test: OpsTest, ops_test_microk8s: OpsTest, https: bool = False):
    """Full network cut for the leader, resulting in IP change."""
    is_cross_model = ops_test.model.name != ops_test_microk8s.model.name
    app_name = APP_NAME
    if is_cross_model:
        app_name = APP_NAME_K8S

    old_leader_name = await get_leader_name(ops_test_microk8s, app_name)
    old_ip = await get_address(ops_test_microk8s, old_leader_name, app_name)

    if is_cross_model:
        # We can't simulate network cut for k8s pod same way as the VM
        # so we instead test how charm will react if pod is deleted
        # which should result in ip change
        pod_name = old_leader_name.replace("/", "-")
        namespace = ops_test_microk8s.model.info.name

        logger.info(f"Network throttle on {old_leader_name} (Pod: {pod_name})...")
        ha_helpers.network_cut_k8s(pod_name, namespace)

        lease_timeout = 10
        logger.info(f"Waiting {lease_timeout}s for Juju to know pod has expired...")
        await asyncio.sleep(lease_timeout)

        logger.info("Waiting for stabilize")
        await ops_test_microk8s.model.wait_for_idle(apps=[app_name], status="active", timeout=1000)

    else:
        # VM
        machine_name = await ha_helpers.get_unit_machine_name(ops_test, old_leader_name)
        logger.info(
            f"Cutting leader unit from network from {old_leader_name} ({machine_name}/{old_ip})..."
        )
        ha_helpers.cut_unit_network(machine_name)

        logger.info(f"Waiting until unit {old_leader_name} is not reachable")
        await ops_test.model.block_until(
            lambda: not ha_helpers.reachable(old_ip, SERVER_PORT),
            timeout=LONG_TIMEOUT,
            wait_period=LONG_WAIT,
        )

        logger.info(f"Waiting until unit {old_leader_name} is 'lost'")
        await ops_test.model.block_until(
            lambda: ["unknown", "lost"]
            == ha_helpers.get_unit_state_from_status(ops_test_microk8s, old_leader_name, app_name),
            timeout=LONG_TIMEOUT,
            wait_period=LONG_WAIT,
        )

        logger.info("Waiting for stabilize")
        await ops_test_microk8s.model.wait_for_idle(apps=[app_name], status="active", timeout=1000)

        logger.info("Checking new leader was elected")
        new_leader_name = await get_leader_name(ops_test_microk8s, app_name)
        assert new_leader_name != old_leader_name

        # Check all nodes but the old leader
        logger.info("Checking Dashboard access for the rest of the nodes...")
        assert await access_all_dashboards(
            ops_test, ops_test_microk8s, skip=[old_leader_name], https=https
        )

        logger.info(f"Restoring network for {old_leader_name}...")
        try:
            ha_helpers.restore_unit_network(machine_name)
        except CalledProcessError:  # in case it was already cleaned up
            pass

    logger.info("Waiting for Juju to detect new IP...")
    await ops_test_microk8s.model.block_until(
        lambda: old_ip
        not in ha_helpers.get_hosts_from_status(ops_test_microk8s, app_name).values(),
        timeout=LONG_TIMEOUT,
        wait_period=LONG_WAIT,
    )

    new_ip = await get_address(ops_test_microk8s, old_leader_name, app_name)
    assert new_ip != old_ip
    logger.info(f"Old IP {old_ip} has changed to {new_ip}...")

    await ops_test.model.wait_for_idle(
        apps=[TLS_CERT_APP_NAME, OPENSEARCH_APP_NAME], wait_for_active=True, timeout=LONG_TIMEOUT
    )
    await ops_test_microk8s.model.wait_for_idle(
        apps=[app_name], wait_for_active=True, timeout=LONG_TIMEOUT
    )

    logger.info("Checking Dashboard access...")
    assert await access_all_dashboards(ops_test, ops_test_microk8s, https=https)


async def network_throttle_leader(
    ops_test: OpsTest, ops_test_microk8s: OpsTest, https: bool = False
):
    """Network interrupt for the leader without IP change."""
    is_cross_model = ops_test.model.name != ops_test_microk8s.model.name
    app_name = APP_NAME
    if is_cross_model:
        app_name = APP_NAME_K8S

    old_leader_name = await get_leader_name(ops_test_microk8s, app_name)
    old_ip = await get_address(ops_test_microk8s, old_leader_name, app_name)

    if is_cross_model:
        pod_name = old_leader_name.replace("/", "-")
        namespace = ops_test_microk8s.model.info.name

        logger.info(f"Network throttle on {old_leader_name} (Pod: {pod_name})...")
        ha_helpers.network_throttle_k8s(pod_name, namespace)

        lease_timeout = 65
        logger.info(f"Waiting {lease_timeout}s for Juju leadership lease to expire...")
        await asyncio.sleep(lease_timeout)

    else:
        logger.info("Network throttle on {old_leader_name}...")
        machine_name = await ha_helpers.get_unit_machine_name(ops_test_microk8s, old_leader_name)
        ha_helpers.network_throttle(machine_name)

    logger.info(f"Waiting until unit {old_leader_name} is not reachable")
    await ops_test_microk8s.model.block_until(
        lambda: not ha_helpers.reachable(old_ip, SERVER_PORT),
        timeout=LONG_TIMEOUT,
        wait_period=LONG_WAIT,
    )

    logger.info(f"Waiting until unit {old_leader_name} is 'lost'")
    await ops_test_microk8s.model.block_until(
        lambda: ["unknown", "lost"]
        == ha_helpers.get_unit_state_from_status(
            ops_test_microk8s, old_leader_name, app_name=app_name
        ),
        timeout=LONG_TIMEOUT,
        wait_period=LONG_WAIT,
    )

    logger.info("Checking leader re-election...")
    new_leader_name = await get_leader_name(ops_test_microk8s, app_name)
    assert new_leader_name != old_leader_name

    logger.info("Checking Dashboard access for the rest of the nodes...")
    assert await access_all_dashboards(
        ops_test, ops_test_microk8s, skip=[old_leader_name], https=https
    )

    logger.info("Restoring network...")
    try:
        if is_cross_model:
            logger.info(
                f"Removing NetworkChaos to restore network to the leader (Pod: {pod_name})"
            )
            ha_helpers.network_restore_throttle_k8s(pod_name, namespace)
        else:
            logger.info(f"Releasing network throttle on LXC container {machine_name}")
            ha_helpers.network_release(machine_name)
    except Exception as e:
        logger.warning(f"Could not restore network: {e}")

    logger.info(f"Waiting until unit {old_leader_name} is reachable again")
    await ops_test_microk8s.model.block_until(
        lambda: ha_helpers.reachable(old_ip, SERVER_PORT),
        timeout=LONG_TIMEOUT,
        wait_period=LONG_WAIT,
    )

    # Double-checking that the network throttle didn't change the IP
    current_ip = await get_address(ops_test_microk8s, old_leader_name, app_name)
    assert old_ip == current_ip

    await ops_test.model.wait_for_idle(
        apps=[TLS_CERT_APP_NAME, OPENSEARCH_APP_NAME], wait_for_active=True, timeout=LONG_TIMEOUT
    )
    await ops_test_microk8s.model.wait_for_idle(
        apps=[app_name], wait_for_active=True, timeout=LONG_TIMEOUT
    )

    logger.info("Checking Dashboard access...")
    assert await access_all_dashboards(ops_test, ops_test_microk8s, https=https)


async def network_cut_application(
    ops_test: OpsTest, ops_test_microk8s: OpsTest, https: bool = False
):
    """Full network cut for the whole application, resulting in IP change."""
    is_cross_model = ops_test.model.name != ops_test_microk8s.model.name
    app_name = APP_NAME
    if is_cross_model:
        app_name = APP_NAME_K8S

    logger.info("Cutting all units from network...")
    unit_ip_map = {}
    if is_cross_model:
        k8s_units = []
        for unit in ops_test_microk8s.model.applications[app_name].units:
            pod_name = unit.name.replace("/", "-")
            namespace = ops_test_microk8s.model.info.name
            ip = await get_address(ops_test_microk8s, unit.name, app_name)

            logger.info(f"Pod deleted on {unit.name} (Pod: {pod_name})...")
            ha_helpers.network_cut_k8s(pod_name, namespace)

            unit_ip_map[unit.name] = ip
            k8s_units.append(pod_name)

        lease_timeout = 10
        logger.info(f"Waiting {lease_timeout}s for Juju to know pod has expired...")
        await asyncio.sleep(lease_timeout)
        units = list(unit_ip_map.keys())
        ips = list(unit_ip_map.values())
    else:
        machines = []
        for unit in ops_test_microk8s.model.applications[app_name].units:
            machine_name = await ha_helpers.get_unit_machine_name(ops_test_microk8s, unit.name)
            ip = await get_address(ops_test_microk8s, unit.name, app_name)

            logger.info(f"Cutting unit {unit.name} from network...")
            ha_helpers.cut_unit_network(machine_name)

            machines.append(machine_name)
            unit_ip_map[unit.name] = ip

        units = list(unit_ip_map.keys())
        ips = list(unit_ip_map.values())

        logger.info(f"Waiting until units {units} are not reachable")
        await ops_test_microk8s.model.block_until(
            lambda: not all(ha_helpers.reachable(ip, SERVER_PORT) for ip in ips),
            timeout=LONG_TIMEOUT,
            wait_period=LONG_WAIT,
        )

        logger.info(f"Waiting until unit {units} are 'lost'")
        await ops_test_microk8s.model.block_until(
            lambda: all(
                ["unknown", "lost"]
                == ha_helpers.get_unit_state_from_status(ops_test_microk8s, unit, app_name)
                for unit in units
            ),
            timeout=LONG_TIMEOUT,
            wait_period=LONG_WAIT,
        )

        logger.info("Checking lack of Dashboard access...")
        assert all_dashboards_unavailable(ops_test, ops_test_microk8s, https=https)

        logger.info("Restoring network...")
        for machine_name in machines:
            try:
                ha_helpers.restore_unit_network(machine_name)
            except CalledProcessError:  # in case it was already cleaned up
                pass

    logger.info("Waiting for Juju to detect new IPs...")
    await ops_test_microk8s.model.block_until(
        lambda: all(
            ha_helpers.get_hosts_from_status(ops_test_microk8s, app_name).get(unit_name)
            and ha_helpers.get_hosts_from_status(ops_test_microk8s, app_name)[unit_name]
            != unit_ip_map[unit_name]
            for unit_name in unit_ip_map
        ),
        timeout=LONG_TIMEOUT,
        wait_period=LONG_WAIT,
    )

    for unit, old_ip in unit_ip_map.items():
        new_ip = await get_address(ops_test_microk8s, unit, app_name)
        assert new_ip != old_ip
        logger.info(f"Old IP {old_ip} has changed to {new_ip}...")

    await ops_test.model.wait_for_idle(
        apps=[TLS_CERT_APP_NAME, OPENSEARCH_APP_NAME], wait_for_active=True, timeout=LONG_TIMEOUT
    )
    await ops_test_microk8s.model.wait_for_idle(
        apps=[app_name], wait_for_active=True, timeout=LONG_TIMEOUT
    )

    logger.info("Checking Dashboard access...")
    assert await access_all_dashboards(ops_test, ops_test_microk8s, https=https)


async def network_throttle_application(
    ops_test: OpsTest, ops_test_microk8s: OpsTest, https: bool = False
):
    """Network interrupt for the whole application without IP change."""
    is_cross_model = ops_test.model.name != ops_test_microk8s.model.name
    logger.info("Cutting all units from network...")
    unit_ip_map = {}
    if is_cross_model:
        k8s_units = []
        app_name = APP_NAME_K8S
        for unit in ops_test_microk8s.model.applications[app_name].units:
            pod_name = unit.name.replace("/", "-")
            namespace = ops_test_microk8s.model.info.name
            ip = await get_address(ops_test_microk8s, unit.name, app_name)

            logger.info(f"Network throttle on {unit.name} (Pod: {pod_name})...")
            ha_helpers.network_throttle_k8s(pod_name, namespace)

            k8s_units.append(pod_name)
            unit_ip_map[unit.name] = ip

        units = list(unit_ip_map.keys())
        ips = list(unit_ip_map.values())
    else:
        app_name = APP_NAME
        machines = []
        for unit in ops_test_microk8s.model.applications[app_name].units:
            machine_name = await ha_helpers.get_unit_machine_name(ops_test_microk8s, unit.name)
            ip = await get_address(ops_test_microk8s, unit.name, app_name)

            logger.info(f"Cutting unit {unit.name} from network...")
            ha_helpers.network_throttle(machine_name)

            machines.append(machine_name)
            unit_ip_map[unit.name] = ip

        units = list(unit_ip_map.keys())
        ips = list(unit_ip_map.values())

    logger.info(f"Waiting until units {units} are not reachable")
    await ops_test_microk8s.model.block_until(
        lambda: not all(ha_helpers.reachable(ip, SERVER_PORT) for ip in ips),
        timeout=LONG_TIMEOUT,
        wait_period=LONG_WAIT,
    )

    logger.info(f"Waiting until unit {units} are 'lost'")
    await ops_test_microk8s.model.block_until(
        lambda: all(
            ["unknown", "lost"]
            == ha_helpers.get_unit_state_from_status(ops_test_microk8s, unit, app_name)
            for unit in units
        ),
        timeout=LONG_TIMEOUT,
        wait_period=LONG_WAIT,
    )

    logger.info("Checking lack of Dashboard access...")
    assert all_dashboards_unavailable(ops_test, ops_test_microk8s, https=https)

    logger.info("Restoring network...")
    if is_cross_model:
        for pod_name in k8s_units:
            logger.info(
                f"Removing NetworkChaos to restore network to the leader (Pod: {pod_name})"
            )
            ha_helpers.network_restore_throttle_k8s(pod_name, namespace)
    else:
        for machine_name in machines:
            try:
                ha_helpers.network_release(machine_name)
            except CalledProcessError:  # in case it was already cleaned up
                pass

    logger.info(f"Waiting until units {units} are reachable again")
    await ops_test_microk8s.model.block_until(
        lambda: all(ha_helpers.reachable(ip, SERVER_PORT) for ip in ips),
        timeout=LONG_TIMEOUT,
        wait_period=LONG_WAIT,
    )

    # Double-checking that the network throttle didn't change the IP
    assert all(
        ha_helpers.get_hosts_from_status(ops_test_microk8s, app_name).get(unit)
        and ha_helpers.get_hosts_from_status(ops_test_microk8s, app_name)[unit]
        == unit_ip_map[unit]
        for unit in unit_ip_map
    )

    await ops_test.model.wait_for_idle(
        apps=[TLS_CERT_APP_NAME, OPENSEARCH_APP_NAME], wait_for_active=True, timeout=LONG_TIMEOUT
    )
    await ops_test_microk8s.model.wait_for_idle(
        apps=[app_name], wait_for_active=True, timeout=LONG_TIMEOUT
    )

    logger.info("Checking Dashboard access...")
    assert await access_all_dashboards(ops_test, ops_test_microk8s, https=https)


##############################################################################
# Tests
##############################################################################


@pytest.mark.abort_on_fail
async def test_network_cut_ip_change_leader_http(
    ops_test: OpsTest, ops_test_microk8s: OpsTest, request
):
    await network_cut_leader(ops_test, ops_test_microk8s)


@pytest.mark.abort_on_fail
async def test_network_cut_no_ip_change_leader_http(
    ops_test: OpsTest, ops_test_microk8s: OpsTest, request
):
    await network_throttle_leader(ops_test, ops_test_microk8s)


@pytest.mark.abort_on_fail
async def test_network_cut_ip_change_application_http(
    ops_test: OpsTest, ops_test_microk8s: OpsTest, request
):
    await network_cut_application(ops_test, ops_test_microk8s)


@pytest.mark.abort_on_fail
async def test_network_no_ip_change_application_http(
    ops_test: OpsTest, ops_test_microk8s: OpsTest, request
):
    await network_throttle_application(ops_test, ops_test_microk8s)


##############################################################################


@pytest.mark.abort_on_fail
async def test_set_tls(ops_test: OpsTest, ops_test_microk8s: OpsTest, request):
    """Not a real test but a separate stage to start TLS testing"""
    is_cross_model = ops_test.model.name != ops_test_microk8s.model.name
    app_name = APP_NAME
    if is_cross_model:
        app_name = APP_NAME_K8S
    logger.info("Initializing TLS Charm connections")
    if is_cross_model:
        await ops_test.model.create_offer(
            "certificates", TLS_CERT_APP_NAME, "self-signed-certificates"
        )
        await ops_test_microk8s.model.consume(f"admin/{ops_test.model_name}.{TLS_CERT_APP_NAME}")

    await ops_test_microk8s.model.integrate(app_name, TLS_CERT_APP_NAME)

    await ops_test.model.wait_for_idle(
        apps=[TLS_CERT_APP_NAME], wait_for_active=True, timeout=LONG_TIMEOUT
    )
    await ops_test_microk8s.model.wait_for_idle(
        apps=[app_name], wait_for_active=True, timeout=LONG_TIMEOUT
    )

    logger.info("Checking Dashboard access after TLS is configured")
    assert await access_all_dashboards(ops_test, ops_test_microk8s, https=True)


##############################################################################


@pytest.mark.abort_on_fail
async def test_network_cut_ip_change_leader_https(
    ops_test: OpsTest, ops_test_microk8s: OpsTest, request
):
    await network_cut_leader(ops_test, ops_test_microk8s, https=True)


@pytest.mark.abort_on_fail
async def test_network_cut_no_ip_change_leader_https(
    ops_test: OpsTest, ops_test_microk8s: OpsTest, request
):
    await network_throttle_leader(ops_test, ops_test_microk8s, https=True)


@pytest.mark.abort_on_fail
async def test_network_cut_ip_change_application_https(
    ops_test: OpsTest, ops_test_microk8s: OpsTest, request
):
    await network_cut_application(ops_test, ops_test_microk8s, https=True)


@pytest.mark.abort_on_fail
async def test_network_cut_no_ip_change_application_https(
    ops_test: OpsTest, ops_test_microk8s: OpsTest, request
):
    await network_throttle_application(ops_test, ops_test_microk8s, https=True)
