# FMOD Studio Binary Bank Format Specification (.bank, FMOD 2.0x / 2.03+)

## 1. Introduction and Architecture

In FMOD Studio 2.x, `*.bank` files (e.g. `Master.bank`, `Host.bank`, `SFX.bank`, `Music.bank`) are composite binary package containers. While `*.strings.bank` files store prefix trees (Radix Trie) mapping URI paths to 128-bit GUIDs, audio bank files store the compiled runtime project data:
1. The mixer bus hierarchy, signal routing graphs, and DSP plugin parameter states.
2. Game parameter definitions and Hermite spline automation curves.
3. Event timelines, transition markers, trigger logic, and condition bytecode.
4. Audio streams packaged inside an **FSB5** (FMOD Sound Bank v5) container using Vorbis (Codec ID 15) or FADPCM compression.

### 1.1. Reverse Engineering Methodology
This specification is derived from disassembly of the FMOD Studio runtime engine (`fmodstudioL.dll`, version 2.02.xx / 2.03.xx) and FMOD Core API (`fmodL.dll`), cross-verified against production bank binaries.

### 1.2. Binary Data Conventions

1. **Byte Order**: All scalar integers (`uint16_t`, `uint32_t`, `int32_t`), floating-point values (IEEE 754 float32), and GUID fields (`Data1`, `Data2`, `Data3`) are serialized in **Little-Endian** byte order.
2. **GUID Representation**:
   ```cpp
   #pragma pack(push, 1)
   struct FMOD_GUID_RAW {
       uint32_t Data1;                  // Low 32 bits (Little-Endian)
       uint16_t Data2;                  // Next 16 bits (Little-Endian)
       uint16_t Data3;                  // Next 16 bits (Little-Endian)
       uint8_t  Data4[8];               // 8 individual bytes (Big-Endian byte order)
   };
   #pragma pack(pop)
   static_assert(sizeof(FMOD_GUID_RAW) == 16, "FMOD_GUID_RAW must be exactly 16 bytes");
   ```
3. **RIFF Chunk 16-Bit Word Alignment**:
   Every RIFF chunk consists of an 8-byte header (`char fourcc[4]`, `uint32_t chunkSize`) followed by `chunkSize` bytes of payload. If `chunkSize` is odd, exactly **1 null byte** (`0x00`) is appended to align the stream to a 2-byte (16-bit word) boundary.
4. **Shifted Varint Decompression (`readShiftedVarint`)**:
   FMOD encodes collection item counts and array lengths as 15-bit shifted varints, decoded by `sub_180138650`:
   - **Bit 15 (`0x8000`)** of the initial 16-bit word serves as the continuation flag (0 = single 16-bit word, 1 = 32-bit multi-word extension).
   - **Bits 1..14** store the value ($N = \text{raw} \gg 1$).
   - **Bit 0** acts as a format/packing tag (e.g. `raw = (N << 1) | 1` for inline/direct records).
   ```cpp
   // [RVA: 0x18013867D in sub_180138650]
   uint32_t readShiftedVarint(RiffStream *stream) {
       uint16_t v;
       stream->read(&v, sizeof(uint16_t));
       uint32_t raw = v;
       if (v & 0x8000) {                 // Bit 15 = multi-word continuation flag
           uint16_t high;
           stream->read(&high, sizeof(uint16_t));
           raw = ((uint32_t)high << 15) | (v & 0x7FFF);
       }
       return raw >> 1;                 // High bits represent value; bit 0 is tag
   }
   ```

---

## 2. Top-Level Container (RIFF / FEV)

An FMOD bank is structured as an extended **RIFF** container using the FourCC identifier `FEV ` (FMOD Event / Bank):

```
+-------------------------------------------------------------------------+
| RIFF Header (12 bytes)                                                  |
| 'RIFF' [uint32 TotalSize] 'FEV '                                        |
+-------------------------------------------------------------------------+
| Format Chunk 'FMT ' (16 bytes)                                          |
| 'FMT ' [uint32 Size=8] [uint32 formatVersion=0x8E] [uint32 minVersion=0x8C] |
+-------------------------------------------------------------------------+
| Composite List 'LIST' of type 'PROJ'                                    |
| 'LIST' [uint32 ProjSize] 'PROJ'                                         |
|   ├── BNKI : Bank metadata header (Bank GUID, flags)                    |
|   ├── IBSS : Input bus subsystem                                        |
|   ├── GBSS : Group mixer buses                                          |
|   ├── RBSS : Return buses                                               |
|   ├── PRTS : Output port buses (if version >= 0x8C)                     |
|   ├── MBSS : Master mixer bus                                           |
|   ├── BEFX : Bus DSP effect chains                                      |
|   ├── PEFX : Plugin effects                                             |
|   ├── SEFX : Send effects                                               |
|   ├── SCFX : Sidechain effects                                          |
|   ├── SSFX : Spatializer effects (if version >= 0x91)                   |
|   ├── VCAS : VCA channels                                               |
|   ├── EVTS : Sound events                                               |
|   ├── TLNS : Event timelines                                            |
|   ├── PMLS : Parameter models                                           |
|   ├── PRMS : Game parameters                                            |
|   ├── CTRS : Transition cues and automation controllers                 |
|   ├── CRVS : Automation spline curves                                   |
|   ├── MPGS : Macro pages                                                |
|   ├── MUIS, SPIS, PRIS, EVIS, WAIS, EFIS, CMDS, SLNS (Sound modules)    |
|   ├── LWVS : Low-resolution waveforms (if version >= 0x51)              |
|   ├── WAVS : Waveform sample descriptors                                |
|   ├── SNAS : Mixer snapshots                                            |
|   ├── MODS : Parameter modulators                                       |
|   ├── SNDH : Sound stream header metadata                               |
|   ├── STDT, STBL : String table (empty for non-strings banks)           |
|   ├── HASH : O(1) GUID lookup hash table                                |
|   └── DEL , MUTE, REFI, PLAT (Metadata extension chunks)                |
+-------------------------------------------------------------------------+
| Raw Audio Chunk 'SND ' (immediately follows LIST PROJ)                  |
| 'SND ' [uint32 SndSize] [16-byte alignment padding] 'FSB5' ...          |
+-------------------------------------------------------------------------+
```

### 2.1. Chunk Hierarchy and Container Map

Within `LIST PROJ`, collections of entities are encapsulated in typed RIFF `LIST` sub-containers.

> [!NOTE]
> **Universal `LCNT` Rule**:
> - For any typed container `LIST <TYPE>` containing $N \ge 1$ elements, the first sub-chunk is strictly an atomic `LCNT` (12 bytes: 4-byte `'LCNT'`, 4-byte size `0x00000004`, 4-byte `uint32_t count = N`).
> - For an empty collection ($N = 0$), the container chunk payload is strictly **4 bytes** containing only the 4-byte list type FourCC (e.g. `'LIST' [size=4] 'SEFX'`), and contains **no** `LCNT` sub-chunk.

| Parent Container (`LIST`) | Child Chunk Tag | Enclosed Structure | Description & Section Reference |
|---|---|---|---|
| `LIST PROJ` | `BNKI` | `BankInfo_BNKI` | Bank identity GUID, build flags, checksum (32B) (§13.3) |
| `LIST WAVS` | `LCNT` + `WAV ` | `SampleDescriptor_WAV` | Waveform sample descriptors (30B per sample) (§4.2, §15.1) |
| `LIST GBSS` | `GBSB` + `BUS ` | `GroupBusHeader_GBSB` | Group mixer buses & routing parameters (§6.1) |
| `LIST IBSS` | `IBSB` + `BUS ` | `GroupBusHeader_GBSB` | Input stage mixer buses (§6.1) |
| `LIST MBSS` | `MBSB` + `BUS ` | `GroupBusHeader_GBSB` | Master mixer bus (§6.1) |
| `LIST RBSS` | `RBSB` + `BUS ` | `ReturnBus_RBSB` | Auxiliary return mixer buses (§6.3) |
| `LIST PRTS` | `PRTB` | `PortBus_PRTB` | Output port buses (if version $\ge 0x8C$) (§6.1) |
| `LIST VCAS` | `VCAB` | `VcaBlock_VCAB` | VCA channel descriptors with target bus array (§6.2) |
| `LIST BEFX` | `BEFF` (`BEFB`+`PMEF`) | `BusEffectHeader_BEFB` | Bus DSP effect instances & parameter state (§5.1) |
| `LIST PEFX` | `PEFF` | `PluginEffect_PEFF` | External VST/AU and custom plugin effects (§5.1) |
| `LIST SEFX` | `SEFF` (`SEFB`+`PRPS`) | `SendEffect_SEFB` | Auxiliary send effect taps (37B) (§6.4) |
| `LIST SCFX` | `SCFF` (`SCEF`) | `SidechainEffect_SCEF`| Sidechain modulator input routing taps (§6.4) |
| `LIST SSFX` | `SSFB` | `Spatializer_SSFB` | 3D object spatializer effect parameters (§5.1) |
| `LIST PRMS` | `PRMB` | `Parameter_PRMB` | Game parameters with inline ASCII name strings (§7.1) |
| `LIST PMLS` | `LCNT` + `PMLO` (`PMLB`+`CTRO`) | `ParameterModel_PMLB` | Parameter models & driven curve GUID lists (§7.3) |
| `LIST MPGS` | `MAP ` | `MacroPage_MAP` | Macro control mapping pages (§7.4) |
| `LIST CRVS` | `CURV` | `CurveHeader_CURV` | Cubic Hermite automation spline curves (§7.2) |
| `LIST CTRS` | `LCNT` + `CTRL` | `Controller_CTRL` | Automation controller property links (52B) (§10.1) |
| `LIST EVTS` | `EVTB` | `EventData_EVTB` | Sound events, 3D settings & track linkages (§8.1) |
| `LIST TLNS` | `TMLN` (`TLNB`+`TRNS`) | `Timeline_TLNB` | Event timelines, tracks & transition logic (§8.2) |
| `LIST WAIS` | `WAIT` (`WAIB`+`INST`+`PRPS`) | `SoundInstance_INST` | Single-sound audio instruments (§9.1, §9.2) |
| `LIST MUIS` | `MUIT` (`PLST`+`INST`+`PRPS`) | `MultiSoundPlaylist_PLST` | Multi-sound randomized / sequential playlists (§9.3) |
| `LIST SPIS` | `SPIT` (`SPIB`+`PLST`+`INST`+`PRPS`) | `Scatterer_SPIB` | Scatterer randomized sound instruments (48B) (§9.4) |
| `LIST EVIS` | `EVIT` (`EVIB`+`INST`+`PRPS`) | `EventInstrument_EVIB` | Referenced event instruments (38B) (§9.4) |
| `LIST CMDS` | `CMDI` (`CMDB`+`INST`+`PRPS`) | `CommandInstrument_CMDB` | Parameter & event command instruments (40B) (§9.4) |
| `LIST PRIS` | `PRIT` (`PRIB`+`INST`+`PRPS`) | `Programmer_PRIB` | Programmer sound callback instruments (18B) (§9.4) |
| `LIST EFIS` | `EFIT` (`EFIB`+`INST`+`PRPS`) | `EffectInstrument_EFIB` | Event-scoped DSP effect instruments (§9.4) |
| `LIST SLNS` | `SLNT` (`SLNB`+`INST`+`PRPS`) | `SilenceInstrument_SLNB` | Timed silence generator instruments (§9.4) |
| `LIST LWVS` | `LWVB` | `LowResWaveform_LWVB` | Low-resolution overview waveforms (§13.5) |
| `LIST SNAS` | `SNAP` (`SNAB`) | `Snapshot_SNAB` | Mixer snapshot parameter override blocks (§12) |
| `LIST MODS` | `MODU` (`MODB`) | `Modulator_MODB` | Parameter modulators (AHDSR, LFO, Randomizer) (§11) |
| `LIST PROJ` | `SNDH` | `SoundHeader_SNDH` | Fixed 12-byte audio stream placement header (§13.1) |
| `LIST PROJ` | `HASH` | `GuidHashTable_HASH` | O(1) GUID lookup index table (20B per entry) (§13.2) |
| `LIST PROJ` | `PLAT` | `PlatformInfo_PLAT` | Target compilation platform identifier GUID (16B) (§13.4) |
| `LIST PROJ` | `DEL ` | `DeletedItems_DEL` | Index of deleted entity GUIDs (tombstones) (§13.6) |
| `LIST PROJ` | `MUTE` | `MutedItems_MUTE` | Index of muted entity GUIDs (§13.6) |
| `LIST PROJ` | `REFI` | `ReferenceIndex_REFI` | Cross-bank external asset reference table (§13.6) |
| Top-level | `SND ` | FSB5 Container | Bit-packed audio streams (Vorbis / FADPCM) (§4.1, §14) |

---

## 3. Bank Deserialization Dispatcher (`sub_18012D6E0`)

The master deserialization routine for banks in `fmodstudioL.dll` is `sub_18012D6E0` (`BankReader::serializeBank`):

```cpp
// [RVA: 0x18012D6E0]
FMOD_RESULT BankReader_serializeBank(BankData *bank, RiffStream *stream) {
    RiffChunk chunkProj;
    
    // 1. Open composite list 'PROJ'
    FMOD_RESULT res = stream->openChunk(&chunkProj, 0x4A4F5250 /* 'PROJ' */, 1 /* isList */);
    if (res != FMOD_OK) return res;

    // 2. Chunk BNKI: Bank metadata
    RiffChunk chunkAtomic;
    res = stream->openChunk(&chunkAtomic, 0x494B4E42 /* 'BNKI' */, 0 /* atomic */);
    if (res != FMOD_OK) goto error;
    res = serializeBankInfo(stream, &bank->info);
    if (res != FMOD_OK) goto error;
    stream->closeChunk(&chunkAtomic);

    // 3. IBSS: Input buses
    res = serializeInputBuses(stream, &bank->inputBuses, 0x53534249 /* 'IBSS' */);
    if (res != FMOD_OK) goto error;

    // 4. GBSS: Group mixer buses
    res = serializeGroupBuses(stream, &bank->groupBuses, 0x53534247 /* 'GBSS' */);
    if (res != FMOD_OK) goto error;

    // 5. RBSS: Return buses
    res = serializeReturnBuses(stream, &bank->returnBuses, 0x53534252 /* 'RBSS' */);
    if (res != FMOD_OK) goto error;

    // 6. PRTS: Port buses (version >= 0x8C / 140)
    if (stream->version >= 0x8C) {
        res = serializePortBuses(stream, &bank->portBuses, 0x53545250 /* 'PRTS' */);
        if (res != FMOD_OK) goto error;
    }

    // 7. MBSS: Master bus
    res = serializeMasterBuses(stream, &bank->masterBuses, 0x5353424D /* 'MBSS' */);
    if (res != FMOD_OK) goto error;

    // 8. BEFX: Bus effect chains
    res = serializeBusEffects(stream, &bank->busEffects, 0x58464542 /* 'BEFX' */);
    if (res != FMOD_OK) goto error;

    // 9. PEFX: Plugin effects
    res = serializePluginEffects(stream, &bank->pluginEffects, 0x58464550 /* 'PEFX' */);
    if (res != FMOD_OK) goto error;

    // 10. SEFX: Send effects
    res = serializeSendEffects(stream, &bank->sendEffects, 0x58464553 /* 'SEFX' */);
    if (res != FMOD_OK) goto error;

    // 11. SCFX: Sidechain inputs
    res = serializeSidechainEffects(stream, &bank->sidechainEffects, 0x58464353 /* 'SCFX' */);
    if (res != FMOD_OK) goto error;

    // 12. SSFX: Spatializer effects (version >= 0x91 / 145)
    if (stream->version >= 0x91) {
        res = serializeSpatializerEffects(stream, &bank->spatializerEffects, 0x58465353 /* 'SSFX' */);
        if (res != FMOD_OK) goto error;
    }

    // 13. VCAS: VCA channels
    res = serializeVcaList(stream, &bank->vcas, 0x53414356 /* 'VCAS' */);
    if (res != FMOD_OK) goto error;

    // 14. EVTS: Sound events
    res = serializeEventList(stream, &bank->events, 0x53545645 /* 'EVTS' */);
    if (res != FMOD_OK) goto error;

    // 15. TLNS: Timelines
    res = serializeTimelineList(stream, &bank->timelines, 0x534E4C54 /* 'TLNS' */);
    if (res != FMOD_OK) goto error;

    // 16. PMLS: Parameter models
    res = serializeParameterModelList(stream, &bank->paramModels, 0x534C4D50 /* 'PMLS' */);
    if (res != FMOD_OK) goto error;

    // 17. PRMS: Game parameters
    res = serializeParameterList(stream, &bank->parameters, 0x534D5250 /* 'PRMS' */);
    if (res != FMOD_OK) goto error;

    // 18. CTRS: Automation controllers
    res = serializeControllerList(stream, &bank->controllers, 0x53525443 /* 'CTRS' */);
    if (res != FMOD_OK) goto error;

    // 19. CRVS: Spline curves
    res = serializeCurveList(stream, &bank->curves, 0x53565243 /* 'CRVS' */);
    if (res != FMOD_OK) goto error;

    // 20. MPGS: Macro pages
    res = serializeMacroPageList(stream, &bank->macroPages, 0x5347504D /* 'MPGS' */);
    if (res != FMOD_OK) goto error;

    // 21. Sound modules (MUIS, SPIS, PRIS, EVIS, WAIS, EFIS, CMDS, SLNS)
    res = serializeSoundModules(stream, &bank->soundModules);
    if (res != FMOD_OK) goto error;

    // 22. SUBS: Legacy subsounds (if version < 0x3C)
    if (stream->version < 0x3C) {
        res = serializeSubsounds(stream, &bank->subsounds, 0x53425553 /* 'SUBS' */);
        if (res != FMOD_OK) goto error;
    }

    // 23. LWVS: Low-res waveforms (if version >= 0x51)
    if (stream->version >= 0x51) {
        res = serializeLowResWaveforms(stream, &bank->lowResWaveforms, 0x5356574C /* 'LWVS' */);
        if (res != FMOD_OK) goto error;
    }

    // 24. WAVS: Waveform descriptors
    res = serializeWaveforms(stream, &bank->waveforms, 0x53564157 /* 'WAVS' */);
    if (res != FMOD_OK) goto error;

    // 25. SNAS: Mixer snapshots
    res = serializeSnapshots(stream, &bank->snapshots, 0x53414E53 /* 'SNAS' */);
    if (res != FMOD_OK) goto error;

    // 26. MODS: Parameter modulators
    res = serializeModulators(stream, &bank->modulators, 0x53444F4D /* 'MODS' */);
    if (res != FMOD_OK) goto error;

    // 27. SNDH: Sound stream headers
    res = stream->openChunk(&chunkAtomic, 0x48444E53 /* 'SNDH' */, 0 /* atomic */);
    if (res != FMOD_OK) goto error;
    res = serializeSoundHeaders(stream, &bank->soundHeaders);
    if (res != FMOD_OK) goto error;
    stream->closeChunk(&chunkAtomic);

    // 28. String tables (empty in audio banks)
    res = serializeStringTable(stream, &bank->stringTable);
    if (res != FMOD_OK) goto error;

    // 29. HASH: Fast lookup table (if version >= 0x59)
    if (stream->version >= 0x59) {
        res = stream->openChunk(&chunkAtomic, 0x48534148 /* 'HASH' */, 0 /* atomic */);
        if (res != FMOD_OK) goto error;
        res = serializeHashTable(stream, &bank->hashTable);
        if (res != FMOD_OK) goto error;
        stream->closeChunk(&chunkAtomic);
    }

    // 30. DEL : Deleted items index (if version >= 0x5D)
    if (stream->version >= 0x5D) {
        res = serializeDeletedList(stream, &bank->deletedList, 0x204C4544 /* 'DEL ' */);
        if (res != FMOD_OK) goto error;
    }

    // 31. MUTE: Muted items index (if version >= 0x5F)
    if (stream->version >= 0x5F) {
        res = serializeMuteList(stream, &bank->muteList, 0x4554554D /* 'MUTE' */);
        if (res != FMOD_OK) goto error;
    }

    // 32. REFI: External reference index (if version >= 0x62)
    if (stream->version >= 0x62) {
        res = serializeReferenceIndex(stream, &bank->refIndex, 0x49464552 /* 'REFI' */);
        if (res != FMOD_OK) goto error;
    }

    // 33. PLAT: Platform info (if version >= 0x78)
    if (stream->version >= 0x78) {
        res = serializePlatformInfo(stream, &bank->platformInfo, 0x54414C50 /* 'PLAT' */);
        if (res != FMOD_OK) goto error;
    }

    // 34. Close 'PROJ' list
    stream->closeChunk(&chunkProj);
    return FMOD_OK;

error:
    return res;
}
```

> [!NOTE]
> **Backwards Compatibility Branches**: The `BankReader::serializeBank` dispatcher maintains legacy parsing branches for older bank versions (such as `SUBS` subsound serialization when `stream->version < 0x3C`). In modern FMOD Studio 2.x banks (`formatVersion >= 0x8C` / 140, as present in `Host.bank` and `Pause.bank`), these legacy branches are bypassed. All bank versions $\ge 0x8C$ require `LIST PRTS` (port buses), and versions $\ge 0x91$ require `LIST SSFX` (spatializer effects).

---

## 4. Audio Streams and Sample Descriptors (`SND `, `FSB5`, `WAVS`)

### 4.1. The `SND ` Container and `FSB5` Header
The `SND ` chunk (`0x20444E53`) is placed immediately following `LIST PROJ`. Inside `SND `, aligned to a 16-byte boundary, begins the **FSB5** (FMOD Sound Bank v5) container with a fixed 60-byte (`0x3C`) base header:

```cpp
#pragma pack(push, 1)
struct FSB5_Header {
    char     magic[4];                  // 'F','S','B','5' (0x35425346)
    uint32_t version;                   // FSB5 version (always 1 in FMOD Studio 2.0x)
    uint32_t numSamples;                // Number of sub-sounds in container
    uint32_t sampleHeaderTableSize;     // Total size of bit-packed sample headers
    uint32_t nameTableSize;             // Size of sample name table including offset array
    uint32_t sampleDataSize;            // Size of compressed audio payload
    uint32_t codec;                     // Codec ID: 15 = Vorbis (FMOD Vorbis Codec)
    uint32_t dataVersion;               // Data version (always 1)
    uint32_t flags;                     // Build flags
    uint8_t  hash[16];                  // 128-bit container hash/GUID
    uint8_t  dummy[8];                  // Padding to 60 bytes (0x3C)
};
#pragma pack(pop)
static_assert(sizeof(FSB5_Header) == 60, "FSB5_Header must be 60 bytes");
```

### 4.2. Sample Descriptor Chunk `WAVS`
Each audio asset referenced by events has a 30-byte descriptor inside `LIST WAVS`:

```cpp
#pragma pack(push, 1)
struct SampleDescriptor_WAV {
    FMOD_GUID_RAW waveformGuid;         // [0x00..0x0F] 16-byte unique waveform asset GUID
    uint32_t      flags;                // [0x10..0x13] Stream and loop flags
    uint32_t      sampleIndexPacked;    // [0x14..0x17] sampleIndex = sampleIndexPacked >> 16
    uint32_t      lengthSamples;        // [0x18..0x1B] Audio duration in PCM samples
    uint16_t      subSoundIndex;        // [0x1C..0x1D] Subsound index within FSB5
};
#pragma pack(pop)
static_assert(sizeof(SampleDescriptor_WAV) == 30, "SampleDescriptor_WAV must be 30 bytes");
```

---

## 5. DSP Chains (`BEFX`, `BEFF`, `BEFB`, `PMEF`)

### 5.1. Bus Effect Descriptor (LIST BEFX / BEFF / BEFB)
Inside `LIST BEFX`, each effect instance is encapsulated in a `BEFF` chunk:

```cpp
#pragma pack(push, 1)
struct BusEffectHeader_BEFB {
    FMOD_GUID_RAW effectGuid;           // [0x00..0x0F] 16-byte effect instance GUID (read by sub_180127360)
    uint32_t      dspType;              // [0x10..0x13] Disk DSP Type enum (translated via sub_1801352A0)
    uint8_t       flags;                // [0x14] Bypass / processing flag (0x00 = active, 0x01 = bypassed)
};
#pragma pack(pop)
static_assert(sizeof(BusEffectHeader_BEFB) == 21, "BusEffectHeader_BEFB must be 21 bytes");
```

> [!NOTE]
> **DSP Chain Placement**: In FMOD 2.x, sound events (`EVTB`) do not contain effect chains directly. An event's DSP effects are hosted on its output bus (`EVTB.outputBusGuid`) or input bus (`EVTB.inputBusGuid`). The bus descriptor in `LIST GBSS` (`BUS ` block) declares `numEffects` followed by the array of `effectGuids` pointing to `BEFB` entries in `LIST BEFX`.

### 5.2. DSP Parameters Deserializer (`PMEF` / `sub_180148D20`)
Inside each effect block, the `PMEF` chunk stores the parameter state:
1. `uint32_t count`: Parameter count read via `sub_180148C40` (assertion: `count <= 1023`).
2. Iterating from `0` to `count - 1`, function `sub_180148D20` deserializes each parameter:

```cpp
// [RVA: 0x180148D20]
FMOD_RESULT deserializeParameter(RiffStream *stream, DspParameter *param) {
    uint8_t typeToken;
    if (stream->read(&typeToken, 1) != 0) return FMOD_ERR_FORMAT;

    uint8_t dataType = typeToken & 0x07; // Bits 0..2 define parameter storage type
    switch (dataType) {
        case 0: // Float (IEEE 754 float32)
            stream->read(&param->floatValue, sizeof(float));
            break;
        case 1: // Integer (int32_t)
            stream->read(&param->intValue, sizeof(int32_t));
            break;
        case 2: // Boolean (uint8_t)
            stream->read(&param->boolValue, sizeof(uint8_t));
            break;
        case 3: { // Data buffer (variable-length blob)
            uint32_t dataLength = readShiftedVarint(stream);
            param->data.resize(dataLength);
            stream->read(param->data.data(), dataLength);
            break;
        }
        default:
            return FMOD_ERR_FORMAT;
    }
    return FMOD_OK;
}
```

### 5.3. Disk DSP Type to Runtime `FMOD_DSP_TYPE` Translation Table (`sub_1801352A0`)

In FMOD Studio binaries, disk DSP indices in `BEFB.dspType` do not match modern runtime `FMOD_DSP_TYPE` values 1:1. Function `sub_1801352A0` (`toFMOD`) translates disk indices using an internal jump table (`jpt_180135306`):

> [!NOTE]
> **Historical Enum Shift**: In FMOD 1.x and early 2.0x, the Core API enumeration contained legacy plugin wrappers: `VSTPLUGIN` (15), `WINAMPPLUGIN` (16), `LADSPAPLUGIN` (23), and `ENVELOPEFOLLOWER` (31). In modern FMOD Studio 2.x Core (`fmod_dsp_effects.h`), these 4 types were removed from the built-in enum, shifting subsequent numeric constants downwards (e.g. `SEND` shifted from 24 to 21, and `MULTIBAND_EQ` shifted from 36 to 32). The binary bank disk serialization retained the legacy 0..37 indices for format stability; `sub_1801352A0` translates them dynamically to the modern runtime enum. Disk types 15, 16, 23, and 31 return 0 (`FMOD_DSP_TYPE_UNKNOWN`) and are handed off to external plugin wrapper hosts.

| Disk Type (`dspType`) | Runtime `FMOD_DSP_TYPE` Enum | Value | FMOD Studio XML Class | Description |
|---|---|---|---|---|
| `0` | `FMOD_DSP_TYPE_UNKNOWN` | 0 | - | Unmapped / generic plugin wrapper |
| `1` | `FMOD_DSP_TYPE_MIXER` | 1 | `MixerBusFader` | Internal mixing bus unit |
| `2` | `FMOD_DSP_TYPE_OSCILLATOR` | 2 | `OscillatorEffect` | Sine/square test tone generator |
| `3` | `FMOD_DSP_TYPE_LOWPASS` | 3 | `LowpassEffect` | Standard lowpass filter |
| `4` | `FMOD_DSP_TYPE_ITLOWPASS` | 4 | `ITLowpassEffect` | Impulse Tracker style resonant lowpass |
| `5` | `FMOD_DSP_TYPE_HIGHPASS` | 5 | `HighpassEffect` | Standard highpass filter |
| `6` | `FMOD_DSP_TYPE_ECHO` | 6 | `EchoEffect` | Basic echo / delay line |
| `7` | `FMOD_DSP_TYPE_FADER` | 7 | `MixerBusFader` | Standard bus gain and level fader |
| `8` | `FMOD_DSP_TYPE_FLANGE` | 8 | `FlangerEffect` | Flange modulation effect |
| `9` | `FMOD_DSP_TYPE_DISTORTION` | 9 | `DistortionEffect` | Distortion stage |
| `10` | `FMOD_DSP_TYPE_NORMALIZE` | 10 | `NormalizeEffect` | Dynamic normalizer |
| `11` | `FMOD_DSP_TYPE_LIMITER` | 11 | `LimiterEffect` | Hard peak limiter |
| `12` | `FMOD_DSP_TYPE_PARAMEQ` | 12 | `ParametricEQEffect` | Parametric equalizer |
| `13` | `FMOD_DSP_TYPE_PITCHSHIFT` | 13 | `PitchShifterEffect` | Pitch shifter |
| `14` | `FMOD_DSP_TYPE_CHORUS` | 14 | `ChorusEffect` | Chorus modulation |
| `15` | `FMOD_DSP_TYPE_UNKNOWN` | 0 | `PluginEffect` | Legacy VST/AU wrapper (handled by plugin manager) |
| `16` | `FMOD_DSP_TYPE_UNKNOWN` | 0 | `PluginEffect` | Legacy Winamp plugin wrapper |
| `17` | `FMOD_DSP_TYPE_ITECHO` | 15 | `ITEchoEffect` | Impulse Tracker resonant echo |
| `18` | `FMOD_DSP_TYPE_COMPRESSOR` | 16 | `CompressorEffect` | Dynamics compressor |
| `19` | `FMOD_DSP_TYPE_SFXREVERB` | 17 | `SFXReverbEffect` | Algorithmic reverb |
| `20` | `FMOD_DSP_TYPE_LOWPASS_SIMPLE` | 18 | `LowpassSimpleEffect` | 6 dB/octave simple lowpass |
| `21` | `FMOD_DSP_TYPE_DELAY` | 19 | `DelayEffect` | Multitap delay |
| `22` | `FMOD_DSP_TYPE_TREMOLO` | 20 | `TremoloEffect` | Amplitude modulation tremolo |
| `23` | `FMOD_DSP_TYPE_UNKNOWN` | 0 | - | Legacy LADSPA plugin wrapper |
| `24` | `FMOD_DSP_TYPE_SEND` | 21 | `MixerSend` | Auxiliary bus send tap |
| `25` | `FMOD_DSP_TYPE_RETURN` | 22 | `MixerReturn` | Auxiliary bus return receiver |
| `26` | `FMOD_DSP_TYPE_HIGHPASS_SIMPLE` | 23 | `HighpassSimpleEffect` | 6 dB/octave simple highpass |
| `27` | `FMOD_DSP_TYPE_PAN` | 24 | `SpatialiserEffect` | Panner and 3D spatializer |
| `28` | `FMOD_DSP_TYPE_THREE_EQ` | 25 | `ThreeEQEffect` | 3-band DJ equalizer (Low, Mid, High) |
| `29` | `FMOD_DSP_TYPE_FFT` | 26 | - | Core FFT spectrum analyzer |
| `30` | `FMOD_DSP_TYPE_LOUDNESS_METER` | 27 | `LoudnessMeter` | ITU-R BS.1770 / EBU R128 loudness meter |
| `31` | `FMOD_DSP_TYPE_UNKNOWN` | 0 | - | Legacy Envelope Follower |
| `32` | `FMOD_DSP_TYPE_CONVOLUTIONREVERB` | 28 | `ConvolutionReverbEffect` | Impulse response convolution reverb |
| `33` | `FMOD_DSP_TYPE_CHANNELMIX` | 29 | `ChannelMixEffect` | Matrix channel mixer and routing |
| `34` | `FMOD_DSP_TYPE_TRANSCEIVER` | 30 | `TransceiverEffect` | Inter-event audio transceiver |
| `35` | `FMOD_DSP_TYPE_OBJECTPAN` | 31 | `ObjectSpatialiserEffect` | Object-based spatializer (Dolby Atmos / spatial audio) |
| `36` | `FMOD_DSP_TYPE_MULTIBAND_EQ` | 32 | `MultibandEqEffect` | 5-band multiband equalizer |
| `37` | `FMOD_DSP_TYPE_MULTIBAND_DYNAMICS` | 33 | `MultibandDynamicsEffect` | Multiband dynamics compressor / expander |

---

## 6. Mixer Routing and Bus Hierarchy (`MBSS`, `GBSS`, `BUS `, `VCAS`, `RBSS`, `SEFX`, `SCFX`)

### 6.1. Mixer Group Buses (`GBSS` / `GBSB` / `BUS `)
Master and group buses are deserialized in `sub_18012A670`:

```cpp
#pragma pack(push, 1)
struct GroupBusHeader_GBSB {
    FMOD_GUID_RAW busGuid;              // [0x00..0x0F] 16-byte bus GUID (read by sub_180127400 -> sub_180128B40)
    FMOD_GUID_RAW parentBusGuid;        // [0x10..0x1F] Parent bus GUID (read by sub_1801330F0 -> sub_180126740, 0 if root)
    uint16_t      busIndex;             // [0x20..0x21] Subsystem bus index
    uint32_t      flags;                // [0x22..0x25] Routing & processing flags
    int32_t       targetPort;           // [0x26..0x29] Target output port index (-1 / 0xFFFFFFFF if none)
};
#pragma pack(pop)
static_assert(sizeof(GroupBusHeader_GBSB) == 42, "GroupBusHeader_GBSB must be 42 bytes");
```

Immediately following `GBSB`, the atomic `BUS ` chunk defines routing parameters and effect attachments:

```cpp
#pragma pack(push, 1)
struct BusCommonBlock_BUS {
    float    volume;                    // [0x00..0x03] Linear gain level (default 1.0f)
    float    pitch;                     // [0x04..0x07] Pitch adjustment (default 1.0f)
    uint32_t channelLayout;             // [0x08..0x0B] Channel layout bitfield / speaker mode
    uint16_t maxInstances;              // [0x0C..0x0D] Voice stealing limit
    uint16_t maxVoices;                 // [0x0E..0x0F] Max concurrent voices
    uint32_t flags;                     // [0x10..0x13] Mute, solo, bypass flags
    uint32_t numEffects;                // [0x14..0x17] Number of attached DSP effects
    // Followed by FMOD_GUID_RAW effectGuids[numEffects] (16 bytes each)
};
#pragma pack(pop)
```

### 6.2. VCA Channels (`VCAS` / `VCAB` / `sub_180137120`)
Inside `LIST VCAS`, each VCA is stored in a dynamic, variable-length `VCAB` chunk:

```cpp
#pragma pack(push, 1)
struct VcaBlock_VCAB {
    FMOD_GUID_RAW vcaGuid;              // [0x00..0x0F] 16-byte unique VCA GUID
    uint32_t      flagsAndCount;        // [0x10..0x13] Control flags and target count
    // Followed by dynamic array:
    // FMOD_GUID_RAW targetBusGuids[numTargetBuses]; (each 16 bytes)
    // Followed by 12-byte trailing runtime control block:
    //   uint32_t vcaIndex;             // Index in runtime VCA array
    //   uint32_t reserved;             // Reserved / status flags
    //   float    volume;               // Linear gain (default 1.0f = 0 dB)
};
#pragma pack(pop)
```

### 6.3. Return Buses (`RBSS` / `RBSB` / `sub_1801461D0`)
Return buses receiving signals from auxiliary sends are defined in `RBSB` (42 bytes):

```cpp
#pragma pack(push, 1)
struct ReturnBus_RBSB {
    FMOD_GUID_RAW returnBusGuid;        // [0x00..0x0F] 16-byte return bus GUID
    FMOD_GUID_RAW parentMasterGuid;     // [0x10..0x1F] Target master bus GUID
    float         volume;               // [0x20..0x23] Return linear gain (default 1.0f)
    uint32_t      flags;                // [0x24..0x27]
    uint16_t      busIndex;             // [0x28..0x29]
};
#pragma pack(pop)
static_assert(sizeof(ReturnBus_RBSB) == 42, "ReturnBus_RBSB must be 42 bytes");
```

### 6.4. Auxiliary Sends (`SEFX` / `SEFF` / `SEFB`)
Inside `LIST SEFX`, send effects begin with an `LCNT` sub-chunk specifying the item count, followed by composite `LIST SEFF` records. Each `LIST SEFF` item encapsulates:
1. An atomic send effect block `SEFB` (`0x42464553`, 37 bytes payload + 1-byte 16-bit word alignment padding = 38 bytes; 46 bytes including the 8-byte RIFF chunk header).
2. Followed by a properties list `LIST PRPS` (12 bytes when empty: 8B RIFF header + 4B `'PRPS'`; 60 bytes when populated with automation properties: 8B RIFF header + 4B `'PRPS'` + 48B property values).
- **Total Item Footprint (including 8-byte `LIST SEFF` RIFF header)**:
  - **Empty `PRPS` (70 bytes)**: $8\text{ (header)} + 4\text{ ('SEFF')} + 46\text{ (SEFB)} + 12\text{ (PRPS)} = 70\text{ bytes}$.
  - **Populated `PRPS` (118 bytes)**: $8\text{ (header)} + 4\text{ ('SEFF')} + 46\text{ (SEFB)} + 60\text{ (PRPS)} = 118\text{ bytes}$.

- **Cross-Bank Size Decomposition**:
  $$\text{Size}(\text{LIST SEFX}) = 16\text{ B (overhead: 4B FourCC + 12B LCNT)} + \sum \text{Size}(\text{LIST SEFF})$$
  - `Music.bank`: $16 + 3 \times 70 = 226\text{ B}$ (exact 3 empty sends).
  - `SFX.bank`: $16 + 21 \times 70 = 1,486\text{ B}$ (exact 21 empty sends).
  - `Host.bank`: $16 + 14 \times 70 + 7 \times 118 = 16 + 980 + 826 = 1,822\text{ B}$ (exact 14 empty sends + 7 populated sends).

```cpp
#pragma pack(push, 1)
struct SendEffect_SEFB {
    FMOD_GUID_RAW sendEffectGuid;       // [0x00..0x0F] 16-byte send effect GUID
    FMOD_GUID_RAW targetReturnBusGuid;  // [0x10..0x1F] Destination MixerReturn GUID
    float         sendLevel;            // [0x20..0x23] Linear send level (default 1.0f = 0 dB)
    uint8_t       bypass;               // [0x24]       Bypass flag (1 = bypassed)
};
#pragma pack(pop)
static_assert(sizeof(SendEffect_SEFB) == 37, "SendEffect_SEFB must be exactly 37 bytes");
```

---

## 7. Parameters and Automation Curves (`PRMS`, `CRVS`, `PMLS`, `MPGS`)

### 7.1. Game Parameters (`PRMS` / `PRMB` / `sub_18012C590`)
Game parameters are stored inside `PRMB` records with an inline variable-length parameter name string:

```cpp
#pragma pack(push, 1)
struct Parameter_PRMB {
    FMOD_GUID_RAW paramGuid;            // [0x00..0x0F] 16-byte unique parameter GUID
    uint32_t      paramType;            // [0x10..0x13] 0 = Continuous, 1 = Discrete, 2 = Labeled
    uint32_t      flags;                // [0x14..0x17] Loop, global, hold, velocity flags
    uint16_t      nameLength;           // [0x18..0x19] Length of inline parameter name string
    // Followed by inline ASCII name:
    // char       name[nameLength];     // e.g. "RPM", "Load", "TopTen"
    // Followed by parameter value bounds:
    // float      minimum;              // Minimum range value (e.g. -1.0f or 0.0f)
    // float      maximum;              // Maximum range value (e.g. 1.0f or 10000.0f)
    // float      defaultValue;         // Default initial value
    // float      seekSpeed;            // Seek speed rate (units/sec)
    // float      velocity;             // Autopitch velocity coefficient
};
#pragma pack(pop)
```

### 7.2. Hermite Automation Splines (`CRVS` / `CURV` / `sub_18013FD60`)
Automation curves inside `CURV` chunks consist of a 36-byte header followed by an array of 16-byte cubic Hermite spline knots:

```cpp
#pragma pack(push, 1)
struct CurveHeader_CURV {
    FMOD_GUID_RAW curveGuid;            // [0x00..0x0F] 16-byte unique curve instance GUID
    FMOD_GUID_RAW ownerGuid;            // [0x10..0x1F] Owner parameter model or curve GUID
    uint32_t      flagsAndCount;        // [0x20..0x23] Bit 0: loopFlag, Bits 1..15: knotCount, Bits 16..31: curveFlags
    // Followed by SplineKnot_CURV knots[knotCount]:
};

struct SplineKnot_CURV {
    float x;                            // [0x00..0x03] Parameter position (timeline sec or param value)
    float y;                            // [0x04..0x07] Automated property output value
    float tangentIn;                    // [0x08..0x0B] Incoming tangent derivative dy/dx
    float tangentOut;                   // [0x0C..0x0F] Outgoing tangent derivative dy/dx
};
#pragma pack(pop)
static_assert(sizeof(CurveHeader_CURV) == 36, "CurveHeader_CURV must be 36 bytes");
static_assert(sizeof(SplineKnot_CURV) == 16, "SplineKnot_CURV must be 16 bytes");
```

> [!NOTE]
> **Curve Ownership**: In parameter automations, `ownerGuid` references the driving parameter model (`ParameterModel_PMLB.modelGuid` or self-referential `curveGuid` registered in `CTRO`). In timeline automations, it references the owning `Event` or `Timeline`.

### 7.3. Parameter Models (`LIST PMLS` / `PMLO` / `PMLB` / `CTRO`)
Inside `LIST PMLS`, parameter models define the relationship between driving game parameters and automation curves. The container begins with an `LCNT` sub-chunk specifying the item count, followed by nested `LIST PMLO` records. Each `LIST PMLO` container encapsulates:
1. An atomic parameter model block `PMLB` (`0x424C4D50`, 38 bytes payload + 8-byte RIFF header = 46 bytes).
2. An atomic controller reference block `CTRO` (`0x4F525443`, variable length): contains a shifted varint count ($K = \text{varint} \gg 1$), a 16-bit element size (`0x0010` = 16 bytes), followed by $K$ driving `FMOD_GUID_RAW curveGuids[K]`. When empty ($K = 0$), `CTRO` payload is strictly 2 bytes (`0x01 0x00`), occupying 10 bytes with its 8-byte RIFF chunk header. When $K \ge 1$, `CTRO` occupies $8 + 2 + 2 + 16 \cdot K = 12 + 16 \cdot K$ bytes.

- **Item Footprint Formula (including 8-byte `LIST PMLO` RIFF header)**:
  $$\text{Size}(\text{LIST PMLO}) = 8\text{ (header)} + 4\text{ ('PMLO')} + 46\text{ (PMLB)} + \text{Size}(\text{CTRO}) = \begin{cases} 68\text{ bytes}, & K = 0 \\ 70 + 16 \cdot K\text{ bytes}, & K \ge 1 \end{cases}$$
  Each additional driving curve adds exactly 16 bytes to the item.

- **Cross-Bank Size Decomposition**:
  $$\text{Size}(\text{LIST PMLS}) = 16\text{ B (overhead)} + \sum \text{Size}(\text{LIST PMLO})$$
  - `Host.bank` (12 models, all $K = 0$): $16 + 12 \times 68 = 832\text{ B}$.
  - `Music.bank` (80 models: 52 with $K=0$, 8 with $K=1$, 2 with $K=2$, 9 with $K=3$, 9 with $K=4$):
    $$16 + 52 \times 68 + 8 \times 86 + 2 \times 102 + 9 \times 118 + 9 \times 134 = 16 + 3536 + 688 + 204 + 1062 + 1206 = 6,712\text{ B}$$
  - `SFX.bank` (86 models: 30 with $K=0$, 44 with $K=1$, 4 with $K=2$, 6 with $K=4$, 1 with $K=9$, 1 with $K=24$):
    $$16 + 30 \times 68 + 44 \times 86 + 4 \times 102 + 6 \times 134 + 1 \times 214 + 1 \times 454 = 16 + 2040 + 3784 + 408 + 804 + 214 + 454 = 7,720\text{ B}$$

```cpp
#pragma pack(push, 1)
struct ParameterModel_PMLB {
    FMOD_GUID_RAW modelGuid;            // [0x00..0x0F] 16-byte parameter model GUID
    FMOD_GUID_RAW parameterGuid;        // [0x10..0x1F] 16-byte driving game parameter GUID (in LIST PRMS)
    uint32_t      flags;                // [0x20..0x23] Parameter model evaluation flags
    uint16_t      subIndex;             // [0x24..0x25] Model sub-index
};
#pragma pack(pop)
static_assert(sizeof(ParameterModel_PMLB) == 38, "ParameterModel_PMLB must be 38 bytes");
```

#### Automation Resolution Chain:
$$\text{GameParameter (PRMS)} \longleftarrow \text{PMLB.parameterGuid} \longleftrightarrow \text{PMLB.modelGuid} \longrightarrow \text{CTRO (curveGuid)} \longleftrightarrow \text{CURV (knots)} \longleftarrow \text{CTRL (targetObjectGuid, propertyTypeIndex)}$$

### 7.4. Macro Pages (`LIST MPGS` / `MAP `)
Inside `LIST MPGS`, macro control pages begin with `LCNT` and encapsulate atomic `MAP ` chunks (`0x2050414D`). Each page contains a fixed 20-byte header followed by a dynamic array of knob / control coordinate mappings:

```cpp
#pragma pack(push, 1)
struct MacroPage_MAP {
    FMOD_GUID_RAW pageGuid;             // [0x00..0x0F] 16-byte macro page GUID
    uint32_t      flags;                // [0x10..0x13] Macro control flags
    // Followed by float controlCoordinates[M] (UI knob canvas positions and mapping thresholds)
};
#pragma pack(pop)
static_assert(sizeof(MacroPage_MAP) == 20, "MacroPage_MAP header must be 20 bytes");
```

- **Dynamic Knob Count ($M$) Derivation**:
  The element count $M$ is **not stored in any explicit header field**. Instead, the deserializer computes it directly from the RIFF chunk payload length:
  $$M = \frac{\text{ChunkPayloadSize} - 20}{4}$$
  Each macro knob control entry is represented by a pair of 32-bit IEEE 754 floats $(X, Y)$ denoting its UI canvas layout coordinates and parameter boundaries (8 bytes per knob). Consequently, $M$ is always an even integer:
  - 2 macro knobs $\rightarrow M = 4$ floats: $20 + 4 \times 4 = 36\text{ B payload}$ ($28 + 16 = 44\text{ B chunk with RIFF header}$).
  - 3 macro knobs $\rightarrow M = 6$ floats: $20 + 6 \times 4 = 44\text{ B payload}$ ($28 + 24 = 52\text{ B chunk with RIFF header}$).
  - 5 macro knobs $\rightarrow M = 10$ floats: $20 + 10 \times 4 = 60\text{ B payload}$ ($28 + 40 = 68\text{ B chunk with RIFF header}$).
  - 7 macro knobs $\rightarrow M = 14$ floats: $20 + 14 \times 4 = 76\text{ B payload}$ ($28 + 56 = 84\text{ B chunk with RIFF header}$).
  - 9 macro knobs $\rightarrow M = 18$ floats: $20 + 18 \times 4 = 92\text{ B payload}$ ($28 + 72 = 100\text{ B chunk with RIFF header}$).

- **Chunk Footprint Formula (including 8-byte RIFF header)**:
  $$\text{Total Size}(\text{MAP }) = 8\text{ (header)} + 20 + 4 \cdot M = 28 + 4 \cdot M\text{ bytes}$$

- **Cross-Bank Size Decomposition**:
  $$\text{Size}(\text{LIST MPGS}) = 16\text{ B (overhead)} + \sum \text{Size}(\text{MAP })$$
  - `Host.bank` (4 pages: 2 with $M=18$, 2 with $M=4$):
    $$16 + 2 \times 100 + 2 \times 44 = 16 + 200 + 88 = 304\text{ B}$$
  - `Music.bank` (6 pages: 2 with $M=18$, 3 with $M=6$, 1 with $M=4$):
    $$16 + 2 \times 100 + 3 \times 52 + 1 \times 44 = 16 + 200 + 156 + 44 = 416\text{ B}$$
  - `SFX.bank` (15 pages: 3 with $M=18$, 1 with $M=14$, 1 with $M=10$, 3 with $M=6$, 7 with $M=4$):
    $$16 + 3 \times 100 + 1 \times 84 + 1 \times 68 + 3 \times 52 + 7 \times 44 = 16 + 300 + 84 + 68 + 156 + 308 = 932\text{ B}$$

---

## 8. Events and Timelines (`EVTS`, `TLNS`, `TRNS`)

### 8.1. Sound Event Descriptor `EVTB` (`sub_180135C90`)
The core structure of a sound event begins with a fixed 93-byte header `EventData_EVTB`, followed by embedded 3D positioning settings, an array of event tracks (`EventTrack`), parameter links, and versioned property models:

```cpp
#pragma pack(push, 1)
struct EventData_EVTB {
    FMOD_GUID_RAW eventGuid;            // [0x00..0x0F] 16-byte unique event GUID
    FMOD_GUID_RAW timelineGuid;         // [0x10..0x1F] Owning timeline GUID (in LIST TLNS)
    FMOD_GUID_RAW outputBusGuid;        // [0x20..0x2F] Target output mixer bus GUID
    FMOD_GUID_RAW inputBusGuid;         // [0x30..0x3F] Target input bus GUID
    FMOD_GUID_RAW masterTrackGuid;      // [0x40..0x4F] Master track GUID
    uint32_t      maxInstances;         // [0x50..0x53] Max simultaneous voice instances
    uint32_t      stealingBehavior;     // [0x54..0x57] Stealing mode (0=Oldest, 1=Quietest, 2=Virtualize, 3=Fail)
    uint8_t       flags;                // [0x58]       Bit 0: isOneShot
    uint32_t      priority;             // [0x59..0x5C] Voice priority
    // Followed by 3DSettings (sub_18013CF90), event tracks array, and versioned properties
};

struct EventTrack {
    FMOD_GUID_RAW trackGuid;            // [0x00..0x0F] 16-byte track GUID (referenced by INST.trackGuid)
    float         volume;               // [0x10..0x13] Track linear volume scalar (default 1.0f)
};
#pragma pack(pop)
static_assert(sizeof(EventTrack) == 20, "EventTrack must be exactly 20 bytes");
```

#### Deserialization Routine (`sub_180135C90`)
```cpp
// [RVA: 0x180135C90]
FMOD_RESULT deserializeEvent(RiffStream *stream, EventData *event) {
    RiffChunk chunk;
    FMOD_RESULT res = stream->openChunk(&chunk, 0x42545645 /* 'EVTB' */, 0 /* atomic */);
    if (res != FMOD_OK) return res;

    // 1-8. Fixed Header Fields (EventData_EVTB)
    stream->read(&event->eventGuid, sizeof(FMOD_GUID_RAW));
    stream->read(&event->timelineGuid, sizeof(FMOD_GUID_RAW));
    stream->read(&event->outputBusGuid, sizeof(FMOD_GUID_RAW));
    stream->read(&event->inputBusGuid, sizeof(FMOD_GUID_RAW));
    stream->read(&event->masterTrackGuid, sizeof(FMOD_GUID_RAW));
    stream->read(&event->maxInstances, sizeof(uint32_t));
    stream->read(&event->stealingBehavior, sizeof(uint32_t));
    uint8_t flags;
    stream->read(&flags, sizeof(uint8_t));
    event->isOneShot = (flags & 1);
    stream->read(&event->priority, sizeof(uint32_t));

    // 9. 3D Positioning Settings (sub_18013CF90)
    res = serialize3DSettings(stream, &event->settings3D);
    if (res != FMOD_OK) return res;

    // 10. Legacy Subsounds (if version < 0x3C, sub_180153E10)
    if (stream->version() < 0x3C) {
        res = serializeLegacySubsound(stream, event);
        if (res != FMOD_OK) return res;
    }

    // 11. Event Tracks Array (shifted varint count, sub_180140310)
    uint32_t trackCount = readShiftedVarint(stream); // count = varint >> 1
    for (uint32_t i = 0; i < trackCount; ++i) {
        EventTrack track;
        stream->read(&track.trackGuid, sizeof(FMOD_GUID_RAW));
        stream->read(&track.volume, sizeof(float));
        event->tracks.push_back(track);
    }

    // 12. Parameter Links (shifted varint count, sub_180140590)
    uint32_t paramLinkCount = readShiftedVarint(stream);
    for (uint32_t i = 0; i < paramLinkCount; ++i) {
        serializeEventParameterLink(stream, event, i);
    }

    // 13. Version Gates:
    if (stream->version() >= 0x30) stream->read(&event->minDistance, sizeof(float));
    if (stream->version() >= 0x34) stream->read(&event->cooldown, sizeof(uint32_t));
    if (stream->version() >= 0x4E) stream->read(&event->maxDistance, sizeof(float));
    if (stream->version() >= 0x61) stream->read(&event->userPropertyFlags, sizeof(uint32_t));
    if (stream->version() >= 0x6B) serializeModelList(stream, &event->userProperties);
    if (stream->version() >= 0x76) serializeModelList(stream, &event->dynamicReferences);
    if (stream->version() >= 0x83) serializeModelList(stream, &event->sandboxProperties);
    if (stream->version() >= 0x89) {
        stream->read(&event->cutoffFrequency, sizeof(float));
        stream->read(&event->panDistance, sizeof(float));
    }

    return stream->closeChunk(&chunk);
}
```

### 8.2. Event Timeline Structure `TLNB` (`sub_18012EF30`)
Timelines are encapsulated inside composite `TMLN` list items within `LIST TLNS`. Each timeline contains an atomic `TLNB` descriptor block followed by a condition transition list `TRNS`:

```cpp
#pragma pack(push, 1)
struct Timeline_TLNB {
    FMOD_GUID_RAW timelineGuid;         // [0x00..0x0F] 16-byte timeline GUID (matches EVTB.timelineGuid)
    // Followed by optional legacy owner GUID (if version < 0x6D)
    // Followed by length & tempo parameters (sub_18013AFD0)
    // Followed by shifted varint timeline tracks array (sub_18013ECE0)
    // Followed by marker lists (sub_180149E70, sub_18013DE60)
};

struct TimelineTrack {
    FMOD_GUID_RAW trackGuid;            // [0x00..0x0F] 16-byte track GUID (matching EventTrack.trackGuid)
    uint32_t      trackIndex;           // [0x10..0x13] Sequential timeline display index (0..T-1)
    uint32_t      flags;                // [0x14..0x17] Track layout, mute, and solo state flags
};
#pragma pack(pop)
static_assert(sizeof(TimelineTrack) == 24, "TimelineTrack must be exactly 24 bytes");
```

#### Track Hierarchy and Dual Resolution Mechanism:
Track GUIDs reside in both `EVTB` and `TLNB`, serving complementary roles:
1. **Event Ownership (`EVTB`)**: `EventData_EVTB` explicitly defines `masterTrackGuid` (16 bytes) and an array of `EventTrack` records (`trackGuid` + `volume` 4B). This defines the audio mixing hierarchy.
2. **Timeline Representation (`TLNB`)**: `Timeline_TLNB` registers those identical track GUIDs within `timelineTracks` (`trackGuid` + `trackIndex` 4B + `flags` 4B), assigning them sequential channel lanes and display coordinates on the timeline canvas.
3. **Sound Module Binding (`INST.trackGuid`)**: Audio instruments in `LIST WAIS`, `MUIS`, `SPIS`, etc., store `SoundInstance_INST.trackGuid`. An instrument resolves to its track by matching `INST.trackGuid` against either `EventData_EVTB.eventTracks` (or `masterTrackGuid`) or `Timeline_TLNB.timelineTracks`. Both resolve to the same underlying track entity.

### 8.3. Transition Conditions Bytecode VM (`TRNS` / `sub_1801274A0`)
Inside `LIST TRNS`, each transition marker is parsed by `sub_18012F460`, which reads the start position (`[rsi+30h]`), region length (`[rsi+34h]`), and invokes the bytecode condition parser `sub_1801274A0`.

#### Condition Bytecode Instruction Set
The condition block begins with a 4-byte `totalByteSize`. Instructions are variable-length tokens evaluated by an internal stack machine:

| Opcode | Token Name | Operand Payload | Stack Behavior / Evaluation Logic |
|---|---|---|---|
| `0x00` | `OP_NOP` | None (1 byte total) | No operation. |
| `0x01` | `OP_PUSH_CONST` | 4 bytes (IEEE 754 float) | Pushes constant value onto operand stack. |
| `0x02` | `OP_READ_PARAM` | 16 bytes (`FMOD_GUID_RAW`) | Reads current game parameter value by GUID and pushes to stack. |
| `0x03` | `OP_READ_PLAYHEAD` | None (1 byte total) | Pushes current timeline playhead position in seconds. |
| `0x10` | `OP_CMP_EQUAL` | None (1 byte total) | Pops two operands, pushes `(b == a ? 1.0f : 0.0f)`. |
| `0x11` | `OP_CMP_LESS` | None (1 byte total) | Pops two operands, pushes `(b < a ? 1.0f : 0.0f)`. |
| `0x12` | `OP_CMP_GREATER` | None (1 byte total) | Pops two operands, pushes `(b > a ? 1.0f : 0.0f)`. |
| `0x20` | `OP_LOGICAL_AND` | None (1 byte total) | Pops two operands, pushes `(a != 0.0f && b != 0.0f ? 1.0f : 0.0f)`. |
| `0x21` | `OP_LOGICAL_OR` | None (1 byte total) | Pops two operands, pushes `(a != 0.0f \|\| b != 0.0f ? 1.0f : 0.0f)`. |
| `0x30` | `OP_QUANTIZE` | 4 bytes (uint32_t mode: Bar/Beat) | Evaluates beat quantization against tempo grid. |
| `0xFF` | `OP_END` | None (1 byte total) | Terminates evaluation. Returns top of stack as boolean result. |

---

## 9. Audio Modules & Instruments

### 9.1. Sound Instance Base Block `INST` (`sub_18012B780`)
All audio instruments share an atomic 130-byte base block `INST` (`0x54534E49`):

```cpp
// [RVA: 0x18012B780]
#pragma pack(push, 1)
struct SoundInstance_INST {
    FMOD_GUID_RAW trackGuid;            // [0x00..0x0F] 16-byte owner track GUID (matches EventTrack.trackGuid)
    float         volume;               // [0x10..0x13] Gain in decibels (default 0.0f dB; contrast with linear 1.0f in BUS/VCAB)
    float         pitch;                // [0x14..0x17] Pitch adjustment (semitones)
    int32_t       flags;                // [0x18..0x1B] Processing flags
    uint32_t      bitfield;             // [0x1C..0x1F] Processing bitfield (uint8_t if version < 0x82)
    float         volumeRandomization;  // [0x20..0x23] Random volume variance (dB)
    float         triggerProbability;   // [0x24..0x27] Trigger chance (100.0f = 100%)
    float         startPosition;        // [0x28..0x2B] Timeline or parameter start position
    float         length;               // [0x2C..0x2F] Region length
    uint32_t      quantizationMode;     // [0x30..0x33] Quantization enum (0..2)
    FMOD_GUID_RAW parameterGuid;        // [0x34..0x43] Parameter GUID (if parameter track, else 0)
    float         cutoffFrequency;      // [0x44..0x47] Filter cutoff
    float         pitchMultiplier;      // [0x48..0x4B] Pitch multiplier
    int32_t       maxVoices;            // [0x4C..0x4F] Voice limit
    int32_t       maxCutCount;          // [0x50..0x53] Voice stealing cut limit (sub_18012D5F0)
    uint16_t      elementSize;          // [0x54..0x55] Routing block size (0x0018 = 24 bytes)
    FMOD_GUID_RAW targetBusGuid;        // [0x56..0x65] 16-byte target routing bus GUID
    uint32_t      channelLayout;        // [0x66..0x69] FMOD_SPEAKERMODE channel mask
    uint32_t      routingFlags;         // [0x6A..0x6D]
    int32_t       loopCount;            // [0x6E..0x71] Loop count (if version >= 0x35)
    uint32_t      fadeMode;             // [0x72..0x75] Fade mode (if version >= 0x47)
    float         fadeCurveIn;          // [0x76..0x79] Fade-in duration (if version >= 0x48)
    float         fadeCurveOut;         // [0x7A..0x7D] Fade-out duration (if version >= 0x50)
    uint32_t      conditionBytecodeSize;// [0x7E..0x81] Condition bytecode size (sub_180127280, if version >= 0x82)
};
#pragma pack(pop)
static_assert(sizeof(SoundInstance_INST) == 130, "SoundInstance_INST must be exactly 130 bytes");
```

> [!NOTE]
> **Volume Unit Conventions**:
> - In `SoundInstance_INST`, `volume` is expressed in **decibels (dB)**, where `0.0f` represents unity gain (0 dB).
> - In contrast, mixer buses (`BusCommonBlock_BUS.volume`), return buses (`ReturnBus_RBSB.volume`), and VCAs (`VcaBlock_VCAB.volume`) store volume as a **linear scalar amplitude**, where `1.0f` represents unity gain (0 dB).

#### 9.1.1. Version Gates and Payload Size (123 B vs 130 B)
The deserializer `sub_18012B780` applies version gates that directly affect the physical byte size of `INST`:
- **Format Version < `0x82` (123 bytes)**: In older bank versions (e.g. `Vehicles.bank`, format version `0x77` / 119):
  1. The `bitfield` at offset `0x1C` is serialized as a 1-byte boolean (`uint8_t`) rather than a 4-byte integer (`uint32_t`), saving 3 bytes.
  2. The transition condition block (`sub_180127280`) is gated by `version >= 0x82` and omitted entirely, saving 4 bytes (the `conditionBytecodeSize` field).
  - Net size: $130 - 3 - 4 = 123$ bytes.
- **Format Version $\ge 0x82$ (130 bytes)**: In modern FMOD Studio 2.x banks (e.g. `Host.bank` and `Pause.bank`, format version `0x8C` / 140):
  - `bitfield` is serialized as a 4-byte `uint32_t` and `conditionBytecodeSize` is present (4 bytes `0x00000000` when no conditions are attached). Total size is exactly 130 bytes.
- **Historical Field Gates in `sub_18012B780`**:
  The 123 B and 130 B sizes strictly assume `version >= 0x50` (where fade curves and loop count are fully active). For legacy bank schemas (`version < 0x50`), earlier field gates reduce the physical byte footprint:
  - `loopCount`: Serialized only if `version >= 0x35`.
  - `fadeMode`: Serialized only if `version >= 0x47`.
  - `fadeCurveIn`: Serialized only if `version >= 0x48`.
  - `fadeCurveOut`: Serialized only if `version >= 0x50`.
  - `conditionBytecode`: Serialized only if `version >= 0x82`.

### 9.2. Single Sound Instrument `WAIS` / `WAIT` / `WAIB`
Inside `LIST WAIS`, each single-sound instrument is stored as a composite `WAIT` list item containing:
1. An atomic payload `WAIB` (`0x42494157`, 32 bytes payload + 8-byte RIFF header = 40 bytes).
2. The shared instrument base block `INST` (`0x54534E49`, 130 bytes payload + 8-byte RIFF header = 138 bytes; 170 bytes when condition bytecode is present).
3. Followed by a property list `LIST PRPS` (12 bytes when empty: 8B RIFF header + 4B `'PRPS'`; 60B to 186B when populated with automated sound properties).

```cpp
#pragma pack(push, 1)
struct SingleSoundPayload_WAIB {
    FMOD_GUID_RAW soundInstanceGuid;    // [0x00..0x0F] 16-byte unique instrument GUID
    FMOD_GUID_RAW waveformAssetGuid;    // [0x10..0x1F] Waveform descriptor GUID in LIST WAVS
};
#pragma pack(pop)
static_assert(sizeof(SingleSoundPayload_WAIB) == 32, "SingleSoundPayload_WAIB must be 32 bytes");
```

- **Item Footprint Formula (including 8-byte `LIST WAIT` RIFF header)**:
  $$\text{Size}(\text{LIST WAIT}) = 8\text{ (header)} + 4\text{ ('WAIT')} + 40\text{ (WAIB)} + \text{Size}(\text{INST}) + \text{Size}(\text{LIST PRPS})$$
  - Base footprint ($138\text{B INST} + 12\text{B empty PRPS}$): $8 + 4 + 40 + 138 + 12 = 202\text{ bytes}$.
  - With condition bytecode ($170\text{B INST} + 12\text{B empty PRPS}$): $8 + 4 + 40 + 170 + 12 = 234\text{ bytes}$ ($+32\text{ B}$ from bytecode).
  - With 1 automated property ($138\text{B INST} + 78\text{B PRPS}$): $8 + 4 + 40 + 138 + 78 = 268\text{ bytes}$.
  - With 1 automated property having $K=2$ driving curves ($138\text{B INST} + 94\text{B PRPS}$): $8 + 4 + 40 + 138 + 94 = 284\text{ bytes}$.
  - With condition bytecode and 1 automated property ($170\text{B INST} + 78\text{B PRPS}$): $8 + 4 + 40 + 170 + 78 = 300\text{ bytes}$.
  - With 2 populated automated properties ($138\text{B INST} + 132\text{B PRPS}$): $8 + 4 + 40 + 138 + 132 = 322\text{ bytes}$.

- **Cross-Bank Size Decomposition**:
  $$\text{Size}(\text{LIST WAIS}) = 16\text{ B (overhead)} + \sum \text{Size}(\text{LIST WAIT})$$
  - `Pause.bank` (19 items, all 202 B): $16 + 19 \times 202 = 3,854\text{ B}$.
  - `Host.bank` (218 items: 217 at 202 B, 1 at 268 B): $16 + 217 \times 202 + 268 = 44,118\text{ B}$.
  - `Music.bank` (105 items: 58 at 202 B, 39 at 268 B, 4 at 284 B, 4 at 300 B):
    $$16 + 58 \times 202 + 39 \times 268 + 4 \times 284 + 4 \times 300 = 16 + 11716 + 10452 + 1136 + 1200 = 24,520\text{ B}$$
  - `SFX.bank` (511 items: 427 at 202 B, 17 at 234 B, 58 at 268 B, 5 at 284 B, 4 at 322 B):
    $$16 + 427 \times 202 + 17 \times 234 + 58 \times 268 + 5 \times 284 + 4 \times 322 = 16 + 86254 + 3978 + 15544 + 1420 + 1288 = 108,500\text{ B}$$

### 9.2.1. Sound Properties Container (`LIST PRPS` / `PROP`)
Terminating every sound module, `LIST PRPS` (`0x53505250`) serializes automated property overrides.

```cpp
#pragma pack(push, 1)
struct Property_PROP {
    uint32_t      propertyIndex;        // [0x00..0x03] Property index (0=Volume, 1=Pitch, 3=Highpass, 4=3DPan, 1000=DSP Param)
    uint16_t      propertyType;         // [0x04..0x05] Property type enum (2=Float/dB, 3=Integer/Enum, 4=3D Vector)
    uint16_t      flags;                // [0x06..0x07] Automation / override flags
    FMOD_GUID_RAW targetGuid;           // [0x08..0x17] 16-byte target object / macro / parameter GUID
    // Followed by shifted varint driving curve count K (K = raw >> 1):
    // - When K == 0 (raw == 1): followed by uint16_t subIndex (4 bytes total trailer -> 28B payload, 36B chunk)
    // - When K >= 1: uint16_t elementSize (0x0010 = 16B), FMOD_GUID_RAW curveGuids[K], uint16_t subIndex
    //   (46B for K=1 -> 54B chunk; 62B for K=2 -> 70B chunk; 64B for dual 1-curve descriptors -> 72B chunk)
};
#pragma pack(pop)
static_assert(sizeof(Property_PROP) == 24, "Property_PROP header must be 24 bytes");
```

- **Container Footprint Formula (including 8-byte RIFF header)**:
  - **Empty Container ($N = 0$)**: Strictly **12 bytes** ($8\text{B RIFF header} + 4\text{B 'PRPS'}$, no `LCNT`).
  - **Populated Container ($N \ge 1$)**:
    $$\text{Size}(\text{LIST PRPS}) = 8\text{ (header)} + 4\text{ ('PRPS')} + 12\text{ (LCNT)} + \sum \text{Size}(\text{PROP})$$
    - $1 \times \text{PROP}$ with $K = 0$ (28B payload, 36B chunk): $8 + 4 + 12 + 36 = \mathbf{60\text{ bytes}}$ (observed in `Host.bank` mixer buses).
    - $1 \times \text{PROP}$ with $K = 1$ (46B payload, 54B chunk): $8 + 4 + 12 + 54 = \mathbf{78\text{ bytes}}$ (observed in `WAIT`, `EVIT`, `MUIT` and mixer buses across `Host.bank`, `Music.bank`, `SFX.bank`).
    - $1 \times \text{PROP}$ with $K = 2$ driving curves (62B payload, 70B chunk): $8 + 4 + 12 + 70 = \mathbf{94\text{ bytes}}$ (observed in `WAIT` items and `MBSS`).
    - $1 \times \text{PROP}$ dual-curve descriptor (64B payload, 72B chunk): $8 + 4 + 12 + 72 = \mathbf{96\text{ bytes}}$ (observed in `Music.bank` `MBSS`).
    - $2 \times \text{PROP}$ with $K = 1$ ($2 \times 54\text{B chunks}$): $8 + 4 + 12 + 108 = \mathbf{132\text{ bytes}}$ (observed in `SFX.bank` `WAIT` items).
    - $3 \times \text{PROP}$ with $K = 1$ ($3 \times 54\text{B chunks}$): $8 + 4 + 12 + 162 = \mathbf{186\text{ bytes}}$ (observed in `Music.bank` `BEFX` and `SFX.bank` `MUIT` items).

> [!NOTE]
> **Resolution of Apparent "+32 B" Increments**:
> In modules such as `WAIT` (234 B vs 202 B) and `CMDI` (242 B vs 210 B), the $+32\text{ B}$ increase does **not** stem from a 44-byte `PRPS` list. In all these instances, `LIST PRPS` remains 12 bytes (empty); the $+32\text{ B}$ is produced by `INST.conditionBytecodeSize = 32`, which expands `INST` payload from 130 B to 162 B (chunk size from 138 B to 170 B).

### 9.3. Multi-Sound Instrument `MUIS` / `MUIT` / `PLST`
`MUIS` structures play randomized or sequential playlists. Each composite `MUIT` item encapsulates an atomic `MUIB` block, playlist descriptor `PLST`, instrument base `INST`, and terminating `LIST PRPS`:

```cpp
#pragma pack(push, 1)
struct MultiSound_MUIB {
    FMOD_GUID_RAW multiSoundGuid;       // [0x00..0x0F] 16-byte MultiSound instrument GUID
};
static_assert(sizeof(MultiSound_MUIB) == 16, "MultiSound_MUIB must be 16 bytes");

struct MultiSoundPlaylist_PLST {
    uint32_t      playMode;             // [0x00..0x03] 0 = Random, 1 = Sequential, 2 = Shuffle
    uint32_t      maxPlaybackCount;     // [0x04..0x07] Concurrent instance limit (typically 1)
    uint16_t      varintItemCount;      // [0x08..0x09] Shifted varint sub-instrument count (N = raw >> 1)
    uint16_t      elementSize;          // [0x0A..0x0B] Entry element size (0x0014 = 20 bytes)
    // Followed by PlaylistItem items[N]:
    // FMOD_GUID_RAW itemGuid (16 bytes) + float weight (4 bytes) = 20 bytes
};
static_assert(sizeof(MultiSoundPlaylist_PLST) == 12, "MultiSoundPlaylist_PLST header must be 12 bytes");
#pragma pack(pop)
```

- **Item Footprint Formula (including 8-byte `LIST MUIT` RIFF header)**:
  $$\text{Size}(\text{LIST MUIT}) = 8\text{ (header)} + 4\text{ ('MUIT')} + 24\text{ (MUIB)} + \text{Size}(\text{PLST}) + \text{Size}(\text{INST}) + \text{Size}(\text{LIST PRPS})$$
  $$\text{Base Size} = 8 + 4 + 24 + (20 + 20 \cdot N) + 138 + 12 = \mathbf{206 + 20 \cdot N\text{ bytes}}$$
  - With empty `PRPS` (12B) and base `INST` (138B):
    - $N = 1$: $206 + 20 = \mathbf{226\text{ B}}$
    - $N = 2$: $206 + 40 = \mathbf{246\text{ B}}$
    - $N = 3$: $206 + 60 = \mathbf{266\text{ B}}$
    - $N = 4$: $206 + 80 = \mathbf{286\text{ B}}$
    - $N = 6$: $206 + 120 = \mathbf{326\text{ B}}$
    - $N = 10$: $206 + 200 = \mathbf{406\text{ B}}$
  - With 1 populated property in `PRPS` (78B container, $+66\text{ B}$):
    - $N = 3$: $266 + 66 = \mathbf{332\text{ B}}$
    - $N = 4$: $286 + 66 = \mathbf{352\text{ B}}$
    - $N = 5$: $306 + 66 = \mathbf{372\text{ B}}$
    - $N = 6$: $326 + 66 = \mathbf{392\text{ B}}$
  - With 3 populated properties in `PRPS` (186B container, $+174\text{ B}$):
    - $N = 5$: $306 + 174 = \mathbf{480\text{ B}}$
    - $N = 6$: $326 + 174 = \mathbf{500\text{ B}}$

- **Cross-Bank Size Decomposition**:
  $$\text{Size}(\text{LIST MUIS}) = 16\text{ B (overhead)} + \sum \text{Size}(\text{LIST MUIT})$$
  - `Music.bank` (7 items: 1 at 246 B, 3 at 266 B, 1 at 286 B, 2 at 326 B):
    $$16 + 1 \times 246 + 3 \times 266 + 1 \times 286 + 2 \times 326 = 16 + 246 + 798 + 286 + 652 = \mathbf{1,998\text{ B}}$$
  - `SFX.bank` (95 items):
    $$16 + 17 \times 226 + 7 \times 246 + 42 \times 266 + 1 \times 270 + 8 \times 286 + 10 \times 332 + 1 \times 352 + 3 \times 372 + 2 \times 392 + 2 \times 406 + 1 \times 480 + 1 \times 500 = \mathbf{26,674\text{ B}}$$

### 9.4. Other Sound Instrument Modules (`SPIS`, `EVIS`, `CMDS`, `PRIS`)
All specialized sound modules follow an identical nesting structure inside their respective RIFF `LIST` containers, each terminating with a `LIST PRPS` sub-chunk (12 bytes when empty):

```cpp
#pragma pack(push, 1)
// LIST SPIS -> SPIT (SPIB + PLST + INST + LIST PRPS)
struct Scatterer_SPIB {
    FMOD_GUID_RAW scattererGuid;        // [0x00..0x0F] 16-byte scatterer instrument GUID
    uint32_t      minInterval;          // [0x10..0x13] Minimum spawn interval (ms)
    uint32_t      maxInterval;          // [0x14..0x17] Maximum spawn interval (ms)
    float         volRandomMin;         // [0x18..0x1B] Volume randomization min (dB)
    float         volRandomMax;         // [0x1C..0x1F] Volume randomization max (dB)
    float         pitchRandom;          // [0x20..0x23] Pitch randomization range (semitones)
    float         spawnRate;            // [0x24..0x27] Spawn rate / polyphony limit
    float         scatterDistanceMin;   // [0x28..0x2B] Min scatter radius (meters)
    float         scatterDistanceMax;   // [0x2C..0x2F] Max scatter radius (meters)
};
static_assert(sizeof(Scatterer_SPIB) == 48, "Scatterer_SPIB must be 48 bytes");

// LIST EVIS -> EVIT (EVIB + INST + LIST PRPS)
struct EventInstrument_EVIB {
    FMOD_GUID_RAW instrumentGuid;       // [0x00..0x0F] 16-byte event instrument GUID
    FMOD_GUID_RAW targetEventGuid;      // [0x10..0x1F] Target event GUID (in LIST EVTS)
    float         initialParamValue;    // [0x20..0x23] Initial parameter value
    uint16_t      flags;                // [0x24..0x25] Parameter pass-through & voice stealing flags
    // Followed by optional shifted varint parameter override array: P * (16B paramGuid + 4B val + 4B flags)
};
static_assert(sizeof(EventInstrument_EVIB) == 38, "EventInstrument_EVIB base must be 38 bytes");

// LIST CMDS -> CMDI (CMDB + INST + LIST PRPS)
struct CommandInstrument_CMDB {
    FMOD_GUID_RAW instrumentGuid;       // [0x00..0x0F] 16-byte command instrument GUID
    uint32_t      commandType;          // [0x10..0x13] Command opcode (0=StartEvent, 1=StopEvent, 2=SetParameter)
    FMOD_GUID_RAW targetGuid;           // [0x14..0x23] Target event or parameter GUID
    float         value;                // [0x24..0x27] Target value operand (if version >= 0x80)
};
static_assert(sizeof(CommandInstrument_CMDB) == 40, "CommandInstrument_CMDB must be 40 bytes for version >= 0x80");

// LIST PRIS -> PRIT (PRIB + INST + LIST PRPS)
struct Programmer_PRIB {
    FMOD_GUID_RAW instrumentGuid;       // [0x00..0x0F] 16-byte programmer sound GUID
    uint16_t      flags;                // [0x10..0x11] Callback mode & audio format hints
};
static_assert(sizeof(Programmer_PRIB) == 18, "Programmer_PRIB must be 18 bytes");
#pragma pack(pop)
```

> [!NOTE]
> **Command Instrument Version Gate (`CMDB`)**:
> - **Format Version $\ge 0x80$ (40 bytes)**: Includes `float value` at offset `0x24` (`static_assert(sizeof(CommandInstrument_CMDB) == 40)`).
> - **Format Version $< 0x80$ (36 bytes)**: The `value` field is omitted entirely, reducing the payload to 36 bytes.

#### Cross-Bank Item Footprint Verification:
1. **`PRIT` (`LIST PRIS`)**:
   $$\text{Size}(\text{LIST PRIT}) = 8\text{ (header)} + 4\text{ ('PRIT')} + 26\text{ (PRIB)} + 138\text{ (INST)} + 12\text{ (PRPS)} = 188\text{ bytes}$$
   - `Host.bank` (21 items): $16 + 21 \times 188 = 3,964\text{ B}$.
2. **`CMDI` (`LIST CMDS`)**:
   $$\text{Size}(\text{LIST CMDI}) = 8\text{ (header)} + 4\text{ ('CMDI')} + 48\text{ (CMDB)} + \text{Size}(\text{INST}) + 12\text{ (PRPS)}$$
   - Base footprint ($138\text{B INST}$): $8 + 4 + 48 + 138 + 12 = 210\text{ bytes}$.
   - With 32B condition bytecode ($170\text{B INST}$): $8 + 4 + 48 + 170 + 12 = 242\text{ bytes}$.
   - `Music.bank` (27 items, all 210 B): $16 + 27 \times 210 = 5,686\text{ B}$.
   - `SFX.bank` (32 items: 31 at 210 B, 1 at 242 B with condition bytecode): $16 + 31 \times 210 + 242 = 6,768\text{ B}$.
3. **`SPIT` (`LIST SPIS`)**:
   $$\text{Size}(\text{LIST SPIT}) = 8\text{ (header)} + 4\text{ ('SPIT')} + 56\text{ (SPIB)} + 40\text{ (PLST)} + 138\text{ (INST)} + 12\text{ (PRPS)} = 258\text{ bytes}$$
   - `SFX.bank` (2 items, both 258 B): $16 + 2 \times 258 = 532\text{ B}$.
4. **`EVIT` (`LIST EVIS`)**:
   $$\text{Size}(\text{LIST EVIT}) = 8\text{ (header)} + 4\text{ ('EVIT')} + \text{Size}(\text{EVIB}) + \text{Size}(\text{INST}) + \text{Size}(\text{LIST PRPS})$$
   - Base footprint ($46\text{B EVIB} + 138\text{B INST} + 12\text{B PRPS}$): $8 + 4 + 46 + 138 + 12 = 208\text{ bytes}$.
   - **234 B**: $72\text{B EVIB (1 override)} + 138\text{B INST} + 12\text{B PRPS} = 208 + 26 = 234\text{ bytes}$.
   - **240 B**: $46\text{B EVIB} + 170\text{B INST (32B condition bytecode)} + 12\text{B PRPS} = 208 + 32 = 240\text{ bytes}$.
   - **258 B**: $96\text{B EVIB (2 overrides)} + 138\text{B INST} + 12\text{B PRPS} = 208 + 50 = 258\text{ bytes}$.
   - **266 B**: $72\text{B EVIB (1 override)} + 170\text{B INST (32B condition bytecode)} + 12\text{B PRPS} = 208 + 26 + 32 = 266\text{ bytes}$.
   - **290 B**: $46\text{B EVIB} + 138\text{B INST} + 94\text{B PRPS (1 populated PROP)} = 208 + 82 = 290\text{ bytes}$.
   - **Cross-Bank Size Decomposition**:
     - `Music.bank` (14 items: 8 at 208 B, 2 at 234 B, 4 at 258 B):
       $$16 + 8 \times 208 + 2 \times 234 + 4 \times 258 = 16 + 1664 + 468 + 1032 = 3,180\text{ B}$$
     - `SFX.bank` (38 items: 12 at 208 B, 23 at 240 B, 1 at 266 B, 2 at 290 B):
       $$16 + 12 \times 208 + 23 \times 240 + 1 \times 266 + 2 \times 290 = 16 + 2496 + 5520 + 266 + 580 = 8,878\text{ B}$$

> [!NOTE]
> **Uninstantiated Instrument Schemas (`EFIS`, `SLNS`)**:
> `LIST EFIS` (Effect Instruments) and `LIST SLNS` (Silence Instruments) evaluate to strictly **4 bytes** (empty collections) across all reference banks (`Pause.bank`, `Host.bank`, `Music.bank`, `SFX.bank`). Their corresponding child block descriptors (`EffectInstrument_EFIB` and `SilenceInstrument_SLNB`) represent uninstantiated project schemas derived from runtime deserialization routines (`sub_18012A340` and `sub_18012A610`) rather than populated bank payload validation.

---

## 10. Automation Controllers (`CTRS` / `CTRL`)

### 10.1. Controller Block `CTRL` (52 bytes)
Stored inside `LIST CTRS`, `CTRL` connects parameters to target properties:

```cpp
#pragma pack(push, 1)
struct Controller_CTRL {
    FMOD_GUID_RAW controllerGuid;       // [0x00..0x0F] 16-byte unique controller GUID
    FMOD_GUID_RAW targetObjectGuid;     // [0x10..0x1F] Target mixer bus, effect instance, or instrument GUID
    FMOD_GUID_RAW curveGuid;            // [0x20..0x2F] Spline curve GUID in LIST CRVS
    uint32_t      propertyTypeIndex;    // [0x30..0x33] Target property index (read by sub_180125B30)
};
#pragma pack(pop)
static_assert(sizeof(Controller_CTRL) == 52, "Controller_CTRL must be 52 bytes");
```

> [!NOTE]
> **Complete Automation Resolution Chain**:
> `CTRL` does not store the game parameter GUID directly. Instead, `CTRL` links `targetObjectGuid` and `propertyTypeIndex` to `curveGuid`. The associated driving game parameter is bound via the parameter model container (`LIST PMLS`):
> $$\text{GameParameter (PRMS)} \longleftarrow \text{PMLB.parameterGuid} \longleftrightarrow \text{PMLB.modelGuid} \longrightarrow \text{CTRO (curveGuid)} \longleftrightarrow \text{CURV (knots)} \longleftarrow \text{CTRL (targetObjectGuid, propertyTypeIndex)}$$

### 10.2. Property Index Encoding (`propertyTypeIndex`)
FMOD Studio uses a dual scheme for `propertyTypeIndex`:
1. **Core Built-in Properties (`propertyTypeIndex < 1000`)**:
   - `0`: Gain / Volume (dB)
   - `1`: Pitch (semitones)
   - `2`: Lowpass Filter Frequency
   - `3`: Highpass Filter Frequency
   - `4`: 3D Pan / Position
   - `5`: Wet Level / Send Level
2. **DSP Plugin Parameters (`propertyTypeIndex >= 1000`)**:
   - The index is computed as `1000 + pluginParameterIndex`.
   - For example, `1001` targets parameter index `1` of the target DSP plugin instance.

---

## 11. Modulators (`MODS` / `MODU` / `MODB`)

Stored in `LIST MODS`, modulators dynamically alter parameters at runtime. Deserialized via `sub_180137FD0` and `sub_180136440`:

```cpp
#pragma pack(push, 1)
struct Modulator_MODB {
    FMOD_GUID_RAW modulatorGuid;        // [0x00..0x0F] 16-byte modulator GUID
    FMOD_GUID_RAW targetPropertyGuid;   // [0x10..0x1F] Target parameter or property GUID
    uint32_t      modulatorType;        // [0x20..0x23] Modulator type enum (0..5)
};
#pragma pack(pop)
static_assert(sizeof(Modulator_MODB) == 36, "Modulator_MODB must be 36 bytes");
```

### Modulator Types and Payload Footprints (`jpt_18013689D`):
- **Type 0 (AHDSR)**: 32 bytes payload (`sub_18012A0E0` deserializes initialValue, attackTime, holdTime, decayTime, sustainLevel, releaseTime, shape curve).
- **Type 1 (Randomizer)**: 20 bytes payload (`sub_180125D10` deserializes randomRange, distribution mode [0=Uniform, 1=Gaussian], and flags).
- **Type 2 (Autopitch)**: 24 bytes payload (`sub_18012B160` deserializes rootPitch, minPitch, maxPitch, and speed thresholds).
- **Type 3 (Sidechain)**: 24 bytes payload (`sub_180125C20` deserializes `16B sidechainBusGuid` + `float attackTime` + `float releaseTime`).
- **Type 4 (Expression)**: Variable length expression operand tokens / evaluation bytecode.
- **Type 5 (LFO)**: 32 bytes payload (`sub_18012ED50` deserializes waveformShape [0=Sine, 1=Triangle, 2=Saw, 3=Square], frequency, phase, depth, and sync flags).

---

## 12. Mixer Snapshots (`SNAS` / `SNAP` / `SNAB`)

Inside `LIST SNAS`, snapshots store mixer state overrides:

```cpp
#pragma pack(push, 1)
struct Snapshot_SNAB {
    FMOD_GUID_RAW snapshotGuid;         // [0x00..0x0F] 16-byte snapshot GUID (read by sub_180127360)
    uint32_t      behaviorFlags;        // [0x10..0x13]
    uint16_t      numOverrides;         // [0x14..0x15] Number of overridden properties (2-byte count, sub_18012E771)
    // Followed by SnapshotPropertyOverride overrides[numOverrides]
};
#pragma pack(pop)

#pragma pack(push, 1)
struct SnapshotPropertyOverride {
    FMOD_GUID_RAW targetBusGuid;        // [0x00..0x0F] 16-byte target mixer bus GUID
    uint32_t      propertyIndex;        // [0x10..0x13] Target property (volume, pitch, cutoff)
    float         value;                // [0x14..0x17] Overridden target value
    float         blendWeight;          // [0x18..0x1B] Interpolation blend weight
};
#pragma pack(pop)
static_assert(sizeof(SnapshotPropertyOverride) == 28, "SnapshotPropertyOverride must be 28 bytes");
```

---

## 13. Auxiliary Chunks (`SNDH`, `HASH`, `BNKI`, `PLAT`, `LWVS`)

### 13.1. Sound Stream Placement Header `SNDH` (`sub_1801578B0`)
Atomic 12-byte chunk defining the byte placement and payload size of the external or embedded audio container:

```cpp
#pragma pack(push, 1)
struct SoundHeader_SNDH {
    uint16_t formatVersion;             // [0x00..0x01] Audio container format revision (always 3)
    uint16_t platform;                  // [0x02..0x03] Target audio platform enum (e.g. 8 = PC Windows)
    uint32_t sndOffset;                 // [0x04..0x07] Absolute file offset directly to 'FSB5' magic header
    uint32_t sndSize;                   // [0x08..0x0B] Byte size of the FSB5 container payload
};
#pragma pack(pop)
static_assert(sizeof(SoundHeader_SNDH) == 12, "SoundHeader_SNDH must be exactly 12 bytes");
```

> [!NOTE]
> **Byte Offset Target & Numerical Example (`Pause.bank`)**:
> - `sndOffset` points **directly to the 16-byte aligned `'FSB5'` magic header**, not to the outer `'SND '` RIFF chunk header.
> - **In `Pause.bank` (Total file size: 62,368 bytes / `0xF3A0`)**:
>   - Outer `'SND '` RIFF chunk begins at offset `0x5A48` (4-byte tag `'SND '` + 4-byte size `0x00009950` = 39,248 bytes).
>   - Alignment padding: 16 null bytes from `0x5A50` to `0x5A60`.
>   - At offset `0x5A60`: `'FSB5'` magic header begins.
>   - `SNDH.sndOffset` is exactly `0x5A60` (`60 5a 00 00`).
>   - `SNDH.sndSize` is exactly `0x9940` (39,232 bytes, the precise size of the FSB5 payload).
>   - `sndOffset + sndSize = 0x5A60 + 0x9940 = 62,368` bytes, terminating exactly at the end of the bank file.

### 13.2. GUID Hash Table `HASH` (`sub_180138650`)
Provides $O(1)$ runtime entity lookup by GUID. Deserialized as a 4-byte header followed by an array of 20-byte entries:

```cpp
#pragma pack(push, 1)
struct GuidHashEntry_HASH {
    FMOD_GUID_RAW guid;                 // [0x00..0x0F] 16-byte entity GUID
    uint32_t      targetOffsetOrIndex;  // [0x10..0x13] Entity index or chunk byte offset
};

struct GuidHashTable_HASH {
    uint16_t countHeader;               // [0x00..0x01] (entryCount << 1) | 1 (shifted varint count)
    uint16_t elementSize;               // [0x02..0x03] Element size (always 0x0014 = 20 bytes)
    // Followed by GuidHashEntry_HASH entries[entryCount]
};
#pragma pack(pop)
static_assert(sizeof(GuidHashEntry_HASH) == 20, "GuidHashEntry_HASH must be exactly 20 bytes");
```

- **Footprint Formula**: $\text{Size}(\text{HASH}) = 4 + 20 \cdot N$ bytes.
  - `Pause.bank`: $4 + 20 \times 31 = 624\text{ B}$.
  - `Host.bank`: $4 + 20 \times 487 = 9,744\text{ B}$.
  - `Music.bank`: $4 + 20 \times 110 = 2,204\text{ B}$.
  - `SFX.bank`: $4 + 20 \times 608 = 12,164\text{ B}$.

### 13.3. Bank Information Header `BNKI` (`loc_18012D738`)
First atomic chunk in `LIST PROJ`:

```cpp
#pragma pack(push, 1)
struct BankInfo_BNKI {
    FMOD_GUID_RAW bankGuid;             // [0x00..0x0F] 16-byte unique Bank GUID
    uint32_t      buildFlags;           // [0x10..0x13] Build flags
    uint32_t      reserved;             // [0x14..0x17]
    uint32_t      itemCount;            // [0x18..0x1B] Compiled entity count (e.g. 0x13 in Pause, 0xF3 in Host)
    uint32_t      projectRevision;      // [0x1C..0x1F] Project asset revision (always 0x07)
};
#pragma pack(pop)
static_assert(sizeof(BankInfo_BNKI) == 32, "BankInfo_BNKI must be 32 bytes");
```

> [!NOTE]
> **`FMT.formatVersion` vs `BNKI`**:
> - `FMT.formatVersion` (8-byte chunk `FMT `, payload `0x0000008E 0x0000008C`) specifies the **binary bank schema specification version** (`0x8E` / 142 in FMOD Studio 2.02.x) that governs chunk layouts and conditional deserialization branches across the engine.
> - `BNKI` stores project-level build metadata (bank GUID, asset build flags, entity count, and revision marker).

### 13.4. Target Platform `PLAT`
Identifies the compilation target platform (16-byte platform GUID):

```cpp
#pragma pack(push, 1)
struct PlatformInfo_PLAT {
    FMOD_GUID_RAW platformGuid;         // [0x00..0x0F] 16-byte target platform GUID (e.g. PC x64 desktop)
};
#pragma pack(pop)
static_assert(sizeof(PlatformInfo_PLAT) == 16, "PlatformInfo_PLAT must be 16 bytes");
```

> [!NOTE]
> `PLAT` is compiled only into `Host.bank` (16 bytes payload) because global platform targets are defined once in the master host bank and are not duplicated into auxiliary sound banks.

### 13.5. Low-Resolution Waveforms `LWVS`
Pre-computed display peak data cache used by FMOD Studio for rendering overview waveforms:
```cpp
#pragma pack(push, 1)
struct LowResWaveform_LWVB {
    FMOD_GUID_RAW waveformAssetGuid;    // [0x00..0x0F] 16-byte waveform asset GUID
    uint32_t      peakDataSize;         // [0x10..0x13] Byte size of pre-computed display peak data
    // Followed by min/max peak byte stream
};
#pragma pack(pop)
```

### 13.6. Metadata Extension Lists (`DEL `, `MUTE`, `REFI`)
- **`LIST DEL ` (`0x204C4544`)**: Array of 16-byte GUIDs representing deleted entities (tombstones) used for live-update project synchronization.
- **`LIST MUTE` (`0x4554554D`)**: Array of 16-byte GUIDs of muted mixer buses or tracks.
- **`LIST REFI` (`0x49464552`)**: Cross-bank external asset reference table.

---

## 14. FSB5 Bit-Packed Audio Container

### 14.1. Header Format
Starts with the fixed 60-byte (`0x3C`) `FSB5_Header` (see Section 4.1).

### 14.2. Subsound Descriptors (64-bit Bitfield)
Immediately following the header, each subsound is defined by a 64-bit bitfield:

```
Bit  0      (1 bit)  : hasExtraChunks (1 if extra chunks follow, 0 otherwise)
Bits 1..4   (4 bits) : Frequency table index:
                       0 = 4000 Hz,  1 = 8000 Hz,  2 = 11000 Hz, 3 = 11025 Hz,
                       4 = 16000 Hz, 5 = 22050 Hz, 6 = 24000 Hz, 7 = 32000 Hz,
                       8 = 44100 Hz, 9 = 48000 Hz, 10 = 96000 Hz
                       (Indices 11..15 are rejected by FMOD; non-standard rates use index 0 + Extra Chunk Type 2)
Bit  5      (1 bit)  : Channel mode (0 = Mono / 1 channel, 1 = Stereo / 2 channels; multichannel > 2ch uses Extra Chunk Type 1)
Bits 6..33  (28 bits): Data Offset in 16-byte blocks (byteOffset = ((descriptor >> 6) & 0xFFFFFFF) << 4)
Bits 34..63 (30 bits): PCM sample count (total duration in samples = descriptor >> 34)
```

> [!NOTE]
> **Cross-Bank Verification (`Pause.bank`)**:
> In `Pause.bank` (subsound 0 descriptor at offset `0x5A9C` = `0x5A60 + 0x3C`), the little-endian bytes are `33 00 00 00 b0 2e 00 00` (`0x00002EB000000033`):
> - `hasExtraChunks` = 1 (bit 0: `0x33 & 1 = 1`)
> - `frequencyIndex` = 9 (bits 1..4: `(0x33 >> 1) & 0xF = 9` $\rightarrow$ **48000 Hz**)
> - `channelMode` = 1 (bit 5: `(0x33 >> 5) & 1 = 1` $\rightarrow$ **Stereo 2ch**, `channels = channelMode + 1 = 2`)
> - `dataOffset` = 0 (bits 6..33: `((0x00002EB000000033 >> 6) & 0xFFFFFFF) << 4 = 0`)
> - `sampleCount` = 2,988 samples (bits 34..63: `0x00002EB000000033 >> 34 = 2,988`, duration = $2988 / 48000 = 0.06225\text{ s}$)

### 14.3. Extra Sub-Chunks
When `hasExtraChunks == 1`, one or more extra sub-chunks follow immediately after the 64-bit descriptor. Each sub-chunk starts with a 32-bit header:
- **Bit 0 (1 bit)**: `moreChunks` (1 if further extra chunks follow, 0 if last chunk).
- **Bits 1..24 (24 bits)**: `chunkSize` (payload size in bytes).
- **Bits 25..31 (7 bits)**: `chunkType` (extra sub-chunk opcode).

#### Complete Extra Chunk Opcode Table:
- **Type 1 (`0x01`, Channels)**: Multichannel override (`uint8_t channelCount` or `uint32_t channelCount` for 4ch, 5.1, 7.1 surround layouts).
- **Type 2 (`0x02`, Frequency)**: Custom non-standard sample rate (`int32_t frequency` in Hz, overriding standard frequency table).
- **Type 3 (`0x03`, Loop Points)**: Loop start and loop end (`int32_t loopStart`, `int32_t loopEnd` in PCM samples).
- **Type 4 (`0x04`, SFX Metadata)**: Free comment and SFX metadata payload (observed in `Host.bank` [260 B], `Music.bank` [780 B], `SFX.bank` [1040 B]).
- **Type 5 (`0x05`, Sync Cue)**: Segment cue / synchronization marker (32-bit integer).
- **Type 6 (`0x06`, XMA Seek Table)**: XMA packet seek table for Xbox 360 audio streams.
- **Type 7 (`0x07`, DSP Coefficients)**: Nintendo GameCube / Wii DSP ADPCM convolution filter impulse response and predictor coefficients.
- **Type 8 (`0x08`, ATRAC9 Multichannel)**: ATRAC9 4ch+ channel configuration (PS Vita).
- **Type 9 (`0x09`, ATRAC9 Setup)**: ATRAC9 codec initialization data.
- **Type 10 (`0x0A`, XWMA Config)**: XWMA configuration parameters.
- **Type 11 (`0x0B`, Vorbis Setup Packet)**: Vorbis codebook configuration, setup CRC32 (`0xC4C30A29` hash), and seek table packet.
- **Type 13 (`0x0D`, Volume Normalization)**: 4-byte IEEE 754 float storing integrated peak normalization gain scalar (`float volumeNormalization`).
- **Type 14 (`0x0E`, Vorbis Intra-Layers)**: Vorbis intra-layers descriptor for multichannel streams.
- **Type 15 (`0x0F`, Opus Size)**: Opus packet frame data size.

---

## 15. Cross-Bank Chunk Structure Matrix

Reference matrix showing the distribution and byte footprint of chunks across production banks:

> [!NOTE]
> Values in this matrix represent the chunk's payload `size` field in the RIFF header (excluding the 8-byte `'RIFF'` or `'LIST'` header itself). Empty collections have a payload size of strictly **4 bytes** (the 4-byte FourCC type), while populated collections ($N \ge 1$) begin with the 12-byte `LCNT` sub-chunk.

| Chunk Tag | Description | Pause.bank | Host.bank | Music.bank | SFX.bank |
|---|---|---|---|---|---|
| `BNKI` | Bank identity (32B) | 32 B | 32 B | 32 B | 32 B |
| `FMT ` | Format version | 8 B | 8 B | 8 B | 8 B |
| `HASH` | GUID hash table | 624 B | 9,744 B | 2,204 B | 12,164 B |
| `PLAT` | Target platform ID | - | 16 B | - | - |
| `SNDH` | Sound headers | 12 B | 12 B | 12 B | 12 B |
| `SND ` | FSB5 audio container | 39,248 B | 1,602,186 B | 57,997,814 B | 14,542,206 B |
| `WAVS` | Waveform descriptors | 472 B | 8,186 B | 3,018 B | 13,962 B |
| `MBSS` | Master mixer bus | 2,866 B | 31,388 B | 6,858 B | 34,072 B |
| `GBSS` | Group mixer buses | 2,410 B | 32,712 B | 7,978 B | 46,210 B |
| `IBSS` | Input mixer buses | 2,410 B | 30,130 B | 3,796 B | 30,130 B |
| `RBSS` | Return mixer buses | 4 B | 2,120 B | 4 B | 4 B |
| `PRTS` | Port buses | 4 B | 4 B | 4 B | 4 B |
| `VCAS` | VCA channels | 4 B | 448 B | 4 B | 4 B |
| `BEFX` | Bus DSP effect chains | 4,766 B | 1,042,264 B | 3,678 B | 1,863,908 B |
| `PEFX` | Plugin effects | 4 B | 4 B | 4 B | 4 B |
| `SEFX` | Send effects | 4 B | 1,822 B | 226 B | 1,486 B |
| `SCFX` | Sidechain inputs | 4 B | 188 B | 4 B | 4 B |
| `PRMS` | Game parameters | 4 B | 1,154 B | 586 B | 1,344 B |
| `PMLS` | Parameter models | 4 B | 832 B | 6,712 B | 7,720 B |
| `MPGS` | Macro parameter groups | 4 B | 304 B | 416 B | 932 B |
| `CRVS` | Automation splines | 4 B | 624 B | 27,272 B | 14,164 B |
| `CTRS` | Controllers | 4 B | 496 B | 4,336 B | 10,036 B |
| `MODS` | Parameter modulators | 4 B | 4 B | 3,188 B | 4,130 B |
| `SNAS` | Mixer snapshots | 4 B | 894 B | 138 B | 708 B |
| `EVTS` | Sound events | 3,436 B | 43,684 B | 8,002 B | 47,188 B |
| `TLNS` | Event timelines | 1,802 B | 22,898 B | 43,990 B | 28,768 B |
| `LWVS` | Low-res waveforms | 4 B | 4 B | 4 B | 4 B |
| `WAIS` | Single sound modules | 3,854 B | 44,118 B | 24,520 B | 108,500 B |
| `MUIS` | Multi-sound modules | 4 B | 4 B | 1,998 B | 26,674 B |
| `SPIS` | Scatterer instruments | 4 B | 4 B | 4 B | 532 B |
| `PRIS` | Programmer instruments | 4 B | 3,964 B | 4 B | 4 B |
| `EVIS` | Event instruments | 4 B | 4 B | 3,180 B | 8,878 B |
| `CMDS` | Command instruments | 4 B | 4 B | 5,686 B | 6,768 B |
| `EFIS` | Effect instruments | 4 B | 4 B | 4 B | 4 B |
| `SLNS` | Silence instruments | 4 B | 4 B | 4 B | 4 B |

### 15.1. Waveform Container Overhead Formula (`WAVS`)
The byte size of `LIST WAVS` across banks follows the exact linear formula for $N \ge 1$:
$$\text{Size}(\text{LIST WAVS}) = 16 + 38 \cdot N$$
where $N$ is the number of sound assets / waveforms in the bank (when $N = 0$, the container size is strictly 4 B).
- **Overhead (16 B)**: 4-byte list type `'WAVS'` + 12-byte `LCNT` sub-chunk (8-byte RIFF header `LCNT` [size=4] + 4-byte `uint32_t count`).
- **Item Size (38 B)**: Each entry consists of an 8-byte RIFF chunk header (`'WAV '` [size=30]) + 30-byte `SampleDescriptor_WAV` payload.
- **Cross-Bank Verification**:
  - `Pause.bank`: $16 + 38 \times 12 = 472\text{ B}$ (exact 12 waveforms).
  - `Host.bank`: $16 + 38 \times 215 = 8,186\text{ B}$ (exact 215 waveforms).
  - `Music.bank`: $16 + 38 \times 79 = 3,018\text{ B}$ (exact 79 waveforms).
  - `SFX.bank`: $16 + 38 \times 367 = 13,962\text{ B}$ (exact 367 waveforms).

---

## 16. End-to-End Resolution: From Path to Audio Sample

The full lookup chain from an abstract URI path in `strings.bank` down to physical PCM audio in `FSB5` operates across six discrete links:

```
[strings.bank] (URI Path -> Event GUID via Radix Trie)
      │
      ▼
[LIST EVTS / EVTB] (Event GUID -> timelineGuid & outputBusGuid)
      │
      ▼
[LIST TLNS / TMLN / TLNB] (timelineGuid -> Array of Track GUIDs)
      │
      ▼
[LIST WAIS / WAIT / INST] (Track GUID == INST.trackGuid -> soundInstanceGuid)
      │
      ▼
[WAIB] (soundInstanceGuid -> waveformAssetGuid)
      │
      ▼
[LIST WAVS / WAV ] (waveformAssetGuid -> sampleIndexPacked >> 16)
      │
      ▼
[FSB5 / SND ] (Sample Index -> Subsound Header & Vorbis/PCM Audio Stream)
```

### Resolution Steps:
1. **Link 1 (`strings.bank` -> Event GUID)**:
   - Input path (e.g. `event:/SFX/MenuOn`) traverses the Radix Trie to retrieve the event's `FMOD_GUID`.
2. **Link 2 (`EVTB` -> Timeline GUID)**:
   - Look up the `EVTB` record in `LIST EVTS` matching the event GUID to extract `timelineGuid`.
3. **Link 3 (`EVTB` / `TLNB` -> Track GUIDs)**:
   - Tracks can be resolved directly from `EventData_EVTB` (via `masterTrackGuid` and `eventTracks[i].trackGuid`).
   - Alternatively, traversing via `Event.timelineGuid` to `LIST TLNS` -> `TMLN` -> `TLNB` yields `timelineTracks[i].trackGuid` (which reference the exact same track GUIDs, while additionally defining track indices and layout flags).
   - These track GUIDs form the key for matching instruments in Step 4.
4. **Link 4 (`INST` / `WAIB` -> Waveform Asset GUID)**:
   - In `LIST WAIS`, scan `WAIT` records whose base block `INST.trackGuid` matches one of the timeline's track GUIDs.
   - Read the accompanying `WAIB` payload to extract `waveformAssetGuid`.
5. **Link 5 (`WAV ` -> Sample Index)**:
   - In `LIST WAVS`, locate the `WAV ` chunk matching `waveformAssetGuid`.
   - Read `sampleIndexPacked` at offset 20 (`0x14`). Compute `sampleIndex = sampleIndexPacked >> 16`.
6. **Link 6 (`FSB5` -> PCM Audio Stream)**:
   - Look up subsound entry `sampleIndex` in the FSB5 container within chunk `SND `.
   - Decompress Vorbis / FADPCM stream into multichannel PCM.

---

## 17. Related Specifications and Schemas

- **FMOD Studio Project XML Model Specification**: For XML metamodel structural rules, bidirectional relationship invariants, and project authoring constraints (`Studio.02.03.00.metamodel.xml`), see [PROJECT_XML_SPECIFICATION.md](PROJECT_XML_SPECIFICATION.md).
- **Strings Bank Specification**: For detailed Radix Trie string table binary layouts, alignment padding, and lookup algorithms, see [STRINGS_BANK_SPECIFICATION.md](STRINGS_BANK_SPECIFICATION.md).

---

## Appendix: Reverse-Engineered Routines & RVA Reference Table

The following reverse-engineered routines in `fmodstudioL.dll` govern the parsing and validation of FMOD Studio banks:

| Function Name / RVA | Target Structure | Description | Validation Status |
|---|---|---|---|
| `sub_18012D6E0` | `BankReader::serializeBank` | Master chunk dispatcher iterating through `LIST PROJ` | Disassembled & Verified |
| `sub_180135C90` | `EventData_EVTB` | Sound event descriptor, 3D settings, event tracks array | Disassembled & Verified |
| `sub_18012EF30` | `Timeline_TLNB` | Event timeline duration, tempo markers, track list | Disassembled & Verified |
| `sub_180140310` | `EventTrack` | Reads 20-byte event track records (`trackGuid` + `volume`) | Disassembled & Verified |
| `sub_18013ECE0` | `TimelineTrack` | Reads shifted varint track list in `TLNB` | Disassembled & Verified |
| `sub_1801274A0` | Condition VM (§8.3) | Bytecode stack machine for timeline marker transition conditions (opcodes 0x00..0x30, 0xFF) | Disassembled & Verified (Full Opcodes) |
| `sub_18012B780` | `SoundInstance_INST` | Base block for all audio instruments (123 B / 130 B / 162 B) | Disassembled & Verified |
| `sub_18012B010` | `Property_PROP` (§9.2.1) | Sound property overrides deserializer inside `LIST PRPS` | Disassembled & Verified |
| `sub_18012AF20` | `MultiSoundPlaylist_PLST` (§9.3) | Playlist item list deserializer inside `MUIS` and `SPIS` | Disassembled & Verified |
| `sub_1801352A0` | `toFMOD` (§5.3) | Maps disk effect type (0..37) to runtime `FMOD_DSP_TYPE` via jump table | Disassembled & Verified |
| `sub_1801578B0` | `SoundHeader_SNDH` | External audio stream placement and size header | Disassembled & Verified |
| `sub_180138650` | `GuidHashTable_HASH` | Deserializes $O(1)$ GUID lookup hash table | Disassembled & Verified |
| `sub_180137FD0` | `Modulator_MODB` (§11) | Deserializes modulator headers and target property GUIDs | Disassembled & Verified |
| `sub_180136440` | Modulator Payloads (§11) | Deserializes type-specific parameters: 0=AHDSR (32B), 1=Randomizer (20B), 2=Autopitch (24B), 3=Sidechain (24B), 4=Expression, 5=LFO (32B) | Disassembled & Verified (All 6 Types) |
| `sub_180125B30` | `PropertyIndexParser` (§10.2) | Decodes `propertyTypeIndex` (0..5 core properties, $\ge 1000$ DSP parameters) | Disassembled & Verified |
| `sub_1801461D0` | `ReturnBus_RBSB` | Auxiliary return mixer bus deserializer | Disassembled & Verified |
| `sub_18012E320` | `SendEffect_SEFB` | Auxiliary send plugin effect deserializer | Disassembled & Verified |
| `sub_18012E0B0` | `Scatterer_SPIB` (§9.4) | Scatterer instrument spawn rate, intervals, and scatter radius | Disassembled & Verified |
| `sub_18012B380` | `EventInstrument_EVIB` (§9.4) | Event instrument referenced GUID and initial parameter overrides | Disassembled & Verified |
| `sub_18012ABCA` | `CommandInstrument_CMDB` (§9.4) | Command instrument opcode, target GUID, and versioned value operand | Disassembled & Verified |
| `sub_180132BD0` | `Programmer_PRIB` (§9.4) | Programmer sound callback hints and format mode | Disassembled & Verified |
| `sub_18012C590` | `Parameter_PRMB` (§7.1) | Game parameter range, seek speed, and inline ASCII name string | Disassembled & Verified |
