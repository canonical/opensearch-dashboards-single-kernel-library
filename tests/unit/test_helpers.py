#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import MagicMock

from ops import ModelError

from single_kernel_opensearch_dashboards.utils.helpers import app_going_down


def _charm(planned_units: int) -> MagicMock:
    charm = MagicMock()
    charm.app.planned_units.return_value = planned_units
    return charm


def test_app_going_down_true_when_no_units_planned():
    assert app_going_down(_charm(0)) is True


def test_app_going_down_false_when_units_planned():
    assert app_going_down(_charm(2)) is False


def test_app_going_down_true_when_this_unit_is_departing():
    charm = _charm(2)
    event = MagicMock(departing_unit=charm.unit)
    assert app_going_down(charm, event) is True


def test_app_going_down_false_when_other_unit_is_departing():
    charm = _charm(2)
    event = MagicMock(departing_unit=MagicMock())
    assert app_going_down(charm, event) is False


def test_app_going_down_true_when_planned_units_raises_model_error():
    charm = _charm(2)
    charm.app.planned_units.side_effect = ModelError("saas application not found")
    assert app_going_down(charm) is True
