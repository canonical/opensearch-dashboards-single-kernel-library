#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Model for the upgrade peer relation."""

from typing import Iterable, MutableMapping

from ops.model import Relation, Unit
from pydantic import BaseModel, Field


class UpgradeUnitModel(BaseModel):
    """Typed view of a single unit's databag on the upgrade relation."""

    state: str = Field(default="")

    @classmethod
    def from_relation(cls, relation: Relation | None, unit: Unit) -> "UpgradeUnitModel":
        """Build the model from a unit's databag on the upgrade relation."""
        data: MutableMapping[str, str] = relation.data[unit] if relation else {}
        return cls.model_validate({"state": data.get("state", "")})

    @staticmethod
    def all_idle(states: Iterable[str]) -> bool:
        """Whether every unit upgrade state is idle (or empty), i.e. no upgrade in progress."""
        states = list(states)
        return not states or set(states) <= {"", "idle"}
