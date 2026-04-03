#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from pathlib import Path

import pytest
import requests
import yaml
from pytest_operator.plugin import OpsTest

from .helpers import (
    CONFIG_OPTS,
    TLS_CERTIFICATES_APP_NAME,
    TLS_STABLE_CHANNEL,
    get_bind_address,
)
from .helpers_jwt import generate_json_web_token

logger = logging.getLogger(__name__)

METADATA_VM = yaml.safe_load(Path("tests/charms/vm/metadata.yaml").read_text())
METADATA_K8S = yaml.safe_load(Path("tests/charms/k8s/metadata.yaml").read_text())
APP_NAME = METADATA_VM["name"]
APP_NAME_K8S = METADATA_K8S["name"]
JWT_APP_NAME = "jwt-integrator"
JWT_REL_NAME = "jwt-configuration"
OPENSEARCH_APP_NAME = "opensearch"
OPENSEARCH_RELATION_NAME = "opensearch-client"
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
    "opensearch-dashboards-image": "ghcr.io/canonical/charmed-opensearch-dashboards:2.19.4-24.04-edge"
}


@pytest.mark.abort_on_fail
async def test_build_and_deploy(
    ops_test: OpsTest, ops_test_microk8s: OpsTest, charmvm: str, charmk8s: str, series: str
):
    """Deploying all charms required for the tests, and wait for their complete setup to be done."""
    is_cross_model = ops_test.model.name != ops_test_microk8s.model.name
    charm = charmvm
    app_name = APP_NAME
    if is_cross_model:
        charm = charmk8s
        app_name = APP_NAME_K8S

    if is_cross_model:
        await ops_test_microk8s.model.deploy(
            charm, application_name=app_name, series=series, resources=RESOURCE
        )

    else:
        await ops_test_microk8s.model.deploy(charm, application_name=app_name, series=series)

    await ops_test.model.set_config(OPENSEARCH_CONFIG)
    config = {"ca-common-name": "CN_CA"}
    await ops_test.model.deploy(
        OPENSEARCH_APP_NAME,
        channel="2/edge",
        num_units=3,
        config=CONFIG_OPTS,
    )
    await ops_test.model.deploy(
        TLS_CERTIFICATES_APP_NAME, channel=TLS_STABLE_CHANNEL, config=config
    )
    await ops_test.model.deploy(JWT_APP_NAME, channel="1/edge")
    await ops_test.model.wait_for_idle(apps=[TLS_CERTIFICATES_APP_NAME], status="active")

    logger.info(f"Integrating {OPENSEARCH_APP_NAME} with {TLS_CERTIFICATES_APP_NAME}")
    await ops_test.model.integrate(OPENSEARCH_APP_NAME, TLS_CERTIFICATES_APP_NAME)
    await ops_test.model.wait_for_idle(
        apps=[OPENSEARCH_APP_NAME, TLS_CERTIFICATES_APP_NAME],
        status="active",
    )

    if is_cross_model:
        await ops_test.model.create_offer("opensearch-client", OPENSEARCH_APP_NAME, "opensearch")
        await ops_test.model.create_offer(JWT_REL_NAME, JWT_APP_NAME, "jwt-integrator")
        await ops_test_microk8s.model.consume(f"admin/{ops_test.model.name}.{OPENSEARCH_APP_NAME}")

        await ops_test_microk8s.model.consume(f"admin/{ops_test.model.name}.{JWT_APP_NAME}")

    logger.info(f"Integrating {app_name} with {OPENSEARCH_APP_NAME}")
    await ops_test_microk8s.model.integrate(OPENSEARCH_APP_NAME, app_name)

    logger.info("Create JWT configuration")
    global generated_jwt
    generated_jwt = generate_json_web_token()

    secret_name = "jwt-signing-key"
    secret_id = await ops_test.model.add_secret(
        name=secret_name, data_args=[f"signing-key={generated_jwt['signing-key']}"]
    )
    await ops_test.model.grant_secret(secret_name=secret_name, application=JWT_APP_NAME)

    jwt_config = {
        "signing-key": secret_id,
        "roles-key": "role",
        "subject-key": "user",
        "jwt-url-parameter": "jwt",
    }
    await ops_test.model.applications[JWT_APP_NAME].set_config(jwt_config)

    logger.info(f"Integrating {OPENSEARCH_APP_NAME} with {JWT_APP_NAME}")
    await ops_test.model.integrate(JWT_APP_NAME, OPENSEARCH_APP_NAME)
    await ops_test.model.wait_for_idle(apps=[OPENSEARCH_APP_NAME, JWT_APP_NAME], status="active")

    logger.info(f"Integrating {app_name} with {JWT_APP_NAME}")
    await ops_test_microk8s.model.integrate(JWT_APP_NAME, app_name)
    await ops_test.model.wait_for_idle(apps=[JWT_APP_NAME], status="active")
    await ops_test_microk8s.model.wait_for_idle(apps=[app_name], status="active")


@pytest.mark.abort_on_fail
async def test_dashboard_access(ops_test: OpsTest, ops_test_microk8s: OpsTest):
    """Test access to dashboard unit with JWT and basic auth."""
    is_cross_model = ops_test.model.name != ops_test_microk8s.model.name
    app_name = APP_NAME
    if is_cross_model:
        app_name = APP_NAME_K8S

    unit = ops_test_microk8s.model.applications[app_name].units[0]
    host = get_bind_address(ops_test_microk8s.model.name, unit.name)
    url = f"http://{host}:5601/api/status"

    logger.info("Test access with JWT")
    jwt_result = requests.get(
        url, headers={"Authorization": f"Bearer {generated_jwt['token']}"}, verify=False
    )
    assert jwt_result.status_code == 200, "Request failed"
    logger.info("Access with JWT successful")

    logger.info(f"Remove relation of {JWT_APP_NAME} with {app_name}")
    await ops_test_microk8s.juju("remove-relation", JWT_APP_NAME, app_name)

    logger.info(f"Remove relation of {JWT_APP_NAME} with {OPENSEARCH_APP_NAME}")
    await ops_test.juju("remove-relation", JWT_APP_NAME, OPENSEARCH_APP_NAME)

    await ops_test.model.wait_for_idle(
        apps=[OPENSEARCH_APP_NAME],
        status="active",
        idle_period=60,
    )

    await ops_test_microk8s.model.wait_for_idle(
        apps=[app_name],
        status="active",
        idle_period=60,
    )

    logger.info("Test access with JWT after disabling")
    jwt_result = requests.get(
        url, headers={"Authorization": f"Bearer {generated_jwt['token']}"}, verify=False
    )
    assert jwt_result.status_code == 401, "`Unauthorized` error expected"
    logger.info("Access with JWT failed as expected")
