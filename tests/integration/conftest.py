import logging
import os
import subprocess
from asyncio import sleep
from typing import Any, AsyncGenerator

import pytest
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

MICROK8S_CLOUD_NAME = "uk8s"


@pytest.fixture(autouse=True, scope="module")
def opensearch_sysctl_settings():
    """Necessary settings for Opensearch

    This should probably rather go to ci.yaml"""
    subprocess.run(["sudo", "sysctl", "-w", "vm.swappiness=0"])
    subprocess.run(["sudo", "sysctl", "-w", "vm.max_map_count=262144"])
    subprocess.run(["sudo", "sysctl", "-w", "net.ipv4.tcp_retries2=5"])


@pytest.fixture
def ubuntu_base():
    """charm base version to use for testing."""
    return os.environ["CHARM_UBUNTU_BASE"]


@pytest.fixture
def series(ubuntu_base):
    """Workaround: python-libjuju does not support deploy base="ubuntu@22.04"; use series"""
    if ubuntu_base == "22.04":
        return "jammy"
    elif ubuntu_base == "24.04":
        return "noble"
    else:
        raise NotImplementedError


@pytest.fixture
def charmvm(ubuntu_base):
    """Path to the vm charm file to use for testing."""
    # Return str instead of pathlib.Path since python-lib juju's model.deploy(), juju deploy, and
    # juju bundle files expect local charms to begin with `./` or `/` to distinguish them from
    # Charmhub charms.
    return f"./tests/charms/vm/opensearch-dashboards_ubuntu@{ubuntu_base}-amd64.charm"


@pytest.fixture
def charmk8s(ubuntu_base):
    """Path to the k8s charm file to use for testing."""
    # Return str instead of pathlib.Path since python-lib juju's model.deploy(), juju deploy, and
    # juju bundle files expect local charms to begin with `./` or `/` to distinguish them from
    # Charmhub charms.
    return f"./tests/charms/k8s/opensearch-dashboards-k8s_ubuntu@{ubuntu_base}-amd64.charm"


@pytest.fixture
def application_charm() -> str:
    """Path to the application charm to use for testing."""
    return "./tests/integration/application_charm/application_ubuntu@22.04-amd64.charm"


def pytest_addoption(parser):
    parser.addoption(
        "--k8s-charm",
        action="store_true",
        default=False,
        help="Run tests targeting the Kubernetes charm.",
    )


@pytest.fixture(scope="class")
def config_matrix_charm():
    """Fixture to provide TLS and Traefik configuration groups from Spread."""
    tls_enabled = os.environ.get("TEST_TLS", "false").lower() == "true"
    traefik_trust_enabled = os.environ.get("TEST_TRAEFIK_TRUST", "false").lower() == "true"
    traefik_enabled = os.environ.get("TEST_TRAEFIK", "false").lower() == "true"
    return {"tls": tls_enabled, "traefik": traefik_enabled, "traefik_trust": traefik_trust_enabled}


@pytest.fixture(scope="class")
def config_matrix_rest():
    """Fixture to provide TLS and Traefik configuration groups from Spread."""
    tls_enabled = os.environ.get("TEST_TLS", "false").lower() == "true"
    traefik_enabled = os.environ.get("TEST_TRAEFIK", "false").lower() == "true"
    return {"tls": tls_enabled, "traefik": traefik_enabled}


@pytest.fixture(scope="module")
async def ops_test_microk8s(
    request, tmp_path_factory, ops_test: OpsTest
) -> AsyncGenerator[OpsTest, Any]:
    """Conditionally returns a MicroK8s OpsTest, or the primary VM OpsTest."""

    if not request.config.getoption("--k8s-charm"):
        yield ops_test
        return

    model_name = f"{ops_test.model_name}-uk8s"

    orig_cloud = getattr(request.config.option, "cloud", None)
    orig_model = getattr(request.config.option, "model", None)
    orig_alias = getattr(request.config.option, "model_alias", None)

    request.config.option.controller = ops_test.controller_name
    request.config.option.cloud = "uk8s"
    request.config.option.model = model_name
    request.config.option.model_alias = model_name

    ops_res = OpsTest(request, tmp_path_factory)
    await ops_res._setup_model()

    request.config.option.cloud = orig_cloud
    request.config.option.model = orig_model
    request.config.option.model_alias = orig_alias

    yield ops_res

    if not ops_test.keep_model:
        await ops_res.forget_model(alias=model_name)
        await ops_res._controller.destroy_model(model_name, destroy_storage=True, force=True)
        while model_name in await ops_res._controller.list_models():
            await sleep(5)
    await ops_res._cleanup_models()


@pytest.fixture(scope="module")
async def ops_test_oauth(
    request, tmp_path_factory, ops_test: OpsTest
) -> AsyncGenerator[OpsTest, Any]:
    """Create second OpsTest object, that is connected to the MicroK8s cloud for oauth testing

    Automatically creates and destroys (unless keep models parameter is used) corresponding Juju model.

    Returns:
        OpsTest object with MicroK8s connection and Juju model.
    """
    model_name = f"{ops_test.model_name}-oauth"
    request.config.option.controller = ops_test.controller_name
    request.config.option.cloud = "uk8s"
    request.config.option.model = model_name
    request.config.option.model_alias = model_name
    ops_res = OpsTest(request, tmp_path_factory)
    await ops_res._setup_model()
    yield ops_res
    if not ops_test.keep_model:
        await ops_res.forget_model(alias=model_name)
        await ops_res._controller.destroy_model(model_name, destroy_storage=True, force=True)
        while model_name in await ops_res._controller.list_models():
            await sleep(5)
    await ops_res._cleanup_models()
