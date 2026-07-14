# apktool

Once you've identified the structure of an APK, the next step is extracting its contents into a format that can be inspected and modified.

This is where [**apktool**](https://apktool.org/) comes in.

apktool is one of the most widely used tools in Android reverse engineering. It decodes an APK into a human-readable project while preserving enough information to rebuild the application later.

Unlike ZIP extractors, apktool understands Android's resource format and Dalvik bytecode.

---

# What Does apktool Extract?

Running apktool against an APK produces a directory similar to the following.

```
Application/

├── AndroidManifest.xml
├── apktool.yml
├── assets/
├── lib/
├── original/
├── res/
├── smali/
├── smali_classes2/
└── unknown/
```

Most of these directories correspond to files that were already present inside the APK.

Others, such as `smali/`, are generated during the decoding process.

---

# AndroidManifest.xml

One of the first files worth inspecting is the application's manifest.

The manifest contains information such as:

- Package name
- Minimum Android version
- Permissions
- Activities
- Services
- Broadcast Receivers
- Content Providers
- Application metadata

This file provides an overview of how Android sees the application before looking at any code.

---

# Resources

Android resources are stored inside the `res/` directory.

Examples include:

- Layout XML files
- Icons
- Drawables
- Strings
- Colors
- Animations

Although these files rarely contain application logic, they often provide valuable context about features that exist within the application.

---

# Smali

One of apktool's most useful features is converting DEX bytecode into **Smali**.

```
classes.dex

↓

smali/
```

Smali is a human-readable representation of Dalvik bytecode.

Although reading Smali takes some practice, it allows you to inspect and modify applications even when the original Java source code is unavailable.

We'll look at Smali in more detail in the next chapter.

---

# Rebuilding an APK

After making changes, apktool can rebuild the project into a new APK.

A typical workflow looks like this.

```
APK

↓

apktool decode

↓

Inspect / Modify

↓

apktool build

↓

Sign APK

↓

Install
```

Rebuilding is commonly used when modifying resources or patching Smali.

---

# Limitations

apktool is extremely useful, but it is important to understand what it does **not** do.

apktool does **not**:

- Decompile Java code.
- Analyze native libraries.
- Instrument a running application.
- Recover high-level application logic.

Those tasks require different tools.

---

# Next

One of apktool's most valuable outputs is the `smali/` directory.

The next chapter introduces **Smali**, explains what it represents, and why it remains an important part of Android reverse engineering.

[04 - Smali](04-smali.md)
