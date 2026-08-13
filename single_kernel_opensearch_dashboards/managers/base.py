#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Base manager for common methods"""

import json
import logging
from typing import Any, cast

import requests
from charmlibs.pathops import PathProtocol
from data_platform_helpers.advanced_statuses import ManagerStatusProtocol
from requests import RequestException
from tenacity import Retrying, stop_after_attempt, wait_fixed

from single_kernel_opensearch_dashboards.common.exceptions import (
    OSDAPIError,
)
from single_kernel_opensearch_dashboards.common.literals import (
    DASHBOARD_USER,
    Substrates,
)
from single_kernel_opensearch_dashboards.core.state import ClusterState
from single_kernel_opensearch_dashboards.workload.base import WorkloadBase
from single_kernel_opensearch_dashboards.workload.k8s import K8sWorkload

logger = logging.getLogger(__name__)
HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
}


class BaseManager(ManagerStatusProtocol):
    """Base OSD Manager.

    Include a set of functions and properties useful to other managers.
    """

    state: ClusterState

    def __init__(self, state: ClusterState, workload: WorkloadBase):
        self.state = state
        self.workload = workload

    def request_opensearch(
        self,
        uri: str,
        method: str = "GET",
        headers: dict | None = None,
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """Issue a "raw"" HTTP(S) request to the OS Rest API.

        Thin wrapper around the Python 'requests' call to access OS API.
        Catching no errors/exceptions.

        Args:
            uri: URI of Opensearch
            method: matching the known http methods.
            headers: request headers as a dict
            payload: JSON / map body payload.
        Returns:
            status_code of request
            JSON body of request
        Raises:
            ReadTimeout: We distinguish if the service was fully unresponsive
            RequestException (including any descendants from requests.exceptions)
            JSONDecodeError
        """
        if headers is None:
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
            }

        return self._request(
            uri,
            method=method,
            headers=headers,
            payload=payload,
            cert_path=self.workload.paths.opensearch_ca,
        )

    def request_opensearch_dashboards(
        self,
        endpoint: str,
        method: str = "GET",
        headers: dict | None = None,
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """Issue a "raw"" HTTP(S) request to the OSD Rest API.

        Thin wrapper around the Python 'requests' call to access OSD API.
        Catching no errors/exceptions.

        Args:
            endpoint: relative to the base uri.
            method: matching the known http methods.
            headers: request headers as a dict
            payload: JSON / map body payload.
        Returns:
            status_code of request
            JSON body of request
        Raises:
            ReadTimeout: We distinguish if the service was fully unresponsive
            RequestException (including any descendants from requests.exceptions)
            JSONDecodeError
        """
        if headers is None:
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "osd-xsrf": "osd-true",
            }
        uri = f"{self.state.url}{endpoint}"
        return self._request(
            uri,
            method=method,
            headers=headers,
            payload=payload,
            cert_path=self.workload.paths.ca,
        )

    def _request(
        self,
        uri: str,
        cert_path: PathProtocol,
        method: str = "GET",
        headers: dict | None = None,
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """Issue a raw authenticated HTTP(S) request to the OpenSearch  or OSD API.

        This method acts as a thin wrapper around `requests.Session`, providing
        automatic retries and authentication using credentials stored in the
        cluster state.

        Args:
        uri: The full destination URL for the request.
        cert_path: Path to the CA certificate used for SSL verification.
        method: HTTP method to use (e.g., "GET", "POST", "PUT"). Defaults to "GET".
        headers: Optional dictionary of HTTP headers to include.
        payload: Optional dictionary to be serialized as JSON in the request body.

        Returns:
        A tuple containing:
            - int: The HTTP status code of the response.
            - dict[str, Any]: The parsed JSON response body.

        Raises:
            OSDAPIError: If the OpenSearch server connection/credentials are missing.
            requests.ReadTimeout: If the request times out after the specified duration.
            requests.RequestException: For general transport-layer or HTTP errors
                after retries are exhausted.
            requests.exceptions.JSONDecodeError: If the response body is not valid JSON.
        """
        if not self.state.opensearch_server or not self.state.opensearch_server.password:
            raise OSDAPIError(
                "Can't query API, no Opensearch connection (i.e. no OSD credentials)."
            )
        request_kwargs = {
            "url": uri,
            "method": method.upper(),
            "verify": cert_path.as_posix(),
            "headers": headers,
            "timeout": (5, 5),
            "data": json.dumps(payload),
        }
        path = None
        if self.state.substrate == Substrates.K8S:
            workload = cast(K8sWorkload, self.workload)
            cert = (
                self.state.unit_server.ca
                if cert_path == self.workload.paths.ca
                else self.state.opensearch_server.tls_ca
            )
            path = workload.write_certs(cert)
            request_kwargs["verify"] = path

        try:
            with requests.Session() as s:
                s.auth = (  # type: ignore [reportAttributeAccessIssue]
                    DASHBOARD_USER,
                    self.state.opensearch_server.password,
                )
                for attempt in Retrying(
                    stop=stop_after_attempt(3),
                    wait=wait_fixed(1),
                    reraise=True,
                ):
                    with attempt:
                        resp = s.request(**request_kwargs)
                        resp.raise_for_status()

        except requests.ReadTimeout as e:
            logger.error(f"Hanging, no response from {uri}: {e}.")
            raise
        except RequestException as e:
            logger.warning(f"Request {method} to {uri} with payload: {payload} failed. \n{e}")
            raise
        finally:
            if self.state.substrate == Substrates.K8S and path:
                workload = cast(K8sWorkload, self.workload)
                workload.remove_certs(path)
        try:
            return resp.status_code, resp.json()
        except requests.exceptions.JSONDecodeError:
            logger.error(f"Failed to decode JSON from {uri}")
            raise
