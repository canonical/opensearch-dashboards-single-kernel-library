#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path

import pytest
import yaml
from pytest_operator.plugin import OpsTest

from .helpers import (
    CONFIG_OPTS,
    DASHBOARD_QUERY_PARAMS,
    TLS_CERTIFICATES_APP_NAME,
    TLS_STABLE_CHANNEL,
    access_all_dashboards,
    access_all_prometheus_exporters,
    all_dashboards_unavailable,
    check_full_status,
    client_run_all_dashboards_request,
    client_run_db_request,
    count_lines_with,
    destroy_cluster,
    get_address,
    get_file_contents,
    get_relations,
    get_unit_relation_data,
    is_https_enabled,
    wait_for_dashboard_idle,
)

logger = logging.getLogger(__name__)

METADATA_VM = yaml.safe_load(Path("tests/charms/vm/metadata.yaml").read_text())
METADATA_K8S = yaml.safe_load(Path("tests/charms/k8s/metadata.yaml").read_text())
PROMETHEUS_APP = "prometheus-k8s"
LOKI_APP = "loki-k8s"
GRAFANA_APP = "grafana-k8s"
COS_PORT = "9684"
OPENSEARCH_APP_NAME = "opensearch"
DUMMY_CHARM = "dummy-charm"
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
COS_AGENT_APP_NAME = "grafana-agent"
COS_CHANNEL = "1/stable"
COS_AGENT_RELATION_NAME = "cos-agent"
DB_CLIENT_APP_NAME = "application"

NUM_UNITS_APP = 3
NUM_UNITS_DB = 3
RESOURCE = {
    "opensearch-dashboards-image": METADATA_K8S["resources"]["opensearch-dashboards-image"][
        "upstream-source"
    ]
}

SUBSTRATE = os.environ.get("SUBSTRATE", "vm").lower()
APP_NAME = METADATA_K8S["name"] if SUBSTRATE == "k8s" else METADATA_VM["name"]


@pytest.mark.usefixtures("config_matrix_charm")
class TestOpenSearchDashboards:
    """Grouped tests for OpenSearch Dashboards."""

    @pytest.mark.abort_on_fail
    async def test_build_and_deploy(
        self,
        ops_test: OpsTest,
        ops_test_microk8s: OpsTest,
        charmvm: str,
        charmk8s: str,
        application_charm: str,
        dashboard_tester_charm: str,
        charm_base: str,
        config_matrix_charm: dict,
    ):
        """Deploying all charms required for the tests, and wait for complete setup."""
        charm = charmk8s if SUBSTRATE == "k8s" else charmvm
        tls = config_matrix_charm["tls"]
        traefik = config_matrix_charm["traefik"]
        traefik_trust = config_matrix_charm["traefik_trust"]

        await ops_test.model.set_config(OPENSEARCH_CONFIG)

        await ops_test.model.deploy(
            OPENSEARCH_APP_NAME, channel="2/stable", num_units=NUM_UNITS_DB, config=CONFIG_OPTS
        )
        await ops_test.model.deploy(application_charm, application_name=DB_CLIENT_APP_NAME)
        await ops_test.model.deploy(
            TLS_CERTIFICATES_APP_NAME,
            channel=TLS_STABLE_CHANNEL,
            config={"ca-common-name": "CN_CA"},
        )

        await ops_test.model.integrate(OPENSEARCH_APP_NAME, TLS_CERTIFICATES_APP_NAME)
        await ops_test.model.integrate(DB_CLIENT_APP_NAME, OPENSEARCH_APP_NAME)

        if SUBSTRATE == "vm":
            # Base does not work with grafana-agent charm so continuing using series
            series = "jammy" if charm_base == "ubuntu@22.04" else "noble"
            await ops_test.model.deploy(COS_AGENT_APP_NAME, channel=COS_CHANNEL, series=series)
        else:
            for app in [PROMETHEUS_APP, LOKI_APP, GRAFANA_APP]:
                await ops_test_microk8s.model.deploy(
                    app, application_name=app, channel="2/stable", trust=True
                )

            await ops_test.model.create_offer(
                "opensearch-client", OPENSEARCH_APP_NAME, "opensearch"
            )
            await ops_test_microk8s.model.consume(
                f"admin/{ops_test.model.name}.{OPENSEARCH_APP_NAME}"
            )
            await ops_test.model.create_offer(
                endpoint=f"{TLS_CERTIFICATES_APP_NAME}:certificates,send-ca-cert",
                offer_name="self-signed-certificates",
            )
            await ops_test_microk8s.model.consume(
                f"admin/{ops_test.model.name}.{TLS_CERTIFICATES_APP_NAME}"
            )

        deploy_kwargs = {
            "application_name": APP_NAME,
            "num_units": NUM_UNITS_APP,
            "base": charm_base,
        }
        if SUBSTRATE == "k8s":
            deploy_kwargs["resources"] = RESOURCE

        await ops_test_microk8s.model.deploy(charm, **deploy_kwargs)
        await ops_test.model.wait_for_idle(
            apps=[OPENSEARCH_APP_NAME, TLS_CERTIFICATES_APP_NAME], status="active", timeout=1000
        )

        await ops_test_microk8s.model.integrate(OPENSEARCH_APP_NAME, APP_NAME)

        if SUBSTRATE == "k8s":
            await ops_test_microk8s.model.wait_for_idle(
                apps=[APP_NAME], status="blocked", timeout=1000
            )
        else:
            await ops_test_microk8s.model.wait_for_idle(
                apps=[APP_NAME], status="active", timeout=1000
            )

        await ops_test.model.wait_for_idle(
            apps=[DB_CLIENT_APP_NAME], status="active", timeout=1000
        )

        if traefik:
            await ops_test_microk8s.model.deploy(
                TRAEFIK_APP_NAME, channel="latest/stable", trust=True
            )
            await ops_test_microk8s.model.wait_for_idle(
                apps=[TRAEFIK_APP_NAME], status="active", timeout=1000
            )
            await ops_test_microk8s.model.integrate(APP_NAME, TRAEFIK_APP_NAME)
            await ops_test_microk8s.model.wait_for_idle(
                apps=[APP_NAME, TRAEFIK_APP_NAME], status="active", timeout=1000
            )
        if traefik_trust:
            await ops_test_microk8s.model.integrate(
                f"{TLS_CERTIFICATES_APP_NAME}:send-ca-cert", TRAEFIK_APP_NAME
            )
            await ops_test_microk8s.model.wait_for_idle(
                apps=[APP_NAME, TRAEFIK_APP_NAME], status="active", timeout=1000
            )
        if tls:
            await ops_test_microk8s.model.integrate(APP_NAME, TLS_CERTIFICATES_APP_NAME)
            if not traefik_trust and traefik:
                await ops_test_microk8s.model.integrate(
                    TRAEFIK_APP_NAME, f"{TLS_CERTIFICATES_APP_NAME}:certificates"
                )
                await ops_test_microk8s.model.wait_for_idle(
                    apps=[APP_NAME, TRAEFIK_APP_NAME], status="active", timeout=1000
                )
            elif not SUBSTRATE == "k8s" or traefik:
                await ops_test_microk8s.model.wait_for_idle(
                    apps=[APP_NAME], status="active", timeout=1000
                )
            else:
                await ops_test_microk8s.model.deploy(
                    dashboard_tester_charm, application_name=DUMMY_CHARM
                )
                await ops_test_microk8s.model.wait_for_idle(
                    apps=[APP_NAME], status="blocked", timeout=1000
                )

    @pytest.mark.abort_on_fail
    async def test_dashboard_access(
        self, ops_test: OpsTest, ops_test_microk8s: OpsTest, config_matrix_charm: dict
    ):
        """Test HTTP/HTTPS access based on the group configuration."""
        https_enabled = is_https_enabled(config_matrix_charm)
        verify = config_matrix_charm.get("tls", False)

        assert await access_all_dashboards(
            ops_test, ops_test_microk8s, https=https_enabled, verify=verify
        )
        assert await access_all_prometheus_exporters(ops_test_microk8s)

    @pytest.mark.abort_on_fail
    async def test_dashboard_tls_lifecycle(
        self, ops_test: OpsTest, ops_test_microk8s: OpsTest, config_matrix_charm: dict
    ):
        """Test HTTPS relation lifecycle (breaking and restoring)."""
        tls = config_matrix_charm["tls"]
        traefik = config_matrix_charm["traefik"]
        traefik_trust = config_matrix_charm["traefik_trust"]

        if not tls:
            pytest.skip("Skipping TLS lifecycle test as TLS is disabled in this matrix run.")

        server_cert = (
            "/etc/opensearch-dashboards/certificates/server.pem"
            if SUBSTRATE == "k8s"
            else "/var/snap/opensearch-dashboards/current/etc/opensearch-dashboards/certificates/server.pem"
        )

        unit = ops_test_microk8s.model.applications[APP_NAME].units[0]
        host_cert = get_file_contents(ops_test_microk8s.model.name, unit.name, server_cert)

        # Breaking the relation shouldn't impact service availability
        # A new certificate is requested when the relation is joined again
        await ops_test_microk8s.juju("remove-relation", APP_NAME, TLS_CERTIFICATES_APP_NAME)
        await ops_test.model.wait_for_idle(
            apps=[TLS_CERTIFICATES_APP_NAME], status="active", timeout=1000
        )

        await wait_for_dashboard_idle(ops_test_microk8s, traefik)
        # TLS Broken on relation removal; we check the connection on HTTP (https=False)

        # If traefik enabled it's related to TLS. Breaking TLS relation breaks the traefik charm, so we do not do that
        https = True if not traefik_trust and traefik and tls else False
        assert await access_all_dashboards(ops_test, ops_test_microk8s, https=https, verify=tls)

        # Restore relation for further tests
        await ops_test_microk8s.model.integrate(APP_NAME, TLS_CERTIFICATES_APP_NAME)
        await ops_test.model.wait_for_idle(
            apps=[TLS_CERTIFICATES_APP_NAME], status="active", timeout=1000, idle_period=20
        )
        await wait_for_dashboard_idle(ops_test_microk8s, traefik)
        new_host_cert = get_file_contents(ops_test_microk8s.model.name, unit.name, server_cert)
        assert host_cert != new_host_cert

        # Verify HTTPS is restored
        assert await access_all_dashboards(
            ops_test,
            ops_test_microk8s,
            https=is_https_enabled(config_matrix_charm),
            verify=tls,
        )

    @pytest.mark.abort_on_fail
    async def test_dashboard_client_data_access(
        self, ops_test: OpsTest, ops_test_microk8s: OpsTest, config_matrix_charm: dict
    ):
        """Test API access to each dashboard unit."""
        client_relation = get_relations(ops_test, OPENSEARCH_RELATION_NAME, DB_CLIENT_APP_NAME)[0]

        # Loading data to Opensearch
        dicts = [
            {"index": {"_index": "albums", "_id": "2"}},
            {"artist": "Herbie Hancock", "genre": ["Jazz"], "title": "Head Hunters"},
            {"index": {"_index": "albums", "_id": "3"}},
            {"artist": "Lydian Collective", "genre": ["Jazz"], "title": "Adventure"},
            {"index": {"_index": "albums", "_id": "4"}},
            {
                "artist": "Liquid Tension Experiment",
                "genre": ["Prog", "Metal"],
                "title": "Liquid Tension Experiment 2",
            },
        ]
        data_dicts = [d for d in dicts if "index" not in d.keys()]

        payload = "\n".join([json.dumps(d) for d in dicts]) + "\n"

        unit_name = ops_test.model.applications[DB_CLIENT_APP_NAME].units[0].name
        await client_run_db_request(
            ops_test,
            unit_name,
            client_relation,
            "POST",
            "/_bulk?refresh=true",
            re.escape(payload),
        )

        # Checking if data got to the DB indeed
        read_db_data = await client_run_db_request(
            ops_test, unit_name, client_relation, "GET", "/albums/_search"
        )
        results = json.loads(read_db_data["results"])
        logging.info(f"Loaded into the database: {results}")

        # Same amount and content of data as uploaded
        assert len(data_dicts) == len(results["hits"]["hits"])
        assert all([hit["_source"] in data_dicts for hit in results["hits"]["hits"]])

        result = await client_run_all_dashboards_request(
            ops_test,
            ops_test_microk8s,
            unit_name,
            client_relation,
            "POST",
            "/internal/search/opensearch-with-long-numerals",
            json.dumps(DASHBOARD_QUERY_PARAMS),
            https=is_https_enabled(config_matrix_charm),
        )

        # Each dashboard query reports the same result as the uploaded data
        assert all(len(data_dicts) == len(res["hits"]["hits"]) for res in result)
        assert all([hit["_source"] in data_dicts for res in result for hit in res["hits"]["hits"]])

    @pytest.mark.abort_on_fail
    async def test_cos_relations(
        self, ops_test: OpsTest, ops_test_microk8s: OpsTest, config_matrix_charm: dict
    ):
        traefik = config_matrix_charm["traefik"]
        if SUBSTRATE == "k8s":
            await ops_test_microk8s.model.integrate(f"{APP_NAME}:metrics-endpoint", PROMETHEUS_APP)
            await ops_test_microk8s.model.integrate(f"{APP_NAME}:logging", LOKI_APP)
            await ops_test_microk8s.model.integrate(f"{APP_NAME}:grafana-dashboard", GRAFANA_APP)

            await ops_test_microk8s.model.wait_for_idle(
                apps=[PROMETHEUS_APP, LOKI_APP, GRAFANA_APP],
                status="active",
                timeout=1000,
                idle_period=30,
            )

            if traefik:
                await ops_test_microk8s.model.wait_for_idle(
                    apps=[PROMETHEUS_APP, LOKI_APP, GRAFANA_APP],
                    status="active",
                    timeout=1000,
                    idle_period=30,
                )
            else:
                await ops_test_microk8s.model.wait_for_idle(
                    apps=[APP_NAME],
                    status="blocked",
                    timeout=1000,
                    idle_period=30,
                )

            expected_results = {
                "metrics_path": "/metrics",
                "static_configs": [{"targets": [f"*:{COS_PORT}"]}],
                "scheme": "http",
            }

            prom_unit = ops_test_microk8s.model.applications[PROMETHEUS_APP].units[0]

            rc, stdout, stderr = await ops_test_microk8s.juju(
                "show-unit", prom_unit.name, "--format=yaml"
            )
            assert rc == 0, f"Failed to get unit data: {stderr}"

            unit_data = yaml.safe_load(stdout)[prom_unit.name]

            metrics_relation = None
            for relation in unit_data.get("relation-info", []):
                related_units = relation.get("related-units", {})
                if any(unit.startswith(f"{APP_NAME}/") for unit in related_units.keys()):
                    metrics_relation = relation
                    break

            assert metrics_relation is not None, f"Could not find relation data for {APP_NAME}"

            app_databag = metrics_relation.get("application-data", {})
            published_jobs = json.loads(app_databag.get("scrape_jobs", "[]"))

            assert len(published_jobs) > 0, "No scrape jobs were published to the relation."

            unit_cos_config = published_jobs[0]
            for key, value in expected_results.items():
                assert unit_cos_config[key] == value

        else:
            await ops_test.model.integrate(COS_AGENT_APP_NAME, APP_NAME)
            await ops_test.model.wait_for_idle(
                apps=[APP_NAME], status="active", timeout=1000, idle_period=30
            )
            await ops_test.model.wait_for_idle(
                apps=[COS_AGENT_APP_NAME], status="blocked", timeout=1000, idle_period=30
            )

            expected_results = [
                {
                    "metrics_path": "/metrics",
                    "scheme": "http",
                }
            ]
            agent_unit = ops_test.model.applications[COS_AGENT_APP_NAME].units[0]
            for unit in ops_test.model.applications[APP_NAME].units:
                unit_ip = await get_address(ops_test, unit.name, APP_NAME)
                relation_data = get_unit_relation_data(
                    ops_test.model.name, agent_unit.name, COS_AGENT_RELATION_NAME
                )
                expected_results[0]["static_configs"] = [{"targets": [f"{unit_ip}:9684"]}]
                unit_data = relation_data[unit.name]
                unit_cos_config = json.loads(unit_data["data"]["config"])
                for key, value in expected_results[0].items():
                    assert unit_cos_config["metrics_scrape_jobs"][0][key] == value

    @pytest.mark.abort_on_fail
    async def test_log_level_change(
        self, ops_test: OpsTest, ops_test_microk8s: OpsTest, config_matrix_charm: dict
    ):
        log_path = "/var/snap/opensearch-dashboards/common/var/log/opensearch-dashboards/opensearch_dashboards.log"
        container = ""
        traefik = config_matrix_charm["traefik"]
        if SUBSTRATE == "k8s":
            log_path = "/var/log/opensearch-dashboards/opensearch_dashboards.log"
            container = "opensearch-dashboards"

        for unit in ops_test_microk8s.model.applications[APP_NAME].units:
            assert count_lines_with(
                ops_test_microk8s.model_full_name, unit.name, log_path, "debug", container
            )

            await ops_test_microk8s.model.applications[APP_NAME].set_config({"log_level": "ERROR"})

            await wait_for_dashboard_idle(ops_test_microk8s, traefik)
            debug_lines = count_lines_with(
                ops_test_microk8s.model_full_name, unit.name, log_path, "debug", container
            )

            assert (
                count_lines_with(
                    ops_test_microk8s.model_full_name, unit.name, log_path, "debug", container
                )
                == debug_lines
            )

        await ops_test_microk8s.model.applications[APP_NAME].set_config({"log_level": "INFO"})
        await wait_for_dashboard_idle(ops_test_microk8s, traefik)

    @pytest.mark.abort_on_fail
    async def test_dashboard_status_changes(
        self, ops_test: OpsTest, ops_test_microk8s: OpsTest, config_matrix_charm: dict
    ):
        """Test status changes based on backend failures."""
        tls = config_matrix_charm["tls"]
        traefik = config_matrix_charm["traefik"]
        logger.info("Breaking opensearch connection")
        await ops_test_microk8s.juju("remove-relation", "opensearch", APP_NAME)
        await ops_test.model.wait_for_idle(
            apps=[OPENSEARCH_APP_NAME], status="active", timeout=1000
        )

        async with ops_test_microk8s.fast_forward("30s"):
            await ops_test_microk8s.model.wait_for_idle(apps=[APP_NAME], status="blocked")

        logger.info("Waiting up to 600s for status OpenSearch connection is missing")
        async with ops_test_microk8s.fast_forward("30s"):
            timeout = 600
            start_time = time.time()
            status_matched = False

            while time.time() - start_time < timeout:
                status_matched = await check_full_status(
                    ops_test_microk8s,
                    app_name=APP_NAME,
                    status="blocked",
                    status_msg="OpenSearch connection is missing",
                )
                if status_matched:
                    break

                await asyncio.sleep(10)

            assert status_matched

        logger.info("Checking if Dashboards have become unavailable")
        assert await all_dashboards_unavailable(ops_test_microk8s, https=tls)

        logger.info("Restoring Opensearch connection")
        await ops_test_microk8s.model.integrate(APP_NAME, OPENSEARCH_APP_NAME)
        await ops_test.model.wait_for_idle(
            apps=[OPENSEARCH_APP_NAME], status="active", timeout=1000
        )
        if not SUBSTRATE == "k8s" or traefik:
            await ops_test_microk8s.model.wait_for_idle(
                apps=[APP_NAME], status="active", timeout=1000
            )
            assert ops_test_microk8s.model.applications[APP_NAME].status == "active"
            assert all(
                unit.workload_status == "active"
                for unit in ops_test_microk8s.model.applications[APP_NAME].units
            )
        else:
            await ops_test_microk8s.model.wait_for_idle(
                apps=[APP_NAME], status="blocked", timeout=1000
            )
            assert ops_test_microk8s.model.applications[APP_NAME].status == "blocked"
            assert all(
                unit.workload_status == "blocked"
                for unit in ops_test_microk8s.model.applications[APP_NAME].units
            )

        logger.info("Checking if Dashboards is available again")
        assert await access_all_dashboards(
            ops_test,
            ops_test_microk8s,
            https=is_https_enabled(config_matrix_charm),
            verify=tls,
        )

        logger.info(
            "Adding a new index with shards allocated to a non-existent node to make cluster health red"
        )
        client_relation = get_relations(ops_test, OPENSEARCH_RELATION_NAME, DB_CLIENT_APP_NAME)[0]

        payload = {
            "settings": {
                "index.routing.allocation.require._name": "non_existent_node",
                "index.number_of_shards": 5,
                "index.number_of_replicas": 0,
            }
        }

        payload = json.dumps(payload)

        unit_name = ops_test.model.applications[DB_CLIENT_APP_NAME].units[0].name
        await client_run_db_request(
            ops_test,
            unit_name,
            client_relation,
            "PUT",
            "/bad_index",
            re.escape(payload),
        )
        logger.info("Waiting up to 600s for OpenSearch service health to become red...")
        async with ops_test_microk8s.fast_forward("30s"):
            timeout = 600
            start_time = time.time()
            status_matched = False

            while time.time() - start_time < timeout:
                status_matched = await check_full_status(
                    ops_test_microk8s,
                    app_name=APP_NAME,
                    status="blocked",
                    status_msg="The OpenSearch service health is red",
                )
                if status_matched:
                    break

                await asyncio.sleep(10)

            assert status_matched

    @pytest.mark.skip(reason="https://warthogs.atlassian.net/browse/DPE-5073")
    async def test_restore_opensearch_restores_osd(
        self, ops_test: OpsTest, ops_test_microk8s: OpsTest, config_matrix_charm: dict
    ):
        """This test shouldn't be separate but a native continuation of the previous one."""
        tls = config_matrix_charm["tls"]
        logger.info("Destroying and restoring the Opensearch cluster")
        await destroy_cluster(ops_test, app=OPENSEARCH_APP_NAME)

        await ops_test.model.deploy(
            OPENSEARCH_APP_NAME,
            channel="2/edge",
            num_units=NUM_UNITS_DB,
            config=CONFIG_OPTS,
        )

        if tls:
            await ops_test.model.integrate(OPENSEARCH_APP_NAME, TLS_CERTIFICATES_APP_NAME)

        async with ops_test.fast_forward("30s"):
            await ops_test.model.wait_for_idle(apps=[OPENSEARCH_APP_NAME], status="blocked")

        await ops_test_microk8s.model.integrate(APP_NAME, OPENSEARCH_APP_NAME)

        await ops_test.model.wait_for_idle(
            apps=[OPENSEARCH_APP_NAME], status="active", timeout=1000
        )

        await ops_test_microk8s.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

        logger.info("Checking if Dashboards is available again")
        assert await access_all_dashboards(
            ops_test, ops_test_microk8s, https=is_https_enabled(config_matrix_charm)
        )
