"""Tests for the checked-in generated model package (``bdf.battinfo.generated``).

The package is not written by hand: ``datamodel-codegen`` renders it from the
eight managed schema files under ``bdf/data/battinfo/schemas/``, under the
render configuration in ``[tool.datamodel-codegen]``. This module's job is to
guard that render, not the model behaviour it produces (covered elsewhere,
against the read-metadata handoff in ``tests/unit/test_metadata_records.py``).
"""

from __future__ import annotations

import importlib
import pkgutil
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel

import bdf.battinfo.generated

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _generated_model_classes() -> list[type[BaseModel]]:
    """Return every pydantic model class declared directly in a ``bdf.battinfo.generated`` module.

    Returns:
        One entry per class defined in (not merely imported into) a module
        of the ``bdf.battinfo.generated`` package tree, including the
        ``modules.common`` subpackage.
    """
    classes = []
    for module_info in pkgutil.walk_packages(
        bdf.battinfo.generated.__path__, prefix=bdf.battinfo.generated.__name__ + "."
    ):
        module = importlib.import_module(module_info.name)
        for obj in vars(module).values():
            if isinstance(obj, type) and issubclass(obj, BaseModel) and obj.__module__ == module.__name__:
                classes.append(obj)
    return classes


def test_every_generated_field_default_is_none_or_a_factory() -> None:
    """A literal generated default fails the guard test: every field's default is `None` or a factory.

    A regenerated model field carrying a literal non-`None` default would
    make `exclude_defaults` silently drop a stated value equal to it, so
    this guard names the offending field rather than let that pass quietly.
    """
    offenders = []
    for model_cls in _generated_model_classes():
        for field_name, field_info in model_cls.model_fields.items():
            if field_info.default_factory is not None:
                continue
            if field_info.default is None:
                continue
            offenders.append(f"{model_cls.__module__}.{model_cls.__qualname__}.{field_name}")
    assert not offenders, f"generated fields with a literal non-None default: {offenders}"


def test_committed_package_matches_a_fresh_render() -> None:
    """A stale generated package fails the staleness check: the generator's own
    ``--check`` regenerates from the bundled schema files, compares the result
    against the committed ``bdf.battinfo.generated`` package, and exits
    non-zero on any difference. It modifies nothing, and it reads the render
    configuration from ``[tool.datamodel-codegen]``, so this test and a
    regeneration can never disagree about the flags. No network: the schema
    files are already on disk."""
    result = subprocess.run(
        [sys.executable, "-m", "datamodel_code_generator", "--check"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "the committed bdf.battinfo.generated package no longer matches a fresh render; "
        f"run `datamodel-codegen` to regenerate it:\n{result.stdout}{result.stderr}"
    )


def test_package_declares_the_five_entity_root_models() -> None:
    """Attributes come from the schema, not from code: the generated package
    exposes one root model per bundled entity schema, each declaring fields
    the corresponding schema's ``properties`` names rather than a
    hand-maintained mirror."""
    from bdf.battinfo.generated.cell_instance_schema import BattinfoCellInstance
    from bdf.battinfo.generated.channel_schema import BattinfoChannelInstance
    from bdf.battinfo.generated.equipment_schema import BattinfoEquipmentInstance
    from bdf.battinfo.generated.test_protocol_schema import BattinfoTestProtocol
    from bdf.battinfo.generated.test_schema import BattinfoTest

    for model_cls in (
        BattinfoTest,
        BattinfoCellInstance,
        BattinfoChannelInstance,
        BattinfoEquipmentInstance,
        BattinfoTestProtocol,
    ):
        assert "schema_version" in model_cls.model_fields


def test_package_imports_without_battinfo_generated_prefix_confusion() -> None:
    """The generated package is importable as an ordinary subpackage of
    ``bdf``, alongside the hand-written ``bdf.metadata`` records it builds on."""
    assert bdf.battinfo.generated.__name__ == "bdf.battinfo.generated"
