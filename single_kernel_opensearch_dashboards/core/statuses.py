#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

from enum import Enum

from data_platform_helpers.advanced_statuses import StatusObject



class CharmStatuses(Enum):
    """Server related statuses."""

    ACTIVE_IDLE = StatusObject(
        status="active",
        message="",
    )

    REMOVING_UNIT = StatusObject(
        status="maintenance",
        message="Removing unit",
    )


class ConfigStatuses(Enum):
    """Config related statuses."""

    INVALID_CONFIG = StatusObject(
        status="blocked",
        message="One or more config options are invalid",
        short_message="Config options are invalid",
        action="Use `juju config` to set correct values",
    )

    WAITING_FOR_PEER = StatusObject(status="waiting", message="Waiting for peer relation")

    JWT_RELATIONS_DATA_FAILED = StatusObject(
        status="blocked",
        message="Failed to get JWT data from relations",
    )

    MISSING_OAUTH_SECRET = StatusObject(
        status="blocked",
        message="Failed to get OAuth provider info from relation",
        short_message="Failed to get OAuth data",
    )


class TLSStatuses(Enum):
    """Config related statuses."""

    WAITING_FOR_TLS = StatusObject(
        status="maintenance", message="Waiting for TLS to be fully configured", running="async"
    )


class HealthStatuses(Enum):
    STATUS_UNAVAILABLE = StatusObject(
        status="blocked",
        message="OpenSearch Dashboards API is unavailable",
        short_message="API unavailable",
    )

    STATUS_UNKNOWN = StatusObject(
        status="blocked",
        message="OpenSearch Dashboards health status is unknown",
        short_message="Health status is unknown",
    )

    STATUS_HANGING = StatusObject(
        status="blocked",
        message="API does not respond, request hanging",
    )

    STATUS_UNHEALTHY = StatusObject(
        status="blocked",
        message="OpenSearch Dashboards is not in a green health state",
        short_message="Dashboards is not in green health",
    )

    STATUS_ERROR = StatusObject(
        status="blocked",
        message="OpenSearch Dashboards is in an error state",
        short_message="Dashboards is in error",
    )

    DB_UNHEALTHY = StatusObject(
        status="blocked",
        message="The OpenSearch service health is red",
    )

    DB_DOWN = StatusObject(
        status="blocked",
        message="OpenSearch service is (partially or fully) down",
        short_message="OpenSearch is down",
    )

    AFTER_RESTART = StatusObject(
        status="maintenance",
        message="Checking OpenSearch Dashboards health after restart/start",
        short_message="Checking health",
        running="blocking",
    )

    WAITING_FOR_GREEN = StatusObject(
        status="maintenance",
        message="Waiting for OpenSearch Dashboards health to be green",
        short_message="Waiting for green health",
        running="blocking",
    )

    WORKLOAD_IS_DOWN = StatusObject(
        status="blocked",
        message="Service is not alive",
    )


class ServerStatuses(Enum):
    """Server related statuses."""

    STARTING_SERVER = StatusObject(
        status="maintenance", message="Starting OpenSearch Dashboards server", running="blocking"
    )

    RESTARTING_SERVER = StatusObject(
        status="maintenance", message="Restarting OpenSearch Dashboards server", running="blocking"
    )

    INSTALLING_SERVER = StatusObject(
        status="maintenance", message="Installing OpenSearch Dashboards", running="blocking"
    )

    SERVERS_IS_DOWN = StatusObject(status="blocked", message="No server is running")

    DB_CONNECTION_MISSING = StatusObject(
        status="blocked",
        message="OpenSearch connection is missing",
        action="Integrate OpenSearch and OpenSearch Dashboards charms",
    )

    WAITING_ON_RESTART = StatusObject(
        status="waiting", message="Waiting on lock for server start/restart"
    )
    CONTAINER_IS_NOT_ACCESSIBLE = StatusObject(status="blocked", message=MSG_CONTAINER_BLOCKED)

class UpgradeStatuses(Enum):
    DB_INCOMPATIBLE_VERSION = StatusObject(
        status="blocked",
        message="Incompatible OpenSearch and OpenSearch Dashboards versions",
        short_message="Incompatible versions",
    )

    WAITING_FOR_UPGRADE = StatusObject(status="waiting", message="Waiting for upgrade to be idle")
