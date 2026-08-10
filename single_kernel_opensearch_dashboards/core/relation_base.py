#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Machinery for relation models.

Includes:
    - RelationModel: Base model for models fetched from a relation databag through
      ClusterState, which can write themselves back to that databag.
    - bind_model_to_repository: build a model through an interface and bind it for self-writes.

Secret fields (annotated ``ExtraSecretStr``) live directly on the peer models alongside
plain fields; the ``PeerModel`` base handles reading/writing them to their Juju secret
group. This charm is small enough that we don't bother splitting secrets into separate
sibling models.
"""

import json
import logging
from contextlib import contextmanager
from typing import Any, Iterator

from ops.model import SecretNotFoundError
from pydantic import BaseModel, PrivateAttr
from pydantic_core import PydanticSerializationError

from single_kernel_opensearch_dashboards.lib.charms.data_platform_libs.v1.data_interfaces import (
    AbstractRepository,
)
from single_kernel_opensearch_dashboards.lib.charms.data_platform_libs.v1.data_interfaces import (
    build_model as _lib_build_model,
)
from single_kernel_opensearch_dashboards.lib.charms.data_platform_libs.v1.data_interfaces import (
    write_model as _write_model,
)

logger = logging.getLogger(__name__)


def stripped_or_none(value: str | None) -> str | None:
    """Collapse empty or whitespace-only values to None."""
    return (value or "").strip() or None


class RelationModel(BaseModel):
    """Base relation model for models fetched from a relation databag through ClusterState.

    Once a model instance is bound, setting any field immediately writes the whole model
    back to its backing relation databag. Use `.update()` to batch several changes into
    one write.

    - BINDING: `bind()` (via `bind_model_to_repository()`) hands the model its databag
      handle so it can read/write. Before that it's a plain in-memory object. ClusterState
      builds+binds a fresh model on each access, so there's no cache.
    - AUTO-SAVE: assigning any non-private field serializes the whole model back to the
      databag. Writes are skipped for an unbound/read-only model or an app databag on a non-leader unit.
    - BATCHING: `with model.update() as m: ...` saves once at the end; also required when
      mutating a list/dict/set field in place (that doesn't go through `__setattr__`).
    """

    # This model's repository for accessing the databag
    _repository: Any = PrivateAttr(default=None)
    # While > 0, field writes stay in-memory; the single update happens when it returns to 0.
    _update_depth_counter: int = PrivateAttr(default=0)
    # True for models loaded from a databag the charm cannot write (a remote app's/unit's).
    _read_only: bool = PrivateAttr(default=False)

    def bind(
        self,
        repository: AbstractRepository,
        read_only: bool = False,
    ) -> "RelationModel":
        """Attach the repository this instance should update itself through.

        `read_only` is for models loaded from a databag the charm cannot write:
        field assignments then only mutate the in-memory instance
        instead of triggering an update.
        """
        self._repository = repository
        self._read_only = read_only
        return self

    @property
    def component(self):
        """The Juju unit/application this model's data is bound to, if any."""
        return self._repository.component if self._repository is not None else None

    @property
    def relation(self):
        """The ops.Relation this model's data is bound to, if any."""
        return self._repository.relation if self._repository is not None else None

    def __setattr__(self, name: str, value: Any) -> None:
        """Update the model whenever a (non-private) attribute is set."""
        super().__setattr__(name, value)
        if not name.startswith("_"):
            self._write_to_databag()

    def __delattr__(self, name: str) -> None:
        """Reset the field to its default value and persist."""
        field_info = type(self).__pydantic_fields__.get(name)
        default = field_info.get_default(call_default_factory=True) if field_info else None
        setattr(self, name, default)

    @contextmanager
    def update(self) -> Iterator["RelationModel"]:
        """Batch several field mutations into a single write.

        Also required for changes that mutate a field's value in place (e.g. appending to
        a list or updating a dict) since those don't go through `__setattr__`.
        """
        self._update_depth_counter += 1
        try:
            yield self
        finally:
            self._update_depth_counter -= 1
            if self._update_depth_counter == 0:
                self._write_to_databag()

    def _writable_repository(self) -> Any:
        """Return the repository to write to, or None if this write should be skipped."""
        if self._update_depth_counter > 0 or self._read_only:
            return None

        repository = self._repository
        if repository is None:
            return None

        relation = getattr(repository, "relation", None)
        if relation is not None and not getattr(relation, "active", True):
            return None

        component: Any = getattr(repository, "component", None)
        local_unit: Any = getattr(repository, "_local_unit", None)
        local_app: Any = getattr(repository, "_local_app", None)
        if component is not None and component not in (local_unit, local_app):
            logger.debug(
                "Not updating %s: bound to remote component %s.",
                type(self).__name__,
                component,
            )
            return None

        if (
            component is not None
            and component == local_app
            and local_unit is not None
            and not local_unit.is_leader()
        ):
            return None

        return repository

    def _write_to_databag(self) -> None:
        """Write the current model state back to its bound relation databag."""
        repository = self._writable_repository()
        if repository is None:
            return

        self._update_depth_counter += 1

        try:
            _write_model(repository, self)
        except (SecretNotFoundError, PydanticSerializationError) as e:
            logger.warning(
                "Secret unavailable while updating %s -- writing non-secret fields only: %s",
                type(self).__name__,
                e,
            )
            try:
                self._write_non_secret_fields(repository)
            except (SecretNotFoundError, PydanticSerializationError) as e2:
                logger.warning(
                    "Skipping write for %s -- fallback write failed: %s",
                    type(self).__name__,
                    e2,
                )
        finally:
            self._update_depth_counter -= 1

    def _write_non_secret_fields(self, repository: Any) -> None:
        """Write the model's plain databag fields, leaving Juju secrets untouched."""
        dumped = self.model_dump(mode="json", exclude_none=False)
        for field, value in dumped.items():
            if value is None:
                repository.delete_field(field)
                continue
            dumped_value = value if isinstance(value, str) else json.dumps(value)
            repository.write_field(field, dumped_value)


def bind_model_to_repository(
    interface: Any,
    relation_id: int,
    model_cls: type[BaseModel],
    component: Any | None = None,
    read_only: bool = False,
) -> Any:
    """Build a model through `interface` and bind it to its repository for data manipulations.

    `interface` is one of the `*RepositoryInterface` classes. If the built model is a
    RelationModel, it is bound so that setting any of its fields (or using `.update()`)
    writes it straight back to the relation databag.

    Pass `read_only=True` when `component` is a remote app/unit whose databag we can't write.
    """
    repository = interface.repository(relation_id, component)
    model = _lib_build_model(repository, model_cls)
    if isinstance(model, RelationModel):
        model.bind(repository, read_only=read_only)
    return model
