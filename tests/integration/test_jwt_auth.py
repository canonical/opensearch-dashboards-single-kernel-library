#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from pathlib import Path

import pytest
import requests
import yaml
from pytest_operator.plugin import OpsTest

from .conftest import Flags
from .helpers import (
    CONFIG_OPTS,
    TLS_CERTIFICATES_APP_NAME,
    TLS_STABLE_CHANNEL,
    get_dashboard_routing,
    is_https_enabled,
    wait_for_dashboard_idle,
)
from .helpers_jwt import generate_json_web_token

logger = logging.getLogger(__name__)

METADATA_VM = yaml.safe_load(Path("tests/charms/dashboards_vm_charm/metadata.yaml").read_text())
METADATA_K8S = yaml.safe_load(Path("tests/charms/dashboards_k8s_charm/metadata.yaml").read_text())
JWT_APP_NAME = "jwt-integrator"
JWT_REL_NAME = "jwt-configuration"
OPENSEARCH_APP_NAME = "opensearch"
TRAEFIK_APP_NAME = "traefik-k8s"
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
    "opensearch-dashboards-image": METADATA_K8S["resources"]["opensearch-dashboards-image"][
        "upstream-source"
    ]
}


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
    """Deploying all charms required for the tests, and wait for their complete setup to be done."""
    app_name = METADATA_K8S["name"] if substrate == "k8s" else METADATA_VM["name"]
    tls = test_flags.tls
    traefik = test_flags.traefik
    if substrate == "k8s":
        await ops_test_microk8s.model.deploy(
            charmk8s, application_name=app_name, base=charm_base, resources=RESOURCE
        )
    else:
        await ops_test_microk8s.model.deploy(charmvm, application_name=app_name, base=charm_base)

    await ops_test.model.set_config(OPENSEARCH_CONFIG)
    config = {"ca-common-name": "CN_CA"}
    await ops_test.model.deploy(
        OPENSEARCH_APP_NAME,
        channel="2/edge",
        num_units=3,
        config=CONFIG_OPTS,
    )

    # TLS is still deployed on the VM model as it's required by OpenSearch
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

    if substrate == "k8s":
        await ops_test.model.create_offer("opensearch-client", OPENSEARCH_APP_NAME, "opensearch")
        await ops_test.model.create_offer(JWT_REL_NAME, JWT_APP_NAME, "jwt-integrator")
        await ops_test_microk8s.model.consume(f"admin/{ops_test.model.name}.{OPENSEARCH_APP_NAME}")
        await ops_test_microk8s.model.consume(f"admin/{ops_test.model.name}.{JWT_APP_NAME}")

        if tls:
            await ops_test.model.create_offer(
                endpoint=f"{TLS_CERTIFICATES_APP_NAME}:certificates,send-ca-cert",
                offer_name="self-signed-certificates",
            )
            await ops_test_microk8s.model.consume(
                f"admin/{ops_test.model.name}.{TLS_CERTIFICATES_APP_NAME}"
            )

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

    if substrate == "k8s":
        await ops_test_microk8s.model.wait_for_idle(
            apps=[app_name], status="blocked", timeout=1000
        )
    else:
        await ops_test_microk8s.model.wait_for_idle(apps=[app_name], status="active", timeout=1000)

    if traefik:
        await ops_test_microk8s.model.deploy(TRAEFIK_APP_NAME, channel="latest/stable", trust=True)
        await ops_test_microk8s.model.wait_for_idle(
            apps=[TRAEFIK_APP_NAME], status="active", timeout=1000
        )
        await ops_test_microk8s.model.integrate(app_name, TRAEFIK_APP_NAME)

    if tls:
        await ops_test_microk8s.model.integrate(app_name, TLS_CERTIFICATES_APP_NAME)
        if traefik:
            await ops_test_microk8s.model.integrate(
                TRAEFIK_APP_NAME, f"{TLS_CERTIFICATES_APP_NAME}:certificates"
            )

    await wait_for_dashboard_idle(ops_test_microk8s, traefik)
    await ops_test.model.wait_for_idle(apps=[JWT_APP_NAME], status="active")


@pytest.mark.abort_on_fail
async def test_dashboard_access(
    ops_test: OpsTest,
    ops_test_microk8s: OpsTest,
    substrate: str,
    test_flags: Flags,
):
    """Test access to dashboard unit with JWT and basic auth."""
    app_name = METADATA_K8S["name"] if substrate == "k8s" else METADATA_VM["name"]
    traefik = test_flags.traefik

    # Calculate protocol depending on tls/traefik state
    protocol = "https" if is_https_enabled(test_flags) else "http"
    unit = ops_test_microk8s.model.applications[app_name].units[0]
    host, port, path = await get_dashboard_routing(
        ops_test_microk8s,
        unit.name,
    )
    url = f"{protocol}://{host}:{port}{path}/api/status"

    logger.info(f"Test access with JWT to {url}")
    jwt_result = requests.get(
        url, headers={"Authorization": f"Bearer {generated_jwt['token']}"}, verify=False
    )
    assert jwt_result.status_code == 200, "Request failed"
    logger.info("Access with JWT successful")

    logger.info(f"Remove relation of {JWT_APP_NAME} with {app_name}")
    await ops_test_microk8s.juju("remove-relation", JWT_APP_NAME, app_name)

    logger.info(f"Remove relation of {JWT_APP_NAME} with {OPENSEARCH_APP_NAME}")
    await ops_test.juju("remove-relation", JWT_APP_NAME, OPENSEARCH_APP_NAME)

    await wait_for_dashboard_idle(ops_test_microk8s, traefik, 60)
    logger.info("Test access with JWT after disabling")
    jwt_result = requests.get(
        url, headers={"Authorization": f"Bearer {generated_jwt['token']}"}, verify=False
    )
    assert jwt_result.status_code == 401, "`Unauthorized` error expected"
    logger.info("Access with JWT failed as expected")
