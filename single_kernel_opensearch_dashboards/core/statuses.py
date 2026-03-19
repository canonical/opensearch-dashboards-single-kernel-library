#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

from enum import Enum

from data_platform_helpers.advanced_statuses import StatusObject

from single_kernel_opensearch_dashboards.common.literals import (
    MSG_AFTER_RESTART,
    MSG_INCOMPATIBLE_UPGRADE,
    MSG_INSTALLING,
    MSG_INVALID_CONFIG,
    MSG_JWT_RELATION_DATA_FAILED,
    MSG_RESTARTING_SERVER,
    MSG_SERVERS_IS_DOWN,
    MSG_STARTING_SERVER,
    MSG_STATUS_DB_DOWN,
    MSG_STATUS_DB_MISSING,
    MSG_STATUS_DB_UNHEALTHY,
    MSG_STATUS_ERROR,
    MSG_STATUS_HANGING,
    MSG_STATUS_OAUTH_INFO_FAILED,
    MSG_STATUS_UNAVAIL,
    MSG_STATUS_UNHEALTHY,
    MSG_STATUS_UNKNOWN,
    MSG_STATUS_WORKLOAD_DOWN,
    MSG_TLS_CONFIG,
    MSG_WAITING_FOR_GREEN,
    MSG_WAITING_FOR_PEER,
    MSG_WAITING_FOR_UPGRADE,
    MSG_WAITING_SERVERS_RESTART,
)


class CharmStatuses(Enum):
    """Server related statuses."""

    ACTIVE_IDLE = StatusObject(
        status="active",
        message="",
    )


class ConfigStatuses(Enum):
    """Config related statuses."""

    INVALID_CONFIG = StatusObject(
        status="blocked",
        message=MSG_INVALID_CONFIG,
        running="blocking",
        action="use `juju config` to set new correct values",
    )

    WAITING_FOR_PEER = StatusObject(status="waiting", message=MSG_WAITING_FOR_PEER)

    JWT_RELATIONS_DATA_FAILED = StatusObject(
        status="blocked",
        message=MSG_JWT_RELATION_DATA_FAILED,
    )

    MISSING_OAUTH_SECRET = StatusObject(
        status="blocked",
        message=MSG_STATUS_OAUTH_INFO_FAILED,
    )


class TLSStatuses(Enum):
    """Config related statuses."""

    WAITING_FOR_TLS = StatusObject(status="maintenance", message=MSG_TLS_CONFIG, running="async")


class HealthStatuses(Enum):
    STATUS_UNAVAILABLE = StatusObject(
        status="blocked",
        message=MSG_STATUS_UNAVAIL,
    )

    STATUS_UNKNOWN = StatusObject(
        status="blocked",
        message=MSG_STATUS_UNKNOWN,
    )

    STATUS_HANGING = StatusObject(
        status="blocked",
        message=MSG_STATUS_HANGING,
    )

    STATUS_UNHEALTHY = StatusObject(
        status="blocked",
        message=MSG_STATUS_UNHEALTHY,
    )

    STATUS_ERROR = StatusObject(
        status="blocked",
        message=MSG_STATUS_ERROR,
    )

    DB_UNHEALTHY = StatusObject(
        status="blocked",
        message=MSG_STATUS_DB_UNHEALTHY,
    )

    DB_DOWN = StatusObject(
        status="blocked",
        message=MSG_STATUS_DB_DOWN,
    )

    AFTER_RESTART = StatusObject(
        status="maintenance", message=MSG_AFTER_RESTART, running="blocking"
    )

    WAITING_FOR_GREEN = StatusObject(
        status="maintenance", message=MSG_WAITING_FOR_GREEN, running="blocking"
    )

    WORKLOAD_IS_DOWN = StatusObject(
        status="blocked",
        message=MSG_STATUS_WORKLOAD_DOWN,
    )


class ServerStatuses(Enum):
    """Server related statuses."""

    STARTING_SERVER = StatusObject(
        status="maintenance", message=MSG_STARTING_SERVER, running="blocking"
    )

    RESTARTING_SERVER = StatusObject(
        status="maintenance", message=MSG_RESTARTING_SERVER, running="blocking"
    )

    INSTALLING_SERVER = StatusObject(
        status="maintenance", message=MSG_INSTALLING, running="blocking"
    )

    SERVERS_IS_DOWN = StatusObject(status="blocked", message=MSG_SERVERS_IS_DOWN)

    DB_CONNECTION_MISSING = StatusObject(
        status="blocked",
        message=MSG_STATUS_DB_MISSING,
        action="integrate OpenSearch and OpenSearch Dashboards charms",
    )

    WAITING_ON_RESTART = StatusObject(status="waiting", message=MSG_WAITING_SERVERS_RESTART)


class UpgradeStatuses(Enum):
    DB_INCOMPATIBLE_VERSION = StatusObject(
        status="blocked",
        message=MSG_INCOMPATIBLE_UPGRADE,
    )

    WAITING_FOR_UPGRADE = StatusObject(status="waiting", message=MSG_WAITING_FOR_UPGRADE)
