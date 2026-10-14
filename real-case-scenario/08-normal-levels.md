# Walkthrough 08: How normal levels are shuffled

## Where we left off

At the end of [07](07-filling-the-board.md), I had fully decoded `ShuffleItemInCell`... and then found out it only runs during the tutorial of the Swap power-up.

So the question from [05](05-game-mecanics.md) was still open for normal levels:

> When I replay a level, the board looks different. What exactly changes?

The only caller of `ShuffleItemInCell` was a function called `GenLevelController.SetupCells`. That's where we start.

## Step 1: Exporting `SetupCells`

The fastest way I found is to search in the file for the calls to other functions of the same class. They're easy to spot, they all start with `GenLevelController__`:

- lines 486–487: `ShuffleItemVisual` + `ShuffleItemInCell`. That's the Swap tutorial from 07.
- lines 469–470: `ShuffleItemVisual` + **`ShuffleCellPos`**. A different pair, just above.

So normal levels probably use `ShuffleCellPos`. But "probably" is what got me in trouble just before. Let's read the condition around it first.

## Step 2: When does `ShuffleCellPos` run?

Here are lines 447 to 471:

```c
    if ( this->fields.currentLevelPlaying >= 2 )
    {
        instance = (__int64)NMSingleton_object___get_instance((const MethodInfo_4A23EB4 *)Method_NMSingleton_LevelDataController__get_instance__);
        if ( !instance )
        goto LABEL_119;
        if ( LevelDataController__get_NumPlay((LevelDataController_o *)instance, nullptr) >= 2
        && !this->fields.isTutMechanic )
        {
        goto LABEL_37;
        }
    }
    instance = (__int64)NMSingleton_object___get_instance((const MethodInfo_4A23EB4 *)Method_NMSingleton_SuperLeagueDataController__get_instance__);
    if ( !instance )
        goto LABEL_119;
    if ( SuperLeagueDataController__IsActive((SuperLeagueDataController_o *)instance, nullptr) )
    {
    LABEL_37:
        GenLevelController__ShuffleItemVisual(this, v68);
        GenLevelController__ShuffleCellPos(this, v69);
    }
```

It looks messy, but it's a stack of `if`s. Let's go one at a time.

### The first `if`: level and number of plays

```c
    if ( this->fields.currentLevelPlaying >= 2 )
    {
      // ...
      if ( LevelDataController__get_NumPlay((LevelDataController_o *)instance, nullptr) >= 2
        && !this->fields.isTutMechanic )
      {
        goto LABEL_37;
      }
    }
```

Three conditions, all with readable names:

- `currentLevelPlaying >= 2`: the level number is 2 or more.
- `LevelDataController.NumPlay >= 2`: some counter is 2 or more.
- `!isTutMechanic`: this is not a level that teaches a new mechanic.

`NumPlay` is the only one that's not obvious. Let's look for it in `dump.cs`.

```bash
grep -B1 "get_NumPlay()" dump.cs
```

```text
	// RVA: 0x2AB2444 Offset: 0x2AAE444 VA: 0x2AB2444
	public int get_NumPlay() { }
```

`get_NumPlay` is how C# compiles a **property**: `NumPlay { get; }`. It returns an `int`. The name suggests "how many times the level has been played". I haven't read its code, so that's an inference.

### `goto LABEL_37`

```c
        goto LABEL_37;
      }
    }
    // ...
    if ( SuperLeagueDataController__IsActive((SuperLeagueDataController_o *)instance, nullptr) )
    {
LABEL_37:
      GenLevelController__ShuffleItemVisual(this, v68);
      GenLevelController__ShuffleCellPos(this, v69);
    }
```

`goto` jumps to a label. Here it jumps **straight inside** the last `if`, skipping the Super League test.

So there are two ways to reach the shuffle:

1. level ≥ 2, and `NumPlay` ≥ 2, and not a mechanic tutorial,
2. **or** Super League is active.

### All together

```csharp
if (!EditorController.instance.IsEditor)
{
    bool normalLevel = currentLevelPlaying >= 2
                    && LevelDataController.instance.NumPlay >= 2
                    && !isTutMechanic;

    if (normalLevel || SuperLeagueDataController.instance.IsActive())
    {
        ShuffleItemVisual();
        ShuffleCellPos();
    }
}
```

So the board is **not** shuffled on level 1, on tutorial levels, or before `NumPlay` reaches 2 (no point in shuffling if we see it for the first time). On every other level, `ShuffleCellPos` runs.

## Step 3: What `ShuffleCellPos` does

### The big picture

Here is what the function does, in plain words:

```text
1. sort the cells into groups
2. in each group, mix the positions of the cells on the board
```

That's all. `ShuffleCellPos` doesn't touch any item. It only changes the positions of the cells.

> **A first example: level 123.** On level 123, the level we've followed since [04](04-opening-assets.md), all 12 cells are the same kind, so there is only **one group**, and the function simply shuffles all 12 positions together. The grouping only changes anything on levels that have moving cells, like level 15 below.

### Our example: level 15

To see grouping at work, I needed a level where it does something. I searched the exported level files for cells that move (`moveType` from 2 to 5), and level 15 was the earliest one I found. Here are its 19 cells, straight from `Lv15_10.asset` (I only kept the fields we need):

```text
cell   posX  posY   cellType  moveType  speed   isLock
 0      -3    -2       0         0        0       0
 1      -9     2       0         0        0       0
 2       3    -2       0         0        0       0
 3       3     6       0         0        0       1
 4       3     2       0         0        0       0
 5      -9     6       0         0        0       1
 6      -3     6       0         0        0       0
 7      -3     2       0         0        0       0
 8      -9    -2       0         0        0       0
 9     -15    -6       3         3        0.8     0
10      -9    -6       3         3        0.8     0
11      -3    -6       3         3        0.8     0
12       3    -6       3         3        0.8     0
13       9    -6       3         3        0.8     0
14     -15   -10       3         2        0.8     0
15      -9   -10       3         2        0.8     0
16      -3   -10       3         2        0.8     0
17       3   -10       3         2        0.8     0
18       9   -10       3         2        0.8     0
```

Three kinds of cells show up:

- cells 0 to 8: `cellType 0`, `moveType 0`. Still cells (`CellNormal`, `Idle`).
- cells 9 to 13: `cellType 3`, `moveType 3`. Moving cells (`CellMove`, `Left`).
- cells 14 to 18: `cellType 3`, `moveType 2`. Moving cells (`CellMove`, `Right`).

The numbers come from the enums in `dump.cs`.

## Step 4: Making the groups

### The number computed for each cell

The first loop goes through every cell and computes a number for it:

```c
    v40 = 100 * klass + 1000 * LODWORD(v92.fields._current[1].monitor);
```

The names are bad here (`klass`, `monitor`). IDA got the types wrong in this loop. But we know how to read it from the memory basics in 07: `[1].monitor` is 16 + 8 = 24 bytes in, which is `0x18`, and `0x18` in `CellInfo` is `cellType`. Same idea for `klass`: it was read just above at `0x20`, which is `moveType`.

So, with real names, the number is:

```csharp
int key = 1000 * cell.cellType + 100 * cell.moveType + (int)(cell.speed * 10);
```

(The `speed` part comes from a few lines above, same trick.)

You don't need to follow the maths. The idea is: **two cells get the same number only if they have the same type, the same movement and the same speed.**

### Our example

Applying the formula to level 15:

```text
group 0      →  cells 0 to 8     (9 still cells)
group 3308   →  cells 9 to 13    (5 cells moving Left)
group 3208   →  cells 14 to 18   (5 cells moving Right)
```

So a cell moving Left can never swap places with a cell moving Right, or with a still cell.

### One special case

Right at the start of the loop:

```c
    if ( klass == 1 )
    {
      // ...
      return;
    }
```

`klass` is `moveType`, and `1` is `Drop` (the `MoveType` enum from 07). `return` means "stop the whole function".

So **if a level has a single Drop cell, nothing is shuffled at all**.

So is the grouping a big deal? In one sense, no, and this part comes from the code. Whatever the groups are, `ShuffleCellPos` only writes `posX` and `posY`. It decides **where** cells sit, never **what is in them**, and the items are changed by another function (Step 6). The groups only limit which cells can trade places.

Whether that limit matters to a player is my opinion, not something the code says: I don't think it does much. A moving row stays a moving row, and a still cell stays among the still ones, so the board keeps its general shape.

## Step 5: Mixing the positions

### In each group

The second loop goes through each group, and does three things:

1. write down the position of every cell in the group,
2. shuffle that list of positions,
3. give the positions back to the cells, in the new order.

The middle step is a single line:

```c
      v80 = CollectionUtils__Shuffle_Vector2Int_(
              (System_Collections_Generic_List_T__o *)v59,
              (const MethodInfo_36A468C *)Method_CollectionUtils_Shuffle_Vector2Int___);
```

`Vector2Int` is Unity's type for a position on a grid: an `x` and a `y`. So this is "shuffle the list of positions".

And the last step writes the new positions into the cells:

```c
        v85->fields.posX = v93.fields.m_X;
```

`posX` gets the `x` of the new position. Same for `posY` just after.


### The result

What changes is where each cell sits. What's inside each cell isn't touched by this function: cell 9 still has its own items, wherever it ends up.

> **About Super League.** There's an `if` around the shuffle: in the Super League mode, the game uses `ShuffleBySeed` instead of `Shuffle`. A *seed* makes the "random" result always the same. My guess is that every player in the league gets the same board, to keep it fair. I didn't dig further.

## Putting it all together

```csharp
private void ShuffleCellPos()
{
    // 1. sort the cells into groups
    var groups = new Dictionary<int, List<CellInfo>>();
    foreach (var cell in _levelInfo.cells)
    {
        if (cell.moveType == MoveType.Drop)
            return;                          // no shuffle at all

        int key = 1000 * cell.cellType + 100 * cell.moveType + (int)(cell.speed * 10);
        groups[key].Add(cell);
    }

    // 2. in each group, mix the positions
    foreach (var group in groups.Values)
    {
        var positions = group.Select(c => new Vector2Int(c.posX, c.posY)).ToList();
        positions.Shuffle();

        for (int i = 0; i < group.Count; i++)
        {
            group[i].posX = positions[i].x;
            group[i].posY = positions[i].y;
        }
    }
}
```

## Step 6: The shuffle that changes what you see

`ShuffleCellPos` moves cells around, but on its own it can't explain what I saw in my own screenshots. On my phone, level 3 had 3 coke cans in one play, and none in the next. Cells moving doesn't change a coke can into something else.

There's one more call in the same `if`, right before `ShuffleCellPos`:

```c
      GenLevelController__ShuffleItemVisual(this, v68);
      GenLevelController__ShuffleCellPos(this, v69);
```

Its name says it all: shuffle the **visual** of the items. Let's export it:

### The big picture

```text
1. list every different item number used in the level
2. shuffle that list
3. pair each item number with a partner from the shuffled list
4. go through the whole level, and replace every item by its partner
```

So the function builds a **swap table**: "every `5` becomes a `71`, every `71` becomes a `218`...". Then it applies it to every slot.

### Where each step is in the code

**Step 1: list the item numbers.** Three loops, one inside the other: every cell, every layer, every slot. For each slot with an item in it (`>= 1`, so not empty), the number is added to a list if it isn't there yet:

```c
        current = v103.fields._current;
        if ( v103.fields._current >= 1 )
        {
          v45 = System_Collections_Generic_List_int___Contains(
                  v31,
                  v103.fields._current,
                  (const MethodInfo_487BA48 *)Method_System_Collections_Generic_List_int__Contains__);
          if ( !v45 )
          {
            // ... v31.Add(current)
```

`Contains` asks "is it already in the list?". If not, it's added. So `v31` ends up with every different item number, once each.

**Step 2: shuffle the list.**

```c
    v55 = CollectionUtils__Shuffle_int_(
            (System_Collections_Generic_List_T__o *)v31,
            (const MethodInfo_36A458C *)Method_CollectionUtils_Shuffle_int___);
```

The same `Shuffle` we saw in `ShuffleCellPos`. In Super League, `ShuffleBySeed` is used instead, like before.

**Step 3: pair them up.** For each item number, the game looks at the shuffled list and picks a partner. It uses `GetItemAsset` to get the item's data, and `IsSameColor` to compare colors:

```c
        v69 = SOItemContainer__GetItemAsset_2B1DC2C((SOItemContainer_o *)v67, v68, nullptr);
        if ( ItemAsset__IsSameColor(v63, v69->fields.colors, v71) )
          break;
```

The partner is the first one in the shuffled list with a matching color. Each partner is then removed from the list (`RemoveAt`), so it's used only once. At the end, `v100` holds the partners, in the same order as the original numbers.

When there's no same-color partner (or the item has no colors), the code jumps to `LABEL_49`, which takes the **first** item of the shuffled list (`get_Item(list, 0)`) and removes it. So the rule is: "first same-color partner, otherwise the first one left".

**Step 4: apply it.** Two lists, `v31` (the original numbers) and `v100` (their partners), are zipped into a `Dictionary<int,int>`:

```c
      System_Collections_Generic_Dictionary_int__int___Add(
        v51,
        v82,
        (int32_t)cells,
        (const MethodInfo_4226888 *)Method_System_Collections_Generic_Dictionary_int__int__Add__);
```

Then, one last time, every cell, layer and slot is visited:

```c
          if ( (int)v95 >= 1 )
          {
            v96 = v90->fields.items;
            v97 = System_Collections_Generic_List_int___get_Item(v90->fields.items, i, ...);
            v98 = System_Collections_Generic_Dictionary_int__int___get_Item(v51, v97, ...);
            System_Collections_Generic_List_int___set_Item(v96, i, v98, ...);
          }
```

In words: if the slot isn't empty, look up its partner in the dictionary, and write it back in the slot. That's the line that changes the coke cans.

### Our example

Keep the board from 07, layer 0 only:

```text
cell A    [ 74  74   0 ]
cell B    [ 63   0   5 ]
cell C    [  5 218   0 ]
```

1. The different numbers: `74, 63, 5, 218`.
2. After a shuffle, say: `218, 5, 74, 63`.
3. Pairs: `74 → 218`, `63 → 5`, `5 → 74`, `218 → 63` (I ignore the color rule here, to keep it simple).
4. Replace everything:

```text
cell A    [ 218  218   0 ]
cell B    [   5    0  74  ]
cell C    [  74   63   0  ]
```

Look at what is the same: inside each cell, the empty slots (`0`) are still in the same slot, and the two `74` of cell A are still two identical items, now `218`. The **pattern** of each cell didn't change, only what the items are.

(Careful, this is only about what `ShuffleItemVisual` does. On the board, `ShuffleCellPos` moves whole cells around, and the empty slots travel with their cell. So the empty slots do end up in different places on screen from one play to the next.)

### What this explains

- **The coke cans.** Three cans in one play and none in the next is this table: the number that was a coke can is paired with another item.
- **The number of empty slots.** `ShuffleItemVisual` skips zeros (`>= 1`), and `ShuffleCellPos` only moves whole cells. So the number of empty slots in a level never changes. Where they sit on the board does change, because their cell moves.

## Answering the question

Back to [05](05-game-mecanics.md) one last time: when I replay a level, what changes?

**Two things, in this order.**

1. `ShuffleItemVisual` replaces every item number by a partner. So the items you see change.
2. `ShuffleCellPos` moves cells to other positions, but only among cells of the same type, movement and speed. The items inside a cell are not changed by this function.

And what stays: the pattern of the level (how items group, where the empty slots are inside each cell), the number of cells, and the timer.

That settles what I wrote in [05](05-game-mecanics.md):

> Since no matter how much I restarted the level, the layout was the same, I'm assuming the content of the cells are being changed.

I had the two functions mixed up. The **layout** does change (`ShuffleCellPos`). And the **items** change too (`ShuffleItemVisual`), which is what I was really noticing.

## The full picture

```text
SetupCells
 │
 ├─ normal level (level ≥ 2, NumPlay ≥ 2, not a tutorial)
 │  or Super League                       → ShuffleItemVisual   (items change)
 │                                         + ShuffleCellPos     (cells change places)
 │
 └─ Swap power-up tutorial                → ShuffleItemVisual   (items change)
                                           + ShuffleItemInCell   (items move between cells, see 07)
```

## Taking a step back

Let's be honest about what the last two chapters cost.

To answer one question ("what changes when I replay a level?"), I had to:

```text
07   decode ShuffleItemInCell                  ~550 lines of pseudo-C
     find out it only runs in a tutorial       X, BL/DCQ, GetTutPowerup, Nullable, PowerupKind
08   read the conditions in SetupCells          NumPlay, isTutMechanic, goto
     decode ShuffleCellPos                      Dictionary, keys, offsets by hand
     decode ShuffleItemVisual                   another 500 lines
```

Several evenings of work. But everything in these chapters comes from code we actually read, and from checking it on the phone. That's the strength of this approach. We saw **every** branch, even the ones that almost never run, like the Swap tutorial.

But it's slow and painful. Reading code tells you what the game **can** do. It can't tell you what it **does** on your phone, right now, with your save. Also, it's hard to verify your claims, you think you can understand the code, but can you really? For `NumPlay`, I could keep reading `get_NumPlay`, then whatever it reads, then whatever that reads...

Or I could just **ask the game**.

What if, instead of reading `ShuffleCellPos` for hours, I could play a level and see a message pop up every time it runs? What if I could see the value of `NumPlay` the moment the game reads it? What if I could change it, and watch what happens?

## Next

The board is built. At the end of `SetupCells`, the level is handed to another class:

```c
  LevelController__InitData(
    (LevelController_o *)instance,
    (System_Collections_Generic_List_Cell__o *)v54,
    (float)levelInfo->fields.timeToPlay,
    levelInfo->fields.numberCellLock,
    v134,
    nullptr);
```

`timeToPlay` is the `300` seconds from `Lv123_10.asset`. It goes into `LevelController.InitData`, which also handles the locked cells. That's our next target.

But this time, we won't start by reading. We'll start by **watching the game run**.

[09 - Watching the game run](09-frida.md)
