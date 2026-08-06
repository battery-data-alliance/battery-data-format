from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

_REQUIRED = "REQUIRED"
_OPTIONAL = "OPTIONAL"

_TEMPLATES: dict[str, Any] = {
    "contribution": {
        "schema_version": "1.0.0",
        "dataset_doi": _REQUIRED,
        "citation_doi": _OPTIONAL,
        "license": _REQUIRED,
        "title": _OPTIONAL,
        "description": _OPTIONAL,
        "creators": [{"name": _OPTIONAL, "orcid": _OPTIONAL, "affiliation": _OPTIONAL}],
        "keywords": [_OPTIONAL],
        "version": _OPTIONAL,
        "publication_date": _OPTIONAL,
    },
    "battery": {
        "schema_version": "1.0.0",
        "battery_model": _OPTIONAL,
        "spec": {
            "manufacturer": {
                "@id": _OPTIONAL,
                "name": _OPTIONAL,
            },
            "productID": _OPTIONAL,
            "year": _OPTIONAL,
            "iec_code": _OPTIONAL,
            "chemistry": _OPTIONAL,
            "form_factor": _OPTIONAL,
            "nominal_voltage_v": _OPTIONAL,
            "rated_capacity_ah": _OPTIONAL,
            "rated_energy_wh": _OPTIONAL,
            "mass_g": _OPTIONAL,
            "volume_l": _OPTIONAL,
            "pe_materials": [_OPTIONAL],
            "ne_materials": [_OPTIONAL],
        },
        "cells": [{"name": _REQUIRED, "cell_id": _OPTIONAL}],
    },
    "excel": {
        "sheet_index": _OPTIONAL,
        "header_row": _OPTIONAL,
        "usecols": _OPTIONAL,
        "skiprows": _OPTIONAL,
        "nrows": _OPTIONAL,
        "rename": {},
    },
    "data_download": [
        {
            "url": _REQUIRED,
            "path": _OPTIONAL,
            "name": _OPTIONAL,
            "encoding_format": _OPTIONAL,
            "description": _OPTIONAL,
        }
    ],
    "mapping": {
        "fields": {
            "test_time_second": {
                "source": _REQUIRED,
                "source_unit": _OPTIONAL,
            },
            "voltage_volt": {
                "source": _REQUIRED,
                "source_unit": _OPTIONAL,
            },
            "current_ampere": {
                "source": _REQUIRED,
                "source_unit": _OPTIONAL,
            },
        },
        "source_locator": {
            "sheet_name": _OPTIONAL,
            "header_row": _OPTIONAL,
            "usecols": _OPTIONAL,
        },
        "source_units": {},
        "scale": {},
        "offset": {},
    },
}

_FILENAMES = {
    "contribution": "contribution.json",
    "battery": "battery.json",
    "excel": "excel.json",
    "data_download": "data_download.json",
    "mapping": "bdf.mapping.json",
}

_ALIASES = {
    "dataset": "contribution",
    "datadownload": "data_download",
    "data-download": "data_download",
    "bdf.mapping": "mapping",
    "bdf.map": "mapping",
}


def templates(
    *names: Iterable[str] | str,
    root: str | Path = ".",
    overwrite: bool = False,
) -> dict[str, Any]:
    """
    Create template sidecar files with REQUIRED/OPTIONAL placeholders.
    Returns a summary dict with created/skipped paths.
    """
    if len(names) == 1 and isinstance(names[0], (list, tuple, set)):
        name_list = list(names[0])
    elif len(names) == 1 and isinstance(names[0], str):
        name_list = [names[0]]
    else:
        name_list = list(names)

    if not name_list:
        name_list = ["contribution", "battery"]

    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)

    created: list[str] = []
    skipped: list[str] = []

    for raw_name in name_list:
        key = str(raw_name).strip().lower().replace(" ", "_")
        key = _ALIASES.get(key, key)
        if key not in _TEMPLATES:
            raise ValueError(f"Unknown template: {raw_name}")
        filename = _FILENAMES[key]
        path = root_path / filename
        if path.exists() and not overwrite:
            skipped.append(str(path))
            continue
        data = _TEMPLATES[key]
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        created.append(str(path))

    return {"root": str(root_path), "created": created, "skipped": skipped}
