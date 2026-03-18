#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Helper methods for Opensearch Dashboards charm."""

import json
import logging

from data_platform_helpers.version_check import get_charm_revision

from single_kernel_opensearch_dashboards.lib.charms.tls_certificates_interface.v3.tls_certificates import (
    CharmBase,
)

logger = logging.getLogger(__name__)


def update_grafana_dashboards_title(charm: CharmBase) -> None:
    """Update the title of the Grafana dashboard file to include the charm revision."""
    revision = get_charm_revision(charm.model.unit)
    dashboard_path = charm.charm_dir / "src/grafana_dashboards/dashboard.json"

    with open(dashboard_path, "r") as file:
        dashboard = json.load(file)

    old_title = dashboard.get("title", "Charmed OpenSearch Dashboards")
    title_prefix = old_title.split(" - Rev")[0]
    new_title = f"{title_prefix} - Rev {revision}"
    dashboard["title"] = new_title

    logger.info(
        "Changing the title of dashboard %s from %s to %s",
        dashboard_path.name,
        old_title,
        new_title,
    )

    with open(dashboard_path, "w") as file:
        json.dump(dashboard, file, indent=4)
