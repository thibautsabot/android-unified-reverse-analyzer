import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { analyzeAndroidPackage } from "../src/analysis/analyzer.js";
import { AndroidPackage } from "../src/apk/android-package.js";
import { createBinaryManifest } from "./helpers/axml-fixture.js";
import { createZip } from "./helpers/zip-fixture.js";

test("detects Unity IL2CPP with evidence and builds a targeted workflow", async () => {
  const directory = await mkdtemp(join(tmpdir(), "aura-unity-"));
  const path = join(directory, "game.apk");
  const metadata = Buffer.alloc(16);
  metadata.writeUInt32LE(0xfab11baf, 0);
  metadata.writeInt32LE(29, 4);

  const apkBuffer = createZip([
    { name: "AndroidManifest.xml", data: createBinaryManifest() },
    {
      name: "classes.dex",
      data: Buffer.from(
        "dex\n035\u0000com/unity3d/player/UnityPlayerActivity com/google/firebase/FirebaseApp",
      ),
    },
    { name: "classes2.dex", data: Buffer.from("dex\n035\u0000secondary") },
    { name: "lib/arm64-v8a/libunity.so", data: Buffer.from("ELF unity") },
    { name: "lib/arm64-v8a/libil2cpp.so", data: Buffer.from("ELF il2cpp") },
    {
      name: "assets/bin/Data/Managed/Metadata/global-metadata.dat",
      data: metadata,
    },
    {
      name: "assets/bin/Data/globalgamemanagers",
      data: Buffer.from("header 2022.3.18f1 data"),
    },
  ]);
  await writeFile(path, apkBuffer);

  const pkg = await AndroidPackage.open(path);
  try {
    const report = await analyzeAndroidPackage(pkg);
    assert.equal(report.application.packageName, "com.example.game");
    assert.equal(report.android.multiDex, true);
    assert.deepEqual(report.android.architectures, ["arm64-v8a"]);

    const unity = report.detections.find((detection) => detection.id === "unity");
    assert.ok(unity);
    assert.equal(unity.status, "confirmed");
    assert.equal(unity.confidence, 100);
    assert.equal(unity.details.unityVersion, "2022.3.18f1");

    const il2cpp = report.detections.find((detection) => detection.id === "il2cpp");
    assert.ok(il2cpp);
    assert.equal(il2cpp.status, "confirmed");
    assert.equal(il2cpp.details.metadataVersion, 29);

    const firebase = report.detections.find((detection) => detection.id === "firebase");
    assert.ok(firebase);
    assert.ok(report.workflow.some((step) => step.tool === "AssetRipper"));
    assert.ok(report.workflow.some((step) => step.tool === "Il2CppDumper or Cpp2IL"));
  } finally {
    await pkg.close();
    await rm(directory, { recursive: true, force: true });
  }
});
