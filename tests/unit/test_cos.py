#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import json

from single_kernel_opensearch_dashboards.core.statuses import CharmStatuses
from single_kernel_opensearch_dashboards.managers.cos import COSManager


def _set_dashboard(harness, tmp_path, content: str | None):
    harness.charm.framework.charm_dir = tmp_path
    if content is not None:
        path = tmp_path / COSManager.GRAFANA_DASHBOARD_PATH
        path.parent.mkdir(parents=True)
        path.write_text(content)


def test_update_grafana_dashboards_title(harness, tmp_path, mocker):
    _set_dashboard(harness, tmp_path, '{"title": "Dash - Rev 1"}')
    mocker.patch(
        "single_kernel_opensearch_dashboards.managers.cos.get_charm_revision",
        return_value=2,
    )

    harness.charm.cos_manager.update_grafana_dashboards_title()

    dashboard = json.loads((tmp_path / COSManager.GRAFANA_DASHBOARD_PATH).read_text())
    assert dashboard["title"] == "Dash - Rev 2"


def test_update_grafana_dashboards_title_no_prior_revision(harness, tmp_path, mocker):
    _set_dashboard(harness, tmp_path, '{"title": "Charmed OpenSearch Dashboards"}')
    mocker.patch(
        "single_kernel_opensearch_dashboards.managers.cos.get_charm_revision",
        return_value=167,
    )

    harness.charm.cos_manager.update_grafana_dashboards_title()

    dashboard = json.loads((tmp_path / COSManager.GRAFANA_DASHBOARD_PATH).read_text())
    assert dashboard["title"] == "Charmed OpenSearch Dashboards - Rev 167"


def test_update_grafana_dashboards_title_skips_broken_file(harness, tmp_path, mocker):
    _set_dashboard(harness, tmp_path, "{not json")
    mocker.patch(
        "single_kernel_opensearch_dashboards.managers.cos.get_charm_revision",
        return_value=2,
    )

    harness.charm.cos_manager.update_grafana_dashboards_title()

    assert (tmp_path / COSManager.GRAFANA_DASHBOARD_PATH).read_text() == "{not json"


def test_cos_statuses_active_without_dashboard_file(harness, tmp_path):
    _set_dashboard(harness, tmp_path, None)

    assert harness.charm.cos_manager.get_statuses("unit", recompute=True) == [
        CharmStatuses.ACTIVE_IDLE.value
    ]
