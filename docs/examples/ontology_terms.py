"""Exercise the offline CLI workflow on fabricated thickness readings.

Run ``python docs/examples/ontology_terms.py`` with this branch installed.
All writes are temporary. No network access or additional dependencies needed.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from custom_measurements import example_data

from bdf import io


def cli(*args: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", "from bdf.cli import app; app()", *args],
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout


def main() -> None:
    frame, metadata = example_data()
    assert metadata.battinfo_dataset.dataset is not None
    assert metadata.battinfo_dataset.dataset.variable_measured
    variable = metadata.battinfo_dataset.dataset.variable_measured[0]
    # Simulate a user who described the measurement but did not know its term.
    variable.same_as = None
    with tempfile.TemporaryDirectory(prefix="bdf-term-example-") as directory:
        root = Path(directory)
        source, second = root / "first.bdf.parquet", root / "second.bdf.parquet"
        profile = root / "height-gauge-mappings.json"
        io.save(frame, source, metadata=metadata)
        io.save(frame, second, metadata=metadata)
        original = source.read_bytes()
        report = json.loads(cli("terms", str(source), "--json"))
        candidate = report["unlinked"][0]["candidates"][0]
        print(f"Suggested: {candidate['label']} ({candidate['iri']})")
        # This explicit, verified choice represents the user's review decision.
        selected_iri = "https://w3id.org/emmo#EMMO_43003c86_9d15_433b_9789_ee2940920656"
        assert candidate["iri"] == selected_iri
        print(
            cli(
                "terms", str(source), "--accept", f"Thickness / mm={selected_iri}", "--save-mappings", str(profile)
            ).strip()
        )
        assert source.read_bytes() == original
        print(cli("terms", str(second), "--mappings", str(profile)).strip())
        converted = root / "converted.bdf.csv"
        cli("convert", str(second), "--to", str(converted))
        restored, linked = io.read(converted)
        assert linked.battinfo_dataset.dataset is not None
        assert linked.battinfo_dataset.dataset.variable_measured
        assert str(linked.battinfo_dataset.dataset.variable_measured[0].same_as) == selected_iri
        assert restored["Thickness / mm"].null_count() == frame["Thickness / mm"].null_count()
        assert json.loads(cli("validate", str(converted), "--json"))["ontology_terms"]["unlinked"] == []
        print("Verified offline discovery, explicit acceptance, profile reuse, conversion and validation.")


if __name__ == "__main__":
    main()
