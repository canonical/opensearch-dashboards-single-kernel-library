#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm-specific exceptions."""


class OSDError(Exception):
    """Charm-specific parent exception."""


class OSDAPIError(OSDError):
    """Exception relating to OSD API access."""


class OSDInstallError(OSDError):
    """Exception relating to OSD installation issues."""


class OSDFileOperationError(OSDError):
    """Exception thrown when file operations related to OSD fail."""


class OSDTLSMissingDataError(OSDError):
    """Raised when required TLS relation data is missing."""


class OSDNotTrusted(OSDError):
    """Raised when K8s charm is not trusted."""
