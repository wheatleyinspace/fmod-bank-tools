# FMOD Studio strings.bank Toolkit & Binary Bank Specification

Open-source toolkit for inspecting, editing, and compiling FMOD Studio 2.x string dictionaries (`*.strings.bank`), generating valid FMOD Studio project skeletons (`.fspro`), and comprehensive technical specifications for the internal `.bank` and `FSB5` binary formats.

---

## Table of Contents

- [Repository Overview](#repository-overview)
- [strings.bank Toolkit Features](#stringsbank-toolkit-features)
- [Graphical Interface (GUI)](#graphical-interface-gui)
- [Command Line Interface (CLI)](#command-line-interface-cli)
- [FMOD Studio Project Skeleton Generator](#fmod-studio-project-skeleton-generator)
- [Technical Specifications](#technical-specifications)
- [Requirements](#requirements)
- [License](#license)

---

## Repository Overview

```
├── fmod_strings_engine.py      # Core engine: Radix Trie compiler, decompiler, 15-bit varint codec
├── strings_editor.py           # CLI for strings.bank (unpack, pack, diff, aliasing)
├── gui_editor.py               # Tkinter GUI editor with live search and inspector
├── fmod_skeleton_generator.py  # FMOD Studio project skeleton generator (.fspro)
├── docs/
│   ├── BANK_SPECIFICATION.md   # Binary specification for *.bank, RIFF/FEV, and FSB5
│   ├── STRINGS_BANK_SPECIFICATION.md # Specification for *.strings.bank and Radix Trie structures
│   └── Studio.02.03.00.metamodel.xml # Reference FMOD Studio metamodel schema
└── .gitignore
```

---

## strings.bank Toolkit Features

`*.strings.bank` files (e.g. `Master.strings.bank`, `Host.strings.bank`) serve as the address dictionary for FMOD Studio. They map human-readable URI paths (`event:/...`, `bus:/...`, `parameter:/...`) to 128-bit `FMOD_GUID` structures via a compressed prefix tree (Radix Trie).

1. **Unpack / Decompile**:
   - Dumps `*.strings.bank` into structured `JSON` or `CSV`.
   - Preserves object categorizations (`event`, `bus`, `vca`, `parameter`, `snapshot`, `bank`).
2. **Pack / Recompile**:
   - Compiles edited `JSON`/`CSV` files back into binary `*.strings.bank` files.
   - Reconstructs the Radix Trie with prefix nodes (`ch = 0`).
   - Sorts GUIDs lexicographically across `(Data1, Data2, Data3, Data4)` for binary search compatibility.
3. **Audio Modding & Aliasing**:
   - **Route redirection (Aliasing)**: Redirect existing game event paths to custom GUIDs without modifying multi-gigabyte audio banks.
   - **Add paths**: Register new event paths for custom sound banks.
   - **Remove paths**: Safely unbind unused sound events.
4. **Diffing**:
   - Compare two `strings.bank` files to detect added, modified, and removed events.
5. **Verification**:
   - Validates internal Trie layout, offsets, and checksums against official FMOD runtime rules.

---

## Graphical Interface (GUI)

Launch the visual editor:
```bash
python gui_editor.py
```

Or open a specific bank directly:
```bash
python strings_editor.py gui Host.strings.bank
```

### GUI Features:
- Real-time search across paths and GUIDs.
- Type filtering: `event`, `bus`, `vca`, `parameter`, `snapshot`, `bank`.
- Sortable table columns.
- Node Inspector: edit paths, reassign GUIDs, or generate random GUIDs (`🎲`).
- One-click event aliasing.
- Project skeleton generator trigger.

---

## Command Line Interface (CLI)

### 1. Inspect and Search
```bash
# Search events matching a keyword
python strings_editor.py list Host.strings.bank --search Timer

# Filter by mixer buses
python strings_editor.py list Host.strings.bank --filter bus

# Filter by VCAs
python strings_editor.py list Host.strings.bank --filter vca
```

### 2. Export (Unpack)
```bash
# Export to JSON
python strings_editor.py unpack Host.strings.bank -o events.json

# Export to CSV (for spreadsheet editing)
python strings_editor.py unpack Host.strings.bank --csv -o events.csv
```

### 3. Rebuild (Pack)
```bash
# Build binary bank from JSON
python strings_editor.py pack events.json Host_modded.strings.bank
```

### 4. Direct Entry Manipulation
```bash
# Add a new event mapping
python strings_editor.py add Host.strings.bank "event:/MyMod/NewSound" "{12345678-1234-1234-1234-123456789abc}"

# Redirect an existing path to another GUID (Aliasing)
python strings_editor.py set-guid Host.strings.bank "event:/SFX/TimerStart" "{12345678-1234-1234-1234-123456789abc}"

# Remove an event path
python strings_editor.py remove Host.strings.bank "event:/SFX/TimerStart"
```

### 5. Diff and Verification
```bash
# Compare two banks
python strings_editor.py diff Host.strings.bank Host_modded.strings.bank

# Verify internal bank integrity
python strings_editor.py verify Host.strings.bank
```

---

## FMOD Studio Project Skeleton Generator

The utility [fmod_skeleton_generator.py](fmod_skeleton_generator.py) reconstructs an FMOD Studio project directory tree (`.fspro`) from any `strings.bank`, `strings.json`, or `GUIDs.txt`:

```bash
# Generate project from strings.bank
python fmod_skeleton_generator.py Host.strings.bank -o MyFmodProject -n "HostAudio"

# Or via strings_editor CLI:
python strings_editor.py skeleton Host.strings.bank -o MyFmodProject -n "HostAudio"
```

### Generated Structure:
- Project file: `MyFmodProject.fspro`.
- Directory hierarchy under `Metadata/` (`Event/`, `EventFolder/`, `Bank/`, `Group/`, `Parameter/`, `Return/`, `Master.xml`).
- Retains original GUIDs for all events, buses, and folders.
- Opens in FMOD Studio (2.0x / 2.03+) with valid schemas.

---

## Technical Specifications

The `docs/` folder provides formal specifications of the FMOD Studio runtime banking architecture:

1. **[docs/STRINGS_BANK_SPECIFICATION.md](docs/STRINGS_BANK_SPECIFICATION.md)** — `strings.bank` internal architecture:
   - 8-byte Radix Trie node layout (`TrieNode`).
   - 15-bit Varint compression scheme.
   - String Pool and reverse lookup indexing tables (Sorted GUIDs, Leaf Mapping, Parent Table).
   - Standalone Python parser implementation.

2. **[docs/BANK_SPECIFICATION.md](docs/BANK_SPECIFICATION.md)** — Binary `.bank` and `FSB5` specification:
   - RIFF/FEV container layout and bank dispatcher (`sub_18012D6E0`).
   - Detailed format breakdown for all chunks: `BNKI`, `MBSS`, `GBSS`, `RBSS`, `BEFX`, `SEFX`, `SCFX`, `EVTS`, `TLNS`, `CRVS`, `CTRS`, `MODS`, `SNAS`, `WAIS`, `MUIS`, `WAVS`, `HASH`, etc.
   - DSP disk-to-runtime dispatch table (`toFMOD`, `sub_1801352A0`, jump table `jpt_180135306`).
   - Controller blocks (`CTRL`, 52 bytes) and Hermite spline curve nodes (`CURV`, 16 bytes).
   - `FSB5` bit-packed sample headers (60-byte base header, 64-bit descriptors, Vorbis setup chunk `0xC4C30A29`).
   - FMOD Studio project object model constraints and XML relationship rules.

---

## Requirements

- **Python**: 3.8+
- **Dependencies**: Python standard library only (`struct`, `json`, `csv`, `os`, `sys`, `xml.etree.ElementTree`, `tkinter`). No external packages required.

---

## License

This project is licensed under the MIT License. FMOD Studio is a registered trademark of Firelight Technologies Pty Ltd. This project is an independent research implementation and is not affiliated with Firelight Technologies.

---

## Note on AI Assistance

Parts of this documentation, tooling scaffolding, and formatting were generated with the assistance of LLMs / AI pair-programming tools during the reverse-engineering process. While all binary offsets, struct layouts, and opcode dispatch tables have been cross-verified against official FMOD binaries in IDA Pro, some phrasing may still carry an AI-assisted tone. 

If you notice any inaccuracies, awkward wording, or missing details, issues and pull requests are very welcome!
