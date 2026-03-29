"""Shared data types for KiCad MCP server."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Point:
    x: float
    y: float


@dataclass
class PinInfo:
    number: str
    uuid: str


@dataclass
class SymbolInstance:
    """A placed symbol in a schematic."""
    uuid: str
    lib_id: str
    reference: str
    value: str
    footprint: str
    position: Point
    rotation: float
    pins: list[PinInfo] = field(default_factory=list)


@dataclass
class WireInfo:
    """A wire segment in a schematic."""
    uuid: str
    points: list[Point]


@dataclass
class LabelInfo:
    """A net label in a schematic."""
    uuid: str
    name: str
    position: Point
    rotation: float
    label_type: str = "label"  # "label", "global_label", "power_port"


@dataclass
class PadInfo:
    """A pad on a PCB footprint."""
    number: str
    net_number: int
    net_name: str
    position: Point


@dataclass
class FootprintInstance:
    """A placed footprint on a PCB."""
    uuid: str
    footprint_lib: str
    reference: str
    value: str
    position: Point
    rotation: float
    layer: str
    pads: list[PadInfo] = field(default_factory=list)


@dataclass
class TraceInfo:
    """A copper trace segment on a PCB."""
    uuid: str
    start: Point
    end: Point
    width: float
    layer: str
    net: int


@dataclass
class ViaInfo:
    """A via on a PCB."""
    uuid: str
    position: Point
    size: float
    drill: float
    net: int


@dataclass
class ZoneInfo:
    """A copper zone/pour on a PCB."""
    uuid: str
    net_number: int
    net_name: str
    layer: str
    outline: list[Point]


@dataclass
class NetInfo:
    """A net declaration in a PCB."""
    number: int
    name: str


@dataclass
class SchematicData:
    """Parsed schematic data."""
    symbols: list[SymbolInstance] = field(default_factory=list)
    wires: list[WireInfo] = field(default_factory=list)
    labels: list[LabelInfo] = field(default_factory=list)


@dataclass
class PCBData:
    """Parsed PCB data."""
    nets: list[NetInfo] = field(default_factory=list)
    footprints: list[FootprintInstance] = field(default_factory=list)
    traces: list[TraceInfo] = field(default_factory=list)
    vias: list[ViaInfo] = field(default_factory=list)
    zones: list[ZoneInfo] = field(default_factory=list)
    board_outline: list[Point] = field(default_factory=list)


@dataclass
class ERCViolation:
    """An ERC violation."""
    severity: str
    message: str
    location: Point | None = None


@dataclass
class DRCViolation:
    """A DRC violation."""
    severity: str
    message: str
    location: Point | None = None
