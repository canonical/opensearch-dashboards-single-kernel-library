#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

from types import SimpleNamespace
from unittest.mock import MagicMock

from single_kernel_opensearch_dashboards.common.literals import RelDepartureReason
from single_kernel_opensearch_dashboards.utils import helpers
from single_kernel_opensearch_dashboards.utils.helpers import relation_departure_reason


def _goal(status: str) -> SimpleNamespace:
    return SimpleNamespace(status=status)


def _goal_state(units=None, relations=None) -> SimpleNamespace:
    return SimpleNamespace(units=units or {}, relations=relations or {})


def _charm(app_name: str = "opensearch-dashboards") -> MagicMock:
    charm = MagicMock()
    charm.app.name = app_name
    return charm


def test_relation_departure_reason_uses_hookcmds_goal_state_for_local_app_removal(mocker):
    mocker.patch(
        "single_kernel_opensearch_dashboards.utils.helpers.hookcmds.goal_state",
        return_value=_goal_state(
            units={
                "opensearch-dashboards/0": _goal("dying"),
                "opensearch-dashboards/1": _goal("dying"),
            }
        ),
    )

    assert (
        relation_departure_reason(_charm(), "dashboard_peers", "opensearch-dashboards")
        == RelDepartureReason.APP_REMOVAL
    )


def test_relation_departure_reason_detects_remote_scale_down(mocker):
    mocker.patch(
        "single_kernel_opensearch_dashboards.utils.helpers.hookcmds.goal_state",
        return_value=_goal_state(
            relations={
                "opensearch-client": {
                    "opensearch": _goal("alive"),
                    "opensearch/0": _goal("alive"),
                    "opensearch/1": _goal("dying"),
                }
            }
        ),
    )

    assert (
        relation_departure_reason(_charm(), "opensearch-client", "opensearch")
        == RelDepartureReason.SCALE_DOWN
    )


def test_relation_departure_reason_treats_goal_state_error_as_relation_broken(mocker):
    mocker.patch(
        "single_kernel_opensearch_dashboards.utils.helpers.hookcmds.goal_state",
        side_effect=helpers.hookcmds.Error(
            returncode=1, cmd=["goal-state"], stderr="cross-model relation"
        ),
    )

    assert (
        relation_departure_reason(_charm(), "opensearch-client", "opensearch")
        == RelDepartureReason.REL_BROKEN
    )
