# 08 — Runtime Board Materialization

## 08.0 Goal of this chapter

Chapter 07 ended with a transformed level blueprint.

This chapter explains how that blueprint becomes:

- runtime `Cell` objects
- runtime `ItemContainer` objects
- per-layer item-type blueprints
- actual spawned `ItemBase` objects
- runtime layer/index coordinates
- spawn events consumed by mechanics

The central distinction is:

```text
_itemTypes = numeric runtime blueprint
_items     = actual runtime ItemBase objects
```

---

# 08.1 Where materialization begins

After preprocessing, `GenLevelController.SetupCells` loops through `CellInfo` entries and creates runtime cells.

High-level flow:

```text
CellInfo
   |
   +--> position
   +--> cell configuration
   +--> List<ItemsInLayerCell>
   |
   v
select / instantiate Cell asset
   |
   v
runtime Cell
   |
   v
runtime ItemContainer
   |
   v
ItemContainer.SetupItem(...)
```

The important architectural fact is:

> The shuffle functions modify the serialized/runtime blueprint **before** `ItemContainer.SetupItem` converts that data into runtime item layers.

---

# 08.2 `ItemContainer.SetupItem` signature

IDA recovers:

```c
ItemContainer__SetupItem(
    ItemContainer_o *this,
    Cell_o *cell,
    List<ItemsInLayerCell> *itemInfos,
    Nullable<int> topLayerOriginOverride,
    ...
)
```

This is the key bridge from `CellInfo` layer data to an `ItemContainer`.

---

# 08.3 Associate the container with its cell

The first important assignment is:

```csharp
this->_cell = cell;
```

Therefore the runtime hierarchy includes:

```text
Cell
 |
 +--> ItemContainer
```

An `ItemContainer` is not an independent global item list. It belongs to a specific runtime cell.

This later becomes important for `MechanicSlotRef` resolution.

---

# 08.4 What if `itemInfos` is empty?

If:

```text
itemInfos == null
```

or:

```text
itemInfos.Count == 0
```

the function creates a default layer structure.

It consults a dictionary/configuration in `InGameDataConst` keyed by:

```text
cell._cellType
```

The lookup returns a `Vector2Int`.

The function uses the `.x` component as a slot count.

It then constructs a new `ItemsInLayerCell` whose integer list contains that many zeros.

Conceptual example:

```text
cell type configuration says 3 slots

generated default layer:
[0,0,0]
```

This means even a cell with no serialized item data has a defined slot layout.

---

# 08.5 Store initial layer metadata

The function sets:

```text
_numLayerWhenInit = itemInfos.Count
_currentTopLayerOrigin = topLayerOriginOverride.value
```

The exact meaning and lifecycle of `currentTopLayerOrigin` are outside the current scope, but the assignment is confirmed.

---

# 08.6 Clear `_itemTypes`

`this->_itemTypes` is cleared before rebuilding.

This object is a list of per-layer structures:

```text
List<ListItemType>
```

Each `ListItemType` contains a list of item-type integers.

This is the runtime numeric blueprint.

---

# 08.7 Convert every `ItemsInLayerCell` into a `ListItemType`

For each serialized/preprocessed layer:

```text
ItemsInLayerCell
    |
    +--> items : List<int>
```

`SetupItem` creates:

```text
ListItemType
```

and copies every integer item ID into its item-type list.

Example input:

```text
itemInfos[0] = [4,4,9]
itemInfos[1] = [2,0,7]
```

becomes:

```text
_itemTypes[0] = [4,4,9]
_itemTypes[1] = [2,0,7]
```

Nothing is spawned yet.

These are still integer type IDs.

---

# 08.8 `_itemTypes` is not `_items`

This is essential.

## `_itemTypes`

```text
List<ListItemType>
```

Purpose:

```text
layer blueprint
integer item IDs
empty slots represented by 0
```

Example:

```text
_itemTypes[1] = [2,0,7]
```

## `_items`

Later populated as:

```text
List<ListItem>
```

where each `ListItem` contains:

```text
List<ItemBase>
```

Example:

```text
_items[1] = [ItemBase(type 2), null, ItemBase(type 7)]
```

The slot indices line up between the two.

---

# 08.9 `OnLayersInitialized`

After `_itemTypes` is built, the function fires:

```text
OnLayersInitialized
```

with information including:

```text
this ItemContainer
_currentTopLayerOrigin
number of runtime blueprint layers
```

The exact listeners have not been analyzed here.

Important point:

> This event happens after the numeric layer blueprint exists and before/around initial runtime layer spawning.

---

# 08.10 Decide how many layers initially appear

The function calls:

```text
ItemContainer.GetNumLayerAppear
```

and clamps the result to the actual number of `_itemTypes` layers.

Then:

```csharp
for (int i = 0; i < layersToAppear; i++)
{
    ActiveItemInLayer(
        i,
        i == 0,
        0.0f);
}
```

Therefore:

```text
layer 0 -> active=true
higher initial layers -> active=false
```

The exact visual/gameplay semantics of `active` are not fully identified because it is passed into an unidentified virtual `ItemBase` method.

Do not rename that parameter's effect beyond the literal boolean behavior yet.

---

# 08.11 `ActiveItemInLayer`

Recovered signature:

```c
ItemContainer__ActiveItemInLayer(
    ItemContainer_o *this,
    int32_t layer,
    bool active,
    float timeDelay,
    ...
)
```

This is the function where numeric item IDs become actual runtime items.

---

# 08.12 Runtime layer creation condition

The function checks:

```text
_items.Count == layer
```

When true, it creates the next runtime `ListItem`.

This is consistent with sequential layer construction.

The new `ListItem` receives a fresh:

```text
List<ItemBase>
```

So conceptually:

```text
_items[layer]
    |
    +--> runtime ItemBase slots
```

---

# 08.13 Read the layer's numeric blueprint

The function obtains:

```text
_itemTypes[layer]
```

and loops over every item-type slot.

Suppose:

```text
_itemTypes[1] = [4,0,9]
```

The function processes:

```text
index 0 -> 4
index 1 -> 0
index 2 -> 9
```

---

# 08.14 Zero means runtime `null`

For:

```text
itemType == 0
```

the function sets the runtime item variable to `null`.

It still appends that null to the runtime layer list.

Therefore:

```text
blueprint:
[4,0,9]
```

becomes:

```text
runtime:
[ItemBase(type 4), null, ItemBase(type 9)]
```

This preserves positional indices.

This is one of the strongest confirmations that `0` is an empty slot rather than a special item type.

---

# 08.15 Nonzero IDs call `ItemUtils.SpawnItem`

For:

```text
itemType != 0
```

the function calls:

```text
ItemUtils.SpawnItem(itemType)
```

This is the observed runtime item creation point.

Conceptual chain:

```text
integer ItemType
      |
      v
ItemUtils.SpawnItem
      |
      v
ItemBase object
```

---

# 08.16 Retrieve the item's asset

Immediately around initialization, the function obtains:

```text
SOItemContainer.Instance
```

then:

```text
SOItemContainer.GetItemAsset(itemType)
```

Therefore the newly created item is initialized with both:

```text
type ID
asset data
```

This ties the runtime object back to the asset catalog discovered earlier.

---

# 08.17 Virtual item initialization call

The spawned object's virtual table is used to call a method with arguments equivalent to:

```text
spawned ItemBase
ItemContainer
ItemAsset
active
slot index
```

The exact managed method name has not yet been identified.

Safe documentation:

> A virtual `ItemBase` initialization/setup-style method is called with the owning container, resolved item asset, active flag, and slot index.

Unsafe documentation:

> Calling it `ItemBase.Setup` or another guessed method name before resolving the vtable slot.

---

# 08.18 Runtime location fields

Immediately after the virtual call, two values are stored on the item.

Later HiddenLayer decompilation reveals their real managed names:

```text
_LastRuntimeLayer_k__BackingField
_LastIndexInLayer_k__BackingField
```

So each spawned item explicitly tracks:

```text
item.itemContainer

item.LastRuntimeLayer

item.LastIndexInLayer
```

Example:

```text
ItemBase(type 9)

itemContainer = Cell X's ItemContainer
LastRuntimeLayer = 1
LastIndexInLayer = 2
```

This is central to mechanic targeting.

---

# 08.19 `InGameEvent.onSpawnItem`

After the item's runtime location is assigned, the game invokes:

```text
InGameEvent.onSpawnItem(item)
```

This creates a generic extension point for mechanics/controllers.

The HiddenLayer system subscribes to this exact event.

Therefore the runtime chain is:

```text
_itemTypes[layer][index]
        |
        v
ItemUtils.SpawnItem
        |
        v
ItemBase initialization
        |
        v
assign itemContainer/layer/index
        |
        v
InGameEvent.onSpawnItem
        |
        v
mechanic listeners
```

That ordering matters.

A mechanic receiving `onSpawnItem` already has access to the item's runtime location fields.

---

# 08.20 Store the result into `_items`

Whether the slot produced:

```text
ItemBase
```

or:

```text
null
```

the result is appended to the new runtime `ListItem`.

This gives exact positional correspondence:

```text
_itemTypes[layer][index]
        |
        v
_items[layer][index]
```

Example:

```text
_itemTypes[0]
[12, 0, 3]

_items[0]
[ItemBase(12), null, ItemBase(3)]
```

---

# 08.21 `timeDelay`

If:

```text
timeDelay > 0
```

the function:

1. iterates newly created runtime items
2. calls another virtual method with argument `0`
3. creates an `Action`
4. schedules:
   ```text
   MonoUtils.Delay(this, timeDelay, callback, ...)
   ```

The callback is:

```text
ItemContainer.<ActiveItemInLayer>b__0
```

The exact meaning of that virtual method and delayed callback has not yet been analyzed.

The safest current interpretation is:

> `ActiveItemInLayer` supports deferred state/appearance behavior for a newly materialized layer.

Do not document the exact animation/visibility semantics until the virtual method and lambda are decompiled.

---

# 08.22 Concrete materialization example

Assume preprocessing produced:

```text
CellInfo logical cell

layer 0:
[12,12,3]

layer 1:
[8,15,0]
```

`ItemContainer.SetupItem` creates:

```text
_itemTypes[0] = [12,12,3]
_itemTypes[1] = [8,15,0]
```

Then `ActiveItemInLayer(0, true, 0)` creates:

```text
_items[0][0] -> ItemBase(type 12)
_items[0][1] -> ItemBase(type 12)
_items[0][2] -> ItemBase(type 3)
```

Each gets:

```text
LastRuntimeLayer = 0
LastIndexInLayer = 0 / 1 / 2
```

For layer 1:

```text
_itemTypes[1] = [8,15,0]
```

materialization gives:

```text
_items[1][0] -> ItemBase(type 8)
_items[1][1] -> ItemBase(type 15)
_items[1][2] -> null
```

with:

```text
LastRuntimeLayer = 1
LastIndexInLayer = 0 / 1
```

for the real objects.

---

# 08.23 Why this matters for HiddenLayerItem

A HiddenLayer mechanic does not need to identify an item by item type.

Every spawned `ItemBase` already has a runtime address tuple:

```text
ItemContainer
RuntimeLayer
IndexInLayer
```

The HiddenLayer attachment wrapper later passes exactly those three pieces of location information into its lower-level matching function.

That is why the runtime materialization work in this chapter directly explains how a serialized `MechanicSlotRef` can target a specific slot.

---

# 08.24 Confirmed vs unresolved

## Confirmed

- `ItemContainer` belongs to a runtime `Cell`.
- `SetupItem` receives `List<ItemsInLayerCell>`.
- empty `itemInfos` produces a default zero-filled layer.
- `_itemTypes` stores integer runtime blueprints.
- `_items` stores runtime object layers.
- `0` becomes runtime `null`.
- positive IDs call `ItemUtils.SpawnItem`.
- `SOItemContainer.GetItemAsset` is used for the item asset.
- runtime items store:
  - `itemContainer`
  - `LastRuntimeLayer`
  - `LastIndexInLayer`
- `onSpawnItem` fires after location assignment.
- HiddenLayer listens to the spawn event.

## Unresolved

- exact name of the vtable method called during item initialization
- exact semantics of `active`
- exact semantics of the second vtable method used in the delayed path
- exact delayed callback behavior
- exact consumers of `DicItemReplace` during item visual/asset initialization
