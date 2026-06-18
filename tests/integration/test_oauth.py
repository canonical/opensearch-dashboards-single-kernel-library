#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
from asyncio import gather
from pathlib import Path

import pytest
import yaml
from oauth_tools import (
    ExternalIdpService,
    access_application_login_page,
    click_on_sign_in_button_by_text,
    complete_auth_code_login,
    deploy_identity_bundle,
)
from playwright.async_api._generated import Page
from pytest_operator.plugin import OpsTest

from .conftest import Flags
from .helpers import (
    CONFIG_OPTS,
    get_dashboard_routing,
)

pytest_plugins = ["oauth_tools.fixtures"]

logger = logging.getLogger(__name__)

METADATA_VM = yaml.safe_load(Path("tests/charms/dashboards_vm_charm/metadata.yaml").read_text())
METADATA_K8S = yaml.safe_load(Path("tests/charms/dashboards_k8s_charm/metadata.yaml").read_text())
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
DATA_INTEGRATOR_NAME = "data-integrator"
DATA_INTEGRATOR_CONFIG = {
    "index-name": "admin-index",
    "extra-user-roles": "admin",
}
RESOURCE = {
    "opensearch-dashboards-image": METADATA_K8S["resources"]["opensearch-dashboards-image"][
        "upstream-source"
    ]
}


@pytest.mark.abort_on_fail
@pytest.mark.skip_if_deployed
async def test_deploy(
    ops_test_vm: OpsTest,
    ops_test: OpsTest,
    charmvm: str,
    charmk8s: str,
    charm_base: str,
    substrate: str,
    test_flags: Flags,
):
    """Deploy OpenSearch and OpenSearch Dashboards but don't wait for completion."""
    await ops_test_vm.model.set_config(OPENSEARCH_CONFIG)

    await ops_test_vm.model.deploy(
        OPENSEARCH_APP_NAME,
        channel="2/edge",
        num_units=2,
        config=CONFIG_OPTS,
    )
    app_name = METADATA_K8S["name"] if substrate == "k8s" else METADATA_VM["name"]
    traefik = test_flags.traefik

    if substrate == "k8s":
        await ops_test.model.deploy(
            charmk8s, application_name=app_name, base=charm_base, resources=RESOURCE
        )
        if traefik:
            await ops_test.model.deploy(TRAEFIK_APP_NAME, channel="latest/stable", trust=True)
    else:
        await ops_test.model.deploy(charmvm, application_name=app_name, base=charm_base)


@pytest.mark.abort_on_fail
@pytest.mark.skip_if_deployed
async def test_deploy_identity_bundle(
    ops_test_vm: OpsTest,
    ops_test: OpsTest,
    ext_idp_service: ExternalIdpService,
):
    """Deploy identity platform and wait for all models to complete deployments."""
    await deploy_identity_bundle(
        ops_test=ops_test,
        bundle_url="./tests/integration/bundle-iam.yaml",
        ext_idp_service=ext_idp_service,
    )
    await gather(
        ops_test_vm.model.wait_for_idle(),
        ops_test.model.wait_for_idle(raise_on_error=False),
    )


@pytest.mark.abort_on_fail
@pytest.mark.skip_if_deployed
async def test_setup_relations(
    ops_test_vm: OpsTest,
    ops_test: OpsTest,
    substrate: str,
    test_flags: Flags,
):
    """Establish all the required relations."""
    app_name = METADATA_K8S["name"] if substrate == "k8s" else METADATA_VM["name"]
    traefik = test_flags.traefik

    if traefik:
        await ops_test.model.integrate(app_name, TRAEFIK_APP_NAME)
        await ops_test.model.wait_for_idle(timeout=1000)

    if substrate == "k8s":
        # Identity bundle (including self-signed-certificates) is in ops_test (k8s).
        # OpenSearch is in ops_test_vm (vm) and needs to consume the certificates offer.
        await ops_test.model.create_offer(
            "certificates", "certificates", "self-signed-certificates"
        )
        await ops_test_vm.model.consume(f"admin/{ops_test.model_name}.certificates")
        await ops_test_vm.model.integrate(f"{OPENSEARCH_APP_NAME}:certificates", "certificates")
        # Dashboards are in the same model as the identity bundle, integrate directly.
        await ops_test.model.integrate(f"{app_name}:certificates", "certificates")
        if traefik:
            await ops_test.model.integrate(f"{TRAEFIK_APP_NAME}:certificates", "certificates")
        await ops_test.model.wait_for_idle(timeout=1000)

        # OpenSearch offer for the cross-model relation to dashboards.
        await ops_test_vm.model.create_offer(
            "opensearch-client", OPENSEARCH_APP_NAME, "opensearch"
        )
        await ops_test.model.consume(f"admin/{ops_test_vm.model.name}.{OPENSEARCH_APP_NAME}")
    else:
        # vm: everything in the same model, integrate directly.
        await ops_test.model.integrate(f"{OPENSEARCH_APP_NAME}:certificates", "certificates")
        await ops_test.model.integrate(f"{app_name}:certificates", "certificates")
        await ops_test.model.wait_for_idle(timeout=1000)

    await ops_test.model.integrate(
        f"{OPENSEARCH_APP_NAME}:opensearch-client", f"{app_name}:opensearch-client"
    )

    await gather(
        ops_test_vm.model.wait_for_idle(status="active", timeout=1000),
        ops_test.model.wait_for_idle(timeout=1000),
    )

    if substrate == "k8s":
        # hydra (oauth) is in ops_test (k8s); opensearch is in ops_test_vm (vm).
        await ops_test.model.create_offer("oauth", "oauth", "hydra")
        await ops_test_vm.model.consume(f"admin/{ops_test.model_name}.oauth")
        await ops_test_vm.model.integrate(f"{OPENSEARCH_APP_NAME}:oauth", "oauth")
        # Dashboards: same model as hydra, integrate directly.
        await ops_test.model.integrate(f"{app_name}:oauth", "oauth")
    else:
        await ops_test.model.integrate(f"{OPENSEARCH_APP_NAME}:oauth", "oauth")
        await ops_test.model.integrate(f"{app_name}:oauth", "oauth")

    await gather(
        ops_test_vm.model.wait_for_idle(status="active"),
        ops_test.model.wait_for_idle(),
    )


@pytest.mark.abort_on_fail
async def test_oauth(
    ops_test: OpsTest,
    page: Page,
    ext_idp_service: ExternalIdpService,
    substrate: str,
    test_flags: Flags,
):
    """Ensure that SSO works for OpenSearch Dashboards login."""
    app_name = METADATA_K8S["name"] if substrate == "k8s" else METADATA_VM["name"]
    traefik = test_flags.traefik

    unit = ops_test.model.applications[app_name].units[0]
    host, port, path = await get_dashboard_routing(
        ops_test,
        unit.name,
    )
    if not traefik:
        url = f"https://{host}:{port}{path}"
    else:
        url = f"https://{host}{path}"
    await access_application_login_page(
        page=page,
        url=url,
        redirect_login_url=f"{url}/app/login",
    )
    await click_on_sign_in_button_by_text(page=page, text="Log in with single sign-on")
    await complete_auth_code_login(page=page, ops_test=ops_test, ext_idp_service=ext_idp_service)
