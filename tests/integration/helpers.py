#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import logging
import os
import socket
import subprocess
from pathlib import Path
from subprocess import PIPE, CalledProcessError, check_output
from typing import Dict
from urllib.parse import urlparse

import requests
import yaml
from juju.relation import Relation
from pytest_operator.plugin import OpsTest
from requests.exceptions import ConnectionError, SSLError, Timeout
from tenacity import (
    Retrying,
    before_sleep_log,
    retry,
    retry_if_exception_type,
    retry_if_result,
    stop_after_attempt,
    wait_fixed,
)

METADATA_VM = yaml.safe_load(Path("tests/charms/vm/metadata.yaml").read_text())
METADATA_K8S = yaml.safe_load(Path("tests/charms/k8s/metadata.yaml").read_text())
SUBSTRATE = os.environ.get("SUBSTRATE", "vm").lower()
APP_NAME = METADATA_K8S["name"] if SUBSTRATE == "k8s" else METADATA_VM["name"]
K8s_APP_NAME = METADATA_K8S["name"]

OPENSEARCH_APP_NAME = "opensearch"
CONFIG_OPTS = {"profile": "testing"}
DUMMY_CHARM = "dummy-charm"
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

TLS_CERTIFICATES_APP_NAME = "self-signed-certificates"
TLS_STABLE_CHANNEL = "1/stable"
COS_AGENT_APP_NAME = "grafana-agent"
COS_AGENT_RELATION_NAME = "cos-agent"
DB_CLIENT_APP_NAME = "application"
TRAEFIK_APP_NAME = "traefik-k8s"


logger = logging.getLogger(__name__)

DASHBOARD_QUERY_PARAMS = {
    "params": {
        "index": "albums*",
        "body": {
            "sort": [{"_score": {"order": "desc"}}],
            "size": 500,
            "version": True,
            "stored_fields": ["*"],
            "script_fields": {},
            "docvalue_fields": [],
            "_source": {"excludes": []},
            "query": {
                "bool": {
                    "must": [],
                    "filter": [{"match_all": {}}],
                    "should": [],
                    "must_not": [],
                }
            },
        },
        "preference": 1717603297253,
    }
}


def is_https_enabled(config_matrix: dict) -> bool:
    """Centralized logic to determine if HTTPS is expected based on matrix and substrate."""
    tls = config_matrix.get("tls", False)
    traefik = config_matrix.get("traefik", False)
    traefik_trust = config_matrix.get("traefik_trust", False)

    if SUBSTRATE == "k8s":
        return (traefik and tls and not traefik_trust) or (tls and not traefik)
    return tls


async def wait_for_dashboard_idle(
    ops_test_microk8s: OpsTest, traefik: bool, idle_period: int = 30
):
    """Standardized wait block for Dashboard app based on substrate and routing."""
    expected_status = "active" if (not SUBSTRATE == "k8s" or traefik) else "blocked"
    apps = [APP_NAME, TRAEFIK_APP_NAME] if (SUBSTRATE == "k8s" and traefik) else [APP_NAME]

    await ops_test_microk8s.model.wait_for_idle(
        apps=apps, status=expected_status, timeout=1000, idle_period=idle_period
    )


def get_relations(ops_test: OpsTest, name: str, app_name: str = "remote-") -> list[Relation]:
    """Get relations of a given name"""
    results = []

    for relation in ops_test.model.relations:
        endpoints_data = relation.data.get("endpoints", [])

        for ep in endpoints_data:
            ep_app_name = ep.get("application-name", "")
            ep_rel_name = ep.get("relation", {}).get("name", "")

            if ep_rel_name == name and app_name in ep_app_name:
                results.append(relation)
                break

    return results


async def get_secret_by_label(ops_test, label: str) -> Dict[str, str]:
    secrets_meta_raw = await ops_test.juju("list-secrets", "--format", "json")
    secrets_meta = json.loads(secrets_meta_raw[1])

    for secret_id in secrets_meta:
        if secrets_meta[secret_id]["label"] == label:
            break

    secret_data_raw = await ops_test.juju("show-secret", "--format", "json", "--reveal", secret_id)
    secret_data = json.loads(secret_data_raw[1])
    return secret_data[secret_id]["content"]["Data"]


def access_prometheus_exporter(host: str) -> bool:
    """Check if a given unit has 'dashboard-exporter' service available and publishing."""
    try:
        # Normal IP address
        socket.inet_aton(host)
    except OSError:
        socket.inet_pton(socket.AF_INET6, host)
        host = f"[{host}]"

    url = f"http://{host}:9684/metrics"
    try:
        response = requests.get(url)
        logger.info(f"Response of exporter: {response}")
    except requests.exceptions.RequestException:
        return False
    return response.status_code == 200 and "opensearch_dashboards_status" in response.text


async def access_all_prometheus_exporters(ops_test: OpsTest) -> bool:
    """Check if a given unit has 'dashboard-exporter' service available and publishing."""
    result = True
    for unit in ops_test.model.applications[APP_NAME].units:
        unit_ip = await get_address(ops_test, unit.name, APP_NAME)
        logger.info(f"Accessing prometheus exporter with {unit_ip} ip")
        result = result and access_prometheus_exporter(unit_ip)
    return result


async def get_dashboard_routing(ops_test: OpsTest, unit_name: str):
    """Returns (host, port, path) dynamically based on Traefik endpoints."""
    if TRAEFIK_APP_NAME in ops_test.model.applications:
        traefik_app = ops_test.model.applications[TRAEFIK_APP_NAME]

        traefik_unit = traefik_app.units[0]
        for unit in traefik_app.units:
            if await unit.is_leader_from_status():
                traefik_unit = unit
                break

        action = await traefik_unit.run_action("show-proxied-endpoints")
        action_result = await action.wait()

        endpoints_json = action_result.results.get("proxied-endpoints", "{}")
        endpoints = json.loads(endpoints_json)

        if APP_NAME in endpoints:
            url = endpoints[APP_NAME]["url"]
            parsed_url = urlparse(url)

            host = parsed_url.hostname
            port = parsed_url.port or (443 if parsed_url.scheme == "https" else 80)
            path = parsed_url.path

            return host, port, path
        else:
            raise RuntimeError(
                f"Endpoint for {APP_NAME} not found in Traefik's proxied-endpoints."
            )

    app_name = unit_name.split("/")[0]
    if DUMMY_CHARM in ops_test.model.applications and app_name == K8s_APP_NAME:
        unit_id = unit_name.split("/")[1]
        host = f"{app_name}-{unit_id}.{app_name}-endpoints"
    else:
        host = get_bind_address(ops_test.model.name, unit_name)

    return host, 5601, ""


async def access_dashboard(
    ops_test_microk8s: OpsTest,
    host: str,
    password: str,
    username: str = "kibanaserver",
    ssl: bool = False,
    verify: bool = False,
    port: int = 5601,
    path: str = None,
) -> bool:
    protocol = "https" if ssl else "http"
    path_str = path if path else ""
    url = f"{protocol}://{host}:{port}{path_str}/auth/login"

    # Only route through dummy charm for internal K8s hostnames
    if host.endswith("-endpoints"):
        logger.info(f"Routing request through {DUMMY_CHARM} to {url}")
        tester_unit = ops_test_microk8s.model.applications[DUMMY_CHARM].units[0]

        action_kwargs = {
            "url": url,
            "method": "POST",
            "username": username,
            "password": password,
        }

        if verify:
            try:
                with open("./ca.pem", "r") as f:
                    action_kwargs["ca_cert"] = f.read()
            except FileNotFoundError:
                logger.error("ca.pem not found locally; internal verification will likely fail.")

        action = await tester_unit.run_action("request", **action_kwargs)
        res = await action.wait()
        return int(res.results.get("status", 0)) == 200

    # Fallback: standard external request (for Traefik / VM tests)
    data = {"username": username, "password": password}
    request_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "osd-xsrf": "true",
    }

    arguments = {"url": url, "headers": request_headers, "json": data, "timeout": 10}
    if verify:
        arguments["verify"] = "./ca.pem"

    logger.info(f"Sending external request to {url}")
    try:
        response = requests.post(**arguments)
        logger.info(f"Got response:{response} from url:{url}")
        return response.status_code == 200
    except requests.exceptions.RequestException as e:
        logger.warning(f"Request failed or timed out: {e}")
        return False


@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(5),
    retry_error_callback=lambda _: False,
    retry=lambda x: x is False,
)
async def dashboard_unavailable(
    ops_test_microk8s: OpsTest, host: str, https: bool = False, port: int = 5601, path: str = None
) -> bool:
    protocol = "https" if https else "http"
    path_str = path if path else ""
    url = f"{protocol}://{host}:{port}{path_str}/auth/login"

    # Only route through dummy charm for internal K8s hostnames
    if host.endswith("-endpoints"):
        tester_unit = ops_test_microk8s.model.applications[DUMMY_CHARM].units[0]
        action_kwargs = {"url": url, "method": "GET"}

        if https:
            try:
                with open("./ca.pem", "r") as f:
                    action_kwargs["ca_cert"] = f.read()
            except FileNotFoundError:
                pass

        action = await tester_unit.run_action("request", **action_kwargs)
        res = await action.wait()
        return int(res.results.get("status", 0)) in (502, 503)

    # Fallback: standard external request
    arguments = {"url": url, "timeout": 10}

    if https:
        arguments["verify"] = "./ca.pem"

    try:
        response = requests.get(**arguments)
    except (ConnectionError, Timeout) as e:
        return True
    return response.status_code == 503


@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(15),
    retry_error_callback=lambda _: False,
    retry=retry_if_result(lambda x: x is False),
)
async def access_all_dashboards(
    ops_test: OpsTest,
    ops_test_microk8s: OpsTest,
    https: bool = False,
    verify: bool = True,
    skip: list[str] = None,
):
    skip = skip or []
    if SUBSTRATE == "k8s":
        relation_id = get_relations(ops_test, "opensearch-client")[0].id
    else:
        relation_id = get_relations(ops_test, "opensearch-client", APP_NAME)[0].id

    if not ops_test_microk8s.model.applications[APP_NAME].units:
        logger.error(f"No units for application {APP_NAME}")
        return False

    dashboard_credentials = await get_secret_by_label(
        ops_test, f"opensearch-client.{relation_id}.user.secret"
    )
    dashboard_password = dashboard_credentials["password"]
    result = True
    # Copying the Dashboard's CA cert locally to use it for SSL verification
    # We only get it once for pipeline efficiency, as it's the same on all units
    if verify:
        unit = ops_test_microk8s.model.applications[APP_NAME].units[0].name
        if unit not in skip and not get_dashboard_ca_cert(ops_test_microk8s.model.name, unit):
            logger.error(f"Couldn't retrieve host certificate for unit {unit}")
            return False

    for unit in ops_test_microk8s.model.applications[APP_NAME].units:
        if unit.name in skip:
            continue

        host, port, path = await get_dashboard_routing(
            ops_test_microk8s,
            unit.name,
        )

        if not host:
            logger.error(f"No hostname provided for {unit.name}, can't check connection.")
            return False

        logger.info(
            f"Attempting to login to {host}:{port} with password {dashboard_password} and path: {path}"
        )
        result &= await access_dashboard(
            ops_test_microk8s=ops_test_microk8s,
            host=host,
            password=dashboard_password,
            ssl=https,
            verify=verify,
            port=port,
            path=path,
        )

    return result


@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(15),
    retry_error_callback=lambda _: False,
    retry=retry_if_result(lambda x: x is False),
)
async def all_dashboards_unavailable(ops_test: OpsTest, https: bool = False) -> bool:
    unavail = True
    for unit in ops_test.model.applications[APP_NAME].units:

        if https:
            if not get_dashboard_ca_cert(ops_test.model.name, unit):
                logger.info(f"Couldn't retrieve host certificate for unit {unit}")
                continue

        host, port, path = await get_dashboard_routing(ops_test, unit.name)

        # We should retry until a host could be retrieved
        if not host:
            continue

        unavail = unavail and await dashboard_unavailable(ops_test, host, https, port, path)
    return unavail


@retry(
    stop=stop_after_attempt(5),
    wait=wait_fixed(30),
    retry_error_callback=lambda _: False,
    retry=(retry_if_result(lambda x: x is False) | retry_if_exception_type(SSLError)),
    before_sleep=before_sleep_log(logger, logging.DEBUG),
)
def get_dashboard_ca_cert(model_full_name: str, unit: str) -> bool:
    if SUBSTRATE == "k8s":
        cmd = (
            f"JUJU_MODEL={model_full_name} juju scp --container opensearch-dashboards "
            f"{unit}:/etc/opensearch-dashboards/certificates/ca.pem ./ca.pem"
        )
    else:
        cmd = (
            f"JUJU_MODEL={model_full_name} juju scp "
            f"ubuntu@{unit}:/var/snap/opensearch-dashboards/current/etc/opensearch-dashboards/certificates/ca.pem ./"
        )

    try:
        output = subprocess.run(
            ["bash", "-c", cmd], timeout=30, check=True, capture_output=True, text=True
        )
    except subprocess.CalledProcessError as err:
        logger.error(f"SCP failed: {err.stderr.strip() if err.stderr else err}")
        return False
    except subprocess.TimeoutExpired as err:
        logger.error(f"SCP timed out: {err}")
        return False

    return output.returncode == 0


def get_file_contents(model_name: str, unit_name: str, filename: str) -> str:
    if SUBSTRATE == "k8s":
        cmd = f"JUJU_MODEL={model_name} juju ssh --container opensearch-dashboards {unit_name} cat {filename}"
    else:
        cmd = f"JUJU_MODEL={model_name} juju ssh {unit_name} sudo cat {filename}"

    try:
        logger.info(f"Getting content of file with command:{cmd}")
        output = subprocess.check_output(["bash", "-c", cmd], text=True, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as err:
        logger.error(f"Failed to read {filename}: {err.output.strip() if err.output else err}")
        return ""

    return output


async def get_address(ops_test: OpsTest, unit_name: str, app_name: str = APP_NAME) -> str:
    """Get the address for a unit."""
    status = await ops_test.model.get_status()  # noqa: F821
    address = status["applications"][app_name]["units"][f"{unit_name}"].get("public-address")
    if not address:
        # If k8s
        address = status["applications"][app_name]["units"][f"{unit_name}"]["address"]

    return address


def get_bind_address(
    model_full_name: str, unit: str, relation_name: str = OPENSEARCH_RELATION_NAME
) -> str:
    """Retrieves the bind address for a specific relation on a given unit."""
    try:
        private_ip = subprocess.check_output(
            [
                "juju",
                "exec",
                "-m",
                model_full_name,
                "--unit",
                unit,
                "--",
                "network-get",
                relation_name,
                "--bind-address",
            ],
            text=True,
            stderr=subprocess.STDOUT,
            timeout=10,
        ).strip()

        if private_ip:
            return private_ip

    except (CalledProcessError, subprocess.TimeoutExpired) as err:
        logger.warning(f"Couldn't retrieve IP address via network-get: {err.output}")

    try:
        info = subprocess.check_output(
            ["juju", "exec", "-m", model_full_name, "--unit", unit, "--", "ip", "a"],
            text=True,
            stderr=subprocess.STDOUT,
            timeout=10,
        )
        logger.info(f"Could not retrieve bind address. Interface info is:\n{info}")
    except (CalledProcessError, subprocess.TimeoutExpired) as err:
        logger.info(f"Couldn't retrieve interface info either: {err.output}")

    return ""


def _get_show_unit_json(model_full_name: str, unit: str) -> Dict:
    """Retrieve the show-unit result in json format."""
    show_unit_res = check_output(
        f"JUJU_MODEL={model_full_name} juju show-unit {unit} --format json",
        stderr=PIPE,
        shell=True,
        universal_newlines=True,
    )

    try:
        show_unit_res_dict = json.loads(show_unit_res)
        return show_unit_res_dict
    except json.JSONDecodeError:
        raise ValueError


def get_app_relation_data(model_full_name: str, unit: str, endpoint: str):
    show_unit = _get_show_unit_json(model_full_name=model_full_name, unit=unit)
    d_relations = show_unit[unit]["relation-info"]
    for relation in d_relations:
        if relation["endpoint"] == endpoint:
            return relation["application-data"]
    raise Exception("No relation found!")


def get_unit_relation_data(model_full_name: str, unit: str, endpoint: str):
    show_unit = _get_show_unit_json(model_full_name=model_full_name, unit=unit)
    d_relations = show_unit[unit]["relation-info"]
    for relation in d_relations:
        if relation["endpoint"] == endpoint:
            return relation["related-units"]
    raise Exception("No relation found!")


async def check_full_status(
    ops_test: OpsTest,
    app_name: str = APP_NAME,
    status: str = "active",
    status_msg: str | None = None,
) -> bool:
    """Compare app and unit status against those requested in the parameters."""
    status_data = await ops_test.model.get_status()  # noqa: F821
    if not status_data.applications[app_name].status.status == status:
        return False

    if not all(
        unit.workload_status.status == status
        for unit in status_data.applications[app_name].units.values()
    ):
        return False

    if status_msg:
        for dashboards_unit in ops_test.model.applications[app_name].units:
            action = await dashboards_unit.run_action("status-detail")
            res = await action.wait()

            json_output = res.results.get("json-output", {})

            try:
                app_status_list = json.loads(json_output.get("app", "[]"))
                app_messages = [comp.get("Message", "") for comp in app_status_list]

                if status_msg not in app_messages:
                    logger.warning(
                        f"Expected APP message '{status_msg}' not found. "
                        f"Current app messages: {app_messages}"
                    )
                    return False

                unit_status_list = json.loads(json_output.get("unit", "[]"))
                unit_messages = [comp.get("Message", "") for comp in unit_status_list]

                if status_msg not in unit_messages:
                    logger.warning(
                        f"Expected UNIT message '{status_msg}' not found. "
                        f"Current unit messages: {unit_messages}"
                    )
                    return False

            except json.JSONDecodeError as e:
                logger.error(f"Failed to decode JSON from status-detail action: {e}")
                return False
    return True


def count_lines_with(
    model_full_name: str, unit: str, file: str, pattern: str, container_name: str = ""
) -> int:
    container = f"--container={container_name}" if container_name else ""
    sudo = "" if container else "sudo -i"
    result = check_output(
        f"JUJU_MODEL={model_full_name} juju ssh {container} {unit} {sudo} 'grep \"{pattern}\" {file} | wc -l'",
        stderr=PIPE,
        shell=True,
        universal_newlines=True,
    )

    return int(result)


async def get_leader_name(ops_test: OpsTest, app_name: str = APP_NAME) -> str:
    """Get the leader unit name."""
    for unit in ops_test.model.applications[app_name].units:
        if await unit.is_leader_from_status():
            return unit.name


@retry(wait=wait_fixed(wait=15), stop=stop_after_attempt(15))
async def client_run_request(
    ops_test,
    unit_name: str,
    relation: Relation,
    method: str,
    headers: str,
    endpoint: str,
    payload: str = None,
    action: str = "run-db-request",
    server_uri: str = "",
):
    # python can't have variable names with a hyphen, and Juju can't have action variables with an
    # underscore, so this is a compromise.

    app_name = unit_name.split("/")[0]
    relation_name = [
        relation.endpoints[i].name for i in range(2) if relation.applications[i].name == app_name
    ][0]
    params = {
        "relation-id": relation.id,
        "relation-name": relation_name,
        "headers": json.dumps(headers),
        "method": method,
        "endpoint": endpoint,
    }
    if payload:
        params["payload"] = payload
    if server_uri:
        params["server-uri"] = server_uri

    action = await ops_test.model.units.get(unit_name).run_action(action, **(params or {}))
    action = await action.wait()

    if action.status != "completed":
        raise Exception(action.results)

    return action.results


@retry(wait=wait_fixed(wait=15), stop=stop_after_attempt(15))
async def client_run_db_request(
    ops_test,
    unit_name: str,
    relation: Relation,
    method: str,
    endpoint: str,
    payload: str = None,
):
    """Client applicatoin issuing a request to Opensearch."""
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    return await client_run_request(
        ops_test, unit_name, relation, method, headers, endpoint, payload
    )


@retry(wait=wait_fixed(wait=15), stop=stop_after_attempt(15))
async def client_run_dashboards_request(
    ops_test,
    unit_name: str,
    relation: Relation,
    method: str,
    host: str,
    endpoint: str,
    payload: str = None,
    https: bool = False,
    port: int = 5601,
    path: str = None,
):
    action = "run-dashboards-request"
    req_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "osd-xsrf": "osd-fetch",
    }

    proto = "https" if https else "http"
    server_uri = f"{proto}://{host}:{port}{path}"

    return await client_run_request(
        ops_test,
        unit_name,
        relation,
        method,
        req_headers,
        endpoint,
        payload,
        action,
        server_uri,
    )


async def client_run_all_dashboards_request(
    ops_test: OpsTest,
    ops_test_microk8s: OpsTest,
    unit_name: str,
    relation: Relation,
    method: str,
    endpoint: str,
    payload: str = None,
    https: bool = False,
):
    """Check if all dashboard instances are accessible."""
    result = []
    if not ops_test_microk8s.model.applications[APP_NAME].units:
        logger.debug(f"No units for application {APP_NAME}")
        return False

    dashboard_credentials = await get_secret_by_label(
        ops_test, f"opensearch-client.{relation.id}.user.secret"
    )
    username = dashboard_credentials.get("username")
    password = dashboard_credentials.get("password")

    ca_cert_content = None
    if https:
        try:
            with open("./ca.pem", "r") as f:
                ca_cert_content = f.read()
        except FileNotFoundError:
            logger.warning("ca.pem not found locally.")

    for dashboards_unit in ops_test_microk8s.model.applications[APP_NAME].units:
        host, port, path = await get_dashboard_routing(ops_test_microk8s, dashboards_unit.name)

        if not host:
            logger.debug(f"No hostname found for {dashboards_unit.name}, can't check connection.")
            return False

        if host.endswith("-endpoints") and DUMMY_CHARM in ops_test_microk8s.model.applications:
            logger.info(f"Routing client data request through dashboard-tester to {host}")
            tester_unit = ops_test_microk8s.model.applications[DUMMY_CHARM].units[0]

            protocol = "https" if https else "http"
            path_str = path if path else ""
            clean_endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"
            url = f"{protocol}://{host}:{port}{path_str}{clean_endpoint}"

            action_kwargs = {
                "url": url,
                "method": method,
                "username": username,
                "password": password,
            }
            if payload:
                action_kwargs["payload"] = payload
            if ca_cert_content:
                action_kwargs["ca_cert"] = ca_cert_content

            action = await tester_unit.run_action("request", **action_kwargs)
            res = await action.wait()

            try:
                text_res = res.results.get("text", "{}")
                parsed_json = json.loads(text_res)

                if isinstance(parsed_json, dict) and "rawResponse" in parsed_json:
                    result.append(parsed_json["rawResponse"])
                else:
                    result.append(parsed_json)

            except json.JSONDecodeError:
                result.append({"rawResponse": res.results.get("text")})

            logger.info(f"Proxy Response from {host}: {res.results.get('status')}")
        else:
            response = await client_run_dashboards_request(
                ops_test, unit_name, relation, method, host, endpoint, payload, https, port, path
            )
            if "results" in response:
                result.append(json.loads(response["results"])["rawResponse"])
            else:
                result.append(response)
            logger.info(f"Response from {unit_name}, {host}: {response}")

    return result


async def destroy_cluster(ops_test, app: str = OPENSEARCH_APP_NAME):
    """Destroy cluster in a forceful way."""
    n_apps_before = len(ops_test.model.applications)
    await ops_test.model.applications[app].destroy(destroy_storage=True, force=True, no_wait=False)

    # destroy does not wait for applications to be removed, perform this check manually
    for attempt in Retrying(stop=stop_after_attempt(100), wait=wait_fixed(10), reraise=True):
        with attempt:
            # pytest_operator has a bug where the number of applications does not get correctly
            # updated. Wrapping the call with `fast_forward` resolves this
            async with ops_test.fast_forward():
                n_apps_after = len(ops_test.model.applications)
            # This case we don't raise an error in the context manager which
            # fails to restore the `update-status-hook-interval` value to it's former state.
            assert n_apps_after == n_apps_before - 1, "old cluster not destroyed successfully."


async def for_machines(ops_test, machines, state="started"):
    for attempt in Retrying(stop=stop_after_attempt(10), wait=wait_fixed(wait=60)):
        with attempt:
            mach_status = json.loads(
                subprocess.check_output(
                    ["juju", "machines", f"--model={ops_test.model.name}", "--format=json"]
                )
            )["machines"]
            for id in machines:
                if (
                    str(id) not in mach_status.keys()
                    or mach_status[str(id)]["juju-status"]["current"] != state
                ):
                    logger.warning(f"machine-{id} either not exist yet or not in {state}")
                    raise Exception()
