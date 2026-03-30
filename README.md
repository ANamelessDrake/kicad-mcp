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

## Usage with Claude Code

Add to your project's `.mcp.json` or `~/.claude.json`:

```json
{
  "mcpServers": {
    "kicad": {
      "command": "python3",
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

Adjust `python3` to match your Python installation (e.g., `python3.12`) and update the `PYTHONPATH` to point to where you cloned this repo.

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `KICAD_SYMBOL_DIR` | KiCad symbol library directory | `/usr/share/kicad/symbols` |
| `KICAD_FOOTPRINT_DIR` | KiCad footprint library directory | `/usr/share/kicad/footprints` |
| `KICAD_3DMODEL_DIR` | KiCad 3D model directory | `/usr/share/kicad/3dmodels` |
| `KICAD_MCP_LOG_LEVEL` | Log level (DEBUG, INFO, WARNING, ERROR) | `INFO` |
| `KICAD_MCP_LOG_FILE` | Optional log file path | *(stderr only)* |
| `FREEROUTING_JAR` | Path to Freerouting JAR file | `freerouting.jar` |

## Tools (29)

### Schematic (8)
| Tool | Description |
|---|---|
| `schematic_read` | Parse a .kicad_sch file into structured JSON |
| `schematic_place_symbol` | Place a component symbol (auto-loads library definitions) |
| `schematic_add_wire` | Add a wire between points |
| `schematic_add_label` | Add a net label |
| `schematic_add_power_symbol` | Add a power port (GND, 3V3, 5V, etc.) |
| `schematic_add_global_label` | Add a global label for inter-sheet connectivity |
| `schematic_delete` | Delete any element by UUID |
| `schematic_run_erc` | Run Electrical Rules Check via kicad-cli |

### PCB (15)
| Tool | Description |
|---|---|
| `pcb_read` | Parse a .kicad_pcb file into structured JSON |
| `pcb_place_footprint` | Place a footprint (auto-loads from library) |
| `pcb_place_footprint_array` | Place an array of footprints (grid or circular pattern) |
| `pcb_move_footprint` | Move a footprint to a new position |
| `pcb_add_trace` | Add copper trace segments |
| `pcb_add_via` | Add a via |
| `pcb_add_zone` | Add a copper zone/pour |
| `pcb_set_board_outline` | Set the board outline on Edge.Cuts |
| `pcb_assign_net_to_pad` | Assign a net to a footprint pad |
| `pcb_add_mounting_hole` | Add a mounting hole |
| `pcb_autoroute` | Autoroute via Freerouting (requires Java + JAR) |
| `pcb_run_drc` | Run Design Rules Check via kicad-cli |
| `pcb_export_image` | Export board as SVG |
| `pcb_export_3d` | Export 3D model (STEP/VRML) |
| `pcb_export_gerbers` | Export Gerber + drill files for manufacturing |

### Utility (6)
| Tool | Description |
|---|---|
| `list_symbols` | Search KiCad symbol libraries |
| `list_footprints` | Search KiCad footprint libraries |
| `get_netlist` | Extract netlist from schematic |
| `sync_schematic_to_pcb` | Update PCB from schematic (nets + footprints) |
| `search_jlcpcb_parts` | Search JLCPCB parts catalog |
| `get_jlcpcb_part` | Get JLCPCB part details by LCSC number |
| `list_jlcpcb_categories` | List JLCPCB part categories |

## Running Tests

```bash
pytest -v
```

## Architecture

```
src/kicad_mcp/
  server.py        # MCP server entry point (FastMCP, 29 tools)
  sexp_parser.py   # Lossless S-expression parser/writer
  schematic.py     # .kicad_sch file operations
  pcb.py           # .kicad_pcb file operations
  library.py       # KiCad library lookup
  cli.py           # kicad-cli + Freerouting wrapper
  jlcpcb.py        # JLCPCB parts search (public API)
  types.py         # Shared data types
```

The server works by directly reading and writing KiCad's S-expression files. A custom parser (`sexp_parser.py`) handles lossless round-trip parsing — it preserves quoting, token ordering, and structure so that modifying one element doesn't corrupt the rest of the file.

## License

MIT
