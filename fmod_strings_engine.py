"""
FMOD Studio strings.bank Engine & Rebuilder
Author: Antigravity
Description:
    Pure-Python parser, Radix Trie generator, and binary compiler for FMOD Studio *.strings.bank files.
    Fully compatible with FMOD Studio 2.0x / 2.03+ runtimes.
    Supports 1-to-1 path/GUID mappings as well as N-to-1 aliased paths (multiple paths sharing the same GUID).
"""

import struct
import re
import collections
from typing import List, Dict, Tuple, Optional, Any, Set

GUID_REGEX = re.compile(
    r'^\{?([0-9a-fA-F]{8})-([0-9a-fA-F]{4})-([0-9a-fA-F]{4})-([0-9a-fA-F]{4})-([0-9a-fA-F]{12})\}?$'
)


def parse_guid_string(guid_str: str) -> Tuple[int, int, int, bytes]:
    """Parses a GUID string '{xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx}' into (Data1, Data2, Data3, Data4[8])."""
    m = GUID_REGEX.match(guid_str.strip())
    if not m:
        raise ValueError(f"Invalid GUID format: '{guid_str}'. Expected format: {{xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx}}")
    d1 = int(m.group(1), 16)
    d2 = int(m.group(2), 16)
    d3 = int(m.group(3), 16)
    d4_hex = m.group(4) + m.group(5)
    d4 = bytes.fromhex(d4_hex)
    return d1, d2, d3, d4


def guid_to_string(d1: int, d2: int, d3: int, d4: bytes) -> str:
    """Formats (Data1, Data2, Data3, Data4) into standard GUID format."""
    return f"{{{d1:08x}-{d2:04x}-{d3:04x}-{d4[:2].hex()}-{d4[2:].hex()}}}"


def guid_to_bytes(d1: int, d2: int, d3: int, d4: bytes) -> bytes:
    """Encodes GUID to 16 bytes in little-endian order for Data1-Data3 and raw bytes for Data4."""
    return struct.pack('<IHH', d1, d2, d3) + d4


def bytes_to_guid(raw: bytes) -> Tuple[int, int, int, bytes]:
    """Decodes 16 raw bytes into (Data1, Data2, Data3, Data4)."""
    if len(raw) != 16:
        raise ValueError(f"GUID bytes must be exactly 16 bytes, got {len(raw)}")
    d1, d2, d3 = struct.unpack('<IHH', raw[:8])
    d4 = raw[8:16]
    return d1, d2, d3, d4


def read_varint(buf: bytes, off: int) -> Tuple[int, int]:
    """Reads a 15-bit chunked varint from buffer. Returns (value, next_offset)."""
    if off + 2 > len(buf):
        raise ValueError(f"Buffer underflow while reading varint at offset {off:#x}")
    w = struct.unpack('<H', buf[off:off+2])[0]
    off += 2
    if w & 0x8000:
        if off + 2 > len(buf):
            raise ValueError(f"Buffer underflow while reading 2nd word of varint at offset {off:#x}")
        w2 = struct.unpack('<H', buf[off:off+2])[0]
        off += 2
        return (w & 0x7FFF) | (w2 << 15), off
    return w, off


def write_varint(val: int) -> bytes:
    """Encodes an integer into a 15-bit chunked varint."""
    if val < 0:
        raise ValueError("Varint values must be non-negative")
    if val < 0x8000:
        return struct.pack('<H', val)
    return struct.pack('<H', (val & 0x7FFF) | 0x8000) + struct.pack('<H', val >> 15)


class StringsEntry:
    def __init__(self, path: str, guid_str: str):
        self.path = path.strip()
        self.guid_str = guid_str.strip().lower()
        self.d1, self.d2, self.d3, self.d4 = parse_guid_string(self.guid_str)
        self.guid_tuple = (self.d1, self.d2, self.d3, self.d4)
        self.guid_bytes = guid_to_bytes(self.d1, self.d2, self.d3, self.d4)

    @property
    def item_type(self) -> str:
        if ':/' in self.path:
            return self.path.split(':/')[0]
        return 'other'

    def to_dict(self) -> Dict[str, str]:
        return {
            'type': self.item_type,
            'path': self.path,
            'guid': self.guid_str
        }

    def __repr__(self) -> str:
        return f"<StringsEntry type={self.item_type} path='{self.path}' guid='{self.guid_str}'>"


class StringsBankModel:
    def __init__(self, bank_guid: Optional[str] = None):
        self.bank_guid = bank_guid or "{00000000-0000-0000-0000-000000000000}"
        self.entries: List[StringsEntry] = []
        self._path_map: Dict[str, StringsEntry] = {}

    def add_entry(self, path: str, guid_str: str) -> StringsEntry:
        clean_path = path.strip()
        if clean_path.lower() in self._path_map:
            # Update existing path's GUID
            entry = self._path_map[clean_path.lower()]
            entry.path = clean_path
            entry.guid_str = guid_str.strip().lower()
            entry.d1, entry.d2, entry.d3, entry.d4 = parse_guid_string(entry.guid_str)
            entry.guid_tuple = (entry.d1, entry.d2, entry.d3, entry.d4)
            entry.guid_bytes = guid_to_bytes(entry.d1, entry.d2, entry.d3, entry.d4)
            return entry
        entry = StringsEntry(clean_path, guid_str)
        self.entries.append(entry)
        self._path_map[clean_path.lower()] = entry
        return entry

    def remove_entry(self, path: str) -> bool:
        clean_path = path.strip().lower()
        if clean_path in self._path_map:
            entry = self._path_map.pop(clean_path)
            self.entries.remove(entry)
            return True
        return False

    def get_entry(self, path: str) -> Optional[StringsEntry]:
        return self._path_map.get(path.strip().lower())

    def rename_entry(self, old_path: str, new_path: str) -> bool:
        old_clean = old_path.strip().lower()
        new_clean = new_path.strip()
        if old_clean not in self._path_map:
            return False
        entry = self._path_map.pop(old_clean)
        entry.path = new_clean
        self._path_map[new_clean.lower()] = entry
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            'bank_guid': self.bank_guid,
            'count': len(self.entries),
            'entries': [e.to_dict() for e in self.entries]
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'StringsBankModel':
        model = cls(bank_guid=data.get('bank_guid'))
        for item in data.get('entries', []):
            model.add_entry(item['path'], item['guid'])
        return model


class RadixTrieBuilderNode:
    def __init__(self, prefix: str = ''):
        self.prefix = prefix
        self.children: List['RadixTrieBuilderNode'] = []
        self.guid_index: Optional[int] = None

    def insert(self, path: str, guid_index: int):
        if not path:
            self.guid_index = guid_index
            return

        for child in self.children:
            common = 0
            mlen = min(len(path), len(child.prefix))
            while common < mlen and path[common].lower() == child.prefix[common].lower():
                common += 1

            if common > 0:
                if common == len(child.prefix):
                    child.insert(path[common:], guid_index)
                    return
                else:
                    split = RadixTrieBuilderNode(child.prefix[:common])
                    child.prefix = child.prefix[common:]
                    split.children.append(child)
                    if common == len(path):
                        split.guid_index = guid_index
                    else:
                        leaf = RadixTrieBuilderNode(path[common:])
                        leaf.guid_index = guid_index
                        split.children.append(leaf)
                    self.children.remove(child)
                    self.children.append(split)
                    return

        leaf = RadixTrieBuilderNode(path)
        leaf.guid_index = guid_index
        self.children.append(leaf)


def _find_riff_chunk(data: bytes, target_id: bytes, start: int, max_len: int) -> Tuple[int, int]:
    """Finds a RIFF chunk by ID within [start, start + max_len]. Returns (chunk_offset, chunk_size)."""
    end = start + max_len
    curr = start
    while curr + 8 <= end:
        cid = data[curr:curr+4]
        size = struct.unpack('<I', data[curr+4:curr+8])[0]
        if cid == target_id:
            return curr, size
        curr += 8 + size
        if size % 2 == 1:
            curr += 1
    return -1, -1


def parse_strings_bank(file_bytes: bytes) -> StringsBankModel:
    """
    Parses a *.strings.bank file using strict RIFF tree traversal and decodes the STDT chunk.
    Traverses all leaf nodes in the Radix Trie so that any alias paths sharing a GUID are fully recovered.
    """
    if len(file_bytes) < 32 or file_bytes[:4] != b'RIFF' or file_bytes[8:12] != b'FEV ':
        raise ValueError("Invalid FMOD Bank file: missing RIFF/FEV header")

    riff_size = struct.unpack('<I', file_bytes[4:8])[0]
    total_data_len = min(len(file_bytes), riff_size + 8)

    # 1. Locate LIST 'PROJ' chunk
    proj_off, proj_size = _find_riff_chunk(file_bytes, b'LIST', 12, total_data_len - 12)
    if proj_off == -1 or file_bytes[proj_off+8:proj_off+12] != b'PROJ':
        raise ValueError("Invalid FMOD Bank: LIST 'PROJ' chunk not found")

    proj_content_start = proj_off + 12
    proj_content_len = proj_size - 4

    # 2. Extract Bank GUID from BNKI chunk if present
    bnki_off, bnki_size = _find_riff_chunk(file_bytes, b'BNKI', proj_content_start, proj_content_len)
    bank_guid_str = None
    if bnki_off != -1 and bnki_size >= 16:
        raw_b_guid = file_bytes[bnki_off+8:bnki_off+24]
        bd1, bd2, bd3, bd4 = bytes_to_guid(raw_b_guid)
        bank_guid_str = guid_to_string(bd1, bd2, bd3, bd4)

    # 3. Locate STDT chunk
    stdt_off, stdt_size = _find_riff_chunk(file_bytes, b'STDT', proj_content_start, proj_content_len)
    if stdt_off == -1:
        raise ValueError("Invalid strings.bank: STDT chunk not found in PROJ list")

    stdt = file_bytes[stdt_off+8 : stdt_off+8+stdt_size]

    # Decode STDT
    off = 0
    version = struct.unpack('<I', stdt[off:off+4])[0]
    off += 4

    # Section 1: Trie nodes
    node_v, off = read_varint(stdt, off)
    node_count = node_v >> 1
    node_has_flag = node_v & 1
    if node_has_flag:
        rec_sz = struct.unpack('<H', stdt[off:off+2])[0]
        off += 2
    nodes = []
    for _ in range(node_count):
        w0 = struct.unpack('<I', stdt[off:off+4])[0]
        w1 = struct.unpack('<I', stdt[off+4:off+8])[0]
        s_off = w0 & 0xFFFFFF
        ch = (w0 >> 24) & 0xFF
        c_or_g = w1 & 0xFFFFFF
        c_cnt = (w1 >> 24) & 0xFF
        nodes.append((s_off, ch, c_or_g, c_cnt))
        off += 8

    # Section 2: Sorted GUID table (Unique GUIDs)
    guid_v, off = read_varint(stdt, off)
    guid_count = guid_v >> 1
    guid_has_flag = guid_v & 1
    if guid_has_flag:
        g_rec_sz = struct.unpack('<H', stdt[off:off+2])[0]
        off += 2
    guids = []
    for _ in range(guid_count):
        d1, d2, d3 = struct.unpack('<IHH', stdt[off:off+8])
        d4 = stdt[off+8:off+16]
        g_str = guid_to_string(d1, d2, d3, d4)
        guids.append(g_str)
        off += 16

    # Section 3: String pool
    str_pool_len, off = read_varint(stdt, off)
    str_pool = stdt[off:off+str_pool_len]
    off += str_pool_len

    # Section 4: GUID -> Leaf index table
    guid_map_cnt, off = read_varint(stdt, off)
    if guid_map_cnt != guid_count:
        raise ValueError(f"STDT corrupted: guid_map_cnt ({guid_map_cnt}) != guid_count ({guid_count})")
    guid_to_leaf = []
    for _ in range(guid_map_cnt):
        b = stdt[off:off+3]
        guid_to_leaf.append(b[0] | (b[1] << 8) | (b[2] << 16))
        off += 3

    # Section 5: Parent table
    parent_cnt, off = read_varint(stdt, off)
    if parent_cnt != node_count:
        raise ValueError(f"STDT corrupted: parent_cnt ({parent_cnt}) != node_count ({node_count})")
    parents = []
    for _ in range(parent_cnt):
        b = stdt[off:off+3]
        val = b[0] | (b[1] << 8) | (b[2] << 16)
        parents.append(val if val != 0xFFFFFF else -1)
        off += 3

    # Path reconstruction:
    # Instead of only walking up from guid_to_leaf (which only yields unique GUID primary paths),
    # we discover ALL paths by walking up from EVERY leaf node in the Trie!
    # This guarantees that aliased paths sharing the same GUID are 100% recovered.
    model = StringsBankModel(bank_guid=bank_guid_str)

    def reconstruct_path_from_node(leaf_idx: int) -> str:
        curr = leaf_idx
        segments = []
        visited = set()
        while curr != -1 and curr is not None:
            if curr in visited or curr >= len(nodes):
                break
            visited.add(curr)
            s_off = nodes[curr][0]
            if s_off != 0xFFFFFF and s_off < len(str_pool):
                end = str_pool.find(b'\x00', s_off)
                if end != -1:
                    seg = str_pool[s_off:end].decode('utf-8', errors='replace')
                    segments.append(seg)
                else:
                    raise ValueError(f"STDT corrupted: unterminated string at offset {s_off:#x}")
            curr = parents[curr]
        return "".join(reversed(segments))

    # Identify all leaf nodes in Trie
    for node_idx, (s_off, ch, c_or_g, c_cnt) in enumerate(nodes):
        if c_cnt == 0 and node_idx != 0: # Leaf node pointing to a GUID
            guid_idx = c_or_g
            if guid_idx < len(guids):
                path = reconstruct_path_from_node(node_idx)
                if path:
                    model.add_entry(path, guids[guid_idx])

    return model


def build_stdt_chunk(model: StringsBankModel) -> bytes:
    """
    Constructs a complete, valid STDT binary chunk from a StringsBankModel:
    1. Deduplicates GUIDs into a strictly sorted table of unique GUIDs.
    2. Constructs a Radix Trie supporting both unique events and aliases.
    3. Sorts children of every node (empty prefix terminal ch=0 first, then byte-sorted prefix).
    4. Flattens tree into TrieNode array.
    5. Generates String Pool.
    6. Builds GUID->Leaf table and Parent table.
    7. Encodes all tables with 15-bit varints and headers.
    """
    entries = model.entries
    if not entries:
        raise ValueError("Cannot build strings.bank: entries list is empty")

    # 1. Deduplicate & sort unique GUIDs according to FMOD struct comparison (Data1, Data2, Data3, Data4)
    unique_guid_tuples = sorted(list(set(e.guid_tuple for e in entries)))
    guid_to_idx = {g_tup: idx for idx, g_tup in enumerate(unique_guid_tuples)}
    guid_bytes_list = [guid_to_bytes(*g_tup) for g_tup in unique_guid_tuples]

    # 2. Build Radix Trie
    root = RadixTrieBuilderNode('')
    for entry in entries:
        root.insert(entry.path, guid_to_idx[entry.guid_tuple])

    # Insert terminal empty-string children for prefix collisions
    def fix_terminals(node: RadixTrieBuilderNode):
        if node.guid_index is not None and len(node.children) > 0:
            term = RadixTrieBuilderNode('')
            term.guid_index = node.guid_index
            node.guid_index = None
            node.children.insert(0, term)
        for c in node.children:
            fix_terminals(c)

    fix_terminals(root)

    # 3. Sort children deterministically:
    # Terminal node (prefix == '') first, then lexicographically by raw UTF-8 bytes (matching FMOD Studio builder)
    def sort_children(node: RadixTrieBuilderNode):
        node.children.sort(key=lambda c: (c.prefix != '', c.prefix.encode('utf-8')))
        for c in node.children:
            sort_children(c)

    sort_children(root)

    # 4. Flatten tree using BFS
    flat_nodes = []
    guid_to_leaf = [None] * len(unique_guid_tuples)
    root_info = {
        'node': root,
        'parent': 0xFFFFFF,
        'child_start': 0,
        'child_cnt': len(root.children)
    }
    flat_nodes.append(root_info)
    queue = collections.deque([0])

    while queue:
        curr_idx = queue.popleft()
        curr_info = flat_nodes[curr_idx]
        c_list = curr_info['node'].children
        if c_list:
            start_idx = len(flat_nodes)
            curr_info['child_start'] = start_idx
            curr_info['child_cnt'] = len(c_list)
            for c in c_list:
                c_idx = len(flat_nodes)
                c_info = {
                    'node': c,
                    'parent': curr_idx,
                    'child_start': 0,
                    'child_cnt': len(c.children)
                }
                flat_nodes.append(c_info)
                if c.guid_index is not None and not c.children:
                    # Set primary leaf for this GUID if not set yet
                    if guid_to_leaf[c.guid_index] is None:
                        guid_to_leaf[c.guid_index] = c_idx
                if c.children:
                    queue.append(c_idx)

    # Fallback for any unmapped GUID (should not occur)
    for i in range(len(guid_to_leaf)):
        if guid_to_leaf[i] is None:
            guid_to_leaf[i] = 0

    # 5. String pool and node records
    str_pool = bytearray()
    node_records = []
    for info in flat_nodes:
        n = info['node']
        if n.prefix:
            s_off = len(str_pool)
            str_pool.extend(n.prefix.encode('utf-8') + b'\x00')
            ch = ord(n.prefix[0].lower())
        else:
            s_off = 0xFFFFFF
            ch = 0

        if info['child_cnt'] > 0:
            child_or_guid = info['child_start']
            child_cnt = info['child_cnt']
        else:
            child_or_guid = n.guid_index if n.guid_index is not None else 0
            child_cnt = 0

        w0 = (s_off & 0xFFFFFF) | (ch << 24)
        w1 = (child_or_guid & 0xFFFFFF) | (child_cnt << 24)
        node_records.append((w0, w1))

    parents_list = [info['parent'] for info in flat_nodes]

    # 6. Pack STDT
    stdt = bytearray()
    stdt.extend(struct.pack('<I', 1)) # Version 1

    # Section 1: Nodes
    stdt.extend(write_varint((len(node_records) << 1) | 1))
    stdt.extend(struct.pack('<H', 8))
    for w0, w1 in node_records:
        stdt.extend(struct.pack('<II', w0, w1))

    # Section 2: GUIDs (strictly unique)
    stdt.extend(write_varint((len(guid_bytes_list) << 1) | 1))
    stdt.extend(struct.pack('<H', 16))
    for gb in guid_bytes_list:
        stdt.extend(gb)

    # Section 3: String pool
    stdt.extend(write_varint(len(str_pool)))
    stdt.extend(str_pool)

    # Section 4: GUID -> Leaf
    stdt.extend(write_varint(len(guid_to_leaf)))
    for leaf in guid_to_leaf:
        stdt.extend(struct.pack('<I', leaf)[:3])

    # Section 5: Parents
    stdt.extend(write_varint(len(parents_list)))
    for p in parents_list:
        stdt.extend(struct.pack('<I', p)[:3])

    return bytes(stdt)


def build_strings_bank(model: StringsBankModel, template_bytes: Optional[bytes] = None) -> bytes:
    """
    Compiles a StringsBankModel into a complete *.strings.bank file.
    If template_bytes is provided, preserves existing FMT, BNKI, and empty project subchunks.
    If template_bytes is None, builds an FMOD 2.0x RIFF container completely from scratch.
    """
    stdt_data = build_stdt_chunk(model)
    stdt_chunk = bytearray(b'STDT' + struct.pack('<I', len(stdt_data)) + stdt_data)
    if len(stdt_data) % 2 == 1:
        stdt_chunk.append(0) # 2-byte word padding

    if template_bytes and len(template_bytes) > 64 and template_bytes[:4] == b'RIFF':
        # Patching inside template container
        riff_total = len(template_bytes)
        proj_off, proj_size = _find_riff_chunk(template_bytes, b'LIST', 12, riff_total - 12)
        stdt_off, stdt_size = _find_riff_chunk(template_bytes, b'STDT', proj_off + 12, proj_size - 4)
        if stdt_off != -1:
            old_stdt_total = 8 + stdt_size + (1 if stdt_size % 2 == 1 else 0)
            new_bank = (
                bytearray(template_bytes[:stdt_off]) +
                stdt_chunk +
                bytearray(template_bytes[stdt_off + old_stdt_total:])
            )
            # Update PROJ LIST size
            proj_sz_pos = proj_off + 4
            new_proj_sz = proj_size - old_stdt_total + len(stdt_chunk)
            new_bank[proj_sz_pos : proj_sz_pos + 4] = struct.pack('<I', new_proj_sz)
            # Update RIFF size
            new_bank[4:8] = struct.pack('<I', len(new_bank) - 8)
            return bytes(new_bank)

    # Build from scratch
    # 1. FMT chunk (FMOD 2.03 version 0x8e, 0x8c)
    fmt_chunk = b'FMT ' + struct.pack('<III', 8, 0x0000008E, 0x0000008C)

    # 2. PROJ LIST subchunks
    proj_body = bytearray()

    # BNKI (Bank Info)
    d1, d2, d3, d4 = parse_guid_string(model.bank_guid)
    bank_guid_bytes = guid_to_bytes(d1, d2, d3, d4)
    bnki_data = bank_guid_bytes + (b'\x00' * 12) + struct.pack('<I', 7) # Type 7 = strings bank
    proj_body += b'BNKI' + struct.pack('<I', len(bnki_data)) + bnki_data

    # Empty project lists (required by FMOD Studio bank deserializer sequence)
    empty_subchunks = [
        b'IBSS', b'GBSS', b'RBSS', b'PRTS', b'MBSS', b'BEFX', b'PEFX', b'SEFX',
        b'SCFX', b'VCAS', b'EVTS', b'TLNS', b'PMLS', b'PRMS', b'CTRS', b'CRVS',
        b'MPGS', b'MUIS', b'SPIS', b'PRIS', b'EVIS', b'WAIS', b'EFIS', b'CMDS',
        b'SLNS', b'LWVS', b'WAVS', b'SNAS', b'MODS'
    ]
    for tag in empty_subchunks:
        proj_body += b'LIST' + struct.pack('<I', 4) + tag

    proj_body += b'SNDH' + struct.pack('<I', 0)
    proj_body += stdt_chunk
    proj_body += b'STBL' + struct.pack('<I', 0)
    proj_body += b'HASH' + struct.pack('<IH', 2, 1) # Empty hash table

    for tag in [b'DEL ', b'MUTE', b'REFI', b'PLAT']:
        proj_body += tag + struct.pack('<I', 0)

    # PROJ LIST chunk
    proj_list_chunk = b'LIST' + struct.pack('<I', len(proj_body) + 4) + b'PROJ' + proj_body
    if len(proj_list_chunk) % 2 == 1:
        proj_list_chunk += b'\x00'

    # Full RIFF file
    riff_body = fmt_chunk + proj_list_chunk
    total_riff = b'RIFF' + struct.pack('<I', len(riff_body) + 4) + b'FEV ' + riff_body
    return total_riff
