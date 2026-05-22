"""Built-in vendor Source instances and the public REGISTRY.

Overview
    Provides `Source` models for known vendor file formats and the
    `REGISTRY` mapping from source id to `Source` instances.

Authoring a new built-in source
    To add a new built-in source:

    1. Define a module-level constant that is a `Source` instance::

           MY_VENDOR = Source(
               id="my_vendor",
               exts=(".csv",),
               magic=("My Vendor file format",),
               metadata=MetadataParser(start_time="Start: (.+)"),
               normalizer=Normalizer(...),
           )

    2. Add the constant to `REGISTRY` so :func:`bdf.read` can auto-detect it.
    3. Add a unit test under ``tests/unit/`` constructing the source and
       verifying header matching against representative headers.

Synonyms
    Use ``{unit}`` as a wildcard token in exemplar strings. For example,
    ``Syn("I[{unit}]")`` matches ``"I[A]"``, ``"I[mA]"``, etc.; captured
    units are validated by Pint for compatibility with the BDF column unit.

    Datetime columns use :class:`DateTimeSyn` with a strftime format instead of
    a numeric ``Syn``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from bdf.normalizer import DateTimeSyn, MetadataParser, Normalizer, Syn


class Source(BaseModel):
    """A single battery cycler source: identity (id, magic, extensions), metadata parser,
    and column-mapping normalizer."""

    model_config = ConfigDict(frozen=True)

    id: str
    magic: tuple[str, ...] = ()
    exts: tuple[str, ...] = ()
    metadata: MetadataParser = Field(default_factory=MetadataParser)
    normalizer: Normalizer

    def score(self, headers: list[str]) -> int:
        return self.normalizer.score(headers)

    def match_magic(self, head: bytes) -> bool:
        if not self.magic:
            return False
        try:
            text = head.decode("utf-8", errors="replace").lower()
        except Exception:
            return False
        return any(m.lower() in text for m in self.magic)


# Arbin .csv

_ARBIN_DT_FMTS = ("%m/%d/%Y %H:%M:%S%.f", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S")

ARBIN_CSV = Source(
    id="arbin_csv",
    exts=(".csv",),
    metadata=MetadataParser(),
    normalizer=Normalizer(
        test_time_second=[Syn("test time ({unit})")],
        voltage_volt=[Syn("voltage ({unit})")],
        current_ampere=[Syn("current ({unit})")],
        unix_time_second=[DateTimeSyn(syn=Syn("date time"), fmts=_ARBIN_DT_FMTS)],
        cycle_count=[Syn("cycle index")],
        step_count=[Syn("step index")],
        step_index=[Syn("data point")],
        step_time_second=[Syn("step time ({unit})")],
        ambient_temperature_celsius=[Syn("aux_temperature_1 ({unit})")],
        charging_capacity_ah=[Syn("charge capacity ({unit})")],
        discharging_capacity_ah=[Syn("discharge capacity ({unit})")],
        charging_energy_wh=[Syn("charge energy ({unit})")],
        discharging_energy_wh=[Syn("discharge energy ({unit})")],
        power_watt=[Syn("power ({unit})")],
        internal_resistance_ohm=[
            Syn("internal resistance ({unit})"),
            Syn("acr ({unit})"),
        ],
    ),
)


# Basytec .txt/.dat

BASYTEC_TXT = Source(
    id="basytec_txt",
    exts=(".txt", ".dat"),
    magic=(
        "resultfile from basytec battery test system",
        "basytec battery test system",
    ),
    metadata=MetadataParser(start_time=r"~Start of Test:\s*(.+)"),
    normalizer=Normalizer(
        test_time_second=[
            Syn("time[{unit}]"),
            Syn("time"),
            DateTimeSyn(syn=Syn("time[h:min:s]"), fmts=("%H:%M:%S.%f",)),
        ],
        voltage_volt=[
            Syn("u[{unit}]"),
            Syn("voltage[{unit}]"),
            Syn("u"),
            Syn("voltage"),
        ],
        current_ampere=[
            Syn("i[{unit}]"),
            Syn("current[{unit}]"),
            Syn("i"),
            Syn("current"),
        ],
        ambient_temperature_celsius=[
            Syn("t1[{unit}]"),
            Syn("temp[{unit}]"),
            Syn("temperature[{unit}]"),
        ],
    ),
)


# BioLogic / EC-Lab / BT-Lab .mpt

BIOLOGIC_MPT = Source(
    id="biologic_mpt",
    exts=(".mpt",),
    magic=("bt-lab ascii file", "ec-lab ascii file"),
    metadata=MetadataParser(start_time=r"Acquisition started on\s*:\s*(.+)"),
    normalizer=Normalizer(
        test_time_second=[
            Syn("time/{unit}"),
            Syn("time / {unit}"),
            Syn("t ({unit})"),
            Syn("time [{unit}]"),
            Syn("relative time({unit})"),
        ],
        voltage_volt=[
            Syn("ewe/{unit}"),
            Syn("ecell/{unit}"),
            Syn("u/{unit}"),
            Syn("u[{unit}]"),
            Syn("ewe ({unit})"),
            Syn("<ewe>/{unit}"),
        ],
        current_ampere=[
            Syn("i[{unit}]"),
            Syn("current / {unit}"),
            Syn("current({unit})"),
            Syn("i({unit})"),
            Syn("i/{unit}"),
            Syn("<i>/{unit}"),
        ],
        cycle_count=[Syn("cycle number"), Syn("z cycle")],
        step_index=[Syn("Ns")],
        step_time_second=[Syn("step time/{unit}")],
        ambient_temperature_celsius=[
            Syn("temperature/{unit}"),
            Syn("temp/{unit}"),
            Syn("t/{unit}"),
            Syn("t/{unit}"),
        ],
        charging_capacity_ah=[
            Syn("q charge/{unit}"),
            Syn("q charge /{unit}"),
        ],
        discharging_capacity_ah=[
            Syn("q discharge/{unit}"),
            Syn("q discharge /{unit}"),
        ],
        step_capacity_ah=[Syn("dq/{unit}")],
        cumulative_capacity_ah=[
            Syn("(q-qo)/{unit}"),
            Syn("capacity/{unit}"),
        ],
        charging_energy_wh=[Syn("energy charge/{unit}")],
        discharging_energy_wh=[Syn("energy discharge/{unit}")],
        cumulative_energy_wh=[Syn("|energy|/{unit}")],
        power_watt=[Syn("p/{unit}")],
        internal_resistance_ohm=[Syn("r/{unit}")],
    ),
)


# Digatron .csv (headers use '#' as qty-unit separator)

_DIGATRON_DT_FMTS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S")

DIGATRON_CSV = Source(
    id="digatron_csv",
    exts=(".csv",),
    metadata=MetadataParser(),
    normalizer=Normalizer(
        test_time_second=[
            Syn("program duration#{unit}"),
            Syn("prog time"),
            Syn("program time"),
        ],
        voltage_volt=[Syn("voltage#{unit}"), Syn("voltage")],
        current_ampere=[Syn("current#{unit}"), Syn("current")],
        unix_time_second=[DateTimeSyn(syn=Syn("timestamp"), fmts=_DIGATRON_DT_FMTS)],
        cycle_count=[Syn("cycle")],
        step_index=[Syn("step")],
        step_time_second=[Syn("step time")],
        charging_capacity_ah=[Syn("AhCha#{unit}")],
        discharging_capacity_ah=[Syn("AhDch#{unit}")],
        step_capacity_ah=[Syn("AhStep#{unit}")],
        net_capacity_ah=[Syn("AhBal#{unit}")],
        cumulative_capacity_ah=[Syn("AhAccu#{unit}"), Syn("AhAccu")],
        charging_energy_wh=[Syn("WhCha#{unit}")],
        discharging_energy_wh=[Syn("WhDch#{unit}")],
        step_energy_wh=[Syn("WhStep#{unit}")],
        cumulative_energy_wh=[Syn("WhAccu#{unit}"), Syn("WhAccu")],
        power_watt=[Syn("watt"), Syn("power#{unit}")],
        temperature_t1_celsius=[Syn("t1#{unit}"), Syn("logtemp001")],
    ),
)


# Land .csv (headers use '_' as qty-unit separator; literal plain strings)

LANDT_CSV = Source(
    id="landt_csv",
    exts=(".csv",),
    metadata=MetadataParser(),
    normalizer=Normalizer(
        test_time_second=[Syn("test_time_s")],
        voltage_volt=[Syn("voltage_v")],
        current_ampere=[Syn("current_a")],
        cycle_count=[Syn("cycle_index")],
        step_count=[Syn("step_index")],
        step_time_second=[Syn("step_time_s")],
    ),
)


# Land .txt

LANDT_TXT = Source(
    id="landt_txt",
    exts=(".txt",),
    metadata=MetadataParser(),
    normalizer=Normalizer(
        test_time_second=[
            Syn("test({unit})"),
            Syn("test ({unit})"),
            Syn("test_time_s"),
            Syn("test time ({unit})"),
            Syn("test time"),
        ],
        voltage_volt=[
            Syn("volts"),
            Syn("volt"),
            Syn("voltage"),
            Syn("V"),
        ],
        current_ampere=[
            Syn("amps"),
            Syn("amp"),
            Syn("current"),
            Syn("A"),
            Syn("i({unit})"),
        ],
        cycle_count=[
            Syn("cycle"),
            Syn("cycle#"),
            Syn("cycle index"),
        ],
        step_count=[
            Syn("step"),
            Syn("step#"),
            Syn("step index"),
        ],
        step_index=[
            Syn("rec#"),
            Syn("record"),
            Syn("record#"),
        ],
        step_time_second=[
            Syn("dpt-time"),
            Syn("dpt time"),
            Syn("step time ({unit})"),
            Syn("step_time_s"),
        ],
    ),
)


# Maccor .csv

_MACCOR_DT_FMTS = ("%d-%b-%y %I:%M:%S %p", "%d-%b-%y %H:%M:%S", "%Y-%m-%d %H:%M:%S")

MACCOR_CSV = Source(
    id="maccor_csv",
    exts=(".csv",),
    magic=("today's date", "date of test:"),
    metadata=MetadataParser(start_time=r"Date of Test:,(.+)"),
    normalizer=Normalizer(
        test_time_second=[
            Syn("test time ({unit})"),
            Syn("test time({unit})"),
        ],
        voltage_volt=[Syn("voltage")],
        current_ampere=[Syn("current")],
        unix_time_second=[DateTimeSyn(syn=Syn("dpt time"), fmts=_MACCOR_DT_FMTS)],
        cycle_count=[Syn("cycle c")],
        step_count=[Syn("step")],
        step_time_second=[Syn("step time ({unit})")],
        ambient_temperature_celsius=[Syn("temp 1")],
        net_capacity_ah=[Syn("capacity")],
        net_energy_wh=[Syn("energy")],
    ),
)


# Neware .csv

_NEWARE_DT_FMTS = ("%Y-%m-%d %H:%M:%S%.f", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S")

NEWARE_CSV = Source(
    id="neware_csv",
    exts=(".csv",),
    metadata=MetadataParser(),
    normalizer=Normalizer(
        test_time_second=[
            Syn("total time({unit})"),
            Syn("test time({unit})"),
            Syn("totaltime({unit})"),
            Syn("totaltime_s"),
            Syn("total time"),
            Syn("总时间({unit})"),
            Syn("测试时间({unit})"),
        ],
        voltage_volt=[
            Syn("voltage({unit})"),
            Syn("电压({unit})"),
            Syn("voltage [{unit}]"),
        ],
        current_ampere=[
            Syn("current({unit})"),
            Syn("电流({unit})"),
            Syn("current [{unit}]"),
        ],
        unix_time_second=[
            DateTimeSyn(syn=Syn("date"), fmts=_NEWARE_DT_FMTS),
            DateTimeSyn(syn=Syn("datetime"), fmts=_NEWARE_DT_FMTS),
            DateTimeSyn(syn=Syn("date_time"), fmts=_NEWARE_DT_FMTS),
        ],
        cycle_count=[Syn("cycle")],
        step_count=[Syn("step")],
        step_index=[Syn("record")],
        step_time_second=[
            Syn("time({unit})"),
            Syn("relative time({unit})"),
            Syn("state time({unit})"),
            Syn("steptime({unit})"),
            Syn("step time({unit})"),
            Syn("steptime_s"),
            Syn("时间({unit})"),
        ],
        ambient_temperature_celsius=[
            Syn("temperature(°c)"),
            Syn("温度(°c)"),
        ],
        charging_capacity_ah=[
            Syn("charge capacity({unit})"),
            Syn("chg.capacity({unit})"),
        ],
        discharging_capacity_ah=[
            Syn("discharge capacity({unit})"),
            Syn("dchg.capacity({unit})"),
        ],
    ),
)


# Novonix UHPC .csv

NOVONIX_CSV = Source(
    id="novonix_csv",
    exts=(".csv",),
    magic=("[summary]", "[data]", "novonix uhpc data file", "novonix"),
    metadata=MetadataParser(),
    normalizer=Normalizer(
        test_time_second=[
            Syn("run time ({unit})"),
            Syn("run-time ({unit})"),
            Syn("runtime ({unit})"),
            Syn("test time ({unit})"),
            Syn("testtime({unit})"),
        ],
        voltage_volt=[
            Syn("potential ({unit})"),
            Syn("voltage ({unit})"),
            Syn("cell voltage ({unit})"),
        ],
        current_ampere=[
            Syn("current ({unit})"),
            Syn("cell current ({unit})"),
        ],
        unix_time_second=[
            Syn("unix time ({unit})"),
            Syn("unixtime ({unit})"),
            DateTimeSyn(syn=Syn("date and time"), fmts=("%Y-%m-%d %H:%M:%S",)),
        ],
        cycle_count=[
            Syn("cycle number"),
            Syn("cycle"),
            Syn("cycle #"),
            Syn("cycle#"),
        ],
        step_count=[
            Syn("step number"),
            Syn("step #"),
            Syn("step#"),
        ],
        step_index=[Syn("step position")],
        step_time_second=[Syn("step time ({unit})"), Syn("steptime({unit})")],
        ambient_temperature_celsius=[
            Syn("temperature (°c)"),
            Syn("ambient temperature (°c)"),
            Syn("ambient temp (°c)"),
        ],
        temperature_t1_celsius=[
            Syn("circuit temperature (°c)"),
            Syn("circuit temp (°c)"),
        ],
        net_capacity_ah=[
            Syn("capacity ({unit})"),
            Syn("net capacity ({unit})"),
        ],
        net_energy_wh=[Syn("energy ({unit})"), Syn("net energy ({unit})")],
        power_watt=[Syn("power({unit})"), Syn("power ({unit})")],
    ),
)


# Registry

_BUILTIN_SOURCES: tuple[Source, ...] = (
    ARBIN_CSV,
    BASYTEC_TXT,
    BIOLOGIC_MPT,
    DIGATRON_CSV,
    LANDT_CSV,
    LANDT_TXT,
    MACCOR_CSV,
    NEWARE_CSV,
    NOVONIX_CSV,
)

REGISTRY: dict[str, Source] = {s.id: s for s in _BUILTIN_SOURCES}


def list_sources() -> list[str]:
    """Return list of registered source IDs."""
    return list(REGISTRY)


def get_source(id: str) -> Source | None:
    """Return Source for id, or None if not registered."""
    return REGISTRY.get(id)


def get_normalizer(source: str | Source) -> Source:
    """Resolve source id or Source object to a registered Source. Raises KeyError for unknown IDs."""
    if isinstance(source, Source):
        return source
    s = REGISTRY.get(source)
    if s is None:
        raise KeyError(f"unknown source id: {source!r}")
    return s


__all__ = [
    "ARBIN_CSV",
    "BASYTEC_TXT",
    "BIOLOGIC_MPT",
    "DIGATRON_CSV",
    "LANDT_CSV",
    "LANDT_TXT",
    "MACCOR_CSV",
    "NEWARE_CSV",
    "NOVONIX_CSV",
    "REGISTRY",
    "Source",
    "get_normalizer",
    "get_source",
    "list_sources",
]
