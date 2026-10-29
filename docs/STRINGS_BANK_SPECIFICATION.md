# FMOD Studio `strings.bank` Format Specification

## 1. Overview and Purpose

In FMOD Studio 2.x, `*.strings.bank` files (e.g., `Master.strings.bank`, `Host.strings.bank`) act as the primary lookup dictionary for the audio runtime.

### Architectural Role
At runtime, game engines and scripts reference audio assets using human-readable URI paths:
```cpp
Studio::System::getEvent("event:/SFX/Explosion", &eventDesc);
Studio::System::setParameterByName("parameter:/WindSpeed", 1.0f);
```

Compiled audio banks (`.bank` files such as `SFX.bank` or `Music.bank`) do not store string identifiers. All events, mixer buses, parameters, and snapshots are indexed internally by 128-bit GUIDs (`FMOD_GUID`):
```cpp
struct FMOD_GUID {
    uint32_t Data1;
    uint16_t Data2;
    uint16_t Data3;
    uint8_t  Data4[8];
};
```

`strings.bank` bridges these two domains:
1. **Forward Lookup (`Path -> GUID`)**: Calling `getEvent("path")` traverses a compressed prefix tree (Radix Trie) inside `strings.bank` to locate the corresponding `FMOD_GUID` in $O(L)$ time (where $L$ is path length), which is then resolved across loaded banks.
2. **Reverse Lookup (`GUID -> Path`)**: Calling `lookupPath` performs a binary search over a sorted GUID array, indexes the leaf node, and walks up to the root via a parent pointer table, assembling the path from a string pool.

### Supported Path Schemes
- `event:/...` — Sound events
- `bus:/...` — Mixer buses (Master Bus, Group Buses)
- `vca:/...` — Voltage-Controlled Amplifiers (VCAs)
- `parameter:/...` — Global and local game parameters
- `snapshot:/...` — Mixer snapshots
- `bank:/...` — Bank identifiers

---

## 2. Top-Level Container (RIFF / FEV)

A `strings.bank` file is encapsulated within a standard RIFF container using the FMOD-specific FourCC identifier `FEV ` (FMOD Event / Bank):

```
+-------------------------------------------------------------+
| RIFF Header (12 bytes)                                      |
| 'RIFF' [uint32 TotalSize] 'FEV '                            |
+-------------------------------------------------------------+
| FMT Chunk (16 bytes)                                        |
| 'FMT ' [uint32 Size=8] [uint32 formatVersion] [uint32 minVersion] |
+-------------------------------------------------------------+
| LIST Chunk: Type 'PROJ'                                     |
| 'LIST' [uint32 ListSize] 'PROJ'                             |
|   ├── 'BNKI' (32 bytes): Bank Information (Bank GUID, flags)|
|   ├── Empty LIST chunks (12 bytes each, size 0):            |
|   │     IBSS, GBSS, RBSS, PRTS, MBSS, BEFX, PEFX, SEFX,    |
|   │     SCFX, VCAS, EVTS, TLNS, PMLS, PRMS, CTRS, CRVS,     |
|   │     MPGS, MUIS, SPIS, PRIS, EVIS, WAIS, EFIS, CMDS,     |
|   │     SLNS, LWVS, WAVS, SNAS, MODS                        |
|   ├── 'SNDH' (8 bytes, size 0): Sample Headers              |
|   ├── 'STDT' (String Table Data) <--- CORE PAYLOAD          |
|   ├── 'STBL' (8 bytes, size 0)                              |
|   ├── 'HASH' (GUID Hash Table)                              |
|   └── 'DEL ', 'MUTE', 'REFI', 'PLAT' (Metadata chunks)      |
+-------------------------------------------------------------+
```

---

## 3. Internal Architecture of the `STDT` Chunk

The `STDT` chunk contains the prefix tree and index tables. It consists of six contiguous sections:

```
[STDT Chunk]
  ├── 1. Version (4 bytes, uint32 = 1)
  ├── 2. Radix Trie Node Table
  ├── 3. Sorted GUID Array
  ├── 4. String Pool
  ├── 5. GUID -> Leaf Node Mapping Table
  └── 6. Parent Node Table
```

### 3.1. Variable-Length Integer Encoding (15-bit Varint)

Element counts across all sections are encoded using 16-bit packed words:

```c
uint32_t read_varint(const uint8_t** stream) {
    uint16_t w = *(const uint16_t*)(*stream);
    *stream += 2;
    if (w & 0x8000) { // If bit 15 is set
        uint16_t w2 = *(const uint16_t*)(*stream);
        *stream += 2;
        return (w & 0x7FFF) | ((uint32_t)w2 << 15);
    }
    return w;
}
```

> [!NOTE]
> For record counters of structured arrays, the stored value is shifted left by 1:
> `stored_value = (item_count << 1) | has_extra_flag`.
> To extract the actual item count: `count = stored_value >> 1`.

---

### 3.2. Section 1: Header
- `uint32_t version`: Format version of the string table (always `0x00000001`).

---

### 3.3. Section 2: Radix Trie Node Table

Defines edges and transitions of the prefix tree for all registered paths.

- **Node Count**: `read_varint() >> 1`
- **Record Size**: `uint16_t record_size` (always `8` bytes)
- **Payload**: `node_count * 8` bytes

#### Node Structure (`TrieNode`, 8 bytes):
```
Bytes 0..2  (24 bits): string_offset (Offset into String Pool; 0xFFFFFF for root)
Byte  3     (8 bits) : char_prefix   (First character of path segment in lowercase, e.g. 'e', 'b')
Bytes 4..6  (24 bits): child_or_guid_index:
                       - If child_count > 0: index of first child node in this table.
                       - If child_count == 0: index of GUID in the Sorted GUID Array.
Byte  7     (8 bits) : child_count   (Number of child edges; 0 indicates a leaf node).
```

C definition:
```cpp
#pragma pack(push, 1)
struct TrieNode {
    uint32_t str_offset : 24;     // Offset in String Pool
    uint8_t  char_prefix;         // First character of the branch token
    uint32_t child_or_guid : 24;  // child_start_index OR guid_index
    uint8_t  child_count;         // Number of contiguous child nodes (0 = leaf)
};
#pragma pack(pop)
```

---

### 3.4. Section 3: Sorted GUID Array

Stores all unique `FMOD_GUID` records in the bank, sorted numerically by `(Data1, Data2, Data3, Data4)` according to the standard `FMOD_GUID::operator<` comparison:

```cpp
bool operator<(const FMOD_GUID &a, const FMOD_GUID &b) {
    if (a.Data1 != b.Data1) return a.Data1 < b.Data1;
    if (a.Data2 != b.Data2) return a.Data2 < b.Data2;
    if (a.Data3 != b.Data3) return a.Data3 < b.Data3;
    return memcmp(a.Data4, b.Data4, 8) < 0;
}
```

> [!IMPORTANT]
> Because `Data1`, `Data2`, and `Data3` are stored in little-endian on disk, a raw 16-byte `memcmp` will compare the least significant byte of `Data1` first, which differs from the numeric field order. The FMOD runtime binary search (`sub_1800A6570`) relies strictly on numeric field ordering.

- **GUID Count**: `read_varint() >> 1`
- **Record Size**: `uint16_t record_size` (always `16` bytes = `0x0010`)
- **Payload**: `guid_count * 16` bytes

```cpp
struct FMOD_GUID {
    uint32_t Data1;
    uint16_t Data2;
    uint16_t Data3;
    uint8_t  Data4[8];
};
```

---

### 3.5. Section 4: String Pool

A contiguous buffer of null-terminated UTF-8 substrings.

- **Length in bytes**: `read_varint()`
- **Payload**: Raw UTF-8 string tokens separated by `\0`.
- Example sequence:
  `bank:/\0Host\0.strings\0Music\0Pause\0SFX\0event:/\0HOST/\0de/\0Accessibility/\0...`

---

### 3.6. Section 5: GUID -> Leaf Node Mapping Table

Provides a direct index from each sorted GUID to its corresponding leaf node in the prefix tree.

- **Record Count**: `read_varint()` (equals `guid_count`)
- **Element Size**: 3 bytes (24-bit unsigned integer, little-endian)
- **Payload**: `guid_count * 3` bytes

Each 24-bit integer points to the leaf row in `TrieNode` associated with `Sorted_GUIDs[i]`.

---

### 3.7. Section 6: Parent Node Table

Enables bottom-up path reconstruction from a leaf node to the root.

- **Record Count**: `read_varint()` (equals `node_count`)
- **Element Size**: 3 bytes (24-bit unsigned integer, little-endian)
- **Payload**: `node_count * 3` bytes
- Value semantics:
  - `0xFFFFFF`: Root node (node 0 has no parent).
  - `parent_index`: Index of parent node in `TrieNode`.

---

## 4. Runtime Traversal Algorithms

### 4.1. Forward Lookup: Path to GUID (`System::lookupID`)

1. Input: URI string (e.g., `"event:/SFX/Explosion"`).
2. Traversal begins at root node `TrieNode[0]`.
3. In function `sub_1800A63D0`:
   - Match the first character of the remaining input against `char_prefix` across child nodes (`child_or_guid .. child_or_guid + child_count - 1`).
   - On a prefix match, fetch the substring from the String Pool at `str_offset`.
   - Verify that the substring matches the input prefix.
   - Advance the input pointer by the substring length.
   - Continue traversing downwards.
4. When the input string is exhausted and a leaf is reached (`child_count == 0`), read `child_or_guid`.
5. Return `Sorted_GUIDs[child_or_guid]`.

### 4.2. Reverse Lookup: GUID to Path (`System::lookupPath`)

1. Function `sub_1800A6570` performs a binary search on the `Sorted GUID Array` for the target `FMOD_GUID`.
2. If not found, return `FMOD_ERR_EVENT_NOTFOUND`.
3. Given `guid_index`:
   - Fetch `leaf_node_index = guid_to_leaf[guid_index]`.
   - Initialize an empty string buffer.
   - Walk upwards:
     - Read `TrieNode[current_node]`.
     - Read string token at `str_offset` and prepend to the buffer.
     - Look up `parent = parent_table[current_node]`.
     - If `parent == 0xFFFFFF`, terminate walk.
     - Else `current_node = parent`.
4. Output: assembled path (e.g., `"event:/SFX/Explosion"`).

---

## 5. Standalone Extraction Script (Python)

The following self-contained script extracts all paths and GUIDs from any `strings.bank` file without external dependencies or native libraries:

```python
import struct

def parse_strings_bank(file_path):
    with open(file_path, 'rb') as f:
        data = f.read()

    # Locate STDT chunk via RIFF traversal (with fallback)
    stdt_pos = -1
    curr = 12
    while curr + 8 <= len(data):
        cid = data[curr:curr+4]
        sz = struct.unpack('<I', data[curr+4:curr+8])[0]
        if cid == b'LIST' and data[curr+8:curr+12] == b'PROJ':
            sub = curr + 12
            sub_end = curr + 8 + sz
            while sub + 8 <= sub_end:
                scid = data[sub:sub+4]
                ssz = struct.unpack('<I', data[sub+4:sub+8])[0]
                if scid == b'STDT':
                    stdt_pos = sub
                    break
                sub += 8 + ssz + (ssz % 2)
            break
        curr += 8 + sz + (sz % 2)

    if stdt_pos == -1:
        stdt_pos = data.find(b'STDT')
        if stdt_pos == -1:
            raise ValueError("STDT chunk not found in bank")

    chunk_size = struct.unpack('<I', data[stdt_pos+4:stdt_pos+8])[0]
    stdt = data[stdt_pos+8:stdt_pos+8+chunk_size]

    def read_varint(buf, off):
        w = struct.unpack('<H', buf[off:off+2])[0]
        off += 2
        if w & 0x8000:
            w2 = struct.unpack('<H', buf[off:off+2])[0]
            off += 2
            return (w & 0x7FFF) | (w2 << 15), off
        return w, off

    off = 4  # Skip uint32 version

    # 1. Radix Trie Nodes
    node_v, off = read_varint(stdt, off)
    node_count = node_v >> 1
    off += 2  # Skip record size uint16
    nodes = []
    for _ in range(node_count):
        val1 = struct.unpack('<I', stdt[off:off+4])[0]
        str_off = val1 & 0xFFFFFF
        ch = (val1 >> 24) & 0xFF
        val2 = struct.unpack('<I', stdt[off+4:off+8])[0]
        child_or_guid = val2 & 0xFFFFFF
        child_cnt = (val2 >> 24) & 0xFF
        nodes.append((str_off, ch, child_or_guid, child_cnt))
        off += 8

    # 2. Sorted GUID Table
    guid_v, off = read_varint(stdt, off)
    guid_count = guid_v >> 1
    off += 2  # Skip record size uint16
    guids = []
    for _ in range(guid_count):
        d1, d2, d3 = struct.unpack('<IHH', stdt[off:off+8])
        d4 = stdt[off+8:off+16]
        guid_str = f"{{{d1:08x}-{d2:04x}-{d3:04x}-{d4[:2].hex()}-{d4[2:].hex()}}}"
        guids.append(guid_str)
        off += 16

    # 3. String Pool
    str_pool_len, off = read_varint(stdt, off)
    str_pool = stdt[off:off+str_pool_len]
    off += str_pool_len

    # 4. GUID -> Leaf Mapping (used for primary reverse lookup)
    guid_map_cnt, off = read_varint(stdt, off)
    off += guid_map_cnt * 3

    # 5. Parent Table
    parent_cnt, off = read_varint(stdt, off)
    assert parent_cnt == node_count
    parents = []
    for _ in range(parent_cnt):
        b = stdt[off:off+3]
        val = b[0] | (b[1] << 8) | (b[2] << 16)
        parents.append(val if val != 0xFFFFFF else -1)
        off += 3

    # Assemble paths by traversing ALL leaf nodes in the Trie
    # (recovers 100% of paths, including aliases sharing a GUID)
    results = []
    for node_idx, (s_off, ch, c_or_g, c_cnt) in enumerate(nodes):
        if c_cnt == 0 and node_idx != 0:  # Leaf node pointing to a GUID
            guid_idx = c_or_g
            if guid_idx < len(guids):
                curr = node_idx
                segments = []
                visited = set()
                while curr != -1 and curr is not None:
                    if curr in visited or curr >= len(nodes):
                        break
                    visited.add(curr)
                    seg_off = nodes[curr][0]
                    if seg_off != 0xFFFFFF and seg_off < len(str_pool):
                        end = str_pool.find(b'\x00', seg_off)
                        if end != -1:
                            seg = str_pool[seg_off:end].decode('utf-8', errors='replace')
                            segments.append(seg)
                    curr = parents[curr]
                full_path = "".join(reversed(segments))
                results.append({"guid": guids[guid_idx], "path": full_path})

    return results
```

---

## 6. Modification and Recompilation

Based on the `STDT` specification, modifying or rebuilding `strings.bank` files requires:
1. Constructing a Radix Trie from sorted input paths.
2. Generating the String Pool with shared prefixes.
3. Sorting GUIDs lexicographically according to `(Data1, Data2, Data3, Data4)`.
4. Computing the 24-bit leaf-mapping and parent-pointer tables.
5. Updating container sizes and recalculating RIFF chunk headers.
