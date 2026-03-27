#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
import responses
from requests import ReadTimeout

from single_kernel_opensearch_dashboards.core.statuses import HealthStatuses

logger = logging.getLogger(__name__)


@pytest.mark.parametrize(
    "harness",
    [
        {
            "add_opensearch": True,
            "opensearch_data": {
                "endpoints": "111.222.333.444:9200,555.666.777.888:9200",
                "tls-ca": "<cert_data_here>",
            },
        }
    ],
    indirect=True,
)
@responses.activate
def test_health_status_ok(harness):
    expected_response = {
        "name": "juju-3e401b-2",
        "uuid": "6e2def14-8870-4a70-bc84-82cdc99823e4",
        "version": {
            "number": "2.13.0",
            "build_hash": "2a9d6dd852c2931f94d292c09ed7a7ba82a43c82",
            "build_number": 7550,
            "build_snapshot": False,
        },
        "status": {
            "overall": {
                "since": "2024-07-06T20:18:31.394Z",
                "state": "green",
                "title": "Green",
                "nickname": "Looking good",
                "icon": "success",
                "uiColor": "secondary",
            },
            "statuses": [
                {
                    "id": "core:opensearch@2.13.0",
                    "message": "You're running OpenSearch Dashboards 2.13.0 with some "
                    "different versions of OpenSearch. Update OpenSearch Dashboards or "
                    "OpenSearch to the same version to prevent compatibility issues: "
                    "v2.14.0 @ 10.230.1.205:9200 (10.230.1.205), v2.14.0 @ "
                    "10.230.1.90:9200 (10.230.1.90)",
                    "since": "2024-07-06T20:18:31.394Z",
                    "state": "green",
                    "icon": "success",
                    "uiColor": "secondary",
                },
                {
                    "id": "core:savedObjects@2.13.0",
                    "message": "SavedObjects service has completed migrations and is available",
                    "since": "2024-07-06T20:18:31.394Z",
                    "state": "green",
                    "icon": "success",
                    "uiColor": "secondary",
                },
                {
                    "id": "plugin:ganttChartDashboards@2.13.0",
                    "message": "All dependencies are available",
                    "since": "2024-07-06T20:18:31.394Z",
                    "state": "green",
                    "icon": "success",
                    "uiColor": "secondary",
                },
            ],
        },
    }

    responses.add(
        method="GET",
        url=f"{harness.charm.state.url}/api/status",
        json=expected_response,
    )

    response = harness.charm.health_manager.dashboards_status()
    assert response[0]
    assert response[1] is None


@pytest.mark.parametrize(
    "harness",
    [
        {
            "add_opensearch": True,
            "opensearch_data": {
                "endpoints": "111.222.333.444:9200,555.666.777.888:9200",
                "tls-ca": "<cert_data_here>",
            },
        }
    ],
    indirect=True,
)
@responses.activate
def test_health_status_service_uniavail(harness):

    responses.add(
        method="GET",
        url=f"{harness.charm.state.url}/api/status",
        status=503,
        body="OpenSearch Dashboards server is not ready yet",
    )

    response = harness.charm.health_manager.dashboards_status()
    assert not response[0]
    assert response[1] == HealthStatuses.STATUS_UNAVAILABLE.value


@pytest.mark.parametrize(
    "harness",
    [
        {
            "add_opensearch": True,
            "opensearch_data": {
                "endpoints": "111.222.333.444:9200,555.666.777.888:9200",
                "tls-ca": "<cert_data_here>",
            },
        }
    ],
    indirect=True,
)
@responses.activate
def test_health_status_service_unresponsive(harness):

    responses.add(
        method="GET",
        url=f"{harness.charm.state.url}/api/status",
        status=503,
        body=ReadTimeout(),
    )

    response = harness.charm.health_manager.dashboards_status()
    assert not response[0]
    assert response[1] == HealthStatuses.STATUS_HANGING.value


@pytest.mark.parametrize(
    "harness",
    [
        {
            "add_opensearch": True,
            "opensearch_data": {
                "endpoints": "111.222.333.444:9200,555.666.777.888:9200",
                "tls-ca": "<cert_data_here>",
            },
        }
    ],
    indirect=True,
)
@responses.activate
def test_health_opensearch_ok(harness):

    opensearch_status = {
        "cluster_name": "opensearch-cluster",
        "status": "green",
        "timed_out": "false",
        "number_of_nodes": 2,
        "number_of_data_nodes": 2,
        "discovered_master": "true",
        "active_primary_shards": 6,
        "active_shards": 12,
        "relocating_shards": 0,
        "initializing_shards": 0,
        "unassigned_shards": 0,
        "delayed_unassigned_shards": 0,
        "number_of_pending_tasks": 0,
        "number_of_in_flight_fetch": 0,
        "task_max_waiting_in_queue_millis": 0,
        "active_shards_percent_as_number": 100.0,
    }

    responses.add(
        method="GET",
        url="https://111.222.333.444:9200/_cluster/health",
        status=200,
        json=opensearch_status,
    )

    assert harness.charm.health_manager.opensearch_status()
    response = harness.charm.health_manager.opensearch_status()
    assert response[0]
    assert response[1] is None


@responses.activate
@pytest.mark.parametrize(
    "harness",
    [
        {
            "add_opensearch": True,
            "opensearch_data": {
                "endpoints": "111.222.333.444:9200,555.666.777.888:9200",
                "tls-ca": "<cert_data_here>",
            },
        }
    ],
    indirect=True,
)
@pytest.mark.parametrize("status", [("yellow"), ("red")])
def test_health_opensearch_not_ok(harness, status):
    opensearch_status = {"status": status}
    responses.add(
        method="GET",
        url="https://111.222.333.444:9200/_cluster/health",
        status=200,
        json=opensearch_status,
    )
    properties = MagicMock()
    properties.read_text.return_value = True
    properties.exists.return_value = True
    with (
        patch(
            "single_kernel_opensearch_dashboards.workload.base.Paths.opensearch_ca",
            new_callable=PropertyMock,
            return_value=properties,
        ),
    ):
        # We should make a distinction between unhealthy and down
        if status == "red":
            assert (
                False,
                HealthStatuses.DB_UNHEALTHY.value,
            ) == harness.charm.health_manager.opensearch_status()
        else:
            assert (True, None) == harness.charm.health_manager.opensearch_status()


@responses.activate
@pytest.mark.parametrize(
    "harness",
    [
        {
            "add_opensearch": True,
            "opensearch_data": {
                "endpoints": "111.222.333.444:9200,555.666.777.888:9200",
                "tls-ca": "<cert_data_here>",
            },
        }
    ],
    indirect=True,
)
def test_health_opensearch_unavailable(harness):

    responses.add(
        method="GET",
        url="https://111.222.333.444:9200/_cluster/health",
        status=503,
    )

    properties = MagicMock()
    properties.read_text.return_value = True
    properties.exists.return_value = True
    with (
        patch(
            "single_kernel_opensearch_dashboards.workload.base.Paths.opensearch_ca",
            new_callable=PropertyMock,
            return_value=properties,
        ),
    ):
        assert (
            False,
            HealthStatuses.DB_DOWN.value,
        ) == harness.charm.health_manager.opensearch_status()


@pytest.mark.parametrize(
    "harness",
    [{"add_peer": False, "log_level": "debug", "add_opensearch": True, "mock_network": True}],
    indirect=True,
)
@responses.activate
def test_api_request(harness):
    expected_response = {
        "name": "juju-3e401b-2",
        "uuid": "6e2def14-8870-4a70-bc84-82cdc99823e4",
        "version": {
            "number": "2.13.0",
            "build_hash": "2a9d6dd852c2931f94d292c09ed7a7ba82a43c82",
            "build_number": 7550,
            "build_snapshot": False,
        },
        "status": {
            "overall": {
                "since": "2024-07-06T20:18:31.394Z",
                "state": "green",
                "title": "Green",
                "nickname": "Looking good",
                "icon": "success",
                "uiColor": "secondary",
            },
            "statuses": [
                {
                    "id": "core:opensearch@2.13.0",
                    "message": "You're running OpenSearch Dashboards 2.13.0 with some "
                    "different versions of OpenSearch. Update OpenSearch Dashboards or "
                    "OpenSearch to the same version to prevent compatibility issues: "
                    "v2.14.0 @ 10.230.1.205:9200 (10.230.1.205), v2.14.0 @ "
                    "10.230.1.90:9200 (10.230.1.90)",
                    "since": "2024-07-06T20:18:31.394Z",
                    "state": "green",
                    "icon": "success",
                    "uiColor": "secondary",
                },
                {
                    "id": "core:savedObjects@2.13.0",
                    "message": "SavedObjects service has completed migrations and is available",
                    "since": "2024-07-06T20:18:31.394Z",
                    "state": "green",
                    "icon": "success",
                    "uiColor": "secondary",
                },
                {
                    "id": "plugin:ganttChartDashboards@2.13.0",
                    "message": "All dependencies are available",
                    "since": "2024-07-06T20:18:31.394Z",
                    "state": "green",
                    "icon": "success",
                    "uiColor": "secondary",
                },
            ],
        },
    }
    harness.charm.state.unit_server.relation = MagicMock(name="test")

    responses.add(
        method="GET",
        url=f"{harness.charm.state.url}/api/status",
        json=expected_response,
    )
    status, body = harness.charm.health_manager.request_opensearch_dashboards("/api/status")
    assert all(field in body for field in ["status", "name", "version"])
    assert all(field in body["status"] for field in ["statuses", "overall"])


@pytest.mark.parametrize(
    "harness",
    [{"add_peer": False, "log_level": "debug", "add_opensearch": True, "mock_network": True}],
    indirect=True,
)
@responses.activate
def test_status(harness):
    expected_response = {
        "name": "juju-3e401b-2",
        "uuid": "6e2def14-8870-4a70-bc84-82cdc99823e4",
        "version": {
            "number": "2.13.0",
            "build_hash": "2a9d6dd852c2931f94d292c09ed7a7ba82a43c82",
            "build_number": 7550,
            "build_snapshot": False,
        },
        "status": {
            "overall": {
                "since": "2024-07-06T20:18:31.394Z",
                "state": "green",
                "title": "Green",
                "nickname": "Looking good",
                "icon": "success",
                "uiColor": "secondary",
            },
            "statuses": [
                {
                    "id": "core:opensearch@2.13.0",
                    "message": "You're running OpenSearch Dashboards 2.13.0 with some "
                    "different versions of OpenSearch. Update OpenSearch Dashboards or "
                    "OpenSearch to the same version to prevent compatibility issues: "
                    "v2.14.0 @ 10.230.1.205:9200 (10.230.1.205), v2.14.0 @ "
                    "10.230.1.90:9200 (10.230.1.90)",
                    "since": "2024-07-06T20:18:31.394Z",
                    "state": "green",
                    "icon": "success",
                    "uiColor": "secondary",
                },
                {
                    "id": "core:savedObjects@2.13.0",
                    "message": "SavedObjects service has completed migrations and is available",
                    "since": "2024-07-06T20:18:31.394Z",
                    "state": "green",
                    "icon": "success",
                    "uiColor": "secondary",
                },
                {
                    "id": "plugin:ganttChartDashboards@2.13.0",
                    "message": "All dependencies are available",
                    "since": "2024-07-06T20:18:31.394Z",
                    "state": "green",
                    "icon": "success",
                    "uiColor": "secondary",
                },
            ],
        },
    }

    responses.add(
        method="GET",
        url=f"{harness.charm.state.url}/api/status",
        json=expected_response,
    )

    status, body = harness.charm.health_manager.request_opensearch_dashboards("/api/status")
    assert all(field in body for field in ["status", "name", "version"])
    assert all(field in body["status"] for field in ["statuses", "overall"])


@pytest.mark.parametrize(
    "harness",
    [{"add_peer": False, "log_level": "debug", "add_opensearch": True, "mock_network": True}],
    indirect=True,
)
@responses.activate
def test_request_timeout(harness):
    """ReadTimeout is "bubbled up" to caller."""
    responses.add(method="GET", url=f"{harness.charm.state.url}/api/status", body=ReadTimeout())
    with pytest.raises(ReadTimeout):
        harness.charm.health_manager.request_opensearch_dashboards("/api/status")
