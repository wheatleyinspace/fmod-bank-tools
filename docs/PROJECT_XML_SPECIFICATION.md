# FMOD Studio Project XML Model Specification

## 1. Overview and Scope

This document specifies the structural invariants, relationship rules, and schema constraints required when generating or validating FMOD Studio project XML files (compatible with `Studio.02.03.00.metamodel.xml`).

While [BANK_SPECIFICATION.md](BANK_SPECIFICATION.md) defines the binary RIFF layout of compiled runtime sound banks, this document covers authoring-level constraints enforced by the FMOD Studio XML validator and compiler.

---

## 2. Core Metamodel Invariants

### 2.1. `ParameterProxy` Context Invariant
1. In the FMOD Studio project XML model, `<object class="ParameterProxy">` acts as the scoped entry point of a game parameter into an event scope.
2. If an event contains an automation curve (`<object class="AutomationCurve">`) targeting a game parameter, the `<object class="Event">` must register the corresponding `ParameterProxy` in its `<relationship name="parameters">`.
3. If this relationship is omitted, the FMOD Studio compiler reports validation failure:
   ```
   Project contains incomplete or malformed components:
   - Event ["Event_Name"]
   ```
4. **Timeline Invariant**: Event timelines do not use a `ParameterProxy`. Instead, they connect directly to `<relationship name="timeline">` on the `Event`.

---

### 2.2. Return Bus Typing: `MixerReturn` vs `MixerGroup`
1. In `Studio.02.03.00.metamodel.xml`, the send effect connection property has a strict type requirement:
   ```xml
   <relationship name="mixerReturn" destinationType="MixerReturn" isRequired="true"/>
   ```
2. Auxiliary return buses must be declared as `<object class="MixerReturn">` located in `Metadata/Return/`. Declaring them as `<object class="MixerGroup">` causes validation failure (`Invalid MixerSend target`).
3. Standard mixer buses located in `Metadata/Group/` must use `<object class="MixerGroup">`.

---

### 2.3. Reciprocal Automator Ownership (`objectBeingAutomated`)
1. In the XML metamodel, the relationship between an automated entity (such as `EventMixerGroup`, `MixerBusFader`, or `SpatialiserEffect`) and its driving `<object class="Automator">` is strictly bidirectional.
2. The `Automator` points forward to the target object:
   ```xml
   <relationship name="objectBeingAutomated">
       <destination>{target_guid}</destination>
   </relationship>
   ```
3. The target object must reciprocally register the automator:
   ```xml
   <relationship name="automators">
       <destination>{automator_guid}</destination>
   </relationship>
   ```
4. If reciprocal registration is missing, FMOD Studio resets `objectBeingAutomated` to null during project load.

---

### 2.4. Single Fader Invariant (`MixerBusFader`)
Each `MixerBusEffectChain` on any track or mixer bus permits strictly **one** instance of `<object class="MixerBusFader">`. Duplicate faders within the same chain trigger validation errors in FMOD Studio.

---

### 2.5. SingleSound Asset Integrity
Every `<object class="SingleSound">` must contain a valid destination link to an existing `AudioFile` asset in `Metadata/AudioFile/`:
```xml
<relationship name="audioFile">
    <destination>{audio_file_guid}</destination>
</relationship>
```
An instrument missing this relationship triggers an asset integrity failure during project build.

---

### 2.6. Mixer Routing and VCA Hierarchy
1. VCA channels are declared as `<object class="MixerVCA">` in `Metadata/Vca/`.
2. Each VCA maintains a list of controlled buses in `<relationship name="controlledBuses">`.
3. In compiled binary banks, VCAs are stored in `LIST VCAS` (`VCAB`), which stores the dynamic list of controlled bus GUIDs. In XML projects, controlled buses reference back to their assigned VCAs through reciprocal relationships where defined by the metamodel.
