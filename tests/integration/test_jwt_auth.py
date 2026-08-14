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
    APP_NAME,
    OPENSEARCH_APP_NAME,
    TLS_CERTIFICATES_APP_NAME,
    TRAEFIK_APP_NAME,
    deploy_base,
    get_dashboard_routing,
    is_https_enabled,
    wait_for_dashboard_idle,
    wait_for_ingress_blocked,
)
from .helpers_jwt import generate_json_web_token

logger = logging.getLogger(__name__)

JWT_APP_NAME = "jwt-integrator"
JWT_REL_NAME = "jwt-configuration"


@pytest.mark.abort_on_fail
async def test_build_and_deploy(
    ops_test: OpsTest,
    substrate: str,
    test_flags: Flags,
):
    """Deploying all charms required for the tests, and wait for their complete setup to be done."""
    tls = test_flags.test_tls
    traefik = test_flags.traefik
    charm_base = test_flags.charm_base

    await ops_test.model.deploy(JWT_APP_NAME, channel="1/edge")
    app_name = await deploy_base(
        ops_test,
        charm_base,
        substrate,
        num_units_db=3,
    )

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
    await ops_test.model.integrate(JWT_APP_NAME, app_name)

    if substrate == "k8s":
        await wait_for_ingress_blocked(ops_test, app_name, timeout=1000)
    else:
        await ops_test.model.wait_for_idle(apps=[app_name], status="active", timeout=1000)

    if traefik:
        await ops_test.model.deploy(TRAEFIK_APP_NAME, channel="latest/stable", trust=True)
        await ops_test.model.wait_for_idle(apps=[TRAEFIK_APP_NAME], status="active", timeout=1000)
        await ops_test.model.integrate(app_name, TRAEFIK_APP_NAME)

    if tls:
        await ops_test.model.integrate(app_name, TLS_CERTIFICATES_APP_NAME)
        if traefik:
            await ops_test.model.integrate(
                TRAEFIK_APP_NAME, f"{TLS_CERTIFICATES_APP_NAME}:certificates"
            )

    await wait_for_dashboard_idle(ops_test, traefik)
    await ops_test.model.wait_for_idle(apps=[JWT_APP_NAME], status="active")


@pytest.mark.abort_on_fail
async def test_dashboard_access(
    ops_test: OpsTest,
    substrate: str,
    test_flags: Flags,
):
    """Test access to dashboard unit with JWT and basic auth."""
    traefik = test_flags.traefik

    # Calculate protocol depending on tls/traefik state
    protocol = "https" if is_https_enabled(test_flags) else "http"
    unit = ops_test.model.applications[APP_NAME].units[0]
    host, port, path, _ = await get_dashboard_routing(
        ops_test,
        unit.name,
    )
    url = f"{protocol}://{host}:{port}{path}/api/status"

    logger.info(f"Test access with JWT to {url}")
    jwt_result = requests.get(
        url, headers={"Authorization": f"Bearer {generated_jwt['token']}"}, verify=False
    )
    assert jwt_result.status_code == 200, "Request failed"
    logger.info("Access with JWT successful")

    logger.info(f"Remove relation of {JWT_APP_NAME} with {APP_NAME}")
    await ops_test.juju("remove-relation", JWT_APP_NAME, APP_NAME)

    logger.info(f"Remove relation of {JWT_APP_NAME} with {OPENSEARCH_APP_NAME}")
    await ops_test.juju("remove-relation", JWT_APP_NAME, OPENSEARCH_APP_NAME)

    await wait_for_dashboard_idle(ops_test, traefik, 60)
    logger.info("Test access with JWT after disabling")
    jwt_result = requests.get(
        url, headers={"Authorization": f"Bearer {generated_jwt['token']}"}, verify=False
    )
    assert jwt_result.status_code == 401, "`Unauthorized` error expected"
    logger.info("Access with JWT failed as expected")
