#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from unittest.mock import MagicMock, PropertyMock, patch

from single_kernel_opensearch_dashboards.common.literals import PEERS_REL_NAME

logger = logging.getLogger(__name__)


def test_log_level_changed(harness):
    properties = MagicMock()
    properties.read_text.return_value = "invalid"
    with (
        patch(
            "single_kernel_opensearch_dashboards.workload.base.Paths.properties",
            new_callable=PropertyMock,
            return_value=properties,
        ),
    ):
        harness.charm.state.unit_server.log_level = "INFO"
        harness.charm.config_manager.set_dashboard_properties()
        assert "logging.verbose: true" in properties.write_text.call_args[0][0]
        harness.charm.state.unit_server.log_level = "ERROR"
        harness.charm.config_manager.set_dashboard_properties()
        assert "logging.silent: true" in properties.write_text.call_args[0][0]
        harness.charm.state.unit_server.log_level = "WARNING"
        harness.charm.config_manager.set_dashboard_properties()
        assert "logging.quiet: true" in properties.write_text.call_args[0][0]


def test_tls_disabled(harness):
    assert "server.ssl.enabled: true" not in harness.charm.config_manager.dashboard_properties()


def test_tls_enabled(harness):
    with (
        patch("ops.framework.EventBase.defer"),
        patch("core.state.ClusterState.stable", new_callable=PropertyMock, return_value=True),
    ):
        harness.charm.unit.add_secret(
            {"private-key": "key", "certificate": "cert", "ca-cert": "exists"},
            label=f"{PEERS_REL_NAME}.opensearch-dashboards.unit",
        )

    assert harness.charm.config_manager.dashboard_properties().get("server.ssl.enabled") is True
