# KiCad MCP Server

An MCP (Model Context Protocol) server that allows LLMs to programmatically create, read, and modify KiCad 8 schematic (`.kicad_sch`) and PCB layout (`.kicad_pcb`) files.

## Features

- **Schematic tools**: Place symbols, add wires, labels, power symbols, global labels, delete elements, run ERC
- **PCB tools**: Place footprints (single or arrays), move footprints, route traces, add vias/zones, set board outline, assign nets to pads, add mounting holes, run DRC, export Gerbers/SVG/3D
- **Library search**: Query KiCad symbol and footprint libraries
- **JLCPCB parts search**: Search the JLCPCB catalog by keyword, category, or LCSC number (no authentication required)
- **Autorouting**: Route PCBs via Freerouting integration (DSN export, route, SES import)
- **File-based**: Directly reads/writes KiCad S-expression files with a lossless parser — no running KiCad instance needed for core operations

## Requirements

- Python 3.10+
- KiCad 8 (for `kicad-cli`, library files, ERC/DRC)
- Java runtime (optional, for Freerouting autorouter)
- [Freerouting JAR](https://github.com/freerouting/freerouting/releases) (optional, for autorouting)

## Installation

```bash
pip install -e ".[dev]"
```

## Running the Server

```bash
python3.13 -m kicad_mcp.server
```

With debug logging:

```bash
KICAD_MCP_LOG_LEVEL=DEBUG python3.13 -m kicad_mcp.server
```

With optional log file:

```bash
KICAD_MCP_LOG_LEVEL=DEBUG KICAD_MCP_LOG_FILE=/tmp/kicad-mcp.log python3.13 -m kicad_mcp.server
```

Use Python 3.13 if KiCad 8 is installed — it includes the `pcbnew` module which enables DSN export for Freerouting autorouting. The server communicates over stdio (MCP protocol) and logs to stderr.

## Usage with Claude Code

Add to your project's `.mcp.json` or `~/.claude.json`:

```json
{
  "mcpServers": {
    "kicad": {
      "command": "python3.13",
      "args": ["-m", "kicad_mcp.server"],
      "env": {
        "PYTHONPATH": "/path/to/kicad-mcp/src",
        "KICAD_SYMBOL_DIR": "/usr/share/kicad/symbols",
        "KICAD_FOOTPRINT_DIR": "/usr/share/kicad/footprints",
        "KICAD_3DMODEL_DIR": "/usr/share/kicad/3dmodels"
      }
    }
  }
}
```

Use the Python version that has KiCad's `pcbnew` module (typically Python 3.13 on Fedora with KiCad 8). Update the `PYTHONPATH` to point to where you cloned this repo.

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `KICAD_SYMBOL_DIR` | KiCad symbol library directory (falls back to `KICAD9_SYMBOL_DIR`, then `KICAD8_SYMBOL_DIR`) | `/usr/share/kicad/symbols` |
| `KICAD_FOOTPRINT_DIR` | KiCad footprint library directory (falls back to `KICAD9_FOOTPRINT_DIR`, then `KICAD8_FOOTPRINT_DIR`) | `/usr/share/kicad/footprints` |
| `KICAD_3DMODEL_DIR` | KiCad 3D model directory | `/usr/share/kicad/3dmodels` |
| `KICAD_MCP_LOG_LEVEL` | Log level (DEBUG, INFO, WARNING, ERROR) | `INFO` |
| `KICAD_MCP_LOG_FILE` | Optional log file path | *(stderr only)* |
| `FREEROUTING_JAR` | Path to Freerouting JAR file | downloaded to `~/.cache/kicad-mcp/freerouting.jar` |

## Tools (53)

### Schematic (24)
| Tool | Description |
|---|---|
| `schematic_read` | Read and parse a .kicad_sch file |
| `schematic_place_symbol` | Place a component symbol in the schematic |
| `schematic_add_wire` | Add a wire between a series of (x, y) points |
| `schematic_add_label` | Add a net label at a position in the schematic |
| `schematic_add_power_symbol` | Add a power port symbol (GND, +3V3, +5V, etc.) at a position |
| `schematic_add_global_label` | Add a global label for inter-sheet connectivity |
| `schematic_delete` | Delete any schematic element by UUID |
| `schematic_add_labels` | Add multiple net labels in a single operation (one file read/write cycle) |
| `schematic_delete_many` | Delete multiple schematic elements by UUID in a single operation |
| `schematic_add_power_symbols` | Add multiple power symbols in a single operation (one file read/write cycle) |
| `schematic_get_pin_positions` | Get the actual pin endpoint positions for a component in schematic coordinates |
| `schematic_add_no_connect` | Add a no-connect (X) flag at a pin position |
| `schematic_add_no_connects` | Add multiple no-connect flags in a single operation |
| `schematic_modify_lib_symbol_pin` | Modify a pin's electrical type in the schematic's lib_symbols section |
| `schematic_annotate` | Assign reference designators to unannotated symbols |
| `schematic_move_symbol` | Move an existing schematic symbol to a new position |
| `schematic_add_lib_symbol` | Add a custom symbol definition to the schematic's lib_symbols section |
| `schematic_delete_lib_symbol` | Delete an unused symbol definition from the schematic's lib_symbols section |
| `schematic_cleanup_lib_symbols` | Remove all orphaned lib_symbol definitions with no matching instances |
| `schematic_set_symbol_property` | Set a property on a placed symbol instance without disturbing pins/wires |
| `schematic_set_lib_symbol_property` | Set a property on a lib_symbol definition (the default for new instances) |
| `schematic_list_symbols` | Return a thin listing of all placed symbols (no pin details) |
| `schematic_rename_label` | Rename all labels matching old_name to new_name, preserving UUIDs |
| `schematic_run_erc` | Run Electrical Rules Check (ERC) on a schematic using kicad-cli |

### PCB (22)
| Tool | Description |
|---|---|
| `pcb_read` | Read and parse a .kicad_pcb file |
| `pcb_place_footprint` | Place a footprint on the PCB |
| `pcb_move_footprint` | Move an existing footprint to a new position |
| `pcb_add_trace` | Add copper trace segments between points |
| `pcb_add_via` | Add a via at a position |
| `pcb_add_zone` | Add a copper zone/pour |
| `pcb_set_board_outline` | Set the board outline on the Edge.Cuts layer |
| `pcb_assign_net_to_pad` | Assign a net to a specific pad on a footprint |
| `pcb_add_mounting_hole` | Add a plated mounting hole footprint (references H1, H2, ...) |
| `pcb_place_footprint_array` | Place an array of identical footprints in a grid or circular pattern |
| `pcb_set_zone_net` | Assign a net to an existing copper zone |
| `pcb_flip_footprint` | Flip a footprint to the opposite side of the board |
| `pcb_autoroute` | Route a PCB automatically |
| `pcb_delete_footprint` | Delete a footprint from the PCB by reference or UUID |
| `pcb_delete_footprints` | Delete multiple footprints in a single operation |
| `pcb_delete_traces` | Delete multiple traces (segments) by UUID in a single operation |
| `pcb_delete_vias` | Delete multiple vias by UUID in a single operation |
| `pcb_run_drc` | Run Design Rules Check (DRC) on a PCB using kicad-cli |
| `pcb_export_image` | Export a PCB image (SVG) for visual review |
| `pcb_export_3d` | Export 3D model of the PCB |
| `pcb_export_gerbers` | Export Gerber and drill files for manufacturing |
| `pcb_export_manufacturing` | Export all manufacturing files for a fab house in one call |

### Utility (7)
| Tool | Description |
|---|---|
| `list_symbols` | Search KiCad's symbol libraries for a component |
| `list_footprints` | Search KiCad's footprint libraries |
| `get_netlist` | Extract netlist from a schematic (component-to-net mapping) |
| `sync_schematic_to_pcb` | Update PCB from schematic: nets, footprints, and pad net assignments |
| `search_jlcpcb_parts` | Search JLCPCB parts catalog. No authentication needed |
| `get_jlcpcb_part` | Get details for a specific JLCPCB part by LCSC number |
| `list_jlcpcb_categories` | List available JLCPCB part categories for filtering searches |

## Running Tests

```bash
pytest -v
```

Tests that need KiCad's libraries or `kicad-cli` are skipped automatically when those aren't installed.

## Architecture

```
src/kicad_mcp/
  server.py        # MCP server entry point (FastMCP)
  sexp_parser.py   # Lossless S-expression parser/writer
  schematic.py     # .kicad_sch file operations
  pcb.py           # .kicad_pcb file operations
  library.py       # KiCad library search, lookup and caching
  cli.py           # kicad-cli + Freerouting wrapper
  jlcpcb.py        # JLCPCB parts search (public API)
  types.py         # Shared data types
```

The server works by directly reading and writing KiCad's S-expression files. A custom parser (`sexp_parser.py`) handles lossless round-trip parsing: it preserves quoting, escapes, number spelling, token ordering and structure, and writes files with KiCad 8's own layout, so a file saved by KiCad comes back byte-for-byte identical apart from the elements that were changed. Writes go to a temporary file that is atomically renamed over the original.

Schematic pin positions and PCB footprint rotation and flipping follow KiCad's own transforms; the test suite checks them against `kicad-cli` netlists and the values `pcbnew` produces.

## License

MIT
