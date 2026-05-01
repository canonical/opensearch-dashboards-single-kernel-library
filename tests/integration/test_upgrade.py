#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import logging
from pathlib import Path

import pytest
import yaml
from pytest_operator.plugin import OpsTest

from .helpers import (
    CONFIG_OPTS,
    TLS_CERTIFICATES_APP_NAME,
    TLS_STABLE_CHANNEL,
    access_all_dashboards,
    get_app_relation_data,
)

logger = logging.getLogger(__name__)

METADATA_VM = yaml.safe_load(Path("tests/charms/vm/metadata.yaml").read_text())
METADATA_K8S = yaml.safe_load(Path("tests/charms/k8s/metadata.yaml").read_text())
APP_NAME = METADATA_VM["name"]
APP_NAME_K8S = METADATA_K8S["name"]
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

NUM_UNITS_APP = 3
NUM_UNITS_DB = 3

RESOURCE = {
    "opensearch-dashboards-image": METADATA_K8S["resources"]["opensearch-dashboards-image"][
        "upstream-source"
    ]
}


@pytest.mark.usefixtures("config_matrix_rest")
class TestUpgrade:
    """Grouped in-place upgrade tests for OpenSearch Dashboards."""

    @pytest.mark.abort_on_fail
    @pytest.mark.skip_if_deployed
    async def test_build_and_deploy(
        self,
        ops_test: OpsTest,
        ops_test_microk8s: OpsTest,
        charmvm: str,
        charmk8s: str,
        charm_base: str,
        config_matrix_rest: dict,
    ):
        """Deploying all charms required for the tests, and wait for their complete setup to be done."""
        is_cross_model = ops_test.model.name != ops_test_microk8s.model.name
        charm = charmk8s if is_cross_model else charmvm
        app_name = APP_NAME_K8S if is_cross_model else APP_NAME
        tls = config_matrix_rest["tls"]
        traefik = config_matrix_rest["traefik"]

        if is_cross_model:
            await ops_test_microk8s.model.deploy(
                charm,
                application_name=app_name,
                num_units=NUM_UNITS_APP,
                base=charm_base,
                resources=RESOURCE,
                trust=True,
            )
        else:
            await ops_test_microk8s.model.deploy(
                charm, application_name=app_name, num_units=NUM_UNITS_APP, base=charm_base
            )

        await ops_test.model.set_config(OPENSEARCH_CONFIG)
        await ops_test.model.deploy(
            OPENSEARCH_APP_NAME,
            channel="2/edge",
            num_units=NUM_UNITS_DB,
            config=CONFIG_OPTS,
        )

        config = {"ca-common-name": "CN_CA"}
        await ops_test.model.deploy(
            TLS_CERTIFICATES_APP_NAME, channel=TLS_STABLE_CHANNEL, config=config
        )

        await ops_test.model.wait_for_idle(
            apps=[TLS_CERTIFICATES_APP_NAME], status="active", timeout=1000
        )

        # Relate it to OpenSearch to set up TLS.
        await ops_test.model.integrate(OPENSEARCH_APP_NAME, TLS_CERTIFICATES_APP_NAME)
        await ops_test.model.wait_for_idle(
            apps=[OPENSEARCH_APP_NAME, TLS_CERTIFICATES_APP_NAME],
            status="active",
            timeout=1000,
        )

        async with ops_test_microk8s.fast_forward():
            await ops_test_microk8s.model.block_until(
                lambda: len(ops_test_microk8s.model.applications[app_name].units) == NUM_UNITS_APP
            )
            await ops_test_microk8s.model.wait_for_idle(
                apps=[app_name], timeout=1000, idle_period=30
            )

        assert ops_test_microk8s.model.applications[app_name].status == "blocked"

        if is_cross_model:
            await ops_test.model.create_offer(
                "opensearch-client", OPENSEARCH_APP_NAME, "opensearch"
            )
            await ops_test_microk8s.model.consume(
                f"admin/{ops_test.model.name}.{OPENSEARCH_APP_NAME}"
            )

            if tls:
                await ops_test.model.create_offer(
                    "certificates", TLS_CERTIFICATES_APP_NAME, "self-signed-certificates"
                )
                await ops_test_microk8s.model.consume(
                    f"admin/{ops_test.model_name}.{TLS_CERTIFICATES_APP_NAME}"
                )

        pytest.relation = await ops_test_microk8s.model.integrate(OPENSEARCH_APP_NAME, app_name)

        if tls:
            await ops_test_microk8s.model.integrate(app_name, TLS_CERTIFICATES_APP_NAME)

        if traefik:
            await ops_test_microk8s.model.deploy(
                TRAEFIK_APP_NAME, channel="latest/stable", trust=True
            )
            await ops_test_microk8s.model.integrate(app_name, TRAEFIK_APP_NAME)
            if tls:
                await ops_test_microk8s.model.integrate(
                    TRAEFIK_APP_NAME, f"{TLS_CERTIFICATES_APP_NAME}:certificates"
                )
            await ops_test_microk8s.model.wait_for_idle(apps=[app_name],
                                                        status="active",
                                                        timeout=1000)
        elif is_cross_model:
            await ops_test_microk8s.model.wait_for_idle(apps=[app_name],
                                                        status="blocked",
                                                        timeout=1000)
        else:
            await ops_test_microk8s.model.wait_for_idle(apps=[app_name],
                                                        status="active",
                                                        timeout=1000)
        await ops_test.model.wait_for_idle(
            apps=[OPENSEARCH_APP_NAME], status="active", timeout=1000
        )


    @pytest.mark.abort_on_fail
    async def test_in_place_upgrade(
        self,
        ops_test: OpsTest,
        ops_test_microk8s: OpsTest,
        charmvm: str,
        charmk8s: str,
        config_matrix_rest: dict,
    ):
        """Test the in-place upgrade handling the appropriate protocol (HTTP/HTTPS)."""
        is_cross_model = ops_test.model.name != ops_test_microk8s.model.name
        app_name = APP_NAME_K8S if is_cross_model else APP_NAME
        charm = charmk8s if is_cross_model else charmvm
        tls = config_matrix_rest["tls"]
        traefik = config_matrix_rest["traefik"]

        leader_unit = None
        for unit in ops_test_microk8s.model.applications[app_name].units:
            if await unit.is_leader_from_status():
                leader_unit = unit
        assert leader_unit

        action = await leader_unit.run_action("pre-upgrade-check")
        await action.wait()

        # ensuring that the upgrade stack is correct
        relation_data = get_app_relation_data(
            model_full_name=ops_test_microk8s.model_full_name,
            unit=f"{app_name}/0",
            endpoint="upgrade",
        )

        assert "upgrade-stack" in relation_data

        assert set(json.loads(relation_data["upgrade-stack"])) == set(
            [
                int(unit.name.split("/")[-1])
                for unit in ops_test_microk8s.model.applications[app_name].units
            ]
        )

        if is_cross_model:
            await ops_test_microk8s.model.applications[app_name].refresh(
                path=charm, resources=RESOURCE
            )
        else:
            await ops_test_microk8s.model.applications[app_name].refresh(path=charm)

        await ops_test_microk8s.model.wait_for_idle(
            apps=[app_name], status="active", timeout=1000, idle_period=120
        )

        # Validate access
        https = False
        if (
            (traefik and tls)
            or (not is_cross_model and tls)
            or (is_cross_model and tls and not traefik)
        ):
            https = True

        verify = True if tls else False

        assert await access_all_dashboards(ops_test, ops_test_microk8s, https=https, verify=verify)
