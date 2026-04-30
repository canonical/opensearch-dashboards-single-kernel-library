#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import logging
import re
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
)

logger = logging.getLogger(__name__)

METADATA_VM = yaml.safe_load(Path("tests/charms/vm/metadata.yaml").read_text())
METADATA_K8S = yaml.safe_load(Path("tests/charms/k8s/metadata.yaml").read_text())
PROMETHEUS_APP = "prometheus-k8s"
LOKI_APP = "loki-k8s"
GRAFANA_APP = "grafana-k8s"
COS_PORT = "9684"
APP_NAME = METADATA_VM["name"]
APP_NAME_K8S = METADATA_K8S["name"]
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
        series: str,
        config_matrix: dict,
    ):
        """Deploying all charms required for the tests, and wait for complete setup."""
        is_cross_model = ops_test.model.name != ops_test_microk8s.model.name
        app_name = APP_NAME_K8S if is_cross_model else APP_NAME
        tls = config_matrix["tls"]
        traefik = config_matrix["traefik"]
        traefik_trust = config_matrix["traefik_trust"]
        active_vm = [OPENSEARCH_APP_NAME, TLS_CERTIFICATES_APP_NAME]
        charm = charmk8s if is_cross_model else charmvm
        config = {"ca-common-name": "CN_CA"}
        deploy_kwargs = {
            "application_name": app_name,
            "num_units": NUM_UNITS_APP,
            "series": series,
        }

        # 1. Deploy OpenSearch, Certificates, and Test Application charms and COS
        await ops_test.model.set_config(OPENSEARCH_CONFIG)

        await ops_test.model.deploy(
            OPENSEARCH_APP_NAME, channel="2/edge", num_units=NUM_UNITS_DB, config=CONFIG_OPTS
        )
        await ops_test.model.deploy(application_charm, application_name=DB_CLIENT_APP_NAME)
        await ops_test.model.deploy(
            TLS_CERTIFICATES_APP_NAME, channel=TLS_STABLE_CHANNEL, config=config
        )

        await ops_test.model.integrate(OPENSEARCH_APP_NAME, TLS_CERTIFICATES_APP_NAME)
        await ops_test.model.integrate(DB_CLIENT_APP_NAME, OPENSEARCH_APP_NAME)

        if not is_cross_model:
            await ops_test.model.deploy(COS_AGENT_APP_NAME, channel=COS_CHANNEL, series=series)
        else:
            await ops_test_microk8s.model.deploy(
                PROMETHEUS_APP,
                application_name=PROMETHEUS_APP,
                channel="2/stable",
                trust=True,
            )
            await ops_test_microk8s.model.deploy(
                LOKI_APP,
                application_name=LOKI_APP,
                channel="2/stable",
                trust=True,
            )
            await ops_test_microk8s.model.deploy(
                GRAFANA_APP,
                application_name=GRAFANA_APP,
                channel="2/stable",
                trust=True,
            )
        # 2. Deploy Dashboards Charm
        if is_cross_model:
            deploy_kwargs["resources"] = RESOURCE
            # Create offers if testing k8s
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
                f"admin/{ops_test.model_name}.{TLS_CERTIFICATES_APP_NAME}"
            )

        await ops_test_microk8s.model.deploy(charm, **deploy_kwargs)

        await ops_test.model.wait_for_idle(apps=active_vm, status="active", timeout=1000)
        await ops_test_microk8s.model.integrate(OPENSEARCH_APP_NAME, app_name)

        if is_cross_model:
            await ops_test_microk8s.model.deploy(
                TRAEFIK_APP_NAME, channel="latest/stable", trust=True
            )
            await ops_test_microk8s.model.wait_for_idle(
                apps=[app_name], status="blocked", timeout=1000
            )
        else:
            await ops_test_microk8s.model.wait_for_idle(
                apps=[app_name], status="active", timeout=1000
            )

        await ops_test.model.wait_for_idle(
            apps=[DB_CLIENT_APP_NAME], status="active", timeout=1000
        )

        if traefik:
            await ops_test_microk8s.model.integrate(app_name, TRAEFIK_APP_NAME)
            await ops_test_microk8s.model.wait_for_idle(
                apps=[app_name, TRAEFIK_APP_NAME], status="active", timeout=1000
            )
        if traefik_trust:
            await ops_test_microk8s.model.integrate(
                f"{TLS_CERTIFICATES_APP_NAME}:send-ca-cert", TRAEFIK_APP_NAME
            )
            await ops_test_microk8s.model.wait_for_idle(
                apps=[app_name, TRAEFIK_APP_NAME], status="active", timeout=1000
            )
        if tls:
            await ops_test_microk8s.model.integrate(app_name, TLS_CERTIFICATES_APP_NAME)
            if not traefik_trust and traefik:
                await ops_test_microk8s.model.integrate(
                    TRAEFIK_APP_NAME, f"TLS_CERTIFICATES_APP_NAME:certificates"
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

    @pytest.mark.abort_on_fail
    async def test_dashboard_access(
        self, ops_test: OpsTest, ops_test_microk8s: OpsTest, config_matrix: dict
    ):
        """Test HTTP/HTTPS access based on the group configuration."""
        tls = config_matrix["tls"]
        traefik_trust = config_matrix["traefik_trust"]
        is_cross_model = ops_test.model.name != ops_test_microk8s.model.name
        traefik = config_matrix["traefik"]
        https = False
        if (
            (traefik and tls and not traefik_trust)
            or (not is_cross_model and tls)
            or (is_cross_model and tls and not traefik)
        ):
            https = True
        verify = True if tls else False
        assert await access_all_dashboards(ops_test, ops_test_microk8s, https=https, verify=verify)
        assert await access_all_prometheus_exporters(ops_test, ops_test_microk8s)

    @pytest.mark.abort_on_fail
    async def test_dashboard_tls_lifecycle(
        self, ops_test: OpsTest, ops_test_microk8s: OpsTest, config_matrix: dict
    ):
        """Test HTTPS relation lifecycle (breaking and restoring)."""
        is_cross_model = ops_test.model.name != ops_test_microk8s.model.name
        app_name = APP_NAME_K8S if is_cross_model else APP_NAME
        tls = config_matrix["tls"]
        traefik = config_matrix["traefik"]
        traefik_trust = config_matrix["traefik_trust"]
        verify = True if tls else False
        if not tls:
            pytest.skip("Skipping TLS lifecycle test as TLS is disabled in this matrix run.")

        server_cert = (
            "/etc/opensearch-dashboards/certificates/server.pem"
            if is_cross_model
            else "/var/snap/opensearch-dashboards/current/etc/opensearch-dashboards/certificates/server.pem"
        )

        unit = ops_test_microk8s.model.applications[app_name].units[0]
        host_cert = get_file_contents(
            ops_test_microk8s.model.name, unit.name, server_cert, is_cross_model
        )

        # Breaking the relation shouldn't impact service availability
        # A new certificate is requested when the relation is joined again
        await ops_test_microk8s.juju("remove-relation", app_name, TLS_CERTIFICATES_APP_NAME)
        await ops_test.model.wait_for_idle(
            apps=[TLS_CERTIFICATES_APP_NAME], status="active", timeout=1000
        )
        await ops_test_microk8s.model.wait_for_idle(apps=[app_name], status="active", timeout=1000)
        if traefik or traefik_trust:
            await ops_test_microk8s.model.wait_for_idle(
                apps=[TRAEFIK_APP_NAME], status="active", timeout=1000
            )
        # TLS Broken on relation removal; we check the connection on HTTP (https=False)

        # If traefik enabled it's related to TLS. Breaking TLS relation breaks the traefik charm, so we do not do that
        https = True if not traefik_trust and traefik and tls else False
        assert await access_all_dashboards(ops_test, ops_test_microk8s, https=https, verify=False)

        # Restore relation for further tests
        await ops_test_microk8s.model.integrate(app_name, TLS_CERTIFICATES_APP_NAME)
        await ops_test.model.wait_for_idle(
            apps=[TLS_CERTIFICATES_APP_NAME], status="active", timeout=1000, idle_period=20
        )
        await ops_test_microk8s.model.wait_for_idle(
            apps=[app_name], status="active", timeout=1000, idle_period=20
        )
        if traefik or traefik_trust:
            await ops_test_microk8s.model.wait_for_idle(
                apps=[TRAEFIK_APP_NAME], status="active", timeout=1000
            )

        new_host_cert = get_file_contents(
            ops_test_microk8s.model.name, unit.name, server_cert, is_cross_model
        )
        assert host_cert != new_host_cert

        https = False
        if (
            (traefik and tls and not traefik_trust)
            or (not is_cross_model and tls)
            or (is_cross_model and tls and not traefik)
        ):
            https = True
        # Verify HTTPS is restored
        assert await access_all_dashboards(ops_test, ops_test_microk8s, https=https, verify=verify)

    @pytest.mark.abort_on_fail
    async def test_dashboard_client_data_access(
        self, ops_test: OpsTest, ops_test_microk8s: OpsTest, config_matrix: dict
    ):
        """Test API access to each dashboard unit."""
        tls = config_matrix["tls"]
        traefik_trust = config_matrix["traefik_trust"]
        is_cross_model = ops_test.model.name != ops_test_microk8s.model.name
        traefik = config_matrix["traefik"]
        https = False
        if (
            (traefik and tls and not traefik_trust)
            or (not is_cross_model and tls)
            or (is_cross_model and tls and not traefik)
        ):
            https = True
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
            https=https,
        )

        # Each dashboard query reports the same result as the uploaded data
        assert all(len(data_dicts) == len(res["hits"]["hits"]) for res in result)
        assert all([hit["_source"] in data_dicts for res in result for hit in res["hits"]["hits"]])

    @pytest.mark.abort_on_fail
    async def test_cos_relations(
        self, ops_test: OpsTest, ops_test_microk8s: OpsTest, config_matrix: dict
    ):
        is_cross_model = ops_test.model.name != ops_test_microk8s.model.name
        if is_cross_model:
            await ops_test_microk8s.model.integrate(
                f"{APP_NAME_K8S}:metrics-endpoint", PROMETHEUS_APP
            )
            await ops_test_microk8s.model.integrate(f"{APP_NAME_K8S}:logging", LOKI_APP)
            await ops_test_microk8s.model.integrate(
                f"{APP_NAME_K8S}:grafana-dashboard", GRAFANA_APP
            )

            await ops_test_microk8s.model.wait_for_idle(
                apps=[APP_NAME_K8S, PROMETHEUS_APP, LOKI_APP, GRAFANA_APP],
                status="active",
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
                if any(unit.startswith(f"{APP_NAME_K8S}/") for unit in related_units.keys()):
                    metrics_relation = relation
                    break

            assert metrics_relation is not None, f"Could not find relation data for {APP_NAME_K8S}"

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
    async def test_log_level_change(self, ops_test, ops_test_microk8s):
        log_path = "/var/snap/opensearch-dashboards/common/var/log/opensearch-dashboards/opensearch_dashboards.log"
        container = ""
        is_cross_model = ops_test.model.name != ops_test_microk8s.model.name
        app_name = APP_NAME_K8S if is_cross_model else APP_NAME
        if is_cross_model:
            log_path = "/var/log/opensearch-dashboards/opensearch_dashboards.log"
            container = "opensearch-dashboards"

        for unit in ops_test_microk8s.model.applications[app_name].units:
            assert count_lines_with(
                ops_test_microk8s.model_full_name, unit.name, log_path, "debug", container
            )

            await ops_test_microk8s.model.applications[app_name].set_config({"log_level": "ERROR"})
            await ops_test_microk8s.model.wait_for_idle(
                apps=[app_name], status="active", timeout=1000, idle_period=30
            )

            debug_lines = count_lines_with(
                ops_test_microk8s.model_full_name, unit.name, log_path, "debug", container
            )

            assert (
                count_lines_with(
                    ops_test_microk8s.model_full_name, unit.name, log_path, "debug", container
                )
                == debug_lines
            )

        await ops_test_microk8s.model.applications[app_name].set_config({"log_level": "INFO"})
        await ops_test_microk8s.model.wait_for_idle(
            apps=[app_name], status="active", timeout=1000, idle_period=30
        )

    @pytest.mark.abort_on_fail
    async def test_dashboard_status_changes(
        self, ops_test: OpsTest, ops_test_microk8s: OpsTest, config_matrix: dict
    ):
        """Test status changes based on backend failures."""
        is_cross_model = ops_test.model.name != ops_test_microk8s.model.name
        app_name = APP_NAME_K8S if is_cross_model else APP_NAME
        tls = config_matrix["tls"]
        traefik = config_matrix["traefik"]
        traefik_trust = config_matrix["traefik_trust"]
        https = False
        if (
            (traefik and tls and not traefik_trust)
            or (not is_cross_model and tls)
            or (is_cross_model and tls and not traefik)
        ):
            https = True
        verify = True if tls else False
        logger.info("Breaking opensearch connection")
        await ops_test_microk8s.juju("remove-relation", "opensearch", app_name)
        await ops_test.model.wait_for_idle(
            apps=[OPENSEARCH_APP_NAME], status="active", timeout=1000
        )

        async with ops_test_microk8s.fast_forward("30s"):
            await ops_test_microk8s.model.wait_for_idle(apps=[app_name], status="blocked")

        assert await check_full_status(
            ops_test_microk8s,
            app_name=app_name,
            status="blocked",
            status_msg="OpenSearch connection is missing",
        )

        logger.info("Checking if Dashboards have become unavailable")
        assert await all_dashboards_unavailable(ops_test, ops_test_microk8s, https=https)

        logger.info("Restoring Opensearch connection")
        await ops_test_microk8s.model.integrate(app_name, OPENSEARCH_APP_NAME)
        await ops_test.model.wait_for_idle(
            apps=[OPENSEARCH_APP_NAME], status="active", timeout=1000
        )
        await ops_test_microk8s.model.wait_for_idle(apps=[app_name], status="active", timeout=1000)

        assert ops_test_microk8s.model.applications[app_name].status == "active"
        assert all(
            unit.workload_status == "active"
            for unit in ops_test_microk8s.model.applications[app_name].units
        )

        logger.info("Checking if Dashboards is available again")
        assert await access_all_dashboards(ops_test, ops_test_microk8s, https=https, verify=verify)

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
        async with ops_test_microk8s.fast_forward("30s"):
            await ops_test_microk8s.model.wait_for_idle(apps=[app_name], status="blocked")

        assert await check_full_status(
            ops_test_microk8s,
            app_name=app_name,
            status="blocked",
            status_msg="The OpenSearch service health is red",
        )

    @pytest.mark.skip(reason="https://warthogs.atlassian.net/browse/DPE-5073")
    async def test_restore_opensearch_restores_osd(
        self, ops_test: OpsTest, ops_test_microk8s: OpsTest, config_matrix: dict
    ):
        """This test shouldn't be separate but a native continuation of the previous one."""
        is_cross_model = ops_test.model.name != ops_test_microk8s.model.name
        app_name = APP_NAME_K8S if is_cross_model else APP_NAME
        tls = config_matrix["tls"]
        traefik = config_matrix["traefik"]
        traefik_trust = config_matrix["traefik_trust"]
        https = False
        if (
            (traefik and tls and not traefik_trust)
            or (not is_cross_model and tls)
            or (is_cross_model and tls and not traefik)
        ):
            https = True
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

        await ops_test_microk8s.model.integrate(app_name, OPENSEARCH_APP_NAME)

        await ops_test.model.wait_for_idle(
            apps=[OPENSEARCH_APP_NAME], status="active", timeout=1000
        )

        await ops_test_microk8s.model.wait_for_idle(apps=[app_name], status="active", timeout=1000)

        logger.info("Checking if Dashboards is available again")
        assert await access_all_dashboards(ops_test, ops_test_microk8s, https=https)
