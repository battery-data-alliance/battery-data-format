from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pandas as pd

from .base import CyclerPlugin, SniffResult

_BLOCK_SAMPLES = {
    0x00000002: 0,  # control marker
    0x00000103: 1,
    0x00000203: 2,
    0x00000303: 3,
    0x00000403: 4,
    0x00000503: 5,
}


def _is_finite(*values: float) -> bool:
    return all(np.isfinite(v) for v in values)


class LandtCCS(CyclerPlugin):
    """
    LAND binary .ccs parser.

    The payload is organized in 128-byte blocks. Each data block starts with a
    type word that encodes how many 24-byte samples follow.
    """

    id = "landt-ccs"
    exts = (".ccs",)
    column_synonyms = {
        "Test Time / s": ["ccs_test_time_s"],
        "Voltage / V": ["ccs_voltage_v"],
        "Current / A": ["ccs_current_a"],
        "Charging Capacity / Ah": ["ccs_charging_capacity_ah"],
        "Discharging Capacity / Ah": ["ccs_discharging_capacity_ah"],
        "Charging Energy / Wh": ["ccs_charging_energy_wh"],
        "Discharging Energy / Wh": ["ccs_discharging_energy_wh"],
        "Internal Resistance / ohm": ["ccs_internal_resistance_ohm"],
        "Step Index / 1": ["ccs_step_index"],
    }

    def sniff(self, path: Path, head: bytes) -> SniffResult:
        score = 0.0
        reasons: list[str] = []

        if path.suffix.lower() in self.exts:
            score += 0.40
            reasons.append("ext")

        if b"WuHan LAND" in head or b"SNEL" in head:
            score += 0.30
            reasons.append("vendor")

        if len(head) >= 0x1004:
            marker = struct.unpack_from("<I", head, 0x1000)[0]
            if marker in _BLOCK_SAMPLES:
                score += 0.25
                reasons.append("block")

        return SniffResult(self.id, min(score, 1.0), "+".join(reasons), {})

    @staticmethod
    def _find_payload_offset(raw: bytes) -> int:
        # In observed files the payload starts at 0x1000. Keep this robust by
        # searching for a run of valid block markers on 128-byte boundaries.
        seek_end = min(len(raw) - 128 * 6, 0x4000)
        for off in range(0, max(seek_end, 0) + 1, 0x80):
            ok = True
            for k in range(6):
                word = struct.unpack_from("<I", raw, off + 128 * k)[0]
                if word not in _BLOCK_SAMPLES:
                    ok = False
                    break
            if ok:
                return off
        raise ValueError("Could not locate CCS payload block sequence.")

    def parse(self, path: Path) -> pd.DataFrame:
        raw = Path(path).read_bytes()
        start = self._find_payload_offset(raw)

        dt_ms: list[int] = []
        voltage: list[float] = []
        current: list[float] = []
        dcap: list[float] = []
        denergy: list[float] = []
        resistance: list[float] = []

        n_blocks = (len(raw) - start) // 128
        for i in range(n_blocks):
            off = start + i * 128
            word = struct.unpack_from("<I", raw, off)[0]
            n_samples = _BLOCK_SAMPLES.get(word)
            if n_samples is None or n_samples == 0:
                continue

            for j in range(n_samples):
                rec_off = off + 8 + j * 24
                dti, v, ia, dq, de, rint = struct.unpack_from("<Ifffff", raw, rec_off)
                if dti <= 0:
                    continue
                if not _is_finite(v, ia, dq, de, rint):
                    continue

                dt_ms.append(int(dti))
                voltage.append(float(v))
                current.append(float(ia))
                dcap.append(abs(float(dq)))
                denergy.append(abs(float(de)))
                resistance.append(float(rint))

        if not dt_ms:
            raise ValueError(f"No measurement records found in {Path(path).name}.")

        dt = np.asarray(dt_ms, dtype="float64") / 1000.0
        t = np.cumsum(dt, dtype="float64")
        t -= t[0]

        i_arr = np.asarray(current, dtype="float64")
        dq_arr = np.asarray(dcap, dtype="float64")
        de_arr = np.asarray(denergy, dtype="float64")
        is_charge = i_arr >= 0.0

        charge_capacity = np.cumsum(np.where(is_charge, dq_arr, 0.0), dtype="float64")
        discharge_capacity = np.cumsum(np.where(~is_charge, dq_arr, 0.0), dtype="float64")
        charge_energy = np.cumsum(np.where(is_charge, de_arr, 0.0), dtype="float64")
        discharge_energy = np.cumsum(np.where(~is_charge, de_arr, 0.0), dtype="float64")

        return pd.DataFrame(
            {
                "ccs_test_time_s": t,
                "ccs_voltage_v": np.asarray(voltage, dtype="float64"),
                "ccs_current_a": i_arr,
                "ccs_charging_capacity_ah": charge_capacity,
                "ccs_discharging_capacity_ah": discharge_capacity,
                "ccs_charging_energy_wh": charge_energy,
                "ccs_discharging_energy_wh": discharge_energy,
                "ccs_internal_resistance_ohm": np.asarray(resistance, dtype="float64"),
                "ccs_step_index": np.arange(1, len(t) + 1, dtype="int64"),
            }
        )
