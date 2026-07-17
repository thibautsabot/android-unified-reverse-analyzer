# AURA: Android Unified Reverse Analyzer

**AURA** is an Android reverse-engineering toolkit for package inspection, environment diagnostics, and runtime analysis.

It is accompanied by a handbook that explains the concepts behind Android and Unity reverse engineering.

Rather than documenting tools in isolation, the handbook focuses on understanding **why** each tool exists, **when** to use it, and **how** it fits into a complete reverse-engineering workflow.

Whether you are a developer trying to understand how an application can be analyzed or a reverse engineer exploring Android and Unity applications, the goal is the same: build a solid mental model before diving into implementation details.

## Installation

```sh
npm install -g aura-tools
aura inspect app.apk
aura doctor
```

---

## Handbook

The handbook explains the concepts behind Android and Unity reverse engineering — why each tool exists, when to use it, and how it fits into a complete workflow.

### Getting Started

- [00 - AURA](docs/00-aura.md) — Optional. Identifies the framework, backend and SDKs in any APK and recommends the right tools for the job.

---

### Part I - Android Foundations

- [01 - Android Reverse Engineering](docs/01-android-reverse-engineering.md)
- [02 - APK](docs/02-apk.md)
- [03 - apktool](docs/03-apktool.md)
- [04 - Smali](docs/04-smali.md)
- [05 - JADX](docs/05-jadx.md)
- [06 - Patching](docs/06-patching.md)
- [07 - Frida](docs/07-frida.md)

---

### Part II - Unity

- [10 - Unity](docs/10-unity.md)
- [11 - Before IL2CPP (Mono)](docs/11-before-il2cpp.md)
- [12 - IL2CPP](docs/12-il2cpp.md)
- [13 - Global Metadata](docs/13-global-metadata.md)
- [14 - Unity Assets](docs/14-unity-assets.md)
- [15 - AssetRipper](docs/15-assetripper.md)
- [16 - Il2CppDumper](docs/16-il2cppdumper.md)

---

### Part III - Native Analysis

- [20 - Native Libraries](docs/20-native-libraries.md)
- [21 - Ghidra](docs/21-ghidra.md)
- [22 - libunity.so](docs/22-libunity.md)
- [23 - libil2cpp.so](docs/23-libil2cpp.md)
