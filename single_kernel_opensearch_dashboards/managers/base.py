#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Base manager for common methods"""
import json
import logging
from typing import Any

import requests
from charmlibs.pathops import PathProtocol
from requests import RequestException
from tenacity import RetryCallState, Retrying, stop_after_attempt, wait_fixed

from single_kernel_opensearch_dashboards.common.exceptions import OSDAPIError
from single_kernel_opensearch_dashboards.common.literals import REQUEST_TIMEOUT
from single_kernel_opensearch_dashboards.core.cluster import ClusterState
from single_kernel_opensearch_dashboards.workload.base import WorkloadBase

logger = logging.getLogger(__name__)
HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
}


class BaseManager:
    def __init__(self, state: ClusterState, workload: WorkloadBase):
        self.state = state
        self.workload = workload

    def request_opensearch(
        self,
        uri: str,
        method: str = "GET",
        headers: dict = None,
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
        headers: dict = None,
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
            uri, method=method, headers=headers, payload=payload, cert_path=self.workload.paths.ca
        )

    def _request(
        self,
        uri: str,
        cert_path: PathProtocol,
        method: str = "GET",
        headers: dict = None,
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:

        if not self.state.opensearch_server:
            raise OSDAPIError(
                "Can't query API, no Opensearch connection (i.e. no OSD credentials)."
            )

        request_kwargs = {
            "url": uri,
            "method": method.upper(),
            "verify": cert_path.as_posix(),
            "headers": headers,
            "timeout": REQUEST_TIMEOUT,
            "data": json.dumps(payload),
        }

        def log_retry(retry_state: RetryCallState) -> None:
            """Log retry attempts."""
            logger.debug(
                f"Retrying... Attempt {retry_state.attempt_number}"
                f"\tException: {retry_state.outcome.exception()}"
            )

        try:
            with requests.Session() as s:
                s.auth = (  # type: ignore [reportAttributeAccessIssue]
                    self.state.opensearch_server.username,
                    self.state.opensearch_server.password,
                )
                for attempt in Retrying(
                    stop=stop_after_attempt(3),
                    wait=wait_fixed(1),
                    reraise=True,
                    before_sleep=log_retry,
                ):
                    with attempt:
                        resp = s.request(**request_kwargs)
                        resp.raise_for_status()
        except requests.ReadTimeout as e:
            logger.error(f"Hanging, no response from {uri}: {e}.")
            raise
        except RequestException as e:
            logger.error(f"Request {method} to {uri} with payload: {payload} failed. \n{e}")
            raise

        try:
            return resp.status_code, resp.json()
        except requests.exceptions.JSONDecodeError:
            logger.error(f"Failed to decode JSON from {uri}")
            raise
