# Smali

Android applications are ultimately executed as **DEX** (Dalvik Executable) bytecode.

Although tools such as **JADX** can often reconstruct readable Java code, the original Java source does not actually exist inside the APK.

Instead, Android executes bytecode stored inside one or more DEX files.

Smali is a human-readable representation of this bytecode.

```
Java

↓

DEX Bytecode

↓

Smali
```

Unlike Java, Smali is much closer to what the Android Runtime (ART) actually executes.

---

# Why Does Smali Exist?

Imagine writing the following Java code.

```java
public boolean isPremium() {
    return true;
}
```

The Java compiler converts this source code into DEX bytecode.

Smali simply provides a readable representation of that bytecode.

```smali
.method public isPremium()Z
    .locals 1

    const/4 v0, 0x1

    return v0
.end method
```

The final letter (`Z`) indicates that the method returns a `boolean`.

Similarly,

```smali
.method public getCoins()I
```

returns an `int`.

Method parameters and return values are represented using **Dalvik type descriptors**.

Rather than reproducing the complete descriptor table here, refer to the official Android documentation:

https://source.android.com/docs/core/runtime/dalvik-bytecode

---

# Why Not Always Use JADX?

JADX does an excellent job reconstructing Java source code.

However, it is still a **decompiler**.

It analyzes DEX bytecode and attempts to reconstruct what the original Java source code probably looked like.

Most of the time those results are accurate, sometimes they are not.

Smali represents the instructions that are actually executed by Android.

---

# Common Smali Patterns

Fortunately, you don't need to understand every Smali instruction to begin reading Android applications.

After reading a few methods, you'll quickly recognize a handful of common patterns.

## Returning a Boolean

Java

```java
public boolean isPremium() {
    return true;
}
```

Smali

```smali
.method public isPremium()Z
    .locals 1

    const/4 v0, 0x1

    return v0
.end method
```

Changing

```smali
const/4 v0, 0x1
```

to

```smali
const/4 v0, 0x0
```

changes the returned value from `true` to `false`.

This is one of the simplest examples of a Smali patch.

---

## Returning an Integer

Java

```java
public int getCoins() {
    return 9999;
}
```

Smali

```smali
.method public getCoins()I
    .locals 1

    const/16 v0, 0x270F

    return v0
.end method
```

Notice that integer values are often represented in hexadecimal.

For example:

```
0x270F = 9999
```

---

## Calling Another Method

Java

```java
login();
```

Smali

```smali
invoke-virtual {p0}, Lcom/example/MainActivity;->login()V
```

Instructions beginning with `invoke-` call another method.

You'll frequently encounter variants such as:

- `invoke-static`
- `invoke-virtual`
- `invoke-direct`
- `invoke-interface`
- `invoke-super`

The complete Smali instruction reference can be found here: https://sallam.gitbook.io/sec-88/android-appsec/smali/smali-cheat-sheet

---

## Conditional Branches

Java

```java
if (isPremium) {
    unlock();
}
```

Smali

```smali
if-eqz v0, :cond_0

invoke-virtual {p0}, Lcom/example/MainActivity;->unlock()V

:cond_0
```

Instructions beginning with `if-` perform conditional branches.

The exact condition depends on the instruction being used, but the overall pattern is easy to recognize.

---

## Creating Objects

Java

```java
User user = new User();
```

Smali

```smali
new-instance v0, Lcom/example/User;

invoke-direct {v0}, Lcom/example/User;-><init>()V
```

`new-instance` allocates a new object.

The constructor is then called using `invoke-direct`.

---

## Reading and Writing Fields

Java

```java
coins = player.coins;
```

Smali

```smali
iget v0, p0, Lcom/example/Player;->coins:I
```

Java

```java
player.coins = 100;
```

Smali

```smali
const/16 v0, 0x64

iput v0, p0, Lcom/example/Player;->coins:I
```

Instructions beginning with `iget` read instance fields.

Instructions beginning with `iput` write instance fields.

---

# When Is Smali Useful?

Many investigations can be completed entirely in JADX.

However, Smali becomes particularly useful when:

- the decompiled Java is difficult to understand,
- the decompiler produces incorrect output,
- you want to modify application behaviour,
- you need to patch conditional branches,
- you want to verify what Android actually executes.

---

# Limitations

Smali only represents the application's DEX bytecode.

It does not tell you anything about:

- native libraries,
- runtime memory,
- network traffic,
- native implementations.

Applications that rely heavily on native code require additional tools and techniques.

---

# Next

Now that we've seen how Android executes DEX bytecode, it's time to look at the opposite direction: reconstructing readable Java from that bytecode.

The next chapter introduces **JADX**, explains how it works, and discusses both its strengths and its limitations.

[05 - JADX](05-jadx.md)
