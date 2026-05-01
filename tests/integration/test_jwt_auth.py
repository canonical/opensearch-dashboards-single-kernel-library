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
    get_dashboard_routing,
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


@pytest.mark.usefixtures("config_matrix_rest")
class TestJWTAuth:
    """Grouped tests for JWT Authentication with OpenSearch Dashboards."""

    @pytest.mark.abort_on_fail
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
        tls = config_matrix_rest.get("tls")
        traefik = config_matrix_rest.get("traefik")

        charm = charmk8s if is_cross_model else charmvm
        app_name = APP_NAME_K8S if is_cross_model else APP_NAME

        if is_cross_model:
            await ops_test_microk8s.model.deploy(
                charm, application_name=app_name, base=charm_base, resources=RESOURCE
            )
        else:
            await ops_test_microk8s.model.deploy(charm, application_name=app_name, base=charm_base)

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

        if is_cross_model:
            await ops_test.model.create_offer(
                "opensearch-client", OPENSEARCH_APP_NAME, "opensearch"
            )
            await ops_test.model.create_offer(JWT_REL_NAME, JWT_APP_NAME, "jwt-integrator")
            await ops_test_microk8s.model.consume(
                f"admin/{ops_test.model.name}.{OPENSEARCH_APP_NAME}"
            )
            await ops_test_microk8s.model.consume(f"admin/{ops_test.model.name}.{JWT_APP_NAME}")
            await ops_test_microk8s.model.deploy(TLS_CERTIFICATES_APP_NAME, channel="1/stable")

            # if tls:
            # await ops_test.model.create_offer(
            #     endpoint=f"{TLS_CERTIFICATES_APP_NAME}:certificates,send-ca-cert",
            #     offer_name="self-signed-certificates",
            # )
            # await ops_test_microk8s.model.consume(
            #     f"admin/{ops_test.model.name}.{TLS_CERTIFICATES_APP_NAME}"
            # )

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
        await ops_test.model.wait_for_idle(
            apps=[OPENSEARCH_APP_NAME, JWT_APP_NAME], status="active"
        )

        logger.info(f"Integrating {app_name} with {JWT_APP_NAME}")
        await ops_test_microk8s.model.integrate(JWT_APP_NAME, app_name)

        if is_cross_model:
            await ops_test_microk8s.model.wait_for_idle(
                apps=[app_name], status="blocked", timeout=1000
            )
        else:
            await ops_test_microk8s.model.wait_for_idle(
                apps=[app_name], status="active", timeout=1000
            )

        if traefik:
            await ops_test_microk8s.model.deploy(
                TRAEFIK_APP_NAME, channel="latest/stable", trust=True
            )
            await ops_test_microk8s.model.wait_for_idle(
                apps=[TRAEFIK_APP_NAME], status="active", timeout=1000
            )
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
            elif not is_cross_model or traefik:
                await ops_test_microk8s.model.wait_for_idle(
                    apps=[app_name], status="active", timeout=1000
                )
            else:
                await ops_test_microk8s.model.wait_for_idle(
                    apps=[app_name], status="blocked", timeout=1000
                )

        await ops_test.model.wait_for_idle(apps=[JWT_APP_NAME], status="active")

    @pytest.mark.abort_on_fail
    async def test_dashboard_access(
        self, ops_test: OpsTest, ops_test_microk8s: OpsTest, config_matrix_rest: dict
    ):
        """Test access to dashboard unit with JWT and basic auth."""
        is_cross_model = ops_test.model.name != ops_test_microk8s.model.name
        tls = config_matrix_rest.get("tls", False)
        traefik = config_matrix_rest.get("traefik", False)

        app_name = APP_NAME_K8S if is_cross_model else APP_NAME

        # Calculate protocol depending on tls/traefik state
        https = False
        if (
            (traefik and tls)
            or (not is_cross_model and tls)
            or (is_cross_model and tls and not traefik)
        ):
            https = True
        protocol = "https" if https else "http"

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

        await ops_test.model.wait_for_idle(
            apps=[OPENSEARCH_APP_NAME],
            status="active",
            idle_period=60,
        )
        if traefik or not is_cross_model:
            await ops_test_microk8s.model.wait_for_idle(
                apps=[app_name],
                status="active",
                idle_period=60,
            )
        else:
            await ops_test_microk8s.model.wait_for_idle(
                apps=[app_name],
                status="blocked",
                idle_period=60,
            )

        logger.info("Test access with JWT after disabling")
        jwt_result = requests.get(
            url, headers={"Authorization": f"Bearer {generated_jwt['token']}"}, verify=False
        )
        assert jwt_result.status_code == 401, "`Unauthorized` error expected"
        logger.info("Access with JWT failed as expected")
