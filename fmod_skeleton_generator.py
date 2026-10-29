"""
FMOD Studio Project Skeleton Generator
Author: Antigravity
Description:
    Automatically generates a complete, valid FMOD Studio project (.fspro + Metadata/)
    skeleton from an FMOD Studio strings.bank, strings.json, or GUIDs.txt file.
    All events, folders, banks, and buses are recreated with their exact GUIDs and path hierarchies.
"""

import os
import sys
import uuid
import json
import re
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any

try:
    import fmod_strings_engine
except ImportError:
    from . import fmod_strings_engine


def new_guid() -> str:
    """Generates a new FMOD-formatted GUID string: {xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx}"""
    return f"{{{str(uuid.uuid4())}}}"


class FmodProjectSkeletonGenerator:
    def __init__(self, serialization_model: str = "Studio.02.03.00"):
        self.serialization_model = serialization_model
        # Mappings: path -> guid
        self.events: Dict[str, str] = {}      # 'event:/a/b' -> '{guid}'
        self.banks: Dict[str, str] = {}       # 'bank:/Master' -> '{guid}'
        self.buses: Dict[str, str] = {}       # 'bus:/Master' -> '{guid}'
        self.vcas: Dict[str, str] = {}        # 'vca:/Master' -> '{guid}'
        self.parameters: Dict[str, str] = {}  # 'parameter:/Param' -> '{guid}'
        self.snapshots: Dict[str, str] = {}   # 'snapshot:/Snap' -> '{guid}'
        self.other_entries: Dict[str, str] = {}

    def load_from_strings_bank(self, file_path: str):
        """Loads path/GUID mappings from a binary *.strings.bank file."""
        with open(file_path, "rb") as f:
            data = f.read()
        model = fmod_strings_engine.parse_strings_bank(data)
        for entry in model.entries:
            self._add_entry(entry.path, entry.guid_str)

    def load_from_json(self, file_path: str):
        """Loads path/GUID mappings from a strings.json file."""
        with open(file_path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        
        # Support various JSON formats:
        # 1. {"entries": [{"path": ..., "guid": ...}, ...]}
        # 2. {"path": "guid", ...}
        if isinstance(data, dict):
            if "entries" in data and isinstance(data["entries"], list):
                for item in data["entries"]:
                    path = item.get("path")
                    guid = item.get("guid") or item.get("guid_str")
                    if path and guid:
                        self._add_entry(path, guid)
            else:
                for path, val in data.items():
                    if isinstance(val, str):
                        self._add_entry(path, val)
                    elif isinstance(val, dict) and "guid" in val:
                        self._add_entry(path, val["guid"])
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    path = item.get("path")
                    guid = item.get("guid") or item.get("guid_str")
                    if path and guid:
                        self._add_entry(path, guid)

    def load_from_guids_txt(self, file_path: str):
        """Loads path/GUID mappings from a text file formatted like GUIDs.txt ({guid} path or path {guid})."""
        guid_re = re.compile(r'\{[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}')
        with open(file_path, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                m = guid_re.search(line)
                if m:
                    g = m.group(0).lower()
                    rest = line.replace(g, "").strip()
                    if rest:
                        self._add_entry(rest, g)

    def _add_entry(self, path: str, guid_str: str):
        guid_norm = guid_str.strip().lower()
        if not guid_norm.startswith("{"):
            guid_norm = f"{{{guid_norm}}}"
        clean_path = path.strip()
        
        if clean_path.startswith("event:/"):
            self.events[clean_path] = guid_norm
        elif clean_path.startswith("bank:/"):
            self.banks[clean_path] = guid_norm
        elif clean_path.startswith("bus:/"):
            self.buses[clean_path] = guid_norm
        elif clean_path.startswith("vca:/"):
            self.vcas[clean_path] = guid_norm
        elif clean_path.startswith("parameter:/"):
            self.parameters[clean_path] = guid_norm
        elif clean_path.startswith("snapshot:/"):
            self.snapshots[clean_path] = guid_norm
        else:
            self.other_entries[clean_path] = guid_norm

    def _resolve_target_bank(self, path: str, bank_map: Dict[str, str], default_bank_guid: Optional[str] = None, project_name: str = "") -> Optional[str]:
        # Filter out .strings bank (e.g. bank:/Host.strings) because strings bank is generated automatically
        valid_banks = {bpath.replace("bank:/", "").strip(): bguid 
                       for bpath, bguid in bank_map.items() 
                       if not bpath.lower().endswith(".strings") and bpath.lower() != "bank:/strings"}
        if not valid_banks:
            return default_bank_guid or (list(bank_map.values())[0] if bank_map else None)

        clean = path.split(":/")[-1]
        parts = [p.lower().strip() for p in clean.split("/")]

        # 1. Exact match on any folder segment (from top to bottom)
        for p in parts:
            for bname, bguid in valid_banks.items():
                if p == bname.lower():
                    return bguid

        # 2. Substring match on whole path
        clean_lower = clean.lower()
        for bname, bguid in valid_banks.items():
            if bname.lower() in clean_lower:
                return bguid

        # 3. Dynamic match against project name or "master"
        candidates = ["master"]
        if project_name:
            candidates.append(project_name.lower().replace(".fspro", ""))
        for pref in candidates:
            for bname, bguid in valid_banks.items():
                if pref in bname.lower() or bname.lower() in pref:
                    return bguid

        # 4. Fallback to default or first sound bank
        return default_bank_guid or list(valid_banks.values())[0]

    def _resolve_target_bus(self, epath: str, master_bus_guid: str) -> str:
        """
        Resolves the best matching mixer bus GUID for an event path.
        Matches top folder to top bus (e.g. HOST -> BigSurvey_HOST),
        then refines if sub-buses match any subfolder/name tokens.
        Falls back to master_bus_guid.
        """
        if not self.buses:
            return master_bus_guid

        clean = epath.replace("event:/", "").strip("/")
        parts = clean.split("/")
        top_folder = parts[0].lower() if parts else ""

        # Filter out master bus from candidates
        non_master_buses = {bpath: bguid for bpath, bguid in self.buses.items() 
                            if bguid != master_bus_guid and bpath.lower() not in ("bus:/", "bus:/master", "bus:/master bus")}

        # 1. Match top folder to top-level buses
        matched_top_bus = None
        for bpath, bguid in non_master_buses.items():
            b_clean = bpath.replace("bus:/", "").strip("/")
            if "/" not in b_clean:  # top-level bus
                tokens = [t.lower() for t in b_clean.split("_")]
                if top_folder in tokens or b_clean.lower() == top_folder:
                    matched_top_bus = bpath
                    break

        if matched_top_bus:
            # Check if any sub-bus under matched_top_bus matches remaining path parts
            remaining_parts = [p.lower() for p in parts[1:]]
            sub_buses = [b for b in non_master_buses.keys() if b.startswith(matched_top_bus + "/")]
            sub_buses.sort(key=lambda x: len(x), reverse=True)
            for sb in sub_buses:
                sub_leaf = sb.split("/")[-1].lower()
                if any(sub_leaf == rp or sub_leaf in rp for rp in remaining_parts):
                    return non_master_buses[sb]
            return non_master_buses[matched_top_bus]

        # 2. General fallback: match any bus leaf to any event token
        for p in parts:
            p_lower = p.lower()
            for bpath, bguid in non_master_buses.items():
                b_leaf = bpath.split("/")[-1].lower()
                if b_leaf == p_lower:
                    return bguid

        return master_bus_guid

    def generate_project(self, output_dir: str, project_name: str = "Project", default_bank_name: Optional[str] = None):
        """
        Generates the full FMOD Studio skeleton project in output_dir.
        """
        out_path = Path(output_dir)
        meta_path = out_path / "Metadata"
        out_path.mkdir(parents=True, exist_ok=True)
        
        # Subdirectories inside Metadata
        dirs_to_create = [
            "Asset",
            "Bank",
            "BankFolder",
            "EffectPresetFolder",
            "EncodingSetting",
            "Event",
            "EventFolder",
            "Group",
            "ParameterPreset",
            "ParameterPresetFolder",
            "Platform",
            "ProfilerFolder",
            "Return",
            "SandboxFolder",
            "Snapshot",
            "SnapshotGroup",
            "VCA",
        ]
        # Clean existing xml files in Metadata to prevent stale GUIDs or duplicate singletons (e.g. SnapshotList)
        if meta_path.exists():
            for root_xml in meta_path.glob("*.xml"):
                try:
                    root_xml.unlink()
                except Exception:
                    pass
            for d in dirs_to_create:
                target_dir = meta_path / d
                if target_dir.exists():
                    for old_xml in target_dir.glob("*.xml"):
                        try:
                            old_xml.unlink()
                        except Exception:
                            pass

        for d in dirs_to_create:
            (meta_path / d).mkdir(parents=True, exist_ok=True)

        # 1. Project file: <project_name>.fspro
        fspro_file = out_path / f"{project_name}.fspro"
        with open(fspro_file, "w", encoding="utf-8") as f:
            f.write(f'<?xml version="1.0" encoding="UTF-8"?>\n<objects serializationModel="{self.serialization_model}" />\n')

        # Generate core workspace GUIDs
        workspace_guid = new_guid()
        master_event_folder_guid = new_guid()
        master_tag_folder_guid = new_guid()
        master_effect_preset_folder_guid = new_guid()
        master_param_preset_folder_guid = new_guid()
        master_bank_folder_guid = new_guid()
        master_sandbox_folder_guid = new_guid()
        master_asset_folder_guid = new_guid()
        mixer_guid = new_guid()
        profiler_session_folder_guid = new_guid()
        platform_guid = new_guid()
        snapshot_group_guid = new_guid()
        reverb_return_guid = new_guid()

        # Master Bus GUID: use existing from strings bank if available, else new
        master_bus_guid = self.buses.get("bus:/") or self.buses.get("bus:/Master")
        if not master_bus_guid:
            # Check any bus ending with Master
            for bpath, bguid in self.buses.items():
                if bpath.lower() in ("bus:/", "bus:/master", "bus:/master bus"):
                    master_bus_guid = bguid
                    break
        if not master_bus_guid:
            master_bus_guid = new_guid()

        # 2. Workspace.xml
        workspace_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<objects serializationModel="{self.serialization_model}">
\t<object class="Workspace" id="{workspace_guid}">
\t\t<property name="builtBanksOutputDirectory">
\t\t\t<value>Build</value>
\t\t</property>
\t\t<property name="builtBanksMirrorBrowserFolderHierarchy">
\t\t\t<value>true</value>
\t\t</property>
\t\t<relationship name="masterEventFolder">
\t\t\t<destination>{master_event_folder_guid}</destination>
\t\t</relationship>
\t\t<relationship name="masterTagFolder">
\t\t\t<destination>{master_tag_folder_guid}</destination>
\t\t</relationship>
\t\t<relationship name="masterEffectPresetFolder">
\t\t\t<destination>{master_effect_preset_folder_guid}</destination>
\t\t</relationship>
\t\t<relationship name="masterParameterPresetFolder">
\t\t\t<destination>{master_param_preset_folder_guid}</destination>
\t\t</relationship>
\t\t<relationship name="masterBankFolder">
\t\t\t<destination>{master_bank_folder_guid}</destination>
\t\t</relationship>
\t\t<relationship name="masterSandboxFolder">
\t\t\t<destination>{master_sandbox_folder_guid}</destination>
\t\t</relationship>
\t\t<relationship name="masterAssetFolder">
\t\t\t<destination>{master_asset_folder_guid}</destination>
\t\t</relationship>
\t\t<relationship name="mixer">
\t\t\t<destination>{mixer_guid}</destination>
\t\t</relationship>
\t\t<relationship name="profilerSessionFolder">
\t\t\t<destination>{profiler_session_folder_guid}</destination>
\t\t</relationship>
\t\t<relationship name="platforms">
\t\t\t<destination>{platform_guid}</destination>
\t\t</relationship>
\t</object>
</objects>
"""
        with open(meta_path / "Workspace.xml", "w", encoding="utf-8") as f:
            f.write(workspace_xml)

        # 3. Master.xml
        master_effect_chain = new_guid()
        master_panner = new_guid()
        master_fader = new_guid()
        master_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<objects serializationModel="{self.serialization_model}">
\t<object class="MixerMaster" id="{master_bus_guid}">
\t\t<property name="name">
\t\t\t<value>Master Bus</value>
\t\t</property>
\t\t<relationship name="effectChain">
\t\t\t<destination>{master_effect_chain}</destination>
\t\t</relationship>
\t\t<relationship name="panner">
\t\t\t<destination>{master_panner}</destination>
\t\t</relationship>
\t\t<relationship name="mixer">
\t\t\t<destination>{mixer_guid}</destination>
\t\t</relationship>
\t</object>
\t<object class="MixerBusEffectChain" id="{master_effect_chain}">
\t\t<relationship name="effects">
\t\t\t<destination>{master_fader}</destination>
\t\t</relationship>
\t</object>
\t<object class="MixerBusPanner" id="{master_panner}">
\t\t<property name="overridingOutputFormat">
\t\t\t<value>2</value>
\t\t</property>
\t</object>
\t<object class="MixerBusFader" id="{master_fader}" />
</objects>
"""
        with open(meta_path / "Master.xml", "w", encoding="utf-8") as f:
            f.write(master_xml)

        # 4. Mixer.xml
        mixer_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<objects serializationModel="{self.serialization_model}">
\t<object class="Mixer" id="{mixer_guid}">
\t\t<relationship name="masterBus">
\t\t\t<destination>{master_bus_guid}</destination>
\t\t</relationship>
\t\t<relationship name="snapshotList">
\t\t\t<destination>{snapshot_group_guid}</destination>
\t\t</relationship>
\t</object>
</objects>
"""
        with open(meta_path / "Mixer.xml", "w", encoding="utf-8") as f:
            f.write(mixer_xml)

        # 5. Tags.xml
        tags_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<objects serializationModel="{self.serialization_model}">
\t<object class="MasterTagFolder" id="{master_tag_folder_guid}">
\t\t<property name="name">
\t\t\t<value>Master</value>
\t\t</property>
\t</object>
</objects>
"""
        with open(meta_path / "Tags.xml", "w", encoding="utf-8") as f:
            f.write(tags_xml)

        # 6. Boilerplate Folders
        # Asset Folder
        with open(meta_path / "Asset" / f"{master_asset_folder_guid}.xml", "w", encoding="utf-8") as f:
            f.write(f'<?xml version="1.0" encoding="UTF-8"?>\n<objects serializationModel="{self.serialization_model}">\n\t<object class="MasterAssetFolder" id="{master_asset_folder_guid}" />\n</objects>\n')

        # Bank Folder
        with open(meta_path / "BankFolder" / f"{master_bank_folder_guid}.xml", "w", encoding="utf-8") as f:
            f.write(f'<?xml version="1.0" encoding="UTF-8"?>\n<objects serializationModel="{self.serialization_model}">\n\t<object class="MasterBankFolder" id="{master_bank_folder_guid}" />\n</objects>\n')

        # EffectPresetFolder
        with open(meta_path / "EffectPresetFolder" / f"{master_effect_preset_folder_guid}.xml", "w", encoding="utf-8") as f:
            f.write(f'<?xml version="1.0" encoding="UTF-8"?>\n<objects serializationModel="{self.serialization_model}">\n\t<object class="MasterEffectPresetFolder" id="{master_effect_preset_folder_guid}" />\n</objects>\n')

        # ParameterPresetFolder
        with open(meta_path / "ParameterPresetFolder" / f"{master_param_preset_folder_guid}.xml", "w", encoding="utf-8") as f:
            f.write(f'<?xml version="1.0" encoding="UTF-8"?>\n<objects serializationModel="{self.serialization_model}">\n\t<object class="MasterParameterPresetFolder" id="{master_param_preset_folder_guid}" />\n</objects>\n')

        # SandboxFolder
        with open(meta_path / "SandboxFolder" / f"{master_sandbox_folder_guid}.xml", "w", encoding="utf-8") as f:
            f.write(f'<?xml version="1.0" encoding="UTF-8"?>\n<objects serializationModel="{self.serialization_model}">\n\t<object class="MasterSandboxFolder" id="{master_sandbox_folder_guid}" />\n</objects>\n')

        # 7. Banks
        # If no banks in strings bank, create a default "Master" bank
        bank_map = dict(self.banks)
        if not bank_map:
            def_bank_guid = new_guid()
            bank_map["bank:/Master"] = def_bank_guid

        master_bank_guid = None
        for bpath, bguid in bank_map.items():
            bname = bpath.replace("bank:/", "").strip()
            is_master = "master" in bname.lower()
            if is_master and not master_bank_guid:
                master_bank_guid = bguid
            
            is_master_prop = ""
            if is_master:
                is_master_prop = '\t\t<property name="isMasterBank">\n\t\t\t<value>true</value>\n\t\t</property>\n'

            bank_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<objects serializationModel="{self.serialization_model}">
\t<object class="Bank" id="{bguid}">
\t\t<property name="name">
\t\t\t<value>{bname}</value>
\t\t</property>
{is_master_prop}\t\t<relationship name="folder">
\t\t\t<destination>{master_bank_folder_guid}</destination>
\t\t</relationship>
\t</object>
</objects>
"""
            with open(meta_path / "Bank" / f"{bguid}.xml", "w", encoding="utf-8") as f:
                f.write(bank_xml)

        # 8. VCAs
        for vpath, vguid in self.vcas.items():
            vname = vpath.replace("vca:/", "").strip()
            vca_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<objects serializationModel="{self.serialization_model}">
\t<object class="MixerVCA" id="{vguid}">
\t\t<property name="name">
\t\t\t<value>{vname}</value>
\t\t</property>
\t\t<relationship name="mixer">
\t\t\t<destination>{mixer_guid}</destination>
\t\t</relationship>
\t</object>
</objects>
"""
            with open(meta_path / "VCA" / f"{vguid}.xml", "w", encoding="utf-8") as f:
                f.write(vca_xml)

        # 9. Buses (MixerGroup)
        for bpath, bguid in self.buses.items():
            if bguid == master_bus_guid or bpath.lower() in ("bus:/", "bus:/master", "bus:/master bus"):
                continue
            bname = bpath.split("/")[-1].strip()
            parent_path = bpath.rpartition("/")[0]
            if parent_path in ("bus:", "bus:/") or parent_path not in self.buses:
                parent_bus_guid = master_bus_guid
            else:
                parent_bus_guid = self.buses[parent_path]

            # Link to VCA if bus matches any VCA name
            masters_block = ""
            for vpath, vguid in self.vcas.items():
                vname = vpath.replace("vca:/", "").strip().lower()
                bname_tokens = [t.lower() for t in bname.split("_")]
                if vname in bname_tokens or vname in bpath.lower().split("/"):
                    masters_block = f"""\t\t<relationship name="masters">
\t\t\t<destination>{vguid}</destination>
\t\t</relationship>
"""
                    break

            chain_guid = new_guid()
            panner_guid = new_guid()
            fader_guid = new_guid()
            group_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<objects serializationModel="{self.serialization_model}">
\t<object class="MixerGroup" id="{bguid}">
\t\t<property name="name">
\t\t\t<value>{bname}</value>
\t\t</property>
{masters_block}\t\t<relationship name="effectChain">
\t\t\t<destination>{chain_guid}</destination>
\t\t</relationship>
\t\t<relationship name="panner">
\t\t\t<destination>{panner_guid}</destination>
\t\t</relationship>
\t\t<relationship name="output">
\t\t\t<destination>{parent_bus_guid}</destination>
\t\t</relationship>
\t</object>
\t<object class="MixerBusEffectChain" id="{chain_guid}">
\t\t<relationship name="effects">
\t\t\t<destination>{fader_guid}</destination>
\t\t</relationship>
\t</object>
\t<object class="MixerBusPanner" id="{panner_guid}" />
\t<object class="MixerBusFader" id="{fader_guid}" />
</objects>
"""
            with open(meta_path / "Group" / f"{bguid}.xml", "w", encoding="utf-8") as f:
                f.write(group_xml)

        # 10. SnapshotGroup & Snapshots (with auto-bank association)
        snapshot_items_xml = ""
        if self.snapshots:
            items_dests = "\n".join([f'\t\t\t<destination>{sguid}</destination>' for sguid in self.snapshots.values()])
            snapshot_items_xml = f'\t\t<relationship name="items">\n{items_dests}\n\t\t</relationship>\n'

        with open(meta_path / "SnapshotGroup" / f"{snapshot_group_guid}.xml", "w", encoding="utf-8") as f:
            f.write(f"""<?xml version="1.0" encoding="UTF-8"?>
<objects serializationModel="{self.serialization_model}">
\t<object class="SnapshotList" id="{snapshot_group_guid}">
\t\t<relationship name="mixer">
\t\t\t<destination>{mixer_guid}</destination>
\t\t</relationship>
{snapshot_items_xml}\t</object>
</objects>
""")

        # Generate individual Snapshots
        for spath, sguid in self.snapshots.items():
            sname = spath.replace("snapshot:/", "").strip()
            strack_guid = new_guid()
            auto_prop_guid = new_guid()
            marker_guid = new_guid()
            snap_timeline_guid = new_guid()
            snap_bank_guid = self._resolve_target_bank(spath, bank_map, default_bank_guid=master_bank_guid, project_name=project_name)
            snap_bank_block = ""
            if snap_bank_guid:
                snap_bank_block = f"""\t\t<relationship name="banks">
\t\t\t<destination>{snap_bank_guid}</destination>
\t\t</relationship>
"""
            snap_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<objects serializationModel="{self.serialization_model}">
\t<object class="Snapshot" id="{sguid}">
\t\t<property name="name">
\t\t\t<value>{sname}</value>
\t\t</property>
\t\t<relationship name="mixer">
\t\t\t<destination>{mixer_guid}</destination>
\t\t</relationship>
\t\t<relationship name="automatableProperties">
\t\t\t<destination>{auto_prop_guid}</destination>
\t\t</relationship>
\t\t<relationship name="markerTracks">
\t\t\t<destination>{marker_guid}</destination>
\t\t</relationship>
\t\t<relationship name="timeline">
\t\t\t<destination>{snap_timeline_guid}</destination>
\t\t</relationship>
{snap_bank_block}\t\t<relationship name="snapshotMasterTrack">
\t\t\t<destination>{strack_guid}</destination>
\t\t</relationship>
\t</object>
\t<object class="EventAutomatableProperties" id="{auto_prop_guid}" />
\t<object class="MarkerTrack" id="{marker_guid}" />
\t<object class="Timeline" id="{snap_timeline_guid}" />
\t<object class="SnapshotMasterTrack" id="{strack_guid}" />
</objects>
"""
            with open(meta_path / "Snapshot" / f"{sguid}.xml", "w", encoding="utf-8") as f:
                f.write(snap_xml)

        # 11. Parameter Presets
        for ppath, pguid in self.parameters.items():
            pname = ppath.replace("parameter:/", "").strip()
            game_param_guid = new_guid()
            param_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<objects serializationModel="{self.serialization_model}">
\t<object class="ParameterPreset" id="{pguid}">
\t\t<property name="name">
\t\t\t<value>{pname}</value>
\t\t</property>
\t\t<relationship name="folder">
\t\t\t<destination>{master_param_preset_folder_guid}</destination>
\t\t</relationship>
\t\t<relationship name="parameter">
\t\t\t<destination>{game_param_guid}</destination>
\t\t</relationship>
\t</object>
\t<object class="GameParameter" id="{game_param_guid}">
\t\t<property name="initialValue">
\t\t\t<value>0</value>
\t\t</property>
\t</object>
</objects>
"""
            with open(meta_path / "ParameterPreset" / f"{pguid}.xml", "w", encoding="utf-8") as f:
                f.write(param_xml)

        # 9. ProfilerFolder
        with open(meta_path / "ProfilerFolder" / f"{profiler_session_folder_guid}.xml", "w", encoding="utf-8") as f:
            f.write(f'<?xml version="1.0" encoding="UTF-8"?>\n<objects serializationModel="{self.serialization_model}">\n\t<object class="ProfilerSessionFolder" id="{profiler_session_folder_guid}" />\n</objects>\n')

        # 10. Platform, EncodingSetting & User Build Settings
        encoding_guid = new_guid()
        with open(meta_path / "Platform" / f"{platform_guid}.xml", "w", encoding="utf-8") as f:
            f.write(f"""<?xml version="1.0" encoding="UTF-8"?>
<objects serializationModel="{self.serialization_model}">
\t<object class="Platform" id="{platform_guid}">
\t\t<property name="hardwareType">
\t\t\t<value>0</value>
\t\t</property>
\t\t<property name="name">
\t\t\t<value>Desktop</value>
\t\t</property>
\t\t<property name="subDirectory">
\t\t\t<value>Desktop</value>
\t\t</property>
\t\t<property name="speakerFormat">
\t\t\t<value>5</value>
\t\t</property>
\t</object>
</objects>
""")

        # EncodingSetting for Desktop platform
        with open(meta_path / "EncodingSetting" / f"{encoding_guid}.xml", "w", encoding="utf-8") as f:
            f.write(f"""<?xml version="1.0" encoding="UTF-8"?>
<objects serializationModel="{self.serialization_model}">
\t<object class="EncodingSetting" id="{encoding_guid}">
\t\t<property name="encodingFormat">
\t\t\t<value>3</value>
\t\t</property>
\t\t<property name="quality">
\t\t\t<value>37</value>
\t\t</property>
\t\t<relationship name="platform">
\t\t\t<destination>{platform_guid}</destination>
\t\t</relationship>
\t\t<relationship name="encodable">
\t\t\t<destination>{platform_guid}</destination>
\t\t</relationship>
\t</object>
</objects>
""")

        # User Workspace & ProjectSettings (.user directory for active platform selection)
        user_meta = out_path / ".user" / "Metadata"
        user_meta.mkdir(parents=True, exist_ok=True)
        project_settings_guid = new_guid()
        with open(user_meta / "Workspace.user.xml", "w", encoding="utf-8") as f:
            f.write(f"""<?xml version="1.0" encoding="UTF-8"?>
<objects serializationModel="{self.serialization_model}">
\t<object class="Workspace" id="{workspace_guid}">
\t\t<relationship name="projectSettings">
\t\t\t<destination>{project_settings_guid}</destination>
\t\t</relationship>
\t</object>
\t<object class="ProjectSettings" id="{project_settings_guid}">
\t\t<relationship name="activePlatform">
\t\t\t<destination>{platform_guid}</destination>
\t\t</relationship>
\t</object>
</objects>
""")
        with open(user_meta / "Mixer.user.xml", "w", encoding="utf-8") as f:
            f.write(f"""<?xml version="1.0" encoding="UTF-8"?>
<objects serializationModel="{self.serialization_model}">
\t<object class="Mixer" id="{mixer_guid}" />
</objects>
""")

        # 8. Event Hierarchy & Folders
        # Root Event Folder
        with open(meta_path / "EventFolder" / f"{master_event_folder_guid}.xml", "w", encoding="utf-8") as f:
            f.write(f"""<?xml version="1.0" encoding="UTF-8"?>
<objects serializationModel="{self.serialization_model}">
\t<object class="MasterEventFolder" id="{master_event_folder_guid}">
\t\t<property name="name">
\t\t\t<value>Master</value>
\t\t</property>
\t</object>
</objects>
""")

        # Build folder paths
        # folder_path_str -> folder_guid
        folder_guids: Dict[str, str] = {}
        
        # Sort events by path for deterministic tree building
        sorted_events = sorted(self.events.items(), key=lambda x: x[0].lower())

        for epath, eguid in sorted_events:
            # epath example: 'event:/HOST/ru/Accessibility/A'
            rel = epath.replace("event:/", "").strip("/")
            parts = rel.split("/")
            event_name = parts[-1]
            folder_parts = parts[:-1]

            # Ensure all parent folders exist
            current_parent_guid = master_event_folder_guid
            accumulated_folder = ""
            for folder_name in folder_parts:
                accumulated_folder = f"{accumulated_folder}/{folder_name}" if accumulated_folder else folder_name
                if accumulated_folder not in folder_guids:
                    f_guid = new_guid()
                    folder_guids[accumulated_folder] = f_guid
                    
                    folder_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<objects serializationModel="{self.serialization_model}">
\t<object class="EventFolder" id="{f_guid}">
\t\t<property name="name">
\t\t\t<value>{folder_name}</value>
\t\t</property>
\t\t<relationship name="folder">
\t\t\t<destination>{current_parent_guid}</destination>
\t\t</relationship>
\t</object>
</objects>
"""
                    with open(meta_path / "EventFolder" / f"{f_guid}.xml", "w", encoding="utf-8") as f:
                        f.write(folder_xml)
                current_parent_guid = folder_guids[accumulated_folder]

            # 9. Create Event XML
            event_mixer_guid = new_guid()
            master_track_guid = new_guid()
            mixer_input_guid = new_guid()
            automatable_props_guid = new_guid()
            marker_track_guid = new_guid()
            group_track_guid = new_guid()
            timeline_guid = new_guid()
            event_master_bus_guid = new_guid()
            input_chain_guid = new_guid()
            input_panner_guid = new_guid()
            master_chain_guid = new_guid()
            master_panner_guid = new_guid()
            group_guid = new_guid()
            group_chain_guid = new_guid()
            group_panner_guid = new_guid()
            fader1 = new_guid()
            fader2 = new_guid()
            fader3 = new_guid()

            # Auto bank association
            target_bank = None
            if default_bank_name:
                for bpath, bguid in bank_map.items():
                    if default_bank_name.lower() in bpath.lower():
                        target_bank = bguid
                        break
            if not target_bank:
                target_bank = self._resolve_target_bank(epath, bank_map, default_bank_guid=master_bank_guid, project_name=project_name)

            bank_block = ""
            if target_bank:
                bank_block = f"""\t\t<relationship name="banks">
\t\t\t<destination>{target_bank}</destination>
\t\t</relationship>
"""

            # Route event to matching mixer group bus
            target_bus_guid = self._resolve_target_bus(epath, master_bus_guid)

            event_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<objects serializationModel="{self.serialization_model}">
\t<object class="Event" id="{eguid}">
\t\t<property name="name">
\t\t\t<value>{event_name}</value>
\t\t</property>
\t\t<property name="outputFormat">
\t\t\t<value>2</value>
\t\t</property>
\t\t<relationship name="folder">
\t\t\t<destination>{current_parent_guid}</destination>
\t\t</relationship>
\t\t<relationship name="mixer">
\t\t\t<destination>{event_mixer_guid}</destination>
\t\t</relationship>
\t\t<relationship name="masterTrack">
\t\t\t<destination>{master_track_guid}</destination>
\t\t</relationship>
\t\t<relationship name="mixerInput">
\t\t\t<destination>{mixer_input_guid}</destination>
\t\t</relationship>
\t\t<relationship name="automatableProperties">
\t\t\t<destination>{automatable_props_guid}</destination>
\t\t</relationship>
\t\t<relationship name="markerTracks">
\t\t\t<destination>{marker_track_guid}</destination>
\t\t</relationship>
\t\t<relationship name="groupTracks">
\t\t\t<destination>{group_track_guid}</destination>
\t\t</relationship>
\t\t<relationship name="timeline">
\t\t\t<destination>{timeline_guid}</destination>
\t\t</relationship>
{bank_block}\t</object>
\t<object class="EventMixer" id="{event_mixer_guid}">
\t\t<relationship name="masterBus">
\t\t\t<destination>{event_master_bus_guid}</destination>
\t\t</relationship>
\t</object>
\t<object class="MasterTrack" id="{master_track_guid}">
\t\t<relationship name="mixerGroup">
\t\t\t<destination>{event_master_bus_guid}</destination>
\t\t</relationship>
\t</object>
\t<object class="MixerInput" id="{mixer_input_guid}">
\t\t<relationship name="effectChain">
\t\t\t<destination>{input_chain_guid}</destination>
\t\t</relationship>
\t\t<relationship name="panner">
\t\t\t<destination>{input_panner_guid}</destination>
\t\t</relationship>
\t\t<relationship name="output">
\t\t\t<destination>{target_bus_guid}</destination>
\t\t</relationship>
\t</object>
\t<object class="EventAutomatableProperties" id="{automatable_props_guid}" />
\t<object class="MarkerTrack" id="{marker_track_guid}" />
\t<object class="GroupTrack" id="{group_track_guid}">
\t\t<relationship name="mixerGroup">
\t\t\t<destination>{group_guid}</destination>
\t\t</relationship>
\t</object>
\t<object class="Timeline" id="{timeline_guid}" />
\t<object class="EventMixerMaster" id="{event_master_bus_guid}">
\t\t<relationship name="effectChain">
\t\t\t<destination>{master_chain_guid}</destination>
\t\t</relationship>
\t\t<relationship name="panner">
\t\t\t<destination>{master_panner_guid}</destination>
\t\t</relationship>
\t\t<relationship name="mixer">
\t\t\t<destination>{event_mixer_guid}</destination>
\t\t</relationship>
\t</object>
\t<object class="MixerBusEffectChain" id="{input_chain_guid}">
\t\t<relationship name="effects">
\t\t\t<destination>{fader1}</destination>
\t\t</relationship>
\t</object>
\t<object class="MixerBusPanner" id="{input_panner_guid}" />
\t<object class="EventMixerGroup" id="{group_guid}">
\t\t<property name="name">
\t\t\t<value>Audio 1</value>
\t\t</property>
\t\t<relationship name="effectChain">
\t\t\t<destination>{group_chain_guid}</destination>
\t\t</relationship>
\t\t<relationship name="panner">
\t\t\t<destination>{group_panner_guid}</destination>
\t\t</relationship>
\t\t<relationship name="output">
\t\t\t<destination>{event_master_bus_guid}</destination>
\t\t</relationship>
\t</object>
\t<object class="MixerBusEffectChain" id="{master_chain_guid}">
\t\t<relationship name="effects">
\t\t\t<destination>{fader2}</destination>
\t\t</relationship>
\t</object>
\t<object class="MixerBusPanner" id="{master_panner_guid}" />
\t<object class="MixerBusFader" id="{fader1}" />
\t<object class="MixerBusEffectChain" id="{group_chain_guid}">
\t\t<relationship name="effects">
\t\t\t<destination>{fader3}</destination>
\t\t</relationship>
\t</object>
\t<object class="MixerBusPanner" id="{group_panner_guid}" />
\t<object class="MixerBusFader" id="{fader2}" />
\t<object class="MixerBusFader" id="{fader3}" />
</objects>
"""
            with open(meta_path / "Event" / f"{eguid}.xml", "w", encoding="utf-8") as f:
                f.write(event_xml)

        return {
            "project_file": str(fspro_file),
            "events_count": len(self.events),
            "folders_count": len(folder_guids) + 1,
            "banks_count": len(bank_map),
            "buses_count": len(self.buses),
            "vcas_count": len(self.vcas),
            "snapshots_count": len(self.snapshots),
            "parameters_count": len(self.parameters),
        }


def generate_skeleton_from_file(input_file: str, output_dir: str, project_name: Optional[str] = None, model: str = "Studio.02.03.00") -> Dict[str, Any]:
    gen = FmodProjectSkeletonGenerator(serialization_model=model)
    inp = Path(input_file)
    if not project_name:
        project_name = inp.stem.replace(".strings", "")

    if inp.suffix.lower() == ".bank":
        gen.load_from_strings_bank(str(inp))
    elif inp.suffix.lower() == ".json":
        gen.load_from_json(str(inp))
    else:
        gen.load_from_guids_txt(str(inp))

    return gen.generate_project(output_dir, project_name=project_name)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate FMOD Studio project skeleton (.fspro) from strings.bank, strings.json, or GUIDs.txt")
    parser.add_argument("input_file", help="Path to strings.bank, strings.json, or GUIDs.txt")
    parser.add_argument("-o", "--output", required=True, help="Destination directory for the .fspro and Metadata")
    parser.add_argument("-n", "--name", help="Project name (default: derived from input file)")
    parser.add_argument("-m", "--model", default="Studio.02.03.00", help="Serialization model version (default: Studio.02.03.00)")
    args = parser.parse_args()

    res = generate_skeleton_from_file(args.input_file, args.output, project_name=args.name, model=args.model)
    print(f"Success! Generated FMOD Studio project:")
    print(f"  Project File: {res['project_file']}")
    print(f"  Total Events: {res['events_count']}")
    print(f"  Total Folders: {res['folders_count']}")
    print(f"  Total Banks: {res['banks_count']}")
    print(f"  Total Buses: {res['buses_count']}")
    print(f"  Total VCAs: {res['vcas_count']}")
    print(f"  Total Snapshots: {res['snapshots_count']}")
    print(f"  Total Parameters: {res['parameters_count']}")
