import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { analyzeAndroidPackage } from "../src/analysis/analyzer.js";
import { AndroidPackage } from "../src/apk/android-package.js";
import { createBinaryManifest } from "./helpers/axml-fixture.js";
import { createZip } from "./helpers/zip-fixture.js";

test("merges base and split APK evidence from an XAPK container", async () => {
  const directory = await mkdtemp(join(tmpdir(), "aura-xapk-"));
  const path = join(directory, "bundle.xapk");

  const baseApk = createZip([
    { name: "AndroidManifest.xml", data: createBinaryManifest({ packageName: "com.example.split" }) },
    { name: "classes.dex", data: Buffer.from("dex\n035\u0000UnityPlayerActivity") },
    { name: "assets/bin/Data/globalgamemanagers", data: Buffer.from("2021.3.44f1") },
  ]);
  const splitApk = createZip([
    {
      name: "AndroidManifest.xml",
      data: createBinaryManifest({ packageName: "com.example.split", splitName: "config.arm64_v8a" }),
    },
    { name: "lib/arm64-v8a/libunity.so", data: Buffer.from("unity") },
    { name: "lib/arm64-v8a/libil2cpp.so", data: Buffer.from("il2cpp") },
    { name: "assets/bin/Data/Managed/Metadata/global-metadata.dat", data: metadataBuffer(27) },
  ]);
  await writeFile(
    path,
    createZip([
      { name: "base.apk", data: baseApk, compress: false },
      { name: "split_config.arm64_v8a.apk", data: splitApk, compress: false },
      { name: "manifest.json", data: "{}" },
    ]),
  );

  const pkg = await AndroidPackage.open(path);
  try {
    const report = await analyzeAndroidPackage(pkg);
    assert.equal(report.application.format, "xapk");
    assert.equal(report.application.packageName, "com.example.split");
    assert.equal(report.android.parts.length, 2);
    assert.deepEqual(report.android.architectures, ["arm64-v8a"]);
    assert.equal(report.detections.find((detection) => detection.id === "unity")?.status, "confirmed");
    assert.equal(report.detections.find((detection) => detection.id === "il2cpp")?.status, "confirmed");
  } finally {
    await pkg.close();
    await rm(directory, { recursive: true, force: true });
  }
});

function metadataBuffer(version: number): Buffer {
  const buffer = Buffer.alloc(8);
  buffer.writeUInt32LE(0xfab11baf, 0);
  buffer.writeInt32LE(version, 4);
  return buffer;
}
