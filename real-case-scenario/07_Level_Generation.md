# 07 — Level Generation and Preprocessing

## 07.0 Goal of this chapter

This chapter explains what happens to the serialized level data **before actual runtime item objects are spawned**.

The core question is:

> Given a level asset containing `LevelInfo`, `CellInfo`, layers, item IDs, and mechanics, how does the game transform that blueprint before it becomes the board the player sees?

This chapter covers:

- serialized board structure
- `GenLevelController.SetupCells`
- normal versus special generation paths
- `ShuffleItemVisual`
- `ShuffleCellPos`
- `ShuffleItemInCell`
- item counting
- `LevelInfo.itemHiden`
- seasonal/Halloween/Christmas replacement setup
- `UpdateDicItemReplace`

---

# 07.1 Serialized board model

The extracted classes and native access patterns establish the following hierarchy:

```text
LevelInfo
|
+-- cells : List<CellInfo>
|     |
|     +-- position : Vector2Int
|     |
|     +-- several cell configuration fields
|     |      currently known by offset, not all by semantic name
|     |
|     +-- layers : List<ItemsInLayerCell>
|            |
|            +-- items : List<int>
|
+-- itemHiden
|
+-- mechanics / other level configuration
```

A conceptual cell might look like:

```text
CellInfo

position = (2, 4)

layer 0:
[4, 4, 9]

layer 1:
[2, 0, 7]
```

## Confirmed item-slot convention

`ItemsInLayerCell.GetNumItem` counts only entries strictly greater than zero.

Later, runtime spawning confirms the same convention:

```text
0   -> empty slot
> 0 -> real item type
```

Therefore:

```text
[4, 0, 9]
```

means:

```text
slot 0 -> item type 4
slot 1 -> empty
slot 2 -> item type 9
```

The zero is not removed from the structure. It is part of the positional layout.

---

# 07.2 How `SetupCells` was found

The useful discovery path was:

```text
LevelInfo
   |
   v
search for classes that hold/use LevelInfo
   |
   v
GenLevelController
   |
   v
follow controller fields / methods
   |
   v
SetupCells
```

This distinction is important for future reverse engineering.

We did **not** begin by assuming the shuffle methods were central.

`SetupCells` was found as the high-level orchestration point, and the shuffle methods became important because `SetupCells` called them.

---

# 07.3 `SetupCells` establishes the generation order

At a high level, `SetupCells` performs:

```text
LevelInfo / CellInfo serialized blueprint
                 |
                 v
      update / preprocess cell data
                 |
                 v
         shuffle transformations
                 |
                 v
  count items + prepare hidden/seasonal state
                 |
                 v
          instantiate runtime Cells
                 |
                 v
        ItemContainer.SetupItem
```

Two generation branches matter.

## Normal branch

The normal setup path calls:

```text
ShuffleItemVisual
ShuffleCellPos
```

## Special tutorial / power-up branch

A separate branch calls:

```text
ShuffleItemVisual
ShuffleItemInCell
```

This is a critical behavioral distinction:

> `ShuffleItemInCell` is **not** the standard normal level-generation path in this `SetupCells` function.

Documentation and later analysis should never describe it as something every level does.

---

# 07.4 `ShuffleItemVisual` — global identity permutation

## Purpose

The best concise description is:

> **Change what each item type represents while keeping every cell, layer, and slot in exactly the same place.**

It does not randomly move individual items.

It creates a global mapping:

```text
old item ID -> replacement item ID
```

and applies that mapping everywhere.

---

# 07.5 Stage 1 — collect every unique positive item ID

The function walks:

```text
every CellInfo
    |
    v
every ItemsInLayerCell
    |
    v
every integer item ID
```

For each item:

```csharp
if (item > 0 && !uniqueItems.Contains(item))
    uniqueItems.Add(item);
```

Example board:

```text
Cell A layer 0: [4, 4, 9]
Cell B layer 0: [9, 2, 0]
Cell C layer 0: [4, 2, 0]
```

produces:

```text
uniqueItems = [4, 9, 2]
```

Important properties:

- duplicates collapse into one ID
- zero is ignored
- the order is the first-encounter order before shuffling

This means the algorithm is operating on **item identities/types**, not individual copies.

---

# 07.6 Stage 2 — shuffle candidate identities

The unique ID list is shuffled.

Normal path:

```text
CollectionUtils.Shuffle<int>
```

Super League path:

```text
CollectionUtils.ShuffleBySeed<int>
```

```text
normal        -> ordinary shuffle
Super League  -> seeded shuffle
```

## The Super League seed

The seed is the sum of two integers:

```csharp
seed = LevelDataController.Instance.NumPlay
     + SuperLeagueDataController.Instance.Data.season;
```

Both reads are confirmed against `dump.cs`:

```text
SuperLeagueDataController._data   0x10   (SuperLeague)
SuperLeague.season               +0x10   (int)
```

In the decompiler this second read appears as a chain through a misapplied
`Il2CppClass` type — something like `((controller[1].klass)->_1).name`. That is not a
class name. `[1]` is `+0x10`, which is `_data`, and `_1.name` is another `+0x10`, which
is `season`. Retyping the variable to `SuperLeagueDataController_o *` makes the
decompiler print the real field names.

### Why this matters for fairness

The seed contains no player identity and no wall-clock time. It depends only on:

- how many levels this player has completed (`NumPlay`)
- which Super League season is currently running

So **every competitor sitting at the same play count during the same season receives an
identical board.** The seeded path is not a cosmetic difference from the normal path —
it is what makes the competition comparable between players.

---

# 07.7 Stage 3 — prefer color-compatible replacement candidates

For each original item ID:

```text
SOItemContainer.GetItemAsset(originalId)
```

retrieves the corresponding `ItemAsset`.

The function then inspects remaining shuffled candidates and calls:

```text
ItemAsset.IsSameColor(...)
```

to find a compatible replacement.

Conceptually:

```csharp
foreach (int original in uniqueItems)
{
    replacement = first remaining candidate
                  whose ItemAsset color is compatible;

    if no compatible candidate exists:
        replacement = first remaining candidate;
}
```

This means the shuffle is not a completely unconstrained permutation.

The code tries to preserve some item-color compatibility.

## Two ways to reach the index-0 fallback

The fallback is not only the "no compatible candidate" case. There are **two** routes to
it, and only one is a colour decision:

**Route 1 — the colour scan finds nothing.** Every remaining candidate is walked,
`IsSameColor` returns false for all of them, and the loop falls out the bottom. This is
the case described above.

**Route 2 — the colour scan never runs.** Before the scan there is a guard equivalent to:

```csharp
if (asset == null || asset.colors == null)
    goto takeFirstCandidate;
```

If `SOItemContainer.GetItemAsset` returns null for the ID, or the asset exists but its
`colors` array is null, the function jumps straight to index 0 without comparing
anything.

Both routes produce the same observable outcome — `candidates[0]` is taken — so the two
are indistinguishable from the resulting board. They mean different things, though.
Route 1 is the algorithm working and finding no colour match. Route 2 is missing or
incomplete asset data, and any item ID whose asset lacks a `colors` array silently opts
out of colour-aware shuffling for the whole level.

There is also a dead third path: a `_size == 0` check leading to `get_Item(candidates, 0)`
on an empty list. It cannot be reached in practice — the candidate list starts with the
same count as the list of originals and loses exactly one entry per outer iteration, so
it is never empty while the loop is still running.

---

# 07.8 Stage 4 — consume replacement candidates one time only

Once a replacement is selected:

```text
replacementList.Add(candidate)
candidateList.RemoveAt(candidateIndex)
```

Therefore one replacement ID cannot be assigned to two original IDs.

This creates a one-to-one permutation.

Example:

```text
original IDs:
[4, 9, 2]

candidate IDs after shuffle:
[2, 4, 9]
```

Possible assignment:

```text
4 -> 2
9 -> 4
2 -> 9
```

After `2` is assigned to `4`, it is removed from the candidate pool.

---

# 07.9 Stage 5 — build the identity map

The two corresponding lists are turned into:

```csharp
Dictionary<int, int>
```

Example:

```text
4 -> 2
9 -> 4
2 -> 9
```

This dictionary is the central result of `ShuffleItemVisual`.

---

# 07.10 Stage 6 — rewrite every positive slot

The function walks every cell/layer/item again:

```csharp
if (items[i] > 0)
    items[i] = map[items[i]];
```

Example:

```text
BEFORE

Cell A: [4, 4, 9]
Cell B: [2, 9, 0]
```

with map:

```text
4 -> 2
9 -> 4
2 -> 9
```

becomes:

```text
AFTER

Cell A: [2, 2, 4]
Cell B: [9, 4, 0]
```

## What did not change?

```text
Cell A remained Cell A
Cell B remained Cell B

layer indices did not change
slot indices did not change
zero slots remained zero
the number of copies in each matching group remained the same
```

Therefore the correct mental model is:

> **`ShuffleItemVisual` shuffles item identity globally, not physical placement.**

---

# 07.11 Why identity shuffling is useful

Suppose the level designer intentionally created:

```text
Apple Apple Apple
Shoe  Shoe  Shoe
Cup   Cup   Cup
```

in carefully chosen slots.

If the engine independently moved individual items, it could destroy puzzle relationships.

Instead, a type permutation can produce:

```text
All Apples -> Cups
All Shoes  -> Apples
All Cups   -> Shoes
```

Every copy of the same original type changes together.

Therefore the combinatorial structure of the board survives while its visual/item identity changes.

---

# 07.12 `ShuffleCellPos` — move whole cell blueprints

## Purpose

`ShuffleCellPos` does not shuffle individual items.

It changes the `Vector2Int` positions assigned to compatible `CellInfo` objects.

The entire `CellInfo` contents remain associated with that object.

---

# 07.13 Cell compatibility grouping

The function iterates:

```text
this->_levelInfo->cells
```

and computes a dictionary key from three `CellInfo` properties.

Current native offsets:

```text
CellInfo +0x18
CellInfo +0x20
CellInfo +0x24
```

The grouping key is effectively:

```csharp
key =
    field18 * 1000 +
    field20 * 100 +
    (int)(field24 * 10.0f);
```

## Confidence rule

The offsets and arithmetic are **CONFIRMED**.

The semantic names of all three properties are **UNKNOWN**.

Do not replace these with guessed names in documentation.

---

# 07.14 Special early-return rule

While iterating cells, the function checks:

```text
CellInfo +0x20 == 1
```

If this is true, `ShuffleCellPos` returns.

This is confirmed behavior.

The semantic meaning of `+0x20` remains unknown.

Safe wording:

> Position shuffling is disabled when this specific `CellInfo` configuration field equals `1`.

Unsafe wording:

> “Position shuffling is disabled for [guessed cell type].”

---

# 07.15 Extract positions

For each compatibility group, the function creates:

```csharp
List<Vector2Int>
```

containing the positions of the cells in that group.

Conceptually:

```text
Cell A -> (0,0)
Cell B -> (2,0)
Cell C -> (4,0)

positions = [(0,0), (2,0), (4,0)]
```

---

# 07.16 Shuffle the position list

Normal:

```text
CollectionUtils.Shuffle<Vector2Int>
```

Super League:

```text
CollectionUtils.ShuffleBySeed<Vector2Int>
```

Again, this mirrors `ShuffleItemVisual`:

```text
normal        -> unseeded/random shuffle
Super League  -> seeded shuffle
```

---

# 07.17 Write positions back to the same `CellInfo` objects

After shuffling:

```text
[(0,0), (2,0), (4,0)]
```

might become:

```text
[(4,0), (0,0), (2,0)]
```

Those coordinates are written back to the original `CellInfo` objects in group order.

Example:

```text
BEFORE

CellInfo A
position = (0,0)
items = [4,4,9]

CellInfo B
position = (2,0)
items = [7,2,2]
```

Possible result:

```text
AFTER

CellInfo A
position = (2,0)
items = [4,4,9]

CellInfo B
position = (0,0)
items = [7,2,2]
```

The contents stayed with the logical `CellInfo`.

Only the coordinate changed.

---

# 07.18 `ShuffleItemInCell` — special cross-cell redistribution

This function is more invasive.

It can move item IDs between cells.

Again:

> This function is used in the special branch observed in `SetupCells`, not the standard normal path.

---

# 07.19 Eligible cells

A cell is included when:

```csharp
(field18 == 0 || field18 == 3)
&& field20 != 1
```

where:

```text
field18 = CellInfo +0x18
field20 = CellInfo +0x20
```

The numeric condition is confirmed.

The semantic property names are not.

The function also determines the maximum number of layers among eligible cells.

---

# 07.20 Process one layer index at a time

This is the most important structural property.

The function iterates:

```text
layer 0
layer 1
layer 2
...
```

For a given layer index, it collects each eligible cell's `ItemsInLayerCell` at that exact index.

It does not put layer-0 items into layer 1.

Example:

```text
Cell A:
layer 0 = [4,4,0]
layer 1 = [7,0,0]

Cell B:
layer 0 = [2,9,0]
layer 1 = [8,8,0]
```

Layer 0 and layer 1 are processed independently.

---

# 07.21 Pool all nonzero IDs for the current layer

For layer 0:

```text
A = [4,4,0]
B = [2,9,0]
```

the pool becomes:

```text
[4,4,2,9]
```

Zero entries are not treated as items.

The function also performs counting/grouping work on the collected IDs before redistribution. Some of this appears intended to track repeated IDs, but the critical placement behavior is independently clear from the later code.

---

# 07.22 Clear all destination slots

Before placing the pooled items back, every slot in each participating `ItemsInLayerCell` is set to zero.

Example:

```text
BEFORE

A = [4,4,0]
B = [2,9,0]
```

temporarily becomes:

```text
A = [0,0,0]
B = [0,0,0]
```

This proves the function is rebuilding the layer distribution rather than making a few local swaps.

---

# 07.23 Shuffle destination containers

The participating `ItemsInLayerCell` list is shuffled using:

```text
CollectionUtils.ShuffleCurrentList<ItemsInLayerCell>
```

This changes tie/order effects during redistribution.

---

# 07.24 Balanced redistribution

For each item ID in the pool:

```text
GetCellLessItem(itemId, destinations)
        |
        v
GetIndexEmpty(destination)
        |
        v
destination.items[index] = itemId
```

This is strong evidence that the algorithm is occupancy-aware.

`GetCellLessItem` considers `ItemsInLayerCell.GetNumItem`.

`GetNumItem` counts positive IDs.

Therefore the destination decision is based at least partly on how full each candidate container is.

A special tie-break exists when two candidates have exactly two occupied slots. Its exact semantic goal has not been safely reconstructed and should not be over-described.

Safe wording:

> `GetCellLessItem` generally selects a least-filled destination, with an additional item-pattern tie-break in a specific two-item case.

---

# 07.25 Shuffle slot order afterward

After redistribution, each destination's internal integer list is shuffled:

```text
ShuffleCurrentList<int>
```

So the final layer gets both:

1. redistributed occupancy across cells
2. randomized slot order inside each destination

---

# 07.26 Correct summary of `ShuffleItemInCell`

```text
Eligible cells only
      |
      v
for each layer index
      |
      +--> collect layer containers
      +--> pool nonzero item IDs
      +--> clear all slots to zero
      +--> shuffle destination order
      +--> redistribute with GetCellLessItem
      +--> put each item in an empty slot
      +--> shuffle internal slot order
```

Properties:

```text
items may change cells      -> YES
items may change layer      -> NO, not in this function
empty-slot layout may move  -> YES
distribution is balanced    -> YES, occupancy-aware
normal SetupCells path      -> NO
```

---

# 07.27 `CountItemAndInitItemHidenAndSeasonalItem`

After shuffle preprocessing, `SetupCells` calls:

```text
CountItemAndInitItemHidenAndSeasonalItem
```

The name is long because the function performs several unrelated-looking setup responsibilities:

1. count positive item slots
2. collect unique item IDs
3. collect unique IDs by layer index
4. choose `itemHidens`
5. prepare Halloween replacements
6. prepare Christmas replacements
7. prepare general seasonal/event replacements
8. merge replacement dictionaries

---

# 07.28 Count every real item

The function walks:

```text
every cell
    |
    v
every layer
    |
    v
every item ID
```

For every positive ID:

```csharp
this->_totalItem++;
```

Example:

```text
[4,4,9,2,9,2,0]
```

produces:

```text
_totalItem = 6
```

Zero does not count as an item.

---

# 07.29 Collect unique item IDs

The same scan maintains a unique list of item types found in the level.

Example:

```text
slots:
[4,4,9,2,9,2]

unique IDs:
[4,9,2]
```

The function also builds a dictionary keyed by layer index where each value is a unique list of item IDs occurring in that layer.

This per-layer information is then flattened/combined into another unique list used later by seasonal replacement logic.

---

# 07.30 Important distinction: two different hidden systems

This game contains at least two systems with “hidden” naming.

They must not be conflated.

## System A — `LevelInfo.itemHiden`

This function selects **item types** into:

```text
GenLevelController.itemHidens
```

## System B — `HiddenLayerItem`

This is a mechanic driven by exact `MechanicSlotRef` locations and an overlay/controller.

They are separate systems with different representations and different runtime controllers.

---

# 07.31 `LevelInfo.itemHiden` selects item types

When:

```text
LevelInfo.itemHiden >= 1
```

the function approximately performs:

```csharp
candidates = uniqueItemIds.Except(itemReverse).ToList();
candidates = Shuffle(candidates);

count = Math.Min(levelInfo.itemHiden, candidates.Count);

this.itemHidens = candidates.Take(count).ToList();
```

Example:

```text
unique IDs:
[2,4,7,9,12]

itemReverse:
[9]

itemHiden:
2
```

After exclusion:

```text
[2,4,7,12]
```

After shuffle, perhaps:

```text
[7,2,12,4]
```

Final:

```text
itemHidens = [7,2]
```

This chooses item **IDs/types**.

It does not choose a cell/layer/slot coordinate.

There is also a hard-coded Tutorial Level 25 adjustment involving IDs `2` and `3`.

---

# 07.32 Seasonal/Halloween/Christmas replacement maps

The function can build:

```text
DicItemHalloween
DicItemChristmas
DicItemSeasonal
```

using controllers/utilities such as:

```text
BuildingHalloweenDataController
BuildingChristmasDataController
EventThemeController
ItemSeasonalUtils
SOItemContainer
RemoteConfigController
```

These dictionaries map integer item IDs to seasonal replacement assets.

This happens before the final merge.

---

# 07.33 `UpdateDicItemReplace`

This function creates:

```csharp
Dictionary<int, ItemAssetSeasonal> DicItemReplace
```

and merges three source dictionaries.

Order:

```text
1. DicItemSeasonal
2. DicItemHalloween
3. DicItemChristmas
```

Each insertion is guarded by:

```csharp
if (!DicItemReplace.ContainsKey(key))
    DicItemReplace.Add(key, value);
```

Therefore existing entries are never overwritten.

## Priority

```text
highest priority
      |
      v
DicItemSeasonal
DicItemHalloween
DicItemChristmas
      ^
      |
lowest priority
```

Example:

```text
Seasonal:
4 -> Seasonal4

Halloween:
4 -> Halloween4
7 -> Halloween7

Christmas:
4 -> Christmas4
7 -> Christmas7
9 -> Christmas9
```

Final:

```text
4 -> Seasonal4
7 -> Halloween7
9 -> Christmas9
```

## Important limitation

`UpdateDicItemReplace` only constructs the final dictionary.

It does **not** itself replace runtime items.

To understand when/how this map affects visuals/assets, inspect consumers of:

```text
GenLevelController.DicItemReplace
```

---

# 07.34 End of preprocessing

By the end of this chapter, the serialized `CellInfo` data has been transformed, but individual runtime item objects are not yet what we are focusing on.

The state is conceptually:

```text
LevelInfo.cells
      |
      +--> potentially remapped item IDs
      +--> potentially shuffled positions
      +--> possibly special redistributed contents
      |
      v
counting / hidden-type selection / seasonal map setup
      |
      v
ready for runtime Cell + ItemContainer materialization
```

The next chapter begins at `ItemContainer.SetupItem`.

---

# 07.35 Confirmed vs unresolved

## Confirmed

- `0` is an empty item slot.
- positive IDs are real item types.
- `ShuffleItemVisual` performs a global ID permutation.
- it preserves exact cell/layer/slot placement.
- it prefers color-compatible candidates.
- the index-0 fallback has two routes: no color match, or a null `ItemAsset`/`colors`.
- the Super League seed is `LevelDataController.NumPlay + SuperLeague.season`, so players
  at the same play count in the same season get an identical board.
- replacement assignment is one-to-one.
- `ShuffleCellPos` shuffles compatible `Vector2Int` coordinates.
- entire `CellInfo` contents remain with the logical object.
- `ShuffleItemInCell` is a special branch.
- it redistributes one layer index at a time.
- it clears and repopulates layer slots.
- destination selection uses occupancy information.
- `CountItem...` counts every positive slot into `_totalItem`.
- `LevelInfo.itemHiden` selects item IDs/types.
- `LevelInfo.itemHiden` is separate from `HiddenLayerItem`.
- seasonal maps are merged with a stable priority.

## Unresolved

- semantic names of `CellInfo +0x18`, `+0x20`, `+0x24`
- exact special tie-break intent inside `GetCellLessItem`
- exact downstream use of `DicItemReplace`
