import assert from "node:assert/strict";
import test from "node:test";
import { parseAndroidManifest } from "../src/apk/axml.js";
import { createBinaryManifest } from "./helpers/axml-fixture.js";

test("parses a binary Android manifest", () => {
  const manifest = parseAndroidManifest(createBinaryManifest());

  assert.equal(manifest.packageName, "com.example.game");
  assert.equal(manifest.versionCode, 514);
  assert.equal(manifest.versionName, "5.14");
  assert.equal(manifest.minSdk, 24);
  assert.equal(manifest.targetSdk, 35);
  assert.equal(manifest.debuggable, false);
  assert.equal(manifest.allowBackup, true);
  assert.deepEqual(manifest.permissions, ["android.permission.INTERNET"]);
  assert.deepEqual(manifest.activities, [
    {
      name: "com.google.firebase.MessagingUnityPlayerActivity",
      exported: true,
      launcher: true,
    },
  ]);
});
