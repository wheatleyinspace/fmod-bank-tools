#!/usr/bin/env python3
"""
FMOD Studio strings.bank CLI Editor
Usage:
    python strings_editor.py unpack Host.strings.bank -o events.json
    python strings_editor.py pack events.json Host_modded.strings.bank
    python strings_editor.py list Host.strings.bank --filter event --search Timer
    python strings_editor.py add Host.strings.bank "event:/Mod/NewSound" "{12345678-1234-1234-1234-123456789abc}"
    python strings_editor.py set-guid Host.strings.bank "event:/SFX/TimerStart" "{12345678-1234-1234-1234-123456789abc}"
    python strings_editor.py remove Host.strings.bank "event:/SFX/TimerStart"
    python strings_editor.py diff Host.strings.bank Host_modded.strings.bank
    python strings_editor.py verify Host.strings.bank
    python strings_editor.py gui [Host.strings.bank]
"""

import sys
import os
import json
import csv
import shutil
import argparse
import ctypes
from typing import Optional

import fmod_strings_engine as eng


def load_model_from_file(file_path: str) -> eng.StringsBankModel:
    if not os.path.exists(file_path):
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    ext = os.path.splitext(file_path)[1].lower()
    if ext == '.json':
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return eng.StringsBankModel.from_dict(data)
    elif ext == '.csv':
        bank_guid = None
        entries_data = []
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith('# bank_guid:'):
                    bank_guid = stripped.split(':', 1)[1].strip()
                elif stripped and not stripped.startswith('#'):
                    entries_data.append(stripped)

        model = eng.StringsBankModel(bank_guid=bank_guid)
        reader = csv.DictReader(entries_data)
        for row in reader:
            if 'path' in row and 'guid' in row:
                model.add_entry(row['path'], row['guid'])
        return model
    else:
        with open(file_path, 'rb') as f:
            raw = f.read()
        return eng.parse_strings_bank(raw)


def backup_file_if_exists(file_path: str):
    """Creates a .bak backup of file_path before modifying it."""
    if os.path.exists(file_path):
        bak_path = file_path + ".bak"
        shutil.copy2(file_path, bak_path)
        print(f"[BACKUP] Created backup: {bak_path}")


def save_model_to_file(model: eng.StringsBankModel, out_path: str, template_path: Optional[str] = None, no_backup: bool = False):
    if not no_backup and os.path.exists(out_path):
        backup_file_if_exists(out_path)

    ext = os.path.splitext(out_path)[1].lower()
    if ext == '.json':
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(model.to_dict(), f, indent=2, ensure_ascii=False)
        print(f"[OK] Exported {len(model.entries)} entries to JSON: {out_path}")
    elif ext == '.csv':
        with open(out_path, 'w', encoding='utf-8', newline='') as f:
            f.write(f"# bank_guid: {model.bank_guid}\n")
            writer = csv.writer(f)
            writer.writerow(['type', 'path', 'guid'])
            for e in model.entries:
                writer.writerow([e.item_type, e.path, e.guid_str])
        print(f"[OK] Exported {len(model.entries)} entries to CSV: {out_path}")
    else:
        template_bytes = None
        if template_path and os.path.exists(template_path):
            with open(template_path, 'rb') as tf:
                template_bytes = tf.read()
        bank_bytes = eng.build_strings_bank(model, template_bytes=template_bytes)
        with open(out_path, 'wb') as f:
            f.write(bank_bytes)
        print(f"[OK] Compiled {len(model.entries)} entries into bank: {out_path} ({len(bank_bytes):,} bytes)")


def cmd_unpack(args):
    model = load_model_from_file(args.input)
    out_path = args.output
    if not out_path:
        base = os.path.splitext(args.input)[0]
        out_path = base + ('.csv' if args.csv else '.json')
    save_model_to_file(model, out_path, no_backup=getattr(args, 'no_backup', False))


def cmd_pack(args):
    model = load_model_from_file(args.input)
    out_path = args.output
    save_model_to_file(model, out_path, template_path=args.template, no_backup=getattr(args, 'no_backup', False))


def cmd_list(args):
    model = load_model_from_file(args.input)
    filter_type = args.filter.lower() if args.filter else None
    search_term = args.search.lower() if args.search else None

    count = 0
    print(f"Bank GUID: {model.bank_guid}")
    print(f"{'#':<5} {'TYPE':<12} {'PATH':<60} {'GUID':<38}")
    print("-" * 118)
    for idx, e in enumerate(model.entries):
        if filter_type and e.item_type.lower() != filter_type:
            continue
        if search_term and search_term not in e.path.lower() and search_term not in e.guid_str:
            continue
        print(f"{idx:<5} {e.item_type:<12} {e.path:<60} {e.guid_str:<38}")
        count += 1
    print("-" * 118)
    print(f"Total matching items: {count} / {len(model.entries)}")


def cmd_add(args):
    model = load_model_from_file(args.file)
    entry = model.add_entry(args.path, args.guid)
    out_path = args.output or args.file
    template = args.file if args.file.endswith('.bank') else None
    save_model_to_file(model, out_path, template_path=template, no_backup=args.no_backup)
    print(f"[OK] Added/Updated: {entry.path} -> {entry.guid_str}")


def cmd_remove(args):
    model = load_model_from_file(args.file)
    if model.remove_entry(args.path):
        out_path = args.output or args.file
        template = args.file if args.file.endswith('.bank') else None
        save_model_to_file(model, out_path, template_path=template, no_backup=args.no_backup)
        print(f"[OK] Removed entry: {args.path}")
    else:
        print(f"[WARN] Entry not found: {args.path}", file=sys.stderr)


def cmd_set_guid(args):
    model = load_model_from_file(args.file)
    entry = model.get_entry(args.path)
    if not entry:
        print(f"Error: Path not found in bank: {args.path}", file=sys.stderr)
        sys.exit(1)
    old_guid = entry.guid_str
    model.add_entry(args.path, args.guid)
    out_path = args.output or args.file
    template = args.file if args.file.endswith('.bank') else None
    save_model_to_file(model, out_path, template_path=template, no_backup=args.no_backup)
    print(f"[OK] Changed GUID for '{args.path}':\n  From: {old_guid}\n  To:   {args.guid}")


def cmd_rename(args):
    model = load_model_from_file(args.file)
    if model.rename_entry(args.old_path, args.new_path):
        out_path = args.output or args.file
        template = args.file if args.file.endswith('.bank') else None
        save_model_to_file(model, out_path, template_path=template, no_backup=args.no_backup)
        print(f"[OK] Renamed:\n  Old: {args.old_path}\n  New: {args.new_path}")
    else:
        print(f"Error: Old path not found: {args.old_path}", file=sys.stderr)
        sys.exit(1)


def cmd_diff(args):
    m1 = load_model_from_file(args.file1)
    m2 = load_model_from_file(args.file2)

    map1 = {e.path.lower(): e for e in m1.entries}
    map2 = {e.path.lower(): e for e in m2.entries}

    added = [e for p, e in map2.items() if p not in map1]
    removed = [e for p, e in map1.items() if p not in map2]
    changed = []
    for p, e1 in map1.items():
        if p in map2 and e1.guid_str != map2[p].guid_str:
            changed.append((e1, map2[p]))

    print(f"=== DIFF: {args.file1} vs {args.file2} ===")
    print(f"Bank GUID: {m1.bank_guid} vs {m2.bank_guid}")
    print(f"Total entries: {len(m1.entries)} vs {len(m2.entries)}")
    print(f"Added (+): {len(added)}, Removed (-): {len(removed)}, Modified GUIDs (~): {len(changed)}")
    print("-" * 80)
    for e in added:
        print(f"+ [ADD]  {e.path} -> {e.guid_str}")
    for e in removed:
        print(f"- [DEL]  {e.path} -> {e.guid_str}")
    for e1, e2 in changed:
        print(f"~ [MOD]  {e1.path}")
        print(f"         - {e1.guid_str}")
        print(f"         + {e2.guid_str}")
    print("-" * 80)


def cmd_verify(args):
    file_path = args.file
    print(f"[*] Verifying bank: {file_path}")
    with open(file_path, 'rb') as f:
        raw = f.read()

    # 1. Internal engine validation
    model = eng.parse_strings_bank(raw)
    print(f"[1/2] Internal Parser: SUCCESS! Loaded {len(model.entries)} items. Bank GUID: {model.bank_guid}")

    # 2. FMOD Studio DLL validation if present
    repo_root = os.path.dirname(os.path.abspath(__file__))
    dll_path = os.path.join(repo_root, 'api', 'studio', 'lib', 'x64', 'fmodstudio.dll')
    core_path = os.path.join(repo_root, 'api', 'core', 'lib', 'x64', 'fmod.dll')

    if os.path.exists(dll_path) and os.path.exists(core_path):
        os.add_dll_directory(os.path.dirname(core_path))
        os.add_dll_directory(os.path.dirname(dll_path))
        fmodstudio = ctypes.CDLL(dll_path)

        system = ctypes.c_void_p()
        fmodstudio.FMOD_Studio_System_Create(ctypes.byref(system), 0x00020300)
        fmodstudio.FMOD_Studio_System_Initialize(system, 128, 0, 0, None)

        bank = ctypes.c_void_p()
        buf = (ctypes.c_char * len(raw)).from_buffer_copy(raw)
        res = fmodstudio.FMOD_Studio_System_LoadBankMemory(system, buf, len(raw), 0, 0, ctypes.byref(bank))
        if res != 0:
            print(f"[2/2] FMOD Runtime Load: FAILED with error code {res}", file=sys.stderr)
            sys.exit(1)

        count = ctypes.c_int()
        fmodstudio.FMOD_Studio_Bank_GetStringCount(bank, ctypes.byref(count))
        print(f"[2/2] FMOD Official Runtime: SUCCESS! Loaded {count.value} items.")

        class FMOD_GUID(ctypes.Structure):
            _fields_ = [('Data1', ctypes.c_uint32), ('Data2', ctypes.c_uint16), ('Data3', ctypes.c_uint16), ('Data4', ctypes.c_uint8 * 8)]

        spot_errors = 0
        sample_entries = model.entries[:min(20, len(model.entries))]
        for e in sample_entries:
            tg = FMOD_GUID()
            r = fmodstudio.FMOD_Studio_System_LookupID(system, e.path.encode('utf-8'), ctypes.byref(tg))
            if r != 0 or bytes(tg) != e.guid_bytes:
                spot_errors += 1
                print(f"      Mismatch on: {e.path}")

        fmodstudio.FMOD_Studio_Bank_Unload(bank)
        fmodstudio.FMOD_Studio_System_Release(system)
        if spot_errors == 0:
            print(f"[*] All spot-checked lookups resolved with 100% accuracy!")
        else:
            print(f"[WARN] {spot_errors} spot-check lookups had issues.")
    else:
        print("[2/2] FMOD DLL not found in api/ folder; skipped native DLL test.")


def cmd_gui(args):
    import gui_editor
    initial_file = args.file if hasattr(args, 'file') else None
    gui_editor.main(initial_file)


def cmd_skeleton(args):
    import fmod_skeleton_generator
    res = fmod_skeleton_generator.generate_skeleton_from_file(args.file, args.output, project_name=args.name, model=args.model)
    print(f"[+] Generated FMOD Studio Project Skeleton:")
    print(f"  * Project File: {res['project_file']}")
    print(f"  * Total Events: {res['events_count']}")
    print(f"  * Folder Hierarchy: {res['folders_count']} folders")
    print(f"  * Total Banks: {res['banks_count']}")


def main():
    parser = argparse.ArgumentParser(description="FMOD Studio strings.bank Decompiler & Rebuilder")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # unpack
    p_unpack = subparsers.add_parser("unpack", help="Decompile strings.bank to JSON or CSV")
    p_unpack.add_argument("input", help="Input *.strings.bank file")
    p_unpack.add_argument("-o", "--output", help="Output JSON/CSV path")
    p_unpack.add_argument("--csv", action="store_true", help="Export to CSV instead of JSON")
    p_unpack.add_argument("--no-backup", action="store_true", help="Do not create a .bak file if output exists")
    p_unpack.set_defaults(func=cmd_unpack)

    # pack
    p_pack = subparsers.add_parser("pack", help="Compile JSON/CSV back to *.strings.bank")
    p_pack.add_argument("input", help="Input JSON or CSV file")
    p_pack.add_argument("output", help="Output *.strings.bank path")
    p_pack.add_argument("--template", help="Optional original bank file to use as template")
    p_pack.add_argument("--no-backup", action="store_true", help="Do not create a .bak file if output exists")
    p_pack.set_defaults(func=cmd_pack)

    # list
    p_list = subparsers.add_parser("list", help="List all events and GUIDs in bank or JSON")
    p_list.add_argument("input", help="Input file (*.strings.bank or *.json)")
    p_list.add_argument("--filter", help="Filter by type (event, bus, vca, parameter, snapshot, bank)")
    p_list.add_argument("--search", help="Search by substring in path or GUID")
    p_list.set_defaults(func=cmd_list)

    # add
    p_add = subparsers.add_parser("add", help="Add or update an event in strings.bank or JSON")
    p_add.add_argument("file", help="Target bank or JSON file")
    p_add.add_argument("path", help="Event path (e.g. event:/Mod/CustomSound)")
    p_add.add_argument("guid", help="GUID string {xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx}")
    p_add.add_argument("-o", "--output", help="Optional output path (defaults to overwriting input)")
    p_add.add_argument("--no-backup", action="store_true", help="Do not create a .bak file when overwriting")
    p_add.set_defaults(func=cmd_add)

    # remove
    p_remove = subparsers.add_parser("remove", help="Remove an event from strings.bank or JSON")
    p_remove.add_argument("file", help="Target bank or JSON file")
    p_remove.add_argument("path", help="Event path to remove")
    p_remove.add_argument("-o", "--output", help="Optional output path (defaults to overwriting input)")
    p_remove.add_argument("--no-backup", action="store_true", help="Do not create a .bak file when overwriting")
    p_remove.set_defaults(func=cmd_remove)

    # set-guid
    p_set_guid = subparsers.add_parser("set-guid", help="Change GUID for an existing path (aliasing)")
    p_set_guid.add_argument("file", help="Target bank or JSON file")
    p_set_guid.add_argument("path", help="Event path")
    p_set_guid.add_argument("guid", help="New GUID string")
    p_set_guid.add_argument("-o", "--output", help="Optional output path (defaults to overwriting input)")
    p_set_guid.add_argument("--no-backup", action="store_true", help="Do not create a .bak file when overwriting")
    p_set_guid.set_defaults(func=cmd_set_guid)

    # rename
    p_rename = subparsers.add_parser("rename", help="Rename an event path")
    p_rename.add_argument("file", help="Target bank or JSON file")
    p_rename.add_argument("old_path", help="Old event path")
    p_rename.add_argument("new_path", help="New event path")
    p_rename.add_argument("-o", "--output", help="Optional output path (defaults to overwriting input)")
    p_rename.add_argument("--no-backup", action="store_true", help="Do not create a .bak file when overwriting")
    p_rename.set_defaults(func=cmd_rename)

    # diff
    p_diff = subparsers.add_parser("diff", help="Show differences between two strings banks")
    p_diff.add_argument("file1", help="First bank or JSON")
    p_diff.add_argument("file2", help="Second bank or JSON")
    p_diff.set_defaults(func=cmd_diff)

    # verify
    p_verify = subparsers.add_parser("verify", help="Verify a strings.bank using internal parser and FMOD Studio DLL")
    p_verify.add_argument("file", help="Bank file to verify")
    p_verify.set_defaults(func=cmd_verify)

    # gui
    p_gui = subparsers.add_parser("gui", help="Launch visual GUI editor")
    p_gui.add_argument("file", nargs="?", help="Optional file to open on startup")
    p_gui.set_defaults(func=cmd_gui)

    # skeleton
    p_skel = subparsers.add_parser("skeleton", help="Generate FMOD Studio project skeleton (.fspro) from strings.bank or JSON")
    p_skel.add_argument("file", help="Input strings.bank, strings.json, or GUIDs.txt")
    p_skel.add_argument("-o", "--output", required=True, help="Destination directory for the .fspro and Metadata")
    p_skel.add_argument("-n", "--name", help="Project name (default: derived from input file)")
    p_skel.add_argument("-m", "--model", default="Studio.02.03.00", help="Serialization model version (default: Studio.02.03.00)")
    p_skel.set_defaults(func=cmd_skeleton)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
