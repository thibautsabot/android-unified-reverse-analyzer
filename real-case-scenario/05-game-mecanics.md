# Walkthrough 05: Following the mechanics

The level file was mostly readable by now. Cells, layers, item IDs, sprites. The one part still sitting there untouched was the block at the bottom of `Lv123_10.asset`:

```yaml
mechanics:
  - type: 12
    data: '{"slots":["7:1:1","1:1:1","8:1:1","8:1:0","7:0:1","6:0:0","6:0:1","6:1:0","6:1:1"]}'
  - type: 1
    data: '{"cells":[1,3,6,11]}'
```

A number and a JSON string. `LevelInfo.cs` declares it as `public List<MechanicData> mechanics;`, and `MechanicData.cs` is about as small as a class gets:

```csharp
public class MechanicData
{
    public MechanicType type;
    public string data;
}
```

Two fields. A type, and a payload. So the payload shape probably depends on the type, which means the enum is the thing to read first.

## Decoding the type numbers

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

Level 123 uses two of them:

- `type: 12` → `HiddenLayerItem`
- `type: 1` → `LockSlot`

`LockSlot` lines up with something I already knew from the level file: `numberCellLock: 4`, and the payload lists exactly four cells, `[1,3,6,11]`. Those are the cells the game blocks behind an ad.

## What is `7:1:1`?

The `HiddenLayerItem` payload was less obvious. Three numbers separated by colons, nine of them, and nothing in the YAML explaining what they point at.

So I grepped for the word in the payload, lowercase, the way it appears in the JSON:

```bash
grep -rn "slots" ExportedProject/Assets/Scripts/Assembly-CSharp/ --include="*.cs"
```

```text
LockedItemData.cs:      public List<MechanicSlotRef> slots;
HiddenLayerItemData.cs: public List<MechanicSlotRef> slots;
MechanicLayerUtils.cs:  public static List<MechanicSlotRef> ReindexSlots(IEnumerable<MechanicSlotRef> slots, int removedCellIndex)
MechanicLayerUtils.cs:  public static HashSet<MechanicSlotRef> ReindexSlotSet(IEnumerable<MechanicSlotRef> slots, int removedCellIndex)
MechanicLayerUtils.cs:  public static Dictionary<MechanicSlotRef, T> ReindexSlotDictionary<T>(IEnumerable<KeyValuePair<MechanicSlotRef, T>> slots, int removedCellIndex)
```

Every hit goes through the same type. Whatever `7:1:1` is, it becomes a `MechanicSlotRef`.

## `MechanicSlotRef.cs`

And this is where the format gave itself up:

```csharp
[Serializable]
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

	public static bool TryParse(string value, out MechanicSlotRef slot)
	{
		slot = default(MechanicSlotRef);
		return false;
	}
}
```

The fields are named `c`, `l`, `i`, which on their own tell you nothing. `FormerlySerializedAs` tells you everything: at some point they were called `cellIndex`, `layer` and `index`, and somebody shortened them without breaking old save data.

So `"7:1:1"` is cell 7, layer 1, slot index 1. A compact pointer at one exact slot on the board.

That `TryParse` body is worth a second look, though. It sets the output to `default` and returns `false`. That is not the real logic, that is AssetRipper handing me an empty stub. Same for `cellIndex => 0` and the rest. Easy to forget when the file otherwise reads like normal C#.

## Who turns the string into the struct

The answer was in the code I had just pasted and I walked straight past it the first time:

```csharp
[JsonConverter(typeof(MechanicSlotRefJsonConverter))]
```

Newtonsoft deserializes the `data` string, hits a `MechanicSlotRef`, and hands it to that converter instead of trying to map `c`/`l`/`i` itself:

```csharp
public sealed class MechanicSlotRefJsonConverter : JsonConverter<MechanicSlotRef>
{
	public override MechanicSlotRef ReadJson(JsonReader reader, Type objectType, MechanicSlotRef existingValue, bool hasExistingValue, JsonSerializer serializer)
	{
		return default(MechanicSlotRef);
	}

	private static int ReadProperty(JObject obj, params string[] names)
	{
		return 0;
	}
}
```

Empty bodies again, so I cannot read the parsing itself. But `ReadProperty(JObject obj, params string[] names)` is a signature that leaks its own intent: it looks up a property under several possible names. Which is exactly what you would write to accept both `"7:1:1"` and `{"cellIndex":7,"layer":1,"index":1}`.

One thing I could not close: the `LockSlot` payload is `{"cells":[1,3,6,11]}`, and I found no class in the export with a matching `cells` field. `CloseCellEndData` has `listCloseCellEndIndex`, which is close in spirit but not the same name. So either it is parsed loosely as a `JObject`, or the class is somewhere I did not look.

## One data class per mechanic

Once I knew what to look for, the pattern was hard to miss:

```csharp
public class ReverseItemData    { public int itemReverse; }
public class CloseCellEndData   { public List<int> listCloseCellEndIndex; }
public class HiddenLayerItemData { public List<MechanicSlotRef> slots; }
```

Each `MechanicType` gets its own little payload class, and most of them have a `Controller` sibling next to it: `ReverseItemController`, `CloseCellEndController`, `LockSlotController`, `BombItemController`, with `MechanicController` and `MechanicDataController` above them.

So `MechanicData` is deliberately generic. The `type` decides which shape the `data` string is deserialized into, and which controller runs it. That is why the level file can carry six different mechanics in one list without six different fields.

## Who owns the `LevelInfo` at runtime

`MechanicData` lives inside `LevelInfo`, so the class holding a `LevelInfo` is the one doing the work. I grepped for it:

```bash
grep -rn "LevelInfo " ExportedProject/Assets/Scripts/Assembly-CSharp/ --include="*.cs"
```

Two hits mattered:

```text
SOLevel.cs:             public LevelInfo level;
GenLevelController.cs:  protected LevelInfo _levelInfo;
```

`SOLevel` I already knew, it is the ScriptableObject `Lv123_10.asset` deserializes into. `GenLevelController` was new:

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

It loads `SOLevel`, keeps the `LevelInfo`, and holds a reference to `LevelController`. First runtime class so far that clearly sits on the path between the asset file and the board.

> `LevelController.cs` is the biggest file I had opened up to that point, more than 50 methods, so I deliberately did not follow it. But the names alone say how close it is to the gameplay: `HandleCellRemovedAtIndex(int removedCellIndex)`, `CheckWin()`, `IsCompleteLevel()`, `CheckStuck(bool isContinueLevel = false)`, `EndGame(EndGameState state)`, `CheckAndInitMechanic()`, plus `isDoneGenLevel` and a `LevelState`. Collecting names, not reading code.

## The shuffle methods

Three method names in `GenLevelController` stopped me:

```csharp
private void ShuffleItemVisual() { }
private void ShuffleCellPos() { }
private void ShuffleItemInCell() { }
```

The bodies are empty, so everything below is a guess from the names plus what I had seen playing the level. I am writing the guesses down anyway, because it is the first point where the data files stopped being enough:

- `ShuffleItemVisual` — changes how items look rather than what they are. Maybe the dark filter, maybe the hidden item with the green background and the question mark.
- `ShuffleCellPos` — moves the cells around the board. Except restarting level 123 always gave me the same layout, so if this runs, it either does not run here or it is not doing what the name suggests.
- `ShuffleItemInCell` — moves items around inside a single cell.

What I could say with more confidence is that the item IDs in the level file never change. Something at runtime is rearranging or repainting them, and it happens in `GenLevelController` before the board reaches `LevelController`. The apparent randomness of a level is not in the asset.

There was one more clue in that direction. Each cell seemed to draw from a fixed pool of items that got mixed around, which is roughly what the `itemsLayer` blob looks like once you know it is three int32 per layer:

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

## Where this stops

The mechanics are decoded. The types, the slot format, the class that parses it, the per-mechanic payloads, and the runtime class that owns the whole thing.

The shuffles are not. Three method names and a guess each, and no way to do better from an asset export, because the one thing AssetRipper cannot give me is the part I now need: the method bodies.

Which means the next step is not another grep.

[06 - Going deeper](06-going-deeper.md)
