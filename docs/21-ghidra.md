# Ghidra

The previous chapter introduced native code and explained why tools such as JADX can no longer help once an application's logic has been compiled into machine instructions.

This is where **Ghidra** comes in.

Ghidra is an open-source reverse engineering suite developed by the NSA. It analyzes compiled binaries and helps reconstruct a higher-level understanding of native code.

Although this chapter focuses on Ghidra, the same concepts apply to other reverse engineering tools such as **IDA** and **Binary Ninja**.

---

# Opening a Native Library

Once a native library has been imported into Ghidra, the first step is usually to run the automatic analysis.

During this process, Ghidra identifies:

- Functions
- Strings
- Data structures
- Cross references
- Imported and exported symbols

The quality of this analysis depends on the available information.

Applications compiled with debugging symbols generally produce much better results than stripped production builds.

---

# Disassembly

The first view presented by Ghidra is the **Listing**.

This is the disassembled machine code.

For example:

```asm
MOV X0, X19

BL 0x123456

RET
```

Unlike raw hexadecimal instructions, assembly provides a human-readable representation of what the processor executes.

This is the closest representation of the original executable.

---

# Decompilation

One of Ghidra's most powerful features is its integrated decompiler.

Instead of reading assembly directly, Ghidra attempts to reconstruct higher-level C-like code.

Assembly:

```asm
MOV W0, #1

RET
```

Decompiler:

```c
return 1;
```

The decompiled output is **not** the original source code.

It is Ghidra's best interpretation of the machine instructions.

---

# Functions

Native libraries are organized as functions.

A simplified view might look like this.

```
Functions

├── FUN_001A42B0

├── FUN_001A4388

├── FUN_001A4400

└── ...
```

Initially, these names rarely provide useful information.

One of the goals of reverse engineering is to identify what each function is responsible for.

---

# Strings

Strings are often one of the quickest ways to navigate a native application.

Examples include:

- Error messages
- Log output
- URLs
- File paths
- Debug text

Finding an interesting string often leads directly to the function that uses it.

This is a common starting point during native investigations.

---

# Cross References

One of Ghidra's most useful features is **Cross References** (often abbreviated as **XREFs**).

Cross references answer questions such as:

- Which functions call this function?
- Which functions read this variable?
- Which code references this string?

Rather than navigating manually, cross references allow you to follow relationships throughout the application.

As applications grow larger, this quickly becomes one of the most frequently used features.

---

# Searching

Just like JADX, Ghidra provides several search capabilities.

Common examples include:

- Function names
- Strings
- Addresses
- Data types
- Symbols

Combined with cross references, searching often becomes the fastest way to navigate a large native library.

---

# Next

Unity applications contain two important native libraries.

The first is `libunity.so`, which contains the Unity engine itself.

The next chapter explores its role and explains why it differs from `libil2cpp.so`.

[22 - libunity.so](22-libunity.md)
