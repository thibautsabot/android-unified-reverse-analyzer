# 09 — HiddenLayerItem Mechanic

## 09.0 Goal of this chapter

The level asset contains mechanics with data shaped like:

```json
{
  "type": 12,
  "data": "{\"slots\":[\"7:1:1\", ...]}"
}
```

Asset extraction identified:

```text
type 12 -> HiddenLayerItem
```

and the mechanic data becomes:

```text
HiddenLayerItemData
    |
    +--> slots : List<MechanicSlotRef>
```

The goal is to determine:

1. how these refs become runtime state
2. how they are matched to actual `ItemBase` objects
3. what is saved/restored
4. what each component in `7:1:1` means

The first three are now largely reconstructed.

The exact first component of `MechanicSlotRef` still needs one lower-level attachment function before it should be called fully confirmed.

---

# 09.1 Do not confuse this with `LevelInfo.itemHiden`

The game contains two distinct concepts.

## `LevelInfo.itemHiden`

Handled by `GenLevelController.CountItemAndInitItemHidenAndSeasonalItem`.

Representation:

```text
List<int> itemHidens
```

Meaning:

```text
selected item TYPES/IDs
```

## `HiddenLayerItem`

Handled by:

```text
HiddenLayerItemController
HiddenLayerPersistenceService
HiddenLayerAttachmentService
```

Representation:

```text
MechanicSlotRef
```

Meaning:

```text
specific runtime/logical locations
```

These systems should be documented separately.

---

# 09.2 High-level architecture

The controller itself is mostly an orchestrator.

```text
HiddenLayerItemController
        |
        +--> HiddenLayerPersistenceService
        |       |
        |       +--> configured data
        |       +--> PendingSlots
        |       +--> ActiveItems
        |       +--> ContainerIndexCache
        |       +--> save / restore
        |
        +--> HiddenLayerAttachmentService
                |
                +--> scan existing board
                +--> test newly spawned items
                +--> attach overlay/runtime mechanic
```

This separation emerged directly from `InitData`, `TryProcessExistingItems`, `HandleSpawnItem`, and `RebuildSlotState`.

---

# 09.3 `HiddenLayerItemController.InitData`

Recovered conceptual behavior:

```csharp
void InitData(
    HiddenLayerItemData data,
    HiddenLayerItem overlayPrefab)
{
    if (data == null)
        data = new HiddenLayerItemData();

    _data = data;
    _overlayPrefab = overlayPrefab;

    EnsureServices();

    _persistenceService.ConfigureData(_data);

    // Attachment service receives/configures the overlay prefab.
    // IDA renders the target field incorrectly because of a bad inferred type.

    _persistenceService.RebuildSlotState();

    if (_stateData.PendingSlots.Count >= 1)
        Subscribe();
}
```

Important facts:

- null data is replaced by a new `HiddenLayerItemData`
- controller stores the data
- controller stores the overlay prefab
- services are ensured
- persistence service receives the data
- slot state is rebuilt
- controller subscribes only if pending slots exist

---

# 09.4 Decompiler type warning in `InitData`

IDA renders an assignment through a field named:

```text
m_CancellationTokenSource
```

on a pointer typed as `HiddenLayerItemController_o *`.

This should not be interpreted literally.

The value being assigned is `_overlayPrefab`, and the receiving object is actually the attachment service.

This is an example of a generic/native type recovery error.

The semantic takeaway is:

> The attachment service is configured with the overlay prefab.

Do not propagate the bogus `m_CancellationTokenSource` name into the game model.

---

# 09.5 `HiddenLayerPersistenceService.RebuildSlotState`

This function tells us how serialized mechanic slots enter runtime state.

First it clears:

```text
state.PendingSlots
state.ActiveItems
```

Then it obtains:

```text
_data.slots
```

which is:

```text
List<MechanicSlotRef>
```

and enumerates that list.

For each current `MechanicSlotRef`:

```csharp
state.PendingSlots.Add(slot);
```

The decompiler's local-variable reconstruction around the struct enumerator is awkward, but the method metadata and collection operations are clear:

```text
List<MechanicSlotRef>.GetEnumerator
Enumerator<MechanicSlotRef>.MoveNext
Enumerator<MechanicSlotRef>.get_Current
HashSet<MechanicSlotRef>.Add
```

Therefore the intended operation is an exact copy of configured refs into the pending set.

---

# 09.6 No coordinate transformation occurs in `RebuildSlotState`

This is important.

The function does not:

```text
parse world coordinates
look up CellInfo positions
remap layer numbers
remap item types
```

It simply copies the configured `MechanicSlotRef` values into:

```text
HashSet<MechanicSlotRef> PendingSlots
```

So:

```text
HiddenLayerItemData.slots
          |
          v
PendingSlots
```

is a direct configuration-to-runtime-state path.

---

# 09.7 `ContainerIndexCache`

At the end of rebuilding slot state, the persistence service clears:

```text
ContainerIndexCache
```

Its generic type is revealed by imported method metadata:

```text
Dictionary<ItemContainer, int>
```

This is one of the strongest clues for the first component of `MechanicSlotRef`.

Why would the HiddenLayer persistence/attachment system maintain:

```text
ItemContainer -> integer
```

unless it needs a stable numeric identifier for containers?

Current status:

- existence/type of the cache: **CONFIRMED**
- first `MechanicSlotRef` component is that integer: **STRONG INFERENCE**
- exact construction/use: waiting on lower-level attachment code

---

# 09.8 Meaning of `PendingSlots`

`PendingSlots` should be understood as:

> HiddenLayer mechanic locations that still need to be resolved/consumed.

Evidence:

1. configured refs are copied into it during initialization
2. controller only subscribes when it is non-empty
3. attachment logic is driven by it
4. `GetPendingSlots` exposes it
5. `LevelController.GetCurrentHiddenLayerItemData` saves it
6. `UpdateFromDataSaved` restores it

Conceptual lifecycle:

```text
configured slots:
A
B
C

player resolves A

remaining PendingSlots:
B
C

save game:
B
C
```

---

# 09.9 `HiddenLayerItemController.HandleGenerationDone`

When generation finishes, if the controller is not in state `2`, it does:

```csharp
TryProcessExistingItems();

InGameEvent.onSpawnItem += HandleSpawnItem;
```

This ordering solves an important race/order problem.

Some runtime items may already exist by the time HiddenLayer subscribes to the spawn event.

Therefore:

1. scan existing items first
2. listen for future item spawns afterward

---

# 09.10 `_controllerState == 2`

Several functions guard against state `2`.

`TryProcessExistingItems` sets:

```text
_controllerState = 2
```

around a full-board scan, then resets it.

This strongly indicates state `2` is a “currently scanning / processing” guard.

Safe wording:

> State `2` suppresses duplicate/re-entrant processing while the attachment service scans existing level items.

The exact enum name is unknown unless the managed enum/class definition gives it.

Do not invent a symbolic name in source-style pseudocode.

---

# 09.11 `TryProcessExistingItems`

The function:

1. ensures services
2. checks state is not `2`
3. checks `PendingSlots` exists and is non-empty
4. checks `LevelController` singleton exists
5. checks its `_itemContainers` exists
6. temporarily sets state `2`
7. calls:
   ```text
   HiddenLayerAttachmentService.ScanLevel(
       attachmentService,
       LevelController.Instance)
   ```
8. resets state

Conceptual flow:

```text
PendingSlots non-empty
        |
        v
runtime LevelController exists
        |
        v
AttachmentService.ScanLevel
        |
        v
inspect existing ItemContainers / ItemBase objects
        |
        v
attempt HiddenLayer attachment
```

The internal scan algorithm has not yet been decompiled, but its role is clear.

---

# 09.12 `HandleSpawnItem`

This function is intentionally tiny:

```csharp
void HandleSpawnItem(ItemBase item)
{
    EnsureServices();

    _attachmentService.TryAttachToItem(item);
}
```

This is the bridge from general item spawning into HiddenLayer logic.

---

# 09.13 Connection to `ItemContainer.ActiveItemInLayer`

Chapter 08 established:

```text
ActiveItemInLayer
      |
      v
ItemUtils.SpawnItem
      |
      v
ItemBase
      |
      +--> itemContainer
      +--> LastRuntimeLayer
      +--> LastIndexInLayer
      |
      v
InGameEvent.onSpawnItem(item)
```

`HandleGenerationDone` subscribes:

```text
HandleSpawnItem
```

to that same event.

Therefore the end-to-end connection is confirmed:

```text
runtime item spawned
       |
       v
item receives runtime location
       |
       v
onSpawnItem
       |
       v
HiddenLayerItemController.HandleSpawnItem
       |
       v
HiddenLayerAttachmentService.TryAttachToItem
```

---

# 09.14 `HiddenLayerAttachmentService.TryAttachToItem` wrapper

The wrapper performs a basic validity check.

It gets:

```text
item->fields.itemContainer
```

If the item has no container, it returns `false`.

Otherwise it calls a lower-level overload with:

```text
this
item
item->itemContainer
item->_LastRuntimeLayer_k__BackingField
item->_LastIndexInLayer_k__BackingField
```

This is one of the strongest discoveries in the entire mechanic investigation.

The matching system receives an explicit runtime tuple:

```text
ItemContainer
RuntimeLayer
IndexInLayer
```

---

# 09.15 What this proves about slot addressing

The runtime side does **not** match only on:

```text
item type ID
sprite
world position
```

The lower-level attachment function is explicitly given:

```text
container
layer
index
```

Therefore HiddenLayer targeting is location-oriented.

This also explains why `ShuffleItemVisual` should not break the mechanic.

A hidden slot can contain a different item identity after the visual/type remap, but it still has the same logical location tuple.

---

# 09.16 Current interpretation of `"7:1:1"`

We can now split the answer by confidence.

## Second component

Strongly associated with:

```text
_LastRuntimeLayer
```

Therefore:

```text
7 : 1 : 1
    ^
    |
runtime layer
```

This is extremely strong evidence, but the final `MechanicSlotRef` construction/comparison should still be read before calling the serialization order absolutely proven.

## Third component

Strongly associated with:

```text
_LastIndexInLayer
```

Therefore:

```text
7 : 1 : 1
        ^
        |
index inside layer
```

Again, the runtime wrapper passes exactly this value.

## First component

The wrapper supplies:

```text
ItemContainer
```

rather than a number.

Separately, persistence state has:

```text
Dictionary<ItemContainer,int> ContainerIndexCache
```

Therefore the strongest current model is:

```text
7 : 1 : 1
^   ^   ^
|   |   |
|   |   +-- slot/index inside runtime layer
|   +------ runtime layer
+---------- logical ItemContainer/cell index
```

But the first component should remain **STRONG INFERENCE** until the lower overload is read.

---

# 09.17 Why `7` is probably not a world coordinate

The runtime wrapper passes the actual `ItemContainer` object.

It does not pass:

```text
Vector2Int cell position
world transform position
x/y coordinates
```

The service also owns:

```text
ContainerIndexCache<ItemContainer,int>
```

This architecture is much more consistent with a logical container index than a spatial coordinate.

That is why the current hypothesis is strong.

---

# 09.18 Interaction with `ShuffleItemVisual`

Suppose a hidden target refers to a slot whose item type is originally `7`.

`ShuffleItemVisual` can remap:

```text
7 -> 15
```

The hidden mechanic still receives the runtime item's:

```text
ItemContainer
RuntimeLayer
IndexInLayer
```

It does not need the original type ID to identify the target location.

Therefore:

> HiddenLayer attachment should follow the target **slot**, not a specific original item identity.

This conclusion is strongly supported by the attachment call signature.

---

# 09.19 Interaction with `ShuffleCellPos`

`ShuffleCellPos` changes `CellInfo.position`.

It does not reorder the `LevelInfo.cells` list in the function we examined.

If the first `MechanicSlotRef` component is a logical cell/container index, then the mechanic should remain associated with the same logical cell while that cell moves to another board coordinate.

Conceptual example:

```text
logical cell/container #7
position before = (2,4)

ShuffleCellPos

logical cell/container #7
position after = (5,1)
```

A ref:

```text
7:1:1
```

would then target the same logical container at its new spatial position.

This is a **STRONG INFERENCE**, not yet confirmed.

The lower attachment overload should reveal how container indexing is performed.

---

# 09.20 Save path — `LevelController.GetCurrentHiddenLayerItemData`

The save helper clears:

```text
levelInfoSaved.hiddenLayerPendingSlots
```

then checks the global/static `HiddenLayerItemController`.

If:

```text
HasPendingSlots()
```

and:

```text
GetPendingSlots()
```

returns a non-empty list, it stores that list into:

```text
levelInfoSaved.hiddenLayerPendingSlots
```

Therefore saved state records unresolved HiddenLayer locations.

---

# 09.21 Load path — `UpdateFromDataSaved`

On restore, the controller:

1. ensures services
2. clears currently tracked runtime items
3. unsubscribes
4. ensures `_data` exists
5. reconfigures persistence service
6. calls:
   ```text
   HiddenLayerPersistenceService.UpdateFromSavedData(pendingSlots)
   ```
7. normalizes controller state if needed
8. subscribes again if restored `PendingSlots` is non-empty

This means the saved pending set becomes the authoritative remaining mechanic state.

---

# 09.22 Why save/restore is important for interpreting the mechanic

Because the game serializes:

```text
List<MechanicSlotRef>
```

rather than a list of item IDs, HiddenLayer state is fundamentally location-based.

The game needs to know which target slots remain unresolved after a reload.

This is additional evidence that `MechanicSlotRef` is not simply an “item type to hide” structure.

---

# 09.23 Swap support

The controller exposes:

```text
HandleSwapStarted
PrepareSwapRelocation
HandleSwapFinished
ApplySwapRelocation
```

These have not yet been fully analyzed.

Their existence strongly indicates the mechanic deliberately maintains correctness when runtime item/container arrangement changes during gameplay.

Possible responsibilities include:

```text
track old target refs
track new item locations
relocate pending/active mechanic state
rebind overlays
```

Do not document the exact relocation algorithm until those functions are inspected.

---

# 09.24 Cell-removal support

The controller also contains:

```text
HandleCellRemovedAtIndex
```

This reinforces the idea that the first component is tied to logical cell/container indexing.

However, method-name evidence alone is not enough to define the serialization format.

The implementation should be inspected if index shifting/remapping becomes important.

---

# 09.25 Full HiddenLayer lifecycle

```text
LEVEL ASSET
    |
    v
HiddenLayerItemData.slots
List<MechanicSlotRef>
    |
    v
HiddenLayerItemController.InitData
    |
    +--> EnsureServices
    |
    +--> PersistenceService.ConfigureData
    |
    +--> PersistenceService.RebuildSlotState
                |
                v
        HashSet<MechanicSlotRef>
             PendingSlots
                |
                +------------------------------+
                |                              |
                | existing runtime items       | future runtime items
                |                              |
                v                              v
       HandleGenerationDone            InGameEvent.onSpawnItem
                |                              |
                v                              v
     TryProcessExistingItems             HandleSpawnItem
                |                              |
                v                              v
 AttachmentService.ScanLevel     AttachmentService.TryAttachToItem
                |                              |
                +---------------+--------------+
                                |
                                v
                     lower TryAttachToItem
                                |
                    ItemContainer + layer + index
                                |
                                v
                      PendingSlots comparison
                                |
                                v
                    attach/track HiddenLayer item
                                |
                                v
                 PendingSlots changes over play
                                |
                    +-----------+-----------+
                    |                       |
                    v                       v
                  save                    reload
                    |                       |
                    v                       v
  hiddenLayerPendingSlots        UpdateFromDataSaved
                                            |
                                            v
                                      PendingSlots
```

---

# 09.26 Concrete hypothetical example

Assume:

```text
HiddenLayerItemData.slots:
["7:1:1"]
```

And after all generation/shuffling, logical container #7 has:

```text
_itemTypes[0] = [12,12,3]
_itemTypes[1] = [8,15,0]
```

When layer 1 materializes:

```text
index 0 -> ItemBase(type 8)
index 1 -> ItemBase(type 15)
index 2 -> null
```

The item at index 1 receives:

```text
itemContainer = container #7 object
LastRuntimeLayer = 1
LastIndexInLayer = 1
```

`onSpawnItem` fires.

HiddenLayer wrapper forwards:

```text
item
container #7
1
1
```

The remaining missing lower-level code should convert/compare this to the configured ref:

```text
7:1:1
```

If it matches, the service attaches/tracks the HiddenLayer overlay/runtime mechanic and updates state.

This is the intended end-to-end model.

Only the exact conversion of `container object -> serialized integer 7` remains to be proven.

---

# 09.27 Critical next function

Inspect:

```text
HiddenLayerAttachmentService__TryAttachToItem_2B06FB0
```

This overload receives:

```text
HiddenLayerAttachmentService
ItemBase
ItemContainer
int runtimeLayer
int indexInLayer
```

Look for:

- `ContainerIndexCache`
- `LevelController._itemContainers`
- `IndexOf`
- dictionary `TryGetValue` / `get_Item`
- `MechanicSlotRef` constructor
- `PendingSlots.Contains`
- `PendingSlots.Remove`
- `ActiveItems.Add`
- overlay instantiation / attachment

Once that function is understood, the docs should be updated from:

```text
first component = likely container index
```

to either:

```text
first component = CONFIRMED container index
```

or whatever the code actually proves.

---

# 09.28 Additional next functions

After the lower attachment overload:

## `HiddenLayerAttachmentService.ScanLevel`

Purpose:

- prove how existing item containers are enumerated
- see whether container order comes from `LevelController._itemContainers`
- potentially prove first-component indexing from the scan side

## `MechanicSlotRef` equality/hash

Because `PendingSlots` is a:

```text
HashSet<MechanicSlotRef>
```

equality/hash semantics are important.

If `MechanicSlotRef` is a value-like type with three integers, this can confirm exact matching fields.

## `HiddenLayerPersistenceService.UpdateFromSavedData`

Useful to verify whether save refs are copied directly or normalized/remapped.

## `PrepareSwapRelocation` / `ApplySwapRelocation`

Useful to understand how exact slot refs change when the game swaps/moves runtime items.

## `HandleCellRemovedAtIndex`

Useful to understand how container indices behave when cells are removed.

---

# 09.29 Confirmed facts

- HiddenLayer uses `HiddenLayerItemData`.
- its configured targets are `List<MechanicSlotRef>`.
- `RebuildSlotState` copies configured refs into `HashSet<MechanicSlotRef> PendingSlots`.
- pending refs represent unresolved mechanic targets.
- HiddenLayer scans existing runtime items after generation.
- HiddenLayer subscribes to `InGameEvent.onSpawnItem`.
- every future spawned item is sent to the attachment service.
- `TryAttachToItem` rejects items with no `itemContainer`.
- it forwards:
  - `ItemBase`
  - `ItemContainer`
  - `LastRuntimeLayer`
  - `LastIndexInLayer`
- persistence owns `Dictionary<ItemContainer,int> ContainerIndexCache`.
- pending refs are saved and restored.
- swap/cell-removal-specific maintenance functions exist.

---

# 09.30 Strong inferences

- `MechanicSlotRef` second component corresponds to runtime layer.
- third component corresponds to index inside runtime layer.
- first component corresponds to a logical ItemContainer/cell index.
- `ShuffleItemVisual` does not invalidate HiddenLayer targeting because targeting is location-based.
- `ShuffleCellPos` probably moves the hidden target spatially with the logical container rather than retargeting a coordinate.

These should remain labeled until the lower attachment function confirms the exact construction/comparison.

---

# 09.31 Remaining unknowns

- exact construction of the runtime `MechanicSlotRef`
- exact source/order of the first component integer
- whether first component indexes `LevelController._itemContainers`, original `LevelInfo.cells`, or another normalized container list
- exact overlay creation/attachment method
- exact point where a pending ref is removed
- active-item tracking details
- swap relocation algorithm
- cell-removal index adjustment behavior
