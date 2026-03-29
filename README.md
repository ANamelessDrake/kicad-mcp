# KiCad MCP Server

An MCP (Model Context Protocol) server that allows LLMs to programmatically create, read, and modify KiCad 8 schematic (`.kicad_sch`) and PCB layout (`.kicad_pcb`) files.

## Features

- **Schematic tools**: Place symbols, add wires, labels, power symbols, run ERC
- **PCB tools**: Place footprints, route traces, add vias/zones, run DRC, export Gerbers
- **Library search**: Query KiCad symbol and footprint libraries
- **File-based**: Directly reads/writes KiCad S-expression files — no running KiCad instance needed

## Requirements

- Python 3.10+
- KiCad 8 (for `kicad-cli` and library files)

## Installation

```bash
pip install -e ".[dev]"
```

## Usage with Claude Code

Add to your MCP configuration:

```json
{
  "mcpServers": {
    "kicad": {
      "command": "python",
      "args": ["-m", "kicad_mcp.server"],
      "env": {
        "KICAD_SYMBOL_DIR": "/usr/share/kicad/symbols",
        "KICAD_FOOTPRINT_DIR": "/usr/share/kicad/footprints",
        "KICAD_3DMODEL_DIR": "/usr/share/kicad/3dmodels"
      }
    }
  }
}
```

## Running Tests

```bash
pytest
```

## License

MIT
