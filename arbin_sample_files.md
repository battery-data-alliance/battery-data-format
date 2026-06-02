# Arbin Sample Files in D:\data

Confirmed Arbin cycler data in two format variants across three datasets.

---

## Format A — Arbin CSV export (space-separated column names)

**Datasets:**
- `D:\data\hina\nacr32140-mp10\wang-2024\data\timeseries\raw\` — 18 files (`.CSV`)
- `D:\data\transimage\nar26700\wang-2024\data\timeseries\raw\` — 18 files (`.CSV`)

**Header row (exact):**
```
Data Point,Date Time,Test Time (s),Step Time (s),Cycle Index,Step Index,TC_Counter1,TC_Counter2,TC_Counter3,TC_Counter4,Current (A),Voltage (V),Power (W),Charge Capacity (Ah),Discharge Capacity (Ah),Charge Energy (Wh),Discharge Energy (Wh),Capacity (Ah),mAh/g,ACR (Ohm),dV/dt (V/s),Internal Resistance (Ohm),dQ/dV (Ah/V),dV/dQ (V/Ah),Aux_Temperature_1 (C),Aux_dT/dt_1 (C/s)
```

**Notes:**
- Delimiter: comma
- Timestamp format: `10/10/2023 16:09:14.661` (MM/DD/YYYY HH:MM:SS.mmm) — note tab before timestamp in row 2
- Temperature: `Aux_Temperature_1 (C)`, `Aux_dT/dt_1 (C/s)` — up to N auxiliary channels
- Arbin-specific extras: `Data Point`, `TC_Counter1-4`, `ACR (Ohm)`, `mAh/g`, `dQ/dV`, `dV/dQ`

---

## Format B — Arbin XLSX export (underscore-separated column names)

**Datasets:**
- `D:\data\mushang\na18650-1250\rodrigueziturriaga-2025\data\timeseries\raw\` — ~10 `.xlsx` files
- `D:\data\a123\anr26650m1-b\catenaro-2021\` — multiple `.xlsx` files

**Header row (from mushang HPPC file):**
```
Date_Time, Test_Time(s), Step_Time(s), Step_Index, Cycle_Index, Voltage(V), Current(A),
Charge_Capacity(Ah), Discharge_Capacity(Ah), Charge_Energy(Wh), Discharge_Energy(Wh),
Internal Resistance(Ohm), dV/dt(V/s),
Aux_Voltage(V)_1 ... Aux_Voltage(V)_5,
Aux_Temperature(°)_1 ... Aux_Temperature(°)_3
```

**Header row (from a123 file — slightly different):**
```
Date_Time, Test_Time(s), Step_Time(s), Step_Index, Voltage(V), Current(A), Surface_Temp(degC)
```

**Notes:**
- No `Data Point` or `TC_Counter` columns
- `Cycle_Index` absent in some a123 files
- Auxiliary temperature encoded as `Aux_Temperature(°)_N` (degree symbol may appear as `°` or `\xb0` or `°`)
- Auxiliary voltage channels present in mushang but not a123
- `Date_Time` values are Python `datetime` objects when read via openpyxl

---

## Key differences between Format A and Format B

| Column concept       | Format A (CSV)                | Format B (XLSX)               |
|----------------------|-------------------------------|-------------------------------|
| Row number           | `Data Point`                  | (absent)                      |
| Timestamp            | `Date Time`                   | `Date_Time`                   |
| Test time            | `Test Time (s)`               | `Test_Time(s)`                |
| Step time            | `Step Time (s)`               | `Step_Time(s)`                |
| Cycle counter        | `Cycle Index`                 | `Cycle_Index`                 |
| Step counter         | `Step Index`                  | `Step_Index`                  |
| Voltage              | `Voltage (V)`                 | `Voltage(V)`                  |
| Current              | `Current (A)`                 | `Current(A)`                  |
| Charge capacity      | `Charge Capacity (Ah)`        | `Charge_Capacity(Ah)`         |
| Discharge capacity   | `Discharge Capacity (Ah)`     | `Discharge_Capacity(Ah)`      |
| AC resistance        | `ACR (Ohm)`                   | (absent or `Internal Resistance(Ohm)`) |
| Aux temperature      | `Aux_Temperature_1 (C)`       | `Aux_Temperature(°)_1`        |
| Aux voltage          | (absent)                      | `Aux_Voltage(V)_1`            |

Both formats are from Arbin MITS Pro — the CSV format appears to be a newer/different export option vs. the XLSX.
