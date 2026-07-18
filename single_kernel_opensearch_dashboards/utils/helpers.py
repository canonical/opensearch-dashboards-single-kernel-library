#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Helper methods for Opensearch Dashboards charm."""

import json
import logging

from data_platform_helpers.version_check import get_charm_revision
from ops import EventBase, ModelError

from single_kernel_opensearch_dashboards.lib.charms.tls_certificates_interface.v3.tls_certificates import (
    CharmBase,
)

logger = logging.getLogger(__name__)


def update_grafana_dashboards_title(charm: CharmBase) -> None:
    """Update the title of the Grafana dashboard file to include the charm revision."""
    revision = get_charm_revision(charm.model.unit)
    dashboard_path = charm.charm_dir / "src/grafana_dashboards/dashboard.json"

    try:
        dashboard = json.loads(dashboard_path.read_text())
        if not isinstance(dashboard, dict):
            logger.error(
                "Dashboard %s is not a JSON object, skipping title update", dashboard_path.name
            )
            return

        old_title = dashboard.get("title", "Charmed OpenSearch Dashboards")
        if not isinstance(old_title, str):
            old_title = "Charmed OpenSearch Dashboards"
        title_prefix = old_title.split(" - Rev")[0]
        new_title = f"{title_prefix} - Rev {revision}"
        dashboard["title"] = new_title

        logger.info(
            "Changing the title of dashboard %s from %s to %s",
            dashboard_path.name,
            old_title,
            new_title,
        )

        dashboard_path.write_text(json.dumps(dashboard, indent=4))
    except (OSError, json.JSONDecodeError) as e:
        logger.error("Failed to update the title of dashboard %s: %s", dashboard_path.name, e)


def is_app_removal(charm: CharmBase, event: EventBase) -> bool:
    """Returns True if the local application, or this unit specifically, is going down.

    Args:
        charm: the charm to check.
        event: the event being handled, if it carries a `departing_unit` (e.g. a
            peer `relation-departed` event) this unit is checked against it.
    """
    if getattr(event, "departing_unit", None) == charm.unit:
        return True

    try:
        return charm.app.planned_units() == 0
    except ModelError:
        # juju check planned units for charm using `goal-state` for all model
        # `goal-state` can fail to resolve the full model state (e.g. a cross-model
        # relation's remote offer is already gone), even though we only care about
        # our own app. Assume the app is going down because that can happen only if model is being destroyed.
        return True
