# Walkthrough 05: Following the mechanics

## Where we left off

At the end of the previous step, we had the level file mostly figured out.

We knew how the board was laid out: cells, layers, item IDs. We knew how item IDs mapped to sprites. There was one section of `Lv123_10.asset` we had not touched yet:

```yaml
mechanics:
  - type: 12
    data: '{"slots":["7:1:1","1:1:1","8:1:1","8:1:0","7:0:1","6:0:0","6:0:1","6:1:0","6:1:1"]}'
```

A type number and a JSON string. The field is declared in `LevelInfo.cs` as:

```csharp
public List<MechanicData> mechanics;
```

And `MechanicData.cs` is minimal:

```csharp
public class MechanicData
{
    public MechanicType type;
    public string data;
}
```

## Decoding the type numbers

The first thing I needed was the enum behind that `type` value. So I opened `MechanicType.cs`.

```csharp
public enum MechanicType
{
    ReverseItem = 0,
    LockSlot = 1,
    CloseCellEnd = 2,
    BombItem = 3,
    HiddenLayerItem = 12,
    LockedItem = 13
}
```

So:

- `type: 12` → `HiddenLayerItem`

## What is `7:1:1`?

The slot references in the hidden layer mechanic data — `"7:1:1"`, `"1:1:1"`, etc. — are not explained anywhere obvious. Three numbers separated by colons. I had no idea what they referred to at this point.

I did not have a fancy plan here. I just started grepping for `slot` (in lowercase like in the YAML), in the same folder (`Script/Assembly-CSharp`).

This is what we have:

```bash
AssetRipper_export_20260718_082434/Scripts/Assembly-CSharp/LockedItemData.cs:	public List<MechanicSlotRef> slots;
AssetRipper_export_20260718_082434/Scripts/Assembly-CSharp/HiddenLayerItemData.cs:	public List<MechanicSlotRef> slots;
AssetRipper_export_20260718_082434/Scripts/Assembly-CSharp/MechanicLayerUtils.cs:	public static List<MechanicSlotRef> ReindexSlots(IEnumerable<MechanicSlotRef> slots, int removedCellIndex)
AssetRipper_export_20260718_082434/Scripts/Assembly-CSharp/MechanicLayerUtils.cs:	public static HashSet<MechanicSlotRef> ReindexSlotSet(IEnumerable<MechanicSlotRef> slots, int removedCellIndex)
AssetRipper_export_20260718_082434/Scripts/Assembly-CSharp/MechanicLayerUtils.cs:	public static Dictionary<MechanicSlotRef, T> ReindexSlotDictionary<T>(IEnumerable<KeyValuePair<MechanicSlotRef, T>> slots, int removedCellIndex)
```

I won't go into every single file, but one thing we notice is that slots is almost always tied to a `MechanicSlotRef` type.

## `MechanicSlotRef.cs`

When I opened `MechanicSlotRef.cs`, the format finally made sense.

```csharp
[JsonConverter(typeof(MechanicSlotRefJsonConverter))]
public struct MechanicSlotRef : IEquatable<MechanicSlotRef>
{
    [SerializeField]
    [FormerlySerializedAs("cellIndex")]
    private int c;

    [SerializeField]
    [FormerlySerializedAs("layer")]
    private int l;

    [SerializeField]
    [FormerlySerializedAs("index")]
    private int i;

    public static bool TryParse(string value, out MechanicSlotRef slot) { ... }
}
```

The field names `c`, `l`, `i` match the old names exposed via `FormerlySerializedAs`: `cellIndex`, `layer`, `index`.

So `"7:1:1"` means cell 7, layer 1, slot index 1. Not mysterious at all. It is just a compact way to point at an exact slot on the board.

## How does the game get from the asset file to this struct?

This was the part I had not connected yet. We know `Lv123_10.asset` contains the mechanics. We know `MechanicSlotRef` is the end type. But who does the parsing in between?

`MechanicData` is declared inside `LevelInfo`. So the question became: which class at runtime actually owns a `LevelInfo`? That class is the one doing the work. I grepped for it:

```bash
grep -rn "LevelInfo " ExportedProject/Assets/Scripts/Assembly-CSharp/ --include="*.cs"
```

The two interesting hits were:

```
SOLevel.cs:             public LevelInfo level;
GenLevelController.cs:  protected LevelInfo _levelInfo;
```

`SOLevel` is already familiar from the previous step. It is the ScriptableObject that wraps the level file — `Lv123_10.asset` deserializes into it.

`GenLevelController` is the new piece. Opening it:

```csharp
public class GenLevelController : Singleton<GenLevelController>
{
    [SerializeField]
    private LevelController _levelController;

    protected LevelInfo _levelInfo;

    protected List<SOLevel> _listLevelsAddress;
    public SOLevel currentSOLevel;

    private void ShuffleItemVisual() { }
    private void ShuffleCellPos() { }
    private void ShuffleItemInCell() { }
    ...
}
```

That was the first time I could actually point at a runtime class and say: okay, this one is probably doing something important with the level data.
It loads `SOLevel`, keeps the `LevelInfo`, and holds a reference to `LevelController`.

_`LevelController.cs` is one of the biggest file I opened so far and has more than 50 methods. To avoid going down on a rabbit hole, I chose not to inspect it further. But here are some findings worth noting:_

```csharp
	private LevelState _levelState;
	private EndGameState _endgameState;
    public bool isDoneGenLevel;
	public bool CanWatchAdToGetMoreTimeEndGame();
    private void OnLoadingSceneDone();
	public void HandleCellRemovedAtIndex(int removedCellIndex);
	private void CheckWin();
	private bool IsCompleteLevel();
	private void CheckStuck(bool isContinueLevel = false);
	public void EndGame(EndGameState state);
    public void CheckAndInitMechanic();
```

At that point I was not reading the code, just collecting clues. But even that already told me this file was sitting very close to the actual gameplay logic.

## The shuffle methods

`GenLevelController` has three methods that really grabbed my attention.
I do not want to pretend I know exactly what each one does, because the bodies were not available in the decompile. But the names are hard to ignore.

Here's my take on it:

- `ShuffleItemVisual` — shuffles which sprite each item type gets, without changing the underlying item values (like a dark filter or maybe even the "hidden item" with a green background and a question mark)
- `ShuffleCellPos` — shuffles the positions of cells on the board (cells being the blocks of 3). Since no matter how mich I restarted the level, the layout was the same, I'm assuming the content of the cells are being changed.
- `ShuffleItemInCell` — shuffles items within each cell (duh)

The item IDs in the level file are stable. What changes is how they are arranged and presented. The player sees a different board every run, but the game logic underneath is always playing from the same template.

This is where the apparent randomness of the level actually comes from. It is not in the asset. It is injected at runtime, in `GenLevelController`, before the board is handed to `LevelController`.

One last interresting fact is that while it looks mostly random, each "cell" seems to have a fixed pool of items that the game was mixing around somehow.

That is why the `itemsLayer` blob kept bothering me:

```yaml
     itemsLayer:
    - items: 4a0000004a000000da000000
    - items: 4d0000003f00000000000000
    - items: 500000000000000050000000
    - items: 5c000000000000005c000000
    - items: 620000001b01000000000000
    - items: 6d0000000000000068000000
    - items: 77000000000000006c000000
    - items: 000000007e00000081000000
```

## Where I think the investigation bottoms out

Those items could be in any order, any layer and the cell can be anywhere in the layout, but this cell will always have these items somehow.

But these are just speculations.

We can see the fields, we can see the names, we can infer a lot from them, but without the real method bodies it is hard to go much further inside AssetRipper alone.

Still, that was already enough to understand something important: this is not just a static level file. The level data, the mechanic data, the slot references, and the runtime controller classes all fit together into one system.

Nonetheless, it's amazing what we can understand from a simple asset extractor and it's already a lot more than I expected.

[06 - Going deeper](06-going-deeper.md)
