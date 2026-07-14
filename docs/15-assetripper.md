# AssetRipper

The previous chapter introduced Unity assets and explained how they differ from executable code.

The next question is straightforward.

How can we inspect those assets?

This is where **AssetRipper** comes in.

AssetRipper is an open-source tool capable of reading Unity asset formats and reconstructing large parts of a Unity project.

Rather than focusing on code, AssetRipper focuses on everything that makes up the application's content.

---

# Why Is This Useful?

Native code explains **how** the application behaves.

Assets explain **what** exists inside the application.

Suppose you're trying to understand a shop popup.

Looking only at the native code might eventually reveal a method such as:

```
UIPopup_ShopGold::Open()
```

That tells you the popup exists.

AssetRipper can reveal something completely different.

```
Canvas

└── ShopPopup

    ├── Buy Button

    ├── Close Button

    ├── Price

    └── Background
```

Suddenly you understand how that popup is constructed.

---

# Recovering the Unity Hierarchy

One of AssetRipper's greatest strengths is reconstructing the relationships between objects.

For example:

```
Scene

└── Canvas

    └── ShopPopup

        ├── BuyButton

        ├── CloseButton

        └── PriceLabel
```

This hierarchy often mirrors what developers originally saw inside the Unity Editor.

Understanding these relationships can significantly simplify reverse engineering.

---

# Components

AssetRipper also recovers the Components attached to GameObjects.

For example:

```
BuyButton

├── RectTransform

├── Image

├── Button

├── UITapButton

└── LocalizeText
```

This immediately answers questions such as:

- Is this object clickable?
- Which custom scripts are attached?
- Which Unity components are used?

---

# Serialized Data

Unity stores most assets in serialized form.

Rather than executable code, these files contain object data describing how Unity should recreate the scene at runtime.

Examples include:

- Positions
- Rotations
- References
- Colors
- Text
- Images
- Component properties

This is one of the reasons AssetRipper can recover so much information without relying on the application's source code.

---

# AssetRipper Does Not Recover Everything

Although AssetRipper is extremely powerful, it has an important limitation.

It does **not** recover application logic.

For example, it can tell you that a button exists.

It cannot tell you what happens after that button is pressed.

That behaviour still lives inside `libil2cpp.so`.

---

# Next

AssetRipper and Il2CppDumper complement each other remarkably well.

Together, these tools explain both the structure of the user interface and the code responsible for its behaviour.

The next chapter introduces **Il2CppDumper**, one of the most important tools in the Unity reverse engineering workflow, and explains how it reconstructs classes, methods and fields from an IL2CPP application.

[16 - Il2CppDumper](16-il2cppdumper.md)
