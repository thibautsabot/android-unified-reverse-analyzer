# Walkthrough 06: Going deeper into the native binary

## The itch that wouldn't go away

At the end of the previous step, I said we had hit the limits of asset analysis. That was true. But I kept thinking about the shuffle methods.

We knew they existed. We knew their names. We had a reasonable guess about what they did. But "here's my take on it" is not the same as knowing. And one thing was genuinely unclear: was the shuffle global, mixing items across the whole board? Or was it scoped to individual cells?

AssetRipper could not answer that. The method bodies were empty. So I went to the one place where the real code actually lives.

## `libil2cpp.so`

When Unity compiles a game with IL2CPP, it takes all the C# code — every class, every method, everything and translates it to native ARM64 machine code. The result is a single shared library: `libil2cpp.so`.

In this APK, it weighs 102 MB. That is not a support library. That is the entire game.

The problem with a 102 MB binary is that it is completely opaque unless you have a way to locate specific functions. There are no symbol names in the `.text` section. Just addresses and machine instructions.

That is where `global-metadata.dat` comes in.

## `global-metadata.dat`: the index

IL2CPP keeps a separate file that maps method names to their positions in the binary, like a table of contents. Without it you would need to guess where `ShuffleItemVisual` starts inside 102 MB of ARM64.

The structure is a binary format with a fixed header followed by several sections. The key ones are the string table (all type and method names), the method definitions, and the type definitions.

To get these file, we will need to extract the .apk file with either a simple unzip or `apktool`.

Because we have an .xapk file, we need to extract all of the child .apk

```bash
apktool d Goods\ Puzzle_\ Sort\ Challenge-5.14.xapk -o decoded_apk # <-- Will unpack the .xapk into multiple files
cd decoded_apk

# Script to extract all .apk inside the folder
for f in *.apk; do
  apktool d "$f" -o "${f%.apk}_decoded"
done
```

Once everything is outpacked, we can look for the interesting files:

```bash
find . -name 'libil2cpp.so' -o -name 'global-metadata.dat'
./config.arm64_v8a_decoded/lib/arm64-v8a/libil2cpp.so
./com.fc.goods.sort.matching.puzzle.triplemaster_decoded/assets/bin/Data/Managed/Metadata/global-metadata.dat
./config.armeabi_v7a_decoded/lib/armeabi-v7a/libil2cpp.so
```

Since my phone (and most of Android) is using arm64-v8, I'll run IL2CPPDump on these two files.
To make things easier, you can copy them in the root directory:

```bash
cp ./config.arm64_v8a_decoded/lib/arm64-v8a/libil2cpp.so .
cp ./com.fc.goods.sort.matching.puzzle.triplemaster_decoded/assets/bin/Data/Managed/Metadata/global-metadata.dat .

./Il2CppDumper ./global-metadata.dat ./libil2cpp.so

Initializing metadata...
Metadata Version: 31
Initializing il2cpp file...
Applying relocations...
WARNING: find .init_proc
ERROR: This file may be protected.
Il2Cpp Version: 31
Searching...
CodeRegistration : 5c51168
MetadataRegistration : 5e14ae0
Dumping...
Done!
Generate struct...
Done!
Generate dummy dll...
Done!
Press any key to exit...
```

You night have noticed `ERROR: This file may be protected.`.
I spent an ungodly amount of time [trying to fix it](https://github.com/perfare/il2cppdumper#error-this-file-may-be-protected) with IL2CPPDumper, installing Magisk module or dumping raw memory..

Since it's out of this walkthrough scope, I gave up at some point.
But it turns out that in our case, it's mostly fine since the program was still able the generate everything!
(`CodeRegistration : 5c51168`, `Generate dummy dll...`)

## Ghidra

Finaly it's time to open our library file!

Since we will be using the dumped python script, make sure to [install PyGhidra](https://pypi.org/project/pyghidra/) first.

Them, run Ghidra _(the way you would launch it depends on the environment that you're in)_.
For my case, it's `python3 -m pyghidra --install-dir /path/to/Ghidra/folder --gui`.

Create a new project and add libil2cpp.so as a new file in the project.
Ghidra will ask if you want to Analyse the file. Say yes and keep the default options.

:warning: Analysing the binary for the first time might take a while. On an M1 Pro, it lasted for about an hour.
You know that you're done when there is no more loading bar at the bottom right of your screen.

![13 - Ghidra Analysis](./screenshots/13-ghidra-analysis.png)
_It stayed on this state for more than 20 minutes_
