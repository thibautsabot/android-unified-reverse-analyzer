# Android Unity Reverse Engineering Handbook

Welcome to the **AURA** handbook.

This documentation aims to explain the concepts behind Android and Unity reverse engineering.

Rather than documenting individual tools, this handbook focuses on understanding **why** each tool exists, **when** it should be used, and **how** it fits into a complete reverse engineering workflow.

Whether you're a developer trying to understand how your application can be analyzed, or a reverse engineer exploring Unity applications, the goal is the same: build a solid mental model before diving into implementation details.

---

# Handbook

## Part I - Android Foundations

These chapters introduce the Android reverse engineering workflow and the tools commonly used to inspect, modify and instrument Android applications.

- [01 - Android Reverse Engineering](01-android-reverse-engineering.md)
- [02 - APK](02-apk.md)
- [03 - apktool](03-apktool.md)
- [04 - Smali](04-smali.md)
- [05 - JADX](05-jadx.md)
- [06 - Patching](06-patching.md)
- [07 - Frida](07-frida.md)

---

## Part II - Unity

Unity applications follow the same Android packaging format, but most of their logic lives outside of the traditional Java layer.

These chapters explain how Unity applications are structured and introduce the tools commonly used to reverse engineer them.

- [10 - Unity](10-unity.md)
- [11 - Before IL2CPP (Mono)](11-before-il2cpp.md)
- [12 - IL2CPP](12-il2cpp.md)
- [13 - Global Metadata](13-global-metadata.md)
- [14 - Unity Assets](14-unity-assets.md)
- [15 - AssetRipper](15-assetripper.md)
- [16 - Il2CppDumper](16-il2cppdumper.md)

---

## Part III - Native Analysis

Once the managed layer has been explored, native analysis becomes essential for understanding Unity's implementation.

- [20 - Native Libraries](20-native-libraries.md)
- [21 - Ghidra](21-ghidra.md)
- [22 - libunity.so](22-libunity.md)
- [23 - libil2cpp.so](23-libil2cpp.md)

---

## Real case Scenario

See [Good Sorting.apk]()
