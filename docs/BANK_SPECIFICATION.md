# FMOD Studio Binary Bank Format Specification (.bank, FMOD 2.0x / 2.03+)

## 1. Introduction and Architecture

In FMOD Studio 2.x, `*.bank` files (e.g. `Master.bank`, `Host.bank`, `SFX.bank`, `Music.bank`) are composite binary package containers. While `*.strings.bank` files store prefix trees (Radix Trie) mapping URI paths to 128-bit GUIDs, audio bank files store the compiled runtime project data:
1. The mixer bus hierarchy, signal routing graphs, and DSP plugin parameter states.
2. Game parameter definitions and Hermite spline automation curves.
3. Event timelines, transition markers, trigger logic, and condition bytecode.
4. Audio streams packaged inside an **FSB5** (FMOD Sound Bank v5) container using Vorbis (Codec ID 15) or FADPCM compression.

### Reverse Engineering Methodology
This specification is derived from disassembly of the FMOD Studio runtime engine (`fmodstudioL.dll`, version 2.02.xx / 2.03.xx) and FMOD Core API (`fmodL.dll`), cross-verified against production bank binaries.

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
|   ├── PMLS : Parameter markers and loops                                |
|   ├── PRMS : Game parameters                                            |
|   ├── CTRS : Transition cues and automation controllers                 |
|   ├── CRVS : Automation spline curves                                   |
|   ├── MPGS : Macro pages                                                |
|   ├── MUIS, SPIS, PRIS, EVIS, WAIS, EFIS, CMDS, SLNS (Sound modules)    |
|   ├── LWVS : Local waveforms (if version >= 0x51)                       |
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
        res = serializeDeletedList(stream, &bank->deletedList, 0x204C4544 /* ' DEL' */);
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

### 5.1. Bus Effect Descriptor `BEFF`
Inside `LIST BEFX`, each effect instance is encapsulated in a `BEFF` chunk:

```cpp
#pragma pack(push, 1)
struct BusEffectHeader_BEFB {
    FMOD_GUID_RAW effectGuid;           // [0x00..0x0F] 16-byte effect instance GUID
    FMOD_GUID_RAW ownerBusGuid;         // [0x10..0x1F] 16-byte parent mixer bus GUID
    uint32_t      dspType;              // [0x20..0x23] Disk DSP Type enum (0..34)
    uint16_t      flags;                // [0x24..0x25] Bypass and processing flags
};
#pragma pack(pop)
static_assert(sizeof(BusEffectHeader_BEFB) == 38, "BusEffectHeader_BEFB must be 38 bytes");
```

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
        case 3: // Data buffer (variable-length blob)
            uint32_t dataLength = readShiftedVarint(stream);
            param->data.resize(dataLength);
            stream->read(param->data.data(), dataLength);
            break;
        default:
            return FMOD_ERR_FORMAT;
    }
    return FMOD_OK;
}
```

### 5.3. Disk DSP Type to Runtime `FMOD_DSP_TYPE` Translation Table (`sub_1801352A0`)
In FMOD Studio binaries, disk DSP indices in `BEFB.dspType` do not match the runtime `FMOD_DSP_TYPE` enumeration 1:1. Function `sub_1801352A0` translates them using an internal switch table (`jpt_180135306`):

| Disk Type (`dspType`) | Runtime `FMOD_DSP_TYPE` | FMOD Studio XML Class | Description |
|---|---|---|---|
| `0` | `FMOD_DSP_TYPE_MIXER` (0) | `MixerBusFader` | Channel fader |
| `1` | `FMOD_DSP_TYPE_OSCILLATOR` (1) | `OscillatorEffect` | Test tone generator |
| `2` | `FMOD_DSP_TYPE_LOWPASS` (2) | `LowpassEffect` | Standard lowpass filter |
| `3` | `FMOD_DSP_TYPE_ITLOWPASS` (3) | `ITLowpassEffect` | Impulse Tracker lowpass |
| `4` | `FMOD_DSP_TYPE_HIGHPASS` (4) | `HighpassEffect` | Standard highpass filter |
| `5` | `FMOD_DSP_TYPE_ECHO` (5) | `EchoEffect` | Basic echo |
| `6` | `FMOD_DSP_TYPE_FADER` (6) | `MixerBusFader` | Bus gain fader |
| `7` | `FMOD_DSP_TYPE_FLANGE` (7) | `FlangerEffect` | Flanger |
| `8` | `FMOD_DSP_TYPE_DISTORTION` (8) | `DistortionEffect` | Distortion |
| `9` | `FMOD_DSP_TYPE_NORMALIZE` (9) | `NormalizeEffect` | Dynamics normalizer |
| `10` | `FMOD_DSP_TYPE_LIMITER` (10) | `LimiterEffect` | Hard limiter |
| `11` | `FMOD_DSP_TYPE_PARAMEQ` (11) | `ParametricEQEffect` | Parametric equalizer |
| `12` | `FMOD_DSP_TYPE_PITCHSHIFT` (12) | `PitchShifterEffect` | Pitch shifter |
| `13` | `FMOD_DSP_TYPE_CHORUS` (13) | `ChorusEffect` | Chorus |
| `14` | `FMOD_DSP_TYPE_VSTPLUGIN` (14) | `PluginEffect` | VST/AU external plugin |
| `15` | `FMOD_DSP_TYPE_WINAMPPLUGIN` (15)| `PluginEffect` | Legacy plugin wrapper |
| `16` | `FMOD_DSP_TYPE_ITECHO` (16) | `ITEchoEffect` | IT Echo |
| `17` | `FMOD_DSP_TYPE_COMPRESSOR` (17) | `CompressorEffect` | Dynamics compressor |
| `18` | `FMOD_DSP_TYPE_SFXREVERB` (18) | `SFXReverbEffect` | Algorithmic reverb |
| `19` | `FMOD_DSP_TYPE_LOWPASS_SIMPLE` (19)| `LowpassSimpleEffect` | Simple 6dB/oct lowpass |
| `20` | `FMOD_DSP_TYPE_DELAY` (20) | `DelayEffect` | Multitap delay |
| `21` | `FMOD_DSP_TYPE_TREMOLO` (21) | `TremoloEffect` | Tremolo |
| `24` | `FMOD_DSP_TYPE_SEND` (24) | `MixerSend` | Bus send tap |
| `25` | `FMOD_DSP_TYPE_RETURN` (25) | `MixerReturn` | Return bus receiver |
| `26` | `FMOD_DSP_TYPE_HIGHPASS_SIMPLE` (26)| `HighpassSimpleEffect` | Simple 6dB/oct highpass |
| `27` | `FMOD_DSP_TYPE_PAN` (27) | `SpatialiserEffect` | Panner / 3D spatializer |
| `28` | `FMOD_DSP_TYPE_THREE_EQ` (28) | `ThreeEQEffect` | 3-band DJ equalizer |
| `30` | `FMOD_DSP_TYPE_LOUDNESS_METER` (30)| `LoudnessMeter` | LUFS/EBU R128 meter |
| `32` | `FMOD_DSP_TYPE_CONVOLUTIONREVERB` (32)| `ConvolutionReverbEffect`| Impulse response reverb |
| `33` | `FMOD_DSP_TYPE_CHANNELMIX` (33) | `ChannelMixEffect` | Matrix channel mixer |
| `34` | `FMOD_DSP_TYPE_MULTIBAND_EQ` (34)| `MultibandEqEffect` | 5-band multiband EQ |

---

## 6. Mixer Routing and Bus Hierarchy (`MBSS`, `GBSS`, `BUS `, `VCAS`, `RBSS`, `SEFX`, `SCFX`)

### 6.1. Mixer Group Buses (`GBSS` / `GBSB` / `BUS `)
Master and group buses are deserialized in `sub_18012A670`:

```cpp
#pragma pack(push, 1)
struct GroupBusHeader_GBSB {
    FMOD_GUID_RAW busGuid;              // [0x00..0x0F] 16-byte bus GUID
    FMOD_GUID_RAW parentBusGuid;        // [0x10..0x1F] Parent bus GUID (0 if root)
    uint16_t      busIndex;             // [0x20..0x21] Subsystem bus index
};
#pragma pack(pop)
static_assert(sizeof(GroupBusHeader_GBSB) == 34, "GroupBusHeader_GBSB must be 34 bytes");
```

Immediately following `GBSB`, the atomic `BUS ` chunk defines routing parameters and effect attachments:

```cpp
#pragma pack(push, 1)
struct BusCommonBlock_BUS {
    float    volume;                    // [0x00..0x03] Gain level (default 1.0f)
    float    pitch;                     // [0x04..0x07] Pitch adjustment (default 1.0f)
    uint32_t channelLayout;             // [0x08..0x0B] Channel layout bitfield / speaker mode
    uint16_t maxInstances;              // [0x0C..0x0D] Voice stealing limit
    uint16_t maxVoices;                 // [0x0E..0x0F] Max concurrent voices
    uint32_t flags;                     // [0x10..0x13] Mute, solo, bypass flags
    uint32_t numEffects;                // [0x14..0x17] Number of attached DSP effects
    // Followed by FMOD_GUID_RAW effectGuids[numEffects]
};
#pragma pack(pop)
```

### 6.2. VCA Channels (`VCAS` / `VCAB` / `sub_180137120`)
Inside `LIST VCAS`, each VCA is stored in a 42-byte atomic `VCAB` chunk:

```cpp
#pragma pack(push, 1)
struct VcaBlock_VCAB {
    FMOD_GUID_RAW vcaGuid;              // [0x00..0x0F] 16-byte unique VCA GUID
    float         volume;               // [0x10..0x13] Linear gain (default 1.0f = 0 dB)
    uint32_t      flags;                // [0x14..0x17] Solo / Mute bitfield
    uint32_t      numTargetBuses;       // [0x18..0x1B] Number of controlled mixer buses
    uint32_t      reserved;             // [0x1C..0x1F]
    uint16_t      vcaIndex;             // [0x20..0x21] Index in runtime VCA array
    uint64_t      padding;              // [0x22..0x29]
};
#pragma pack(pop)
static_assert(sizeof(VcaBlock_VCAB) == 42, "VcaBlock_VCAB must be 42 bytes");
```

### 6.3. Return Buses (`RBSS` / `RBSB` / `sub_18012A670`)
Return buses receiving signals from auxiliary sends are defined in `RBSB` (42 bytes):

```cpp
#pragma pack(push, 1)
struct ReturnBus_RBSB {
    FMOD_GUID_RAW returnBusGuid;        // [0x00..0x0F] 16-byte return bus GUID
    FMOD_GUID_RAW parentMasterGuid;     // [0x10..0x1F] Target master bus GUID
    float         volume;               // [0x20..0x23] Return gain (default 1.0f)
    uint32_t      flags;                // [0x24..0x27]
    uint16_t      busIndex;             // [0x28..0x29]
};
#pragma pack(pop)
```

### 6.4. Auxiliary Sends (`SEFX` / `SEFB` / `sub_18012E320`)
Send plugin blocks in `SEFB` connect a channel's output to a return bus:

```cpp
#pragma pack(push, 1)
struct SendEffect_SEFB {
    FMOD_GUID_RAW sendEffectGuid;       // [0x00..0x0F] 16-byte send effect GUID
    FMOD_GUID_RAW targetReturnBusGuid;  // [0x10..0x1F] Destination MixerReturn GUID
    float         sendLevel;            // [0x20..0x23] Send level (default 1.0f = 0 dB)
    uint8_t       bypass;               // [0x24]       Bypass flag (1 = bypassed)
};
#pragma pack(pop)
static_assert(sizeof(SendEffect_SEFB) == 37, "SendEffect_SEFB must be 37 bytes");
```

---

## 7. Parameters and Automation Curves (`PRMS`, `CRVS`, `PMLS`, `MPGS`)

### 7.1. Game Parameters (`PRMS` / `PRMB` / `sub_18012C590`)
Game parameters are stored as 52-byte atomic `PRMB` records:

```cpp
#pragma pack(push, 1)
struct Parameter_PRMB {
    FMOD_GUID_RAW paramGuid;            // [0x00..0x0F] 16-byte parameter GUID
    float         minimum;              // [0x10..0x13] Minimum range value
    float         maximum;              // [0x14..0x17] Maximum range value
    float         defaultValue;         // [0x18..0x1B] Default initial value
    uint32_t      paramType;            // [0x1C..0x1F] 0 = Continuous, 1 = Discrete, 2 = Labeled
    uint32_t      flags;                // [0x20..0x23] Loop, global, velocity flags
    float         seekSpeed;            // [0x24..0x27] Seek speed rate
    float         velocity;             // [0x28..0x2B] Autopitch velocity
    uint32_t      enumerationIndex;     // [0x2C..0x2F] Enum label array index
    uint16_t      runtimeIndex;         // [0x30..0x31] Runtime lookup index
};
#pragma pack(pop)
static_assert(sizeof(Parameter_PRMB) == 52, "Parameter_PRMB must be 52 bytes");
```

### 7.2. Hermite Automation Splines (`CRVS` / `CURV` / `sub_18013FD60`)
Automation curves in `CURV` chunks use cubic Hermite splines. Each spline knot occupies exactly 16 bytes:

```cpp
#pragma pack(push, 1)
struct SplineKnot_CURV {
    float x;                            // [0x00..0x03] Parameter position (timeline sec or param value)
    float y;                            // [0x04..0x07] Automated property output value
    float tangentIn;                    // [0x08..0x0B] Incoming tangent derivative dy/dx
    float tangentOut;                   // [0x0C..0x0F] Outgoing tangent derivative dy/dx
};
#pragma pack(pop)
static_assert(sizeof(SplineKnot_CURV) == 16, "SplineKnot_CURV must be 16 bytes");
```

---

## 8. Events and Timelines (`EVTS`, `TLNS`, `TRNS`)

### 8.1. Sound Event Descriptor `EVTB` (`sub_180135C90`)
The core structure of a sound event is deserialized in `sub_180135C90`:

```cpp
// [RVA: 0x180135C90]
FMOD_RESULT deserializeEvent(RiffStream *stream, EventData *event) {
    RiffChunk chunk;
    FMOD_RESULT res = stream->openChunk(&chunk, 0x42545645 /* 'EVTB' */, 0 /* atomic */);
    if (res != FMOD_OK) return res;

    // 1. Event GUID (16 bytes)
    stream->read(&event->eventGuid, sizeof(FMOD_GUID_RAW));

    // 2. Timeline GUID (16 bytes)
    stream->read(&event->timelineGuid, sizeof(FMOD_GUID_RAW));

    // 3. Output Bus GUID (16 bytes)
    stream->read(&event->outputBusGuid, sizeof(FMOD_GUID_RAW));

    // 4. Input Bus GUID (16 bytes)
    stream->read(&event->inputBusGuid, sizeof(FMOD_GUID_RAW));

    // 5. Master Track GUID (16 bytes)
    stream->read(&event->masterTrackGuid, sizeof(FMOD_GUID_RAW));

    // 6. Max Instances (uint32_t) and Stealing Behavior (uint32_t)
    stream->read(&event->maxInstances, sizeof(uint32_t));
    stream->read(&event->stealingBehavior, sizeof(uint32_t));

    // 7. Flags (uint8_t)
    uint8_t flags;
    stream->read(&flags, sizeof(uint8_t));
    event->isOneShot = (flags & 1);

    // 8. Priority (uint32_t)
    stream->read(&event->priority, sizeof(uint32_t));

    // 9. 3D Positioning Settings (sub_18013CF90)
    res = serialize3DSettings(stream, &event->settings3D);
    if (res != FMOD_OK) return res;

    // 10. Legacy Subsounds (if version < 0x3C, sub_180153E10)
    if (stream->version() < 0x3C) {
        res = serializeLegacySubsound(stream, event);
        if (res != FMOD_OK) return res;
    }

    // 11. Event Tracks (15-bit shifted varint, sub_180140310)
    uint32_t trackCount = readShiftedVarint(stream); // count = varint >> 1
    for (uint32_t i = 0; i < trackCount; ++i) {
        stream->beginElement();
        res = serializeEventTrack(stream, event, i);
        stream->endElement();
        if (res != FMOD_OK) return res;
    }

    // 12. Parameter Links (15-bit shifted varint, sub_180140590)
    uint32_t paramLinkCount = readShiftedVarint(stream);
    for (uint32_t i = 0; i < paramLinkCount; ++i) {
        stream->beginElement();
        res = serializeEventParameterLink(stream, event, i);
        stream->endElement();
        if (res != FMOD_OK) return res;
    }

    // 13. Version Gates (sub_180135C90):
    if (stream->version() >= 0x30) stream->read(&event->minDistance, sizeof(float));          // sub_180125D10
    if (stream->version() >= 0x34) stream->read(&event->cooldown, sizeof(uint32_t));          // sub_180125B30
    if (stream->version() >= 0x4E) stream->read(&event->maxDistance, sizeof(float));          // sub_180125D10
    if (stream->version() >= 0x61) stream->read(&event->userPropertyFlags, sizeof(uint32_t)); // sub_180125C20
    if (stream->version() >= 0x6B) serializeModelList(stream, &event->userProperties);        // sub_18013B620
    if (stream->version() >= 0x76) serializeModelList(stream, &event->dynamicReferences);     // sub_180139810
    if (stream->version() >= 0x83) serializeModelList(stream, &event->sandboxProperties);     // sub_18013C1C0
    if (stream->version() >= 0x89) {
        stream->read(&event->cutoffFrequency, sizeof(float));                                 // sub_180125D10
        stream->read(&event->panDistance, sizeof(float));                                     // sub_180125D10
    }

    return stream->closeChunk(&chunk);
}
```

### 8.2. Event Timeline Structure `TLNB` (`sub_18012EF30`)
Timelines are parsed by `sub_18012EF30`:
- Encapsulated in composite list `TMLN` containing atomic `TLNB` and transition list `TRNS`.
- Fields read within `TLNB`:
  1. `[r15+10h]`: 16-byte timeline GUID (`sub_180127360`).
  2. If version < `0x6D`: reads 16-byte legacy owner GUID (`sub_180127870`).
  3. `[r15+38h]` and `[r15+48h]`: Duration (`float length`) and tempo/BPM parameters (`sub_18013AFD0`).
  4. If version >= `0x84`: reads track list via `sub_18013ECE0`; otherwise via `sub_180138270`.
  5. `[r15+78h]` and `[r15+88h]`: Marker lists via `sub_180149E70` and `sub_18013DE60`.

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
    FMOD_GUID_RAW trackGuid;            // [0x00..0x0F] 16-byte owner track GUID
    float         volume;               // [0x10..0x13] Gain (default 0.0f dB)
    float         pitch;                // [0x14..0x17] Pitch adjustment (semitones)
    int32_t       flags;                // [0x18..0x1B] Processing flags
    uint32_t      bitfield;             // [0x1C..0x1F]
    float         volumeRandomization;  // [0x20..0x23] Random volume variance
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
    uint32_t      conditionBytecodeSize;// [0x7E..0x81] Condition bytecode size (sub_180127280)
};
#pragma pack(pop)
static_assert(sizeof(SoundInstance_INST) == 130, "SoundInstance_INST must be exactly 130 bytes");
```

### 9.2. Single Sound Instrument `WAIS` / `WAIB`
Inside `LIST WAIS`, each `WAIT` chunk encapsulates an `INST` base block followed by a 32-byte `WAIB` payload:

```cpp
#pragma pack(push, 1)
struct SingleSoundPayload_WAIB {
    FMOD_GUID_RAW soundInstanceGuid;    // [0x00..0x0F] 16-byte unique instrument GUID
    FMOD_GUID_RAW waveformAssetGuid;    // [0x10..0x1F] Waveform descriptor GUID in LIST WAVS
};
#pragma pack(pop)
static_assert(sizeof(SingleSoundPayload_WAIB) == 32, "SingleSoundPayload_WAIB must be 32 bytes");
```

### 9.3. Multi-Sound Instrument `MUIS` / `PLST`
`MUIS` structures play randomized or sequential playlists defined in `PLST`:

```cpp
#pragma pack(push, 1)
struct MultiSoundPlaylist_PLST {
    FMOD_GUID_RAW multiSoundGuid;       // [0x00..0x0F] 16-byte MultiSound GUID
    uint32_t      playMode;             // [0x10..0x13] 0 = Random, 1 = Sequential
    uint32_t      maxPlaybackCount;     // [0x14..0x17] Concurrent instance limit
    uint32_t      numPlaylistItems;     // [0x18..0x1B] Number of sub-instruments
    // Followed by FMOD_GUID_RAW itemGuids[numPlaylistItems]
};
#pragma pack(pop)
```

---

## 10. Automation Controllers (`CTRS` / `CTRL`)

### 10.1. Controller Block `CTRL` (52 bytes)
Stored inside `LIST CTRS`, `CTRL` connects parameters to target properties:

```cpp
#pragma pack(push, 1)
struct Controller_CTRL {
    FMOD_GUID_RAW controllerGuid;       // [0x00..0x0F] 16-byte controller GUID
    FMOD_GUID_RAW targetObjectGuid;     // [0x10..0x1F] Target bus, effect, or instrument GUID
    uint32_t      propertyTypeIndex;    // [0x20..0x23] Target property index
    FMOD_GUID_RAW curveGuid;            // [0x24..0x33] Spline curve GUID in LIST CRVS
};
#pragma pack(pop)
static_assert(sizeof(Controller_CTRL) == 52, "Controller_CTRL must be 52 bytes");
```

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
```

### Modulator Types (`jpt_18013689D`):
- **Type 0 (AHDSR)**: Attack, Hold, Decay, Sustain, Release envelope generator.
- **Type 1 (Randomizer)**: Random variance generator with uniform or Gaussian distribution.
- **Type 2 (Autopitch)**: Adjusts pitch based on vehicle speed/parameter velocity.
- **Type 3 (Sidechain)**: Reads signal amplitude from an auxiliary bus to modulate a property.
- **Type 4 (Expression)**: Mathematical expression evaluator.
- **Type 5 (LFO)**: Low-frequency oscillator with Sine, Triangle, Sawtooth, or Square waveforms.

---

## 12. Mixer Snapshots (`SNAS` / `SNAP` / `SNAB`)

Inside `LIST SNAS`, snapshots store mixer state overrides:

```cpp
#pragma pack(push, 1)
struct Snapshot_SNAB {
    FMOD_GUID_RAW snapshotGuid;         // [0x00..0x0F] 16-byte snapshot GUID
    uint32_t      behaviorFlags;        // [0x10..0x13]
    uint32_t      numOverrides;         // [0x14..0x17] Number of overridden properties
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

## 13. Auxiliary Chunks (`SNDH`, `HASH`, `BNKI`, `PLAT`)

### 13.1. Sound Stream Headers `SNDH` (`sub_1801578B0`)
Atomic chunk storing byte offsets and lengths of FSB5 sample streams within `SND `:

```cpp
#pragma pack(push, 1)
struct SoundHeaderRecord_SNDH {
    uint32_t streamOffset;              // Byte offset from start of SND payload
    uint32_t streamSize;                // Compressed byte size
    uint32_t sampleRate;                // Audio sample rate (e.g. 48000)
    uint16_t numChannels;               // Channel count (1 = Mono, 2 = Stereo, 6 = 5.1)
    uint16_t codecId;                   // Codec identifier (15 = Vorbis)
};
#pragma pack(pop)
```

### 13.2. GUID Hash Table `HASH` (`sub_180138650`)
Provides $O(1)$ runtime entity lookup by GUID:
- Contains uniform bucket counts and hash table buckets.
- If all entries have uniform size, uses array indexing; otherwise reads a secondary offset table.

### 13.3. Bank Information Header `BNKI` (`loc_18012D738`)
First atomic chunk in `LIST PROJ`:

```cpp
#pragma pack(push, 1)
struct BankInfo_BNKI {
    FMOD_GUID_RAW bankGuid;             // [0x00..0x0F] 16-byte Bank GUID
    uint32_t      buildFlags;           // [0x10..0x13]
    uint32_t      formatVersion;        // [0x14..0x17] Internal format revision
    uint32_t      sourceChecksum;       // [0x18..0x1B] Checksum of source project assets
    uint32_t      reserved;             // [0x1C..0x1F]
};
#pragma pack(pop)
static_assert(sizeof(BankInfo_BNKI) == 32, "BankInfo_BNKI must be 32 bytes");
```

### 13.4. Target Platform `PLAT`
Identifies the compilation target platform (GUID indicating PC x64, Xbox, PlayStation, Switch, iOS, or Android).

---

## 14. FSB5 Bit-Packed Audio Container

### 14.1. Header Format
Starts with the fixed 60-byte (`0x3C`) `FSB5_Header` (see Section 4.1).

### 14.2. Subsound Descriptors (64-bit Bitfield)
Immediately following the header, each subsound is defined by a 64-bit bitfield:

```
Bits 0..19   (20 bits): Sample Rate index or direct frequency
Bits 20..21  (2 bits) : Channels index (0=Mono, 1=Stereo, 2=5.1)
Bits 22..49  (28 bits): Data Offset relative to start of sample data block
Bits 50..51  (2 bits) : Loop mode flags
Bits 52..62  (11 bits): Extra chunk flags bitmask
Bit  63      (1 bit)  : More extra chunks follow flag
```

### 14.3. Extra Sub-Chunks
When extra flags are present, additional sub-chunks follow the 64-bit descriptor:
- **Type 1 (Channels)**: Explicit channel count override.
- **Type 2 (Frequency)**: Custom sample rate integer.
- **Type 3 (Loop Points)**: Start and end sample offsets for looping.
- **Type 6 (Vorbis Setup Packet)**: Stores Vorbis codebook configuration (`0xC4C30A29` hash).
- **Type 11 (Volume Normalization)**: Peak amplitude and LUFS integrated loudness metrics.

---

## 15. Cross-Bank Chunk Structure Matrix

Reference matrix showing the distribution and byte footprint of chunks across production banks:

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
| `VCAS` | VCA channels | 4 B | 448 B | 4 B | 4 B |
| `BEFX` | Bus DSP effect chains | 4,766 B | 1,042,264 B | 3,678 B | 1,863,908 B |
| `SEFX` | Send effects | 4 B | 1,822 B | 226 B | 1,486 B |
| `SCFX` | Sidechain inputs | 4 B | 188 B | 4 B | 4 B |
| `PRMS` | Game parameters | 4 B | 1,154 B | 586 B | 1,344 B |
| `PMLS` | Parameter lines | 4 B | 832 B | 6,712 B | 7,720 B |
| `MPGS` | Macro parameter groups | 4 B | 304 B | 416 B | 932 B |
| `CRVS` | Automation splines | 4 B | 624 B | 27,272 B | 14,164 B |
| `CTRS` | Controllers | 4 B | 496 B | 4,336 B | 10,036 B |
| `MODS` | Parameter modulators | 4 B | 4 B | 3,188 B | 4,130 B |
| `SNAS` | Mixer snapshots | 4 B | 894 B | 138 B | 708 B |
| `EVTS` | Sound events | 3,436 B | 43,684 B | 8,002 B | 47,188 B |
| `TLNS` | Event timelines | 1,802 B | 22,898 B | 43,990 B | 28,768 B |
| `WAIS` | Single sound modules | 3,854 B | 44,118 B | 24,520 B | 108,500 B |
| `MUIS` | Multi-sound modules | 4 B | 4 B | 1,998 B | 26,674 B |
| `SPIS` | Scatterer instruments | 4 B | 4 B | 4 B | 532 B |
| `PRIS` | Programmer instruments | 4 B | 3,964 B | 4 B | 4 B |
| `EVIS` | Event instruments | 4 B | 4 B | 3,180 B | 8,878 B |
| `CMDS` | Command instruments | 4 B | 4 B | 5,686 B | 6,768 B |
| `EFIS` | Effect instruments | 4 B | 4 B | 4 B | 4 B |
| `SLNS` | Silence instruments | 4 B | 4 B | 4 B | 4 B |

---

## 16. End-to-End Resolution: From Path to Audio Sample

The full lookup chain from an abstract URI path in `strings.bank` down to physical PCM audio in `FSB5` operates across five discrete links:

```
[strings.bank] (URI Path -> Event GUID via Radix Trie)
      │
      ▼
[LIST EVTS / EVTB] (Event GUID -> Timeline GUID)
      │
      ▼
[LIST WAIS / WAIT / INST] (Timeline GUID == ownerGuid -> Waveform Asset GUID)
      │
      ▼
[LIST WAVS / WAV ] (Waveform Asset GUID -> sampleIndexPacked >> 16)
      │
      ▼
[FSB5 / SND ] (Sample Index -> Name Table / Sample Name & Compressed Audio Data)
```

### Resolution Steps:
1. **Link 1 (`strings.bank` -> Event GUID)**:
   - Input path (e.g. `event:/SFX/MenuOn`) traverses the Radix Trie to retrieve the event's `FMOD_GUID`.
2. **Link 2 (`EVTB` -> Timeline GUID)**:
   - Look up the `EVTB` record matching the event GUID to extract `timelineGuid`.
3. **Link 3 (`INST` -> Waveform GUID)**:
   - In `LIST WAIS`, scan `INST` records for `ownerGuid == timelineGuid`.
   - In the accompanying `WAIB` payload, read `waveformAssetGuid`.
4. **Link 4 (`WAV ` -> Sample Index)**:
   - In `LIST WAVS`, locate the `WAV ` chunk matching `waveformAssetGuid`.
   - Read `sampleIndexPacked` at offset 20 (`0x14`). Compute `sampleIndex = sampleIndexPacked >> 16`.
5. **Link 5 (`FSB5` -> PCM Audio Stream)**:
   - Look up entry `sampleIndex` in the FSB5 container to locate the stream offset and name entry.
   - Decompress Vorbis / FADPCM stream into multichannel PCM.

---

## 17. FMOD Studio Project Object Model Constraints

When generating or validating FMOD Studio project XML structures (`Studio.02.03.00.metamodel.xml`), the following structural schema invariants apply:

### 17.1. `ParameterProxy` Context Invariant
1. In the FMOD Studio XML model, `<object class="ParameterProxy">` acts as the entry point of a parameter into an event scope.
2. If an event contains an automation curve (`AutomationCurve`) targeting a game parameter, but `<relationship name="parameters">` of `<object class="Event">` does not contain the corresponding `ParameterProxy`, the compiler rejects the model:
   ```
   Project contains incomplete or malformed components:
   - Event ["Event_Name"]
   ```
3. Timelines do not use `ParameterProxy`; they connect directly via `<relationship name="timeline">`.

### 17.2. Return Bus Typing: `MixerReturn` vs `MixerGroup`
1. In `Studio.02.03.00.metamodel.xml`, the send effect connection property has a strict type requirement:
   ```xml
   <relationship name="mixerReturn" destinationType="MixerReturn" isRequired="true"/>
   ```
2. Auxiliary return buses must be declared as `<object class="MixerReturn">` in `Metadata/Return/`. Declaring them as `MixerGroup` causes validation failure (`Invalid MixerSend target`).

### 17.3. Reciprocal Automator Ownership (`Automator.objectBeingAutomated`)
1. In the XML model, the link between an automated entity (e.g. `EventMixerGroup`) and its `Automator` is bidirectional.
2. The `Automator` references the object:
   ```xml
   <relationship name="objectBeingAutomated">
       <destination>{target_guid}</destination>
   </relationship>
   ```
   The target object must reciprocate:
   ```xml
   <relationship name="automators">
       <destination>{automator_guid}</destination>
   </relationship>
   ```
3. Without reciprocal registration, FMOD Studio resets `objectBeingAutomated` to null.

### 17.4. Single Fader Invariant (`MixerBusFader`)
Each `MixerBusEffectChain` in a track or bus permits strictly **one** instance of `<object class="MixerBusFader">`. Duplicate faders cause effect chain validation errors.

### 17.5. SingleSound Asset Integrity
Every `<object class="SingleSound">` must contain a valid relationship to an existing `AudioFile` asset:
```xml
<relationship name="audioFile">
    <destination>{audio_file_guid}</destination>
</relationship>
```
An instrument missing this relationship triggers an asset integrity failure during project build.

### 17.6. DSP Effect Placement: `EVTB` vs `GBSB` / `BUS `
In compiled banks, `EVTB` chunks do not contain effect chains directly. An event's DSP chain is bound through its internal bus GUIDs:
- `outputBusGuid`: The master effect chain for the event.
- `inputBusGuid`: The input stage effect chain.
The bus descriptors in `LIST GBSS` (`GBSB` / `BUS `) declare the array of `effectGuids` pointing to effect instances in `LIST BEFX`.
