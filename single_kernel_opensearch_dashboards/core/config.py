#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm config."""

from typing import Literal

from single_kernel_opensearch_dashboards.lib.charms.data_platform_libs.v1.data_models import BaseConfigModel

class CharmConfig(BaseConfigModel):
    """Structured charm config."""

    log_level: Literal["ERROR", "WARNING", "INFO"]
