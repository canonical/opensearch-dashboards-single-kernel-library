#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Model for the ingress relation"""

import json
import logging
from typing import MutableMapping

from ops.model import Relation
from pydantic import BaseModel, Field
from urllib3.util import parse_url

logger = logging.getLogger(__name__)


class IngressModel(BaseModel):
    """State collection of the Ingress relation metadata for the requirer."""

    # The ingress URL published by the provider
    url: str | None = Field(default=None)

    @classmethod
    def from_relation(cls, relation: Relation | None) -> "IngressModel":
        """Build the model from the ingress provider's application databag."""
        data: MutableMapping[str, str] = (
            relation.data[relation.app] if relation and relation.app else {}
        )
        payload = json.loads(data.get("ingress") or "{}")
        return cls.model_validate({"url": payload.get("url")})

    @property
    def base_path(self) -> str | None:
        """Return the ingress base path."""
        ingress_url = self.url
        if not ingress_url:
            return None
        return parse_url(ingress_url).path
