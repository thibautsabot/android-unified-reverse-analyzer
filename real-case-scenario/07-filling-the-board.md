# Walkthrough 07: How the game fills the board

## Where we left off

At the end of the previous step, Ghidra was still chewing through `libil2cpp.so`, and Il2CppDumper had given us a pile of files I did not really know how to use yet.

The question from [05 - Game Mechanics](05-game-mecanics.md) was still open:

> When the game "shuffles" a level, does it mix items across the whole board, or only inside each cell?

AssetRipper could only show us the method names (`ShuffleItemVisual`, `ShuffleCellPos`, `ShuffleItemInCell`). The bodies were empty.

This time we are going to read the real code.

I'll be honest: this part was the hardest so far. The first time I opened a decompiled IL2CPP function, I was completely lost. So this walkthrough goes slowly, and explains every step, including the tooling I had to build to make the output readable.

> ### Before you start: this part is hard, and that's normal
>
> Chapters 07 and 08 are the most confusing of the whole series. If you get lost, if you have to re-read a paragraph three times, if the pseudo-C looks like noise, that is **not** a sign that you're not cut out for this. It's what reading native code feels like at the beginning. It was for me, and from what I've seen, it is for most people.
>
> A few things that might help:
>
> - **Keep going, even if you don't understand everything.** You don't need to follow every line. Each chapter repeats the same few patterns (a loop, a list, a dictionary), and they get familiar after a while.
> - **Skim first, then come back.** Read the "big picture" part of each step before the code. It tells you what to look for.
> - **You will make mistakes, and so did I.** Several times while writing these chapters, I got something wrong, found out later, and had to fix it. It's part of the process.
> - **There is an easier way.** Reading code is slow. In [09](09-frida.md), we'll watch the game run with a tool called Frida, and answer in minutes questions that took hours here. That's not a replacement for what you're about to learn: Frida needs the names, addresses and offsets we find here to know where to look. But it makes the whole thing much lighter.
>
> So take your time. This is the hard part, and the rest of the series is built on it.

## The goal

We know what the level file looks like (`Lv123_10.asset`):

```yaml
cells:
  - posX: -9
    posY: 1
    itemsLayer:
      - items: 4a0000004a000000da000000   # [74, 74, 218]
      - items: 4d0000003f00000000000000   # [77, 63, 0]
      ...
```

We want to know what happens between that file and the board on screen.

We will focus on one function: `GenLevelController.ShuffleItemInCell`.

## The words I use

A quick reminder of the words from [04](04-opening-assets.md), since the code is going to use them a lot:

| Word | Meaning | In the code |
|---|---|---|
| **cell** | one shelf: the visible row of 3, plus the rows queued behind it | `CellInfo`, `cells` |
| **position** | where a cell sits on the board | `posX`, `posY` |
| **layer** | one row of 3. Layer 0 is the visible one | `itemsLayer[L]` |
| **slot** | one of the 3 places of a layer | `items[i]` |
| **item** | what's in a slot. `0` means empty | the `int` in `items` |

Two rules to keep in mind:

- **A cell has a position. A layer doesn't.** `ShuffleCellPos` moves a cell by changing `posX` / `posY`, and only those. The functions that change *items* are another matter, and we'll meet them.
- **"Layer 1" means the same thing for every cell:** the second row in the queue. So when the code says "layer `L` of every cell", it means all the second layer of items, across the whole board.

I'll also use **bag** (or `pool` in the code) for a temporary list of items the function builds, and **group** for cells that are put together.

## Step 1: Ghidra or IDA?

In the previous step I started with Ghidra. It works, but for this binary I switched to **IDA** (with the Hex-Rays decompiler).

Both tools do the same job:

```text
machine code (ARM64)  →  assembly  →  pseudo-C
```

The difference is mostly comfort. On IL2CPP code, IDA's pseudo-C was a bit easier to read, and its Python API let me automate the cleanup I describe below.

Everything in this walkthrough can be done in Ghidra too, but the scripts I wrote are IDA-only.

## Step 2: Opening the library in IDA

Which file do we open? Only one:

```text
config.arm64_v8a_decoded/lib/arm64-v8a/libil2cpp.so
```

The same file we gave to Il2CppDumper. `global-metadata.dat` is **not** opened in IDA. Il2CppDumper already used it to produce `script.json` and `il2cpp.h`.

1. **File → Open** → `libil2cpp.so`
2. Loader: `ELF64 for ARM64 (AArch64)`. Keep the defaults.
3. Wait for the auto-analysis. You know it's done when the bottom-left corner says `AU: idle`.

:warning: As with Ghidra, the first analysis is long. Go grab a coffee. Or several.

When it's done, save the database (`.i64`). Everything we do from now on is stored in it.

## Step 3: Giving names to 265,000 functions

At this point, IDA shows functions like this:

```text
sub_2AECC14
sub_2AED970
sub_278BD2C
```

102 MB of anonymous functions. Useless.

Il2CppDumper produced two files that fix this:

```text
script.json   ← address → name of every method (e.g. 0x2AECC14 = GenLevelController$$ShuffleItemInCell)
il2cpp.h      ← C structures describing every class (fields, offsets)
```

Il2CppDumper ships an IDA script to import them, but I ended up writing my own: [`scripts/ida_il2cpp.py`](../scripts/ida_il2cpp.py). It does the same import, plus a lot of cleanup that we will need later.

1. **File → Script file…** → `scripts/ida_il2cpp.py`
2. It asks for `script.json`, then `il2cpp.h` (both in the Il2CppDumper output folder).
3. Wait. Again.

## Step 4: The wall of text

This is what the first decompile of `ShuffleItemInCell` looked like (heavily shortened):

```c
void GenLevelController__ShuffleItemInCell(GenLevelController_o *this, const MethodInfo *method)
{
  __int64 v3; // x1
  ... 130 more variable declarations ...

  if ( (byte_63FCDBC & 1) == 0 )
  {
    il2cpp_codegen_initialize_method_metadata(&Method_CollectionUtils_ShuffleCurrentList_int___, method);
    ... 35 more lines like this ...
    byte_63FCDBC = 1;
  }
  ...
  while ( 1 )
  {
    ...
    v42 = *(Il2CppObject **)&v127.fields._current;
    if ( !*(_QWORD *)&v127.fields._current )
      il2cpp_throw_NullReferenceException_278C014(v40, v41);
    v43 = *(_DWORD *)(*(_QWORD *)&v127.fields._current + 24LL);
    if ( (!v43 || v43 == 3) && *(_DWORD *)(*(_QWORD *)&v127.fields._current + 32LL) != 1 )
    {
      ...
      klass = v42[3].klass;
      namespaze = (int)klass->_1.namespaze;
      ...
```

~550 lines. `klass->_1.namespaze`? `+ 24LL`? I had no idea what I was looking at.

It took me a while to understand that **most of this is not game logic**. It's noise generated by IL2CPP. Before reading anything, we need to learn to recognize it.

## Step 5: A few basics first

I had to step back and learn a few things about memory. You can't read this code without them.

### Memory is a long row of numbered boxes

```text
address:  1000 1001 1002 1003 1004 1005 1006 1007 ...
value:     2C   01   00   00   07   00   00   00  ...
```

Each box holds one byte. An **address** is just the box number, usually written in hexadecimal (`0x1000`).

An `int` takes 4 boxes. A pointer (the address of something else) takes 8 boxes on a 64-bit phone.

Values are stored "backwards" (little-endian): `2C 01 00 00` means `0x0000012C` = 300. That's exactly why the `items: 4a000000...` blobs from [04](04-opening-assets.md) looked weird.

### An object is a block of boxes with a fixed layout

Take `CellInfo`, from `dump.cs`:

```text
public class CellInfo
{
    public int posX;          // 0x10
    public int posY;          // 0x14
    public int cellType;      // 0x18
    public int numberLayer;   // 0x1C
    public int moveType;      // 0x20
    public float speed;       // 0x24
    public int isLock;        // 0x28
    public List<ItemsInLayerCell> itemsLayer; // 0x30
}
```

The `// 0x18` comments are **offsets**: the position of each field from the start of the object.

In memory, a `CellInfo` living at address `0x5000` looks like this:

```text
0x5000  +0x00  klass       → "I am a CellInfo" (8 bytes)
0x5008  +0x08  monitor     → used for locking, ignore (8 bytes)
0x5010  +0x10  posX
0x5014  +0x14  posY
0x5018  +0x18  cellType
0x501C  +0x1C  numberLayer
0x5020  +0x20  moveType
...
0x5030  +0x30  itemsLayer  → address of another object (the list)
```

Every object starts with a 16-byte header (`klass` + `monitor`). That's why fields start at `0x10`.

The machine never stores the word "cellType". It only knows "read 4 bytes at +0x18".

**That's the core trick of reverse engineering: turn offsets back into names.**

So when the decompiler writes:

```c
*(_DWORD *)(cell + 24LL)
```

24 is `0x18`, so it means `cell.cellType`.

And `v42[3].klass` means `v42 + 3 × 16 bytes` = `+0x30` = `cell.itemsLayer`. The decompiler gets the address right but the name completely wrong.

### Pointers: following the arrows

`itemsLayer` at `+0x30` does not contain the list. It contains the **address** of the list.

```text
cell (0x5000)                 list (0x9000)
+0x30: 0x9000  ───────────►   +0x10 _items  ──► actual array
                              +0x18 _size   = 8
```

So `cell.itemsLayer.Count` is two jumps: read `+0x30` to get `0x9000`, then read `0x9000 + 0x18`.

That's what `klass->_1.namespaze` was: `List._size`, read through the wrong type.

### One exception: structs

Some types in `dump.cs` are `struct`, not `class`:

```text
private struct GenLevelController.<>c__DisplayClass51_0
{
    public List<ItemsInLayerCell> itemsInLayerCells; // 0x0
}
```

Structs have **no header**, so their first field is at `0x0`. Keep that in mind when an offset looks "too small".

## Step 6: Letting the script do the cleanup

At first, I fixed all of this by hand in IDA: click a variable, press `Y`, type `CellInfo_o *`, repeat for every variable in every function.

It works, but it gets boring very fast. And since all of these patterns are generated by IL2CPP, they are the same in **every** IL2CPP game. So I automated them in [`scripts/ida_il2cpp.py`](../scripts/ida_il2cpp.py):

### Before / after

```c
// before
v43 = *(_DWORD *)(*(_QWORD *)&v127.fields._current + 24LL);
if ( (!v43 || v43 == 3) && *(_DWORD *)(*(_QWORD *)&v127.fields._current + 32LL) != 1 )
...
klass = v42[3].klass;
namespaze = (int)klass->_1.namespaze;
```

```c
// after
cellType = v130.fields._current->fields.cellType;
if ( (!cellType || cellType == 3) && v130.fields._current->fields.moveType != 1 )
...
itemsLayer = current->fields.itemsLayer;
v52 = itemsLayer->fields._size;
```

Same code. Now readable.

But not finished. The types are right, but most variables are still called `v55`, `v58`, `v61`... Reading `v58->fields._size` tells you nothing. That's the next step.

## Step 7: Giving variables real names

### Why IDA uses `v55`, `v58`...

When C# is compiled, the names of local variables are thrown away. The phone doesn't need them. So IDA invents names: `v1`, `v2`, `v3`..., in the order it meets them.

Sometimes IDA can guess a real name. For example:

```c
  levelInfo = this->fields._levelInfo;
```

The value comes straight from the `_levelInfo` field, so IDA reuses the field name. But that only works for simple cases like this one. Everything else is `vSomething`, and it's our job to name it.

Naming variables is not cosmetic. It's **how you understand the function**: you can't name a variable until you know what it is. And once it's named, every line that uses it becomes easier to read.

### How to rename in IDA

1. In the pseudocode window, click on a variable.
2. Press `N`.
3. Type the new name, press `Enter`.

Every occurrence of that variable in the function changes at once, and the name is saved in the database.

Before renaming, there's one more trick I used all the time: **just click on a variable**. IDA highlights it everywhere in the function. That's how you follow a variable from line to line without getting lost.

Don't worry about getting a name wrong. Press `N` again and fix it. A name is just a label for you, the code never changes. When I'm not sure, I use a temporary name like `maybe_layer`, and rename it later.

### Shapes to recognize

To find out what a variable is, I ask two questions:

```text
1. Where does it get its value?
2. What does the code do with it afterwards?
```

To answer them, you need to recognize a few shapes that come back again and again. Four of them are enough for this function.

**Shape 1: creating an object.**

```c
      v58 = (System_Collections_Generic_List_int__o *)il2cpp_codegen_object_new(
                                                        System_Collections_Generic_List_int__TypeInfo,
                                                        v57);
      System_Collections_Generic_List_int____ctor(
        v58,
        (const MethodInfo_487AE38 *)Method_System_Collections_Generic_List_int___ctor__);
```

`object_new` reserves the memory, `ctor` (*constructor*) prepares it. The type is written in the name: `List_int`. So this is `v58 = new List<int>()`.

**Shape 2: a `foreach` loop.**

```c
      while ( 1 )
      {
        v59 = System_Collections_Generic_List_Enumerator_object___MoveNext(
                (System_Collections_Generic_List_Enumerator_object__o *)&v130,
                (const MethodInfo_43FF11C *)Method_System_Collections_Generic_List_Enumerator_CellInfo__MoveNext__);
        if ( !v59 )
          break;
        v61 = v130.fields._current;
```

`MoveNext` means "go to the next element". If there's none left, `break` leaves the loop. `_current` is the element we're on. The type is in the name again: `Enumerator_CellInfo`.

So this is `foreach (var v61 in ...)`, and `v61` is a `CellInfo`.

**Shape 3: reading an element of a list.**

```c
                v86 = System_Collections_Generic_List_int___get_Item(
                        v85,
                        v77,
                        (const MethodInfo_487B3D0 *)Method_System_Collections_Generic_List_int__get_Item__);
```

`get_Item(list, index)` is the compiled version of `list[index]`. So this is `v86 = v85[v77]`.

**Shape 4: a counter.**

```c
            v77 = 0;
            do
            {
              // ...
              v77 = v90;
            }
            while ( v90 != v75 );
```

(and somewhere inside, `v90 = v77 + 1`)

Starts at 0, goes up by one each turn, stops at a limit. That's a `for` loop: `for (v77 = 0; v77 < v75; v77++)`.

### Naming them

With these four shapes, here is how I named the variables, using the same two questions: *where does it get its value, and what happens to it afterwards?*

| IDA name | What I saw | New name |
|---|---|---|
| `v55` | Shape 4 (a counter, `0` up to `v125`), then used as `v62[v55]`, where `v62` is read at `+ 48` = `0x30` = `itemsLayer` in `CellInfo` | `L`, the layer number |
| `v58` | Shape 1 (a new `List<int>`), then things are added to it, it is sorted, and looped over | `pool`, a temporary storage for items |
| `v61` | Shape 2: it receives `_current` of a loop over `CellInfo`s | `cell` |
| `v77` | Shape 4 again, and used as the index in `get_Item(items, v77)` | `I`, the slot index |
| `v86` | Shape 3: `items[v77]`, the number stored in a slot | `itemId` |

The line for `v55` is the one that took the longest. A counter alone doesn't say what it counts. What gave it away is **what it was used as an index into**: a list at `0x30` in `CellInfo`, which `dump.cs` tells us is `itemsLayer`.

Here is the effect. Before renaming:

```c
              v78 = v61->fields.itemsLayer;
              v79 = System_Collections_Generic_List_object___get_Item(v78, v55, ...);
              v81 = v79->fields.items;
              if ( System_Collections_Generic_List_int___get_Item(v81, v77, ...) )
```

After:

```c
              v78 = cell->fields.itemsLayer;
              v79 = System_Collections_Generic_List_object___get_Item(v78, L, ...);
              v81 = v79->fields.items;
              if ( System_Collections_Generic_List_int___get_Item(v81, I, ...) )
```

(I shortened the last argument of each call to `...`.)

Now it almost reads out loud: "take layer `L` of the cell, look at slot `I`, if it's not empty...".

You don't have to rename everything. Five names were enough here. A good rule: **rename a variable when you catch yourself asking "what was `v58` again?"**

## Step 8: Reading the function

### The big picture

Before any code, here is what the function does:

```text
For each layer (layer 0, then layer 1, ...):
  1. take every item of that layer, from every cell, and put them in a bag (sorted)
  2. empty the cells
  3. put the items back, one by one
  4. mix the order of the items inside each cell
```

That's the whole function. Let's follow one small example through the four steps. Three cells, layer 0 only (`0` is an empty slot):

```text
cell A    [ 74  74   0 ]
cell B    [ 63   0   5 ]
cell C    [  5 218   0 ]
```

### 1. Fill the bag, and sort it

The loop over layers is the `L` counter from Step 7. Inside it, the function creates `pool` (Shape 1), reads every slot of layer `L` of every cell, skips the zeros, and adds the rest to `pool`. Then:

```c
      System_Collections_Generic_List_int___Sort(
        pool,
        (const MethodInfo_487D0DC *)Method_System_Collections_Generic_List_int__Sort__);
```

The numbers are put in order.

```text
after filling:   [74, 74, 63, 5, 5, 218]
after sorting:   [5, 5, 63, 74, 74, 218]
```

Look at the sorted bag: **identical items are now next to each other**. That's the key to the whole function.

### 2. Empty the cells

```c
          System_Collections_Generic_List_int___set_Item(
            v113,
            v114,
            0,
            (const MethodInfo_487B424 *)Method_System_Collections_Generic_List_int__set_Item__);
```

`get_Item` reads, `set_Item(list, index, value)` writes. Here the value is `0`, inside two loops (every cell, every slot). So every slot of the layer is set to empty. All the items are now only in the bag.

### 3. Put the items back

This is the heart of the function. For each item of the sorted bag:

```c
        CellLessItem_51_0 = GenLevelController___ShuffleItemInCell_g__GetCellLessItem_51_0(
                              v128.fields._current,
                              (GenLevelController___c__DisplayClass51_0_o *)&list,
                              v115);
        IndexEmpty = (unsigned int)ItemsInLayerCell__GetIndexEmpty(CellLessItem_51_0, v118);
        // ... items[IndexEmpty] = the item
```

1. `GetCellLessItem` returns a cell. Its name says what it does: *get the cell with less items*.
2. `GetIndexEmpty` returns the first empty slot of that cell.
3. The item is written there.

The helper's main part is short:

```c
    else if ( (int)NumItem < v12 )
    {
      v12 = NumItem;
      v11 = current;
    }
```

It loops over the cells, and keeps the one with the fewest items so far. (There is an extra rule when cells already hold 2 items. I didn't dig into it.)

Compare with the start:

```text
before                      after
cell A   [ 74  74   0 ]     [  5  74   0 ]
cell B   [ 63   0   5 ]     [  5  74   0 ]
cell C   [  5 218   0 ]     [ 63 218   0 ]
```

Cell A started with two `74` side by side. After the function, they're split apart. **What the code does is spread identical items across different cells.**, probably so the board isn't too easy to clear.

### 4. Shuffle inside each cell

`GetIndexEmpty` always takes the first free slot, so after step 3 the items are packed on the left, and the empty slot is always on the right:

```text
after step 3 (packed left)
cell A   [  5  74   0 ]
cell B   [  5  74   0 ]
cell C   [ 63 218   0 ]
```

A last loop fixes that:

```c
        CollectionUtils__ShuffleCurrentList_int_(
          (System_Collections_Generic_IList_T__o *)v127.fields._current->fields.items,
          (const MethodInfo_36A559C *)Method_CollectionUtils_ShuffleCurrentList_int___);
```

`items` is the 3 slots of a cell, and `Shuffle` mixes their order. A cell keeps the same items, but each one lands in a random slot:

```text
after step 4 (random order inside each cell)
cell A   [ 74   0   5 ]
cell B   [  5  74   0 ]
cell C   [  0  63 218 ]
```

The slots are random, so you get something different every time.

## Putting it all together

```csharp
private void ShuffleItemInCell()
{
    for (int L = 0; L < numberOfLayers; L++)
    {
        var pool = new List<int>();                          // 1. fill the bag
        foreach (var cell in cells)
            foreach (int item in cell.itemsLayer[L].items)
                if (item != 0) pool.Add(item);
        pool.Sort();                                         //    and sort it

        foreach (var cell in cells)                          // 2. empty the cells
            for (int i = 0; i < 3; i++)
                cell.itemsLayer[L].items[i] = 0;

        foreach (int item in pool)                           // 3. put the items back
        {
            var target = GetCellLessItem(item);
            target.items[target.GetIndexEmpty()] = item;
        }

        foreach (var cell in cells)                          // 4. shuffle inside each cell
            cell.itemsLayer[L].items.Shuffle();
    }
}
```

About 20 lines of C#, out of ~550 lines of pseudo-C. Most of the rest was IL2CPP noise.

## Step 9: Who calls it?

At this point, I was pretty happy. I thought I had found how every level was shuffled.

But I had skipped a basic question: **when does this function actually run?** A function can be perfectly understood and still almost never be used.

### Finding the callers

In IDA, put the cursor on the function name (at the top of the pseudocode, `GenLevelController__ShuffleItemInCell`) and press `X`.

`X` opens the **cross references** window: every place in the binary that mentions this function. I got two lines:

```text
BL   GenLevelController$$ShuffleItemInCell
DCQ  GenLevelController$$ShuffleItemInCell
```

They don't mean the same thing.

**`BL`** stands for *Branch with Link*. It's the ARM64 instruction to call a function. So this line is a real call: some code runs `ShuffleItemInCell()`.

**`DCQ`** stands for *Define Constant Quadword*. It's not an instruction, it's data: 8 bytes that hold the address of the function. IL2CPP keeps a big table with the address of every method of the game (Unity uses it to find methods by name at runtime). Every single method has a `DCQ` there, so it tells us nothing.

Only one `BL`: **`ShuffleItemInCell` is called from exactly one place.**

### Reading the caller

Double-click the `BL` line. IDA jumps to the call, inside a function named `GenLevelController__SetupCells`. Press `F5`. Here is the code around the call:

```c
  instance = (__int64)NMSingleton_object___get_instance((con  st MethodInfo_4A23EB4 *)Method_NMSingleton_PowerUpDataController__get_instance__);
  if ( !instance )
    goto LABEL_119;
  TutPowerup = (unsigned __int64)PowerUpDataController__GetTutPowerup((PowerUpDataController_o *)instance, nullptr);
  if ( HIDWORD(TutPowerup) == 2 && (_BYTE)TutPowerup )
  {
    v72 = PowerupInGameController_TypeInfo;
    if ( !*(&PowerupInGameController_TypeInfo->_2.cctor_finished + 1) )
    {
      j_il2cpp_runtime_class_init_0(PowerupInGameController_TypeInfo);
      v72 = PowerupInGameController_TypeInfo;
    }
    v72->static_fields->isTut = 1;
    GenLevelController__ShuffleItemVisual(this, v70);
    GenLevelController__ShuffleItemInCell(this, v73);
  }
```

Let's take it piece by piece.

```c
  instance = NMSingleton_object___get_instance(... Method_NMSingleton_PowerUpDataController__get_instance__);
```

A **singleton** is a class that has only one object, shared by the whole game, and reachable from anywhere through `.instance`. Same trick as before: the real class name is in the last argument, `PowerUpDataController`. So this is `PowerUpDataController.instance`, the object that manages power-ups.

```c
  TutPowerup = PowerUpDataController__GetTutPowerup(instance, nullptr);
```

It calls `GetTutPowerup()` ("get tutorial power-up", I guess...). To know what it returns, I searched for it in `dump.cs`:

```bash
grep -B1 "GetTutPowerup()" dump.cs # `-B1` also prints the line before, which holds the address
```

```text
	// RVA: 0x2AB62DC Offset: 0x2AB22DC VA: 0x2AB62DC
	public Nullable<PowerupKind> GetTutPowerup() { }
```

`Nullable<PowerupKind>` means "a `PowerupKind`, or nothing". In memory it's a tiny struct:

```text
+0  hasValue   (1 byte)    is there a value?
+4  value      (4 bytes)   the PowerupKind
```

8 bytes in total, so it fits in one register, and the function returns it as a single 64-bit number. That's why IDA has to cut it in two:

| IDA | Means |
|---|---|
| `(_BYTE)TutPowerup` | the lowest byte → `hasValue` |
| `HIDWORD(TutPowerup)` | the high 32 bits → `value` |

So the `if` reads:

```c
  if ( HIDWORD(TutPowerup) == 2 && (_BYTE)TutPowerup )
```

"if there is a tutorial power-up, and it's power-up number 2".

Number 2 of what? Another `grep` with `enum PowerupKind` :

```text
public enum PowerupKind // TypeDefIndex: 1265
{
	// Fields
	public int value__; // 0x0
	public const PowerupKind BreakItem = 0;
	public const PowerupKind Freeze = 1;
	public const PowerupKind Swap = 2;
	public const PowerupKind Replace = 3;
}
```

**2 is `Swap`.**

The block inside the `if`:

```c
    v72->static_fields->isTut = 1;
    GenLevelController__ShuffleItemVisual(this, v70);
    GenLevelController__ShuffleItemInCell(this, v73);
```

Sets a global "we're in a tutorial" flag, then runs two shuffles

In C#:

```csharp
PowerupKind? tut = PowerUpDataController.instance.GetTutPowerup();
if (tut.HasValue && tut.Value == PowerupKind.Swap)
{
    PowerupInGameController.isTut = true;
    ShuffleItemVisual();
    ShuffleItemInCell();
}
```

### What it means

**`ShuffleItemInCell` only runs during the tutorial of the Swap power-up.** Not on normal levels.

I have a guess about why: the function spreads identical items apart, which might make a board that a player can only solve by swapping, so it could be a good board to teach the Swap power-up. But that is a guess.

I'll be honest: that one stung a bit. I spent hours decoding a function, line by line, before asking the simplest question: **does it even run?** And the only way I had to answer it was to read more code: find the caller, decode its condition, look up two more types.

There has to be a faster way to know what a game is *actually* doing. Keep that thought, we'll come back to it.

## Answering the question

Back to the question from [05](05-game-mecanics.md): is the shuffle global, or per cell?

For `ShuffleItemInCell`: **neither, it's per layer.**

```text
            layer 0          layer 1
cell A    [ 74 74  0 ]      [ 77 63  0 ]
cell B    [ 63  0  5 ]      [ 74  0 74 ]
cell C    [  5 218 0 ]      [ 12 12  0 ]
               ↕                  ↕
         mixed together     mixed together
                  (never with each other)
```

- Items move between cells, but never change layer.
- Nothing is added or removed.
- Identical items are spread apart.

But this function only runs during the Swap tutorial. **So it does not explain what we can se on normal levels.** The randomness of a normal level comes from somewhere else.

That's the most important lesson of this step: understanding what a function does is only half the job. Always check **when** it runs.

## Next

`SetupCells` is the function that prepares the board. The tutorial is one branch of it. The normal levels must be handled in the rest of it.

Remember the other two methods from [05](05-game-mecanics.md): `ShuffleItemVisual` and `ShuffleCellPos`. `ShuffleItemVisual` already showed up in the tutorial branch, but `ShuffleCellPos` hasn't. That's where we'll go next: open `SetupCells`, and find what happens when it's **not** the tutorial.

[08 - Normal levels](08-normal-levels.md)
