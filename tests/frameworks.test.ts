import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { analyzeAndroidPackage } from "../src/analysis/analyzer.js";
import { AndroidPackage } from "../src/apk/android-package.js";
import { createBinaryManifest } from "./helpers/axml-fixture.js";
import { createZip } from "./helpers/zip-fixture.js";

test("detects Flutter from independent package fingerprints", async () => {
  const directory = await mkdtemp(join(tmpdir(), "aura-flutter-"));
  const path = join(directory, "flutter.apk");
  await writeFile(
    path,
    createZip([
      {
        name: "AndroidManifest.xml",
        data: createBinaryManifest({ packageName: "com.example.flutter" }),
      },
      { name: "classes.dex", data: Buffer.from("dex\n035\u0000") },
      { name: "lib/arm64-v8a/libflutter.so", data: Buffer.from("flutter") },
      { name: "lib/arm64-v8a/libapp.so", data: Buffer.from("dart") },
      {
        name: "assets/flutter_assets/AssetManifest.bin",
        data: Buffer.from("assets"),
      },
    ]),
  );

  const pkg = await AndroidPackage.open(path);
  try {
    const report = await analyzeAndroidPackage(pkg);
    const flutter = report.detections.find(
      (detection) => detection.id === "flutter",
    );
    assert.ok(flutter);
    assert.equal(flutter.confidence, 100);
    assert.equal(flutter.status, "confirmed");
    assert.equal(
      report.detections.some((detection) => detection.id === "unity"),
      false,
    );
  } finally {
    await pkg.close();
    await rm(directory, { recursive: true, force: true });
  }
});

test("does not confuse React Native with the Meta SDK", async () => {
  const directory = await mkdtemp(join(tmpdir(), "aura-react-native-"));

  const path = join(directory, "react-native.apk");

  await writeFile(
    path,
    createZip([
      {
        name: "AndroidManifest.xml",
        data: createBinaryManifest({
          packageName: "com.example.reactnative",
        }),
      },
      {
        name: "classes.dex",
        data: Buffer.from("dex\n035\u0000com/facebook/react/ReactApplication"),
      },
      {
        name: "assets/index.android.bundle",
        data: Buffer.from("__d(function() {})"),
      },
    ]),
  );

  const pkg = await AndroidPackage.open(path);

  try {
    const report = await analyzeAndroidPackage(pkg);

    assert.ok(
      report.detections.some((detection) => detection.id === "react-native"),
    );

    assert.equal(
      report.detections.some((detection) => detection.id === "facebook-sdk"),
      false,
    );
  } finally {
    await pkg.close();

    await rm(directory, {
      recursive: true,
      force: true,
    });
  }
});

test("does not mark one SDK byte signature as confirmed", async () => {
  const directory = await mkdtemp(join(tmpdir(), "aura-sdk-score-"));

  const path = join(directory, "sdk.apk");

  await writeFile(
    path,
    createZip([
      {
        name: "AndroidManifest.xml",
        data: createBinaryManifest({
          packageName: "com.example.sdk",
        }),
      },
      {
        name: "classes.dex",
        data: Buffer.from("dex\n035\u0000com/appsflyer/AppsFlyerLib"),
      },
    ]),
  );

  const pkg = await AndroidPackage.open(path);

  try {
    const report = await analyzeAndroidPackage(pkg);

    const appsFlyer = report.detections.find(
      (detection) => detection.id === "appsflyer",
    );

    assert.ok(appsFlyer);
    assert.equal(appsFlyer.confidence, 100);
    assert.equal(appsFlyer.status, "likely");
  } finally {
    await pkg.close();

    await rm(directory, {
      recursive: true,
      force: true,
    });
  }
});
