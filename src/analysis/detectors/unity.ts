import {
  createDetection,
  evidence,
} from "../detection.js";
import type { Detection, Detector, DetectorContext, Evidence } from "../types.js";

const UNITY_VERSION_PATTERN = /\b(20\d{2}\.\d+\.\d+[abfp]\d+(?:c\d+)?)\b/;
const IL2CPP_METADATA_MAGIC = 0xfab11baf;

export class UnityDetector implements Detector {
  readonly id = "unity";

  async detect(context: DetectorContext): Promise<Detection[]> {
    const evidences: Evidence[] = [];
    const details: Record<string, string | number | boolean | string[]> = {};

    const unityLibraries = context.findEntries(/^lib\/[^/]+\/libunity\.so$/i);
    if (unityLibraries.length > 0) {
      evidences.push(
        evidence({
          id: "unity-library",
          summary: "Unity engine library",
          detail: "libunity.so is shipped by Unity Android builds.",
          weight: 45,
          source: "file",
          locations: unityLibraries.map((entry) => context.location(entry)),
        }),
      );
    }

    const manifestActivities = context.manifestStrings.filter((value) =>
      /(?:^|\.)UnityPlayerActivity$|com\.unity3d\.player/i.test(value),
    );
    if (manifestActivities.length > 0) {
      evidences.push(
        evidence({
          id: "unity-activity",
          summary: "Unity Android activity",
          detail: "The Android manifest references UnityPlayerActivity or the Unity player package.",
          weight: 25,
          source: "manifest",
          locations: [...new Set(manifestActivities)].slice(0, 5),
        }),
      );
    } else {
      const dexLocations = await context.searchDex("UnityPlayerActivity");
      if (dexLocations.length > 0) {
        evidences.push(
          evidence({
            id: "unity-dex",
            summary: "Unity player classes",
            detail: "UnityPlayerActivity appears in DEX bytecode.",
            weight: 20,
            source: "dex",
            locations: dexLocations,
          }),
        );
      }
    }

    const unityData = context.findEntries(/^assets\/bin\/Data\//i);
    if (unityData.length > 0) {
      evidences.push(
        evidence({
          id: "unity-data",
          summary: "Unity data directory",
          detail: "assets/bin/Data contains serialized Unity player data.",
          weight: 15,
          source: "file",
          locations: unityData.slice(0, 5).map((entry) => context.location(entry)),
        }),
      );
    }

    const coreAssets = context.findEntries(
      /(?:^|\/)(?:globalgamemanagers|globalgamemanagers\.assets|resources\.assets|data\.unity3d|unity default resources)$/i,
    );
    if (coreAssets.length > 0) {
      evidences.push(
        evidence({
          id: "unity-core-assets",
          summary: "Unity serialized assets",
          detail: "The package contains files commonly produced by the Unity player build pipeline.",
          weight: 15,
          source: "file",
          locations: coreAssets.slice(0, 5).map((entry) => context.location(entry)),
        }),
      );
    }

    const version = await detectUnityVersion(context, coreAssets);
    if (version) {
      details.unityVersion = version.version;
      evidences.push(
        evidence({
          id: "unity-version",
          summary: `Unity version ${version.version}`,
          detail: "A Unity version string was found in serialized player data.",
          weight: 5,
          source: "content",
          locations: [version.location],
        }),
      );
    }

    const detection = createDetection({
      id: "unity",
      name: "Unity",
      category: "framework",
      evidence: evidences,
      details,
    });
    return detection ? [detection] : [];
  }
}

export class Il2CppDetector implements Detector {
  readonly id = "il2cpp";

  async detect(context: DetectorContext): Promise<Detection[]> {
    const evidences: Evidence[] = [];
    const details: Record<string, string | number | boolean | string[]> = {};

    const libraries = context.findEntries(/^lib\/[^/]+\/libil2cpp\.so$/i);
    if (libraries.length > 0) {
      evidences.push(
        evidence({
          id: "il2cpp-library",
          summary: "IL2CPP native library",
          detail: "libil2cpp.so contains native code generated from managed assemblies.",
          weight: 55,
          source: "file",
          locations: libraries.map((entry) => context.location(entry)),
        }),
      );
    }

    const metadata = context.findEntries(/(?:^|\/)global-metadata\.dat$/i);
    if (metadata.length > 0) {
      evidences.push(
        evidence({
          id: "il2cpp-metadata",
          summary: "IL2CPP global metadata",
          detail: "global-metadata.dat describes the managed types compiled by IL2CPP.",
          weight: 35,
          source: "file",
          locations: metadata.map((entry) => context.location(entry)),
        }),
      );

      const first = metadata[0];
      if (first) {
        try {
          const contents = await context.read(first);
          if (contents.length >= 8 && contents.readUInt32LE(0) === IL2CPP_METADATA_MAGIC) {
            const metadataVersion = contents.readInt32LE(4);
            details.metadataVersion = metadataVersion;
            evidences.push(
              evidence({
                id: "il2cpp-metadata-header",
                summary: `IL2CPP metadata format ${metadataVersion}`,
                detail: "The metadata file starts with the expected IL2CPP header magic.",
                weight: 10,
                source: "content",
                locations: [context.location(first)],
              }),
            );
          }
        } catch {
          // The filename remains valid evidence even if the file cannot be parsed.
        }
      }
    }

    const detection = createDetection({
      id: "il2cpp",
      name: "IL2CPP",
      category: "backend",
      evidence: evidences,
      details,
    });
    return detection ? [detection] : [];
  }
}

export class UnityMonoDetector implements Detector {
  readonly id = "unity-mono";

  async detect(context: DetectorContext): Promise<Detection[]> {
    // IL2CPP also ships Assembly-CSharp.dll for reflection — don't report Mono if IL2CPP is present.
    if (context.findEntries(/^lib\/[^/]+\/libil2cpp\.so$/i).length > 0) {
      return [];
    }

    const evidences: Evidence[] = [];

    const assemblies = context.findEntries(/(?:^|\/)Managed\/Assembly-CSharp\.dll$/i);
    if (assemblies.length > 0) {
      evidences.push(
        evidence({
          id: "assembly-csharp",
          summary: "Managed Unity game assembly",
          detail: "Assembly-CSharp.dll indicates a managed Mono scripting backend.",
          weight: 60,
          source: "file",
          locations: assemblies.map((entry) => context.location(entry)),
        }),
      );
    }

    const monoLibraries = context.findEntries(
      /^lib\/[^/]+\/(?:libmonobdwgc-2\.0|libmono|libmonosgen-2\.0)\.so$/i,
    );
    if (monoLibraries.length > 0) {
      evidences.push(
        evidence({
          id: "mono-runtime",
          summary: "Mono runtime library",
          detail: "The package contains a native Mono runtime used to execute managed assemblies.",
          weight: 40,
          source: "file",
          locations: monoLibraries.map((entry) => context.location(entry)),
        }),
      );
    }

    const detection = createDetection({
      id: "unity-mono",
      name: "Unity Mono",
      category: "backend",
      evidence: evidences,
    });
    return detection ? [detection] : [];
  }
}

async function detectUnityVersion(
  context: DetectorContext,
  // globalgamemanagers / resources.assets already found by the caller
  candidates: ReturnType<DetectorContext["findEntries"]>,
): Promise<{ version: string; location: string } | undefined> {
  // Widen the search to other serialized asset files that also carry the version header.
  const additional = context.findEntries(
    /(?:^|\/)(?:sharedassets\d+\.assets|level\d+|resources\.assets)$/i,
  );
  // Cap at 12 files — version detection is opportunistic, no need to scan everything.
  const entries = [...candidates, ...additional].slice(0, 12);

  for (const entry of entries) {
    // Skip huge asset bundles to avoid loading hundreds of MiB into memory.
    if (entry.uncompressedSize > 64 * 1024 * 1024) {
      continue;
    }
    try {
      const buffer = await context.read(entry);
      // Unity embeds the version string near the start of the file header — no need to scan the whole file.
      const searchWindow = buffer.subarray(0, Math.min(buffer.length, 2 * 1024 * 1024));
      // latin1 maps each byte 1:1 to a character, making regex safe on arbitrary binary data.
      const match = UNITY_VERSION_PATTERN.exec(searchWindow.toString("latin1"));
      if (match?.[1]) {
        return { version: match[1], location: context.location(entry) };
      }
    } catch {
      // Version detection is optional — a read failure doesn't invalidate the other evidence.
    }
  }
  return undefined;
}
