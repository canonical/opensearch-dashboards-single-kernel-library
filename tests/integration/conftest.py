import logging
import os
import subprocess
from asyncio import sleep
from typing import Any, AsyncGenerator, Literal

import pytest
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

K8S_CLOUD_NAME = "uk8s"
SUBSTRATE = os.environ.get("SUBSTRATE", "vm").lower()


@pytest.fixture(autouse=True, scope="module")
def opensearch_sysctl_settings():
    """Necessary settings for Opensearch

    This should probably rather go to ci.yaml"""
    subprocess.run(["sudo", "sysctl", "-w", "vm.swappiness=0"])
    subprocess.run(["sudo", "sysctl", "-w", "vm.max_map_count=262144"])
    subprocess.run(["sudo", "sysctl", "-w", "net.ipv4.tcp_retries2=5"])


@pytest.fixture(scope="session")
def substrate() -> Literal["k8s", "vm"]:
    """Returns the substrate"""
    if SUBSTRATE not in ("k8s", "vm"):
        raise ValueError(
            f"Substrate has invalid value. Correct values are k8s, vm. Current value {SUBSTRATE}."
        )
    return SUBSTRATE


@pytest.fixture
def application_charm() -> str:
    """Path to the application charm to use for testing."""
    return "./tests/integration/dashboards_application_charm/application_ubuntu@24.04-amd64.charm"


def pytest_collection_modifyitems(config, items):
    tls = os.environ.get("TEST_TLS", "false").lower() == "true"
    skip_vm_only = pytest.mark.skip(reason="VM-only scenario.")
    skip_k8s_only = pytest.mark.skip(reason="K8s-only scenario.")
    skip_tls_only = pytest.mark.skip(reason="TLS is disabled in this matrix run.")
    for item in items:
        if SUBSTRATE != "vm" and "vm_only" in item.keywords:
            item.add_marker(skip_vm_only)
        if SUBSTRATE != "k8s" and "k8s_only" in item.keywords:
            item.add_marker(skip_k8s_only)
        if not tls and "tls_only" in item.keywords:
            item.add_marker(skip_tls_only)


def pytest_configure(config):
    if SUBSTRATE == "k8s":
        k8s_cloud = os.environ.get("K8S_CLOUD", "uk8s")
        if not getattr(config.option, "cloud", None):
            config.option.cloud = k8s_cloud


class Flags:
    def __init__(self):
        self.test_tls = os.environ.get("TEST_TLS", "false").lower() == "true"
        self.traefik = os.environ.get("TEST_TRAEFIK", "false").lower() == "true"
        self.transfer_traefik_ca = os.environ.get("TRANSFER_TRAEFIK_CA", "false").lower() == "true"
        self.charm_base = f"ubuntu@{os.environ.get('CHARM_UBUNTU_BASE', '24.04')}"
        if self.transfer_traefik_ca and not self.traefik:
            raise ValueError("TRANSFER_TRAEFIK_CA=true requires TEST_TRAEFIK=true.")


@pytest.fixture(scope="class")
def test_flags() -> Flags:
    """Fixture to provide TLS and Traefik configuration groups from Spread."""
    return Flags()


@pytest.fixture(scope="module")
async def ops_test_k8s(
    request, tmp_path_factory, ops_test: OpsTest
) -> AsyncGenerator[OpsTest, Any]:
    """Returns the k8s OpsTest hosting the identity/IAM bundle."""
    if SUBSTRATE != "vm":
        yield ops_test
        return

    model_name = f"{ops_test.model_name}-k8s"

    orig_cloud = getattr(request.config.option, "cloud", None)
    orig_model = getattr(request.config.option, "model", None)
    orig_alias = getattr(request.config.option, "model_alias", None)

    request.config.option.controller = ops_test.controller_name
    request.config.option.cloud = K8S_CLOUD_NAME
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
