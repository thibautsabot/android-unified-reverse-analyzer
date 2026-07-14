# APK

Android applications are distributed as **APK** files (Android Package).

Despite the different file extension, an APK is simply a ZIP archive following a structure understood by the Android operating system.

You can verify this yourself by renaming an APK to `.zip` and opening it with any archive manager.

---

# APK Structure

A typical APK looks similar to the following.

```
Application.apk

├── AndroidManifest.xml
├── classes.dex
├── resources.arsc
├── res/
├── assets/
├── lib/
│   ├── arm64-v8a/
│   ├── armeabi-v7a/
│   └── x86_64/
└── META-INF/
```

Each file or directory serves a different purpose.

| File / Directory      | Description                                                           |
| --------------------- | --------------------------------------------------------------------- |
| `AndroidManifest.xml` | Declares the application's components, permissions and configuration. |
| `classes.dex`         | Compiled Java / Kotlin bytecode executed by Android Runtime (ART).    |
| `resources.arsc`      | Compiled Android resource table.                                      |
| `res/`                | Images, layouts, strings and other Android resources.                 |
| `assets/`             | Arbitrary application files bundled by the developer.                 |
| `lib/`                | Native shared libraries (`.so`).                                      |
| `META-INF/`           | APK signature and certificate information.                            |

---

# Why This Matters

Knowing what an APK contains helps determine where to continue your investigation.

For example:

- Looking for Activities or Services? → `AndroidManifest.xml`
- Interested in Java or Kotlin code? → `classes.dex`
- Looking for native code? → `lib/`
- Looking for bundled data or game assets? → `assets/`

Reverse engineering often starts by identifying **where** the information you're looking for is likely to live.

---

# Java Isn't Everything

For many Android applications, most of the application logic is contained inside one or more DEX files.

While this is true for many applications, Android allows developers to move functionality into native libraries.

```
lib/

├── libfoo.so
├── libbar.so
└── libnative.so
```

Depending on the application, these native libraries may contain anything from critical code to the majority of the application's business logic.

Recognising this early can save a significant amount of time during an investigation.

---

# The Assets Directory

Unlike Android resources (`res/`), the contents of the `assets/` directory are not interpreted by Android itself.

Developers can store almost any kind of data there.

Common examples include:

- Configuration files
- Databases
- Images
- Audio
- Fonts
- Machine learning models
- Game assets
- Custom binary formats

Many frameworks including game engines make extensive use of this directory.

---

# Next

The next step is learning how to extract and rebuild these files.

That's where **apktool** comes in.

[03 - apktool](03-apktool.md)
