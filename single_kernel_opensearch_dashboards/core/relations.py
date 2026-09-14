#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Relations wrappers used by state to access models"""

import json
import logging
from typing import Any

import ops
from dpcharmlibs.interfaces import (
    AbstractRepository,
    build_model,
    write_model,
)
from pydantic import BaseModel

from single_kernel_opensearch_dashboards.core.relation_models import (
    IngressModel,
    OAuthModel,
    OSDClusterModel,
    OSDServerModel,
    UpgradeUnitModel,
)

logger = logging.getLogger(__name__)


class RelationState:
    """Base wrapper for models"""

    # Wrapper-internal attributes that must never reach databag
    # Used to avoid setattr on this fields
    RESERVED_ATTRS = {"repository", "component", "relation", "model"}

    def __init__(
        self,
        model_cls: type[BaseModel],
        repository: AbstractRepository | None,
        component: ops.model.Unit | ops.model.Application | None = None,
    ) -> None:
        self.repository = repository
        self.component = (
            component if component is not None else getattr(repository, "component", None)
        )
        self.relation = self.repository.relation if self.repository is not None else None
        self.model = build_model(repository, model_cls) if repository else model_cls()

    def __getattr__(self, name: str) -> Any:
        """Delegate unknown reads to the underlying model."""
        model = self.__dict__.get("model")
        if model is None:
            raise AttributeError(name)
        return getattr(model, name)

    def __setattr__(self, name: str, value: Any) -> None:
        """Update model-field writes to the databag."""
        model = self.__dict__.get("model")
        if (
            name not in self.RESERVED_ATTRS
            and model is not None
            and name in type(model).model_fields
        ):
            self.update({name: value})
        else:
            object.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        """Reset a single model field to its default and write."""
        self.reset(name)

    def reset(self, *names: str) -> None:
        """Reset the given model field(s) to their default(s) in a single write."""
        if not self.repository or self.model is None:
            logger.warning(
                "Fields %s were attempted to be deleted on the relation before it exists.",
                list(names),
            )
            return

        for field in names:
            field_info = type(self.model).model_fields.get(field)
            default = field_info.get_default(call_default_factory=True) if field_info else None
            setattr(self.model, field, default)

        self.write()

    def update(self, items: dict[str, Any]) -> None:
        """Apply the given field changes and update the whole model in a single write."""
        if not self.repository or self.model is None:
            logger.warning(
                "Fields %s were attempted to be written on the relation before it exists.",
                list(items.keys()),
            )
            return

        for field, value in items.items():
            setattr(self.model, field.replace("-", "_"), value)

        self.write()

    def write(self) -> None:
        """Write the whole model."""
        write_model(self.repository, self.model)

        # TODO: remove then https://github.com/canonical/data-platform-libs/issues/272 is fixed
        dumped = self.model.model_dump(mode="json", exclude_none=True)
        for field, value in dumped.items():
            serialized = value if isinstance(value, str) else json.dumps(value)
            if not serialized:
                self.repository.delete_field(field)


class OSDCluster(RelationState):
    """State wrapper for the dashboards application databag."""

    model: OSDClusterModel

    def __init__(
        self,
        repository: AbstractRepository | None,
        component: ops.model.Application,
    ):
        super().__init__(OSDClusterModel, repository, component)


class OSDServer(RelationState):
    """State wrapper for a dashboards unit databag."""

    model: OSDServerModel

    def __init__(
        self,
        repository: AbstractRepository | None,
        component: ops.model.Unit,
    ):
        super().__init__(OSDServerModel, repository, component)


class UpgradeUnit(RelationState):
    """State wrapper for a unit's upgrade peer databag"""

    model: UpgradeUnitModel

    def __init__(
        self,
        repository: AbstractRepository | None,
        component: ops.model.Unit,
    ):
        super().__init__(UpgradeUnitModel, repository, component)


class Ingress(RelationState):
    """State wrapper for the ingress application databag."""

    model: IngressModel

    def __init__(
        self,
        repository: AbstractRepository | None,
        component: ops.model.Application | None,
    ):
        super().__init__(IngressModel, repository, component)


class OAuth(RelationState):
    """State wrapper for the oauth application databag."""

    model: OAuthModel

    def __init__(
        self,
        repository: AbstractRepository | None,
        component: ops.model.Application | None,
        client_secret: str = "",
    ):
        super().__init__(OAuthModel, repository, component)
        self.model.client_secret = client_secret
