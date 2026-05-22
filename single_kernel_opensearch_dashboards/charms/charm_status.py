#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""OpenSearch Dashboards Status Handler"""
from abc import abstractmethod
from typing import Protocol

from data_platform_helpers.advanced_statuses import StatusHandler
from ops import CharmEvents, EventBase, Unit


class StatusHandlingCharm(Protocol):
    # Abstract solution to not create a circular dependency with events.
    # To set running statuses we need to access charm.status_handler
    # which we can only get by passing OpenSearchDashboardsBaseCharm as argument
    # so to not create dependencies we are using separate class
    status_handler: StatusHandler
    on: CharmEvents
    unit: Unit

    def __init__(self, *args):
        super().__init__(*args)

    @abstractmethod
    def emit_restart(self, event: EventBase) -> None: ...
