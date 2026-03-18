#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""OpenSearch Dashboards Charm for advanced status handling."""
from abc import ABC

from data_platform_helpers.advanced_statuses import StatusHandler

from single_kernel_opensearch_dashboards.core.config import CharmConfig
from single_kernel_opensearch_dashboards.lib.charms.data_platform_libs.v1.data_models import (
    TypedCharmBase,
)


class OpenSearchDashboardsStatusHandler(TypedCharmBase[CharmConfig], ABC):
    # Abstract solution to not create a circular dependency with events.
    # To set running statuses we need to access charm.status_handler
    # which we can only get by passing OpenSearchDashboardsBaseCharm as argument
    # so to not create dependencies we are using separate class
    status_handler: StatusHandler

    def __init__(self, *args):
        super().__init__(*args)
