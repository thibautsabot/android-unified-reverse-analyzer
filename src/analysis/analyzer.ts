import { basename } from "node:path";
import type { AndroidPackage } from "../apk/android-package.js";
import { InspectionContext } from "./context.js";
import { FlutterDetector, GodotDetector, ReactNativeDetector, UnrealDetector, XamarinDetector, CordovaDetector, Cocos2dDetector, LibGDXDetector, DefoldDetector, CapacitorDetector } from "./detectors/frameworks.js";
import { ThirdPartySdkDetector } from "./detectors/sdks.js";
import { Il2CppDetector, UnityDetector, UnityMonoDetector } from "./detectors/unity.js";
import type { Detection, Detector, InspectionReport } from "./types.js";
import { buildRecommendedWorkflow } from "./workflow.js";
import { uniqueSorted } from "../utils.js";

const DEFAULT_DETECTORS: Detector[] = [
  new UnityDetector(),
  new Il2CppDetector(),
  new UnityMonoDetector(),
  new FlutterDetector(),
  new ReactNativeDetector(),
  new UnrealDetector(),
  new GodotDetector(),
  new Cocos2dDetector(),
  new LibGDXDetector(),
  new DefoldDetector(),
  new XamarinDetector(),
  new CordovaDetector(),
  new CapacitorDetector(),
  new ThirdPartySdkDetector(),
];

export async function analyzeAndroidPackage(
  pkg: AndroidPackage,
  detectors: readonly Detector[] = DEFAULT_DETECTORS,
): Promise<InspectionReport> {
  const context = new InspectionContext(pkg);
  const detections = (
    await Promise.all(detectors.map(async (detector) => detector.detect(context)))
  )
    .flat()
    .sort(detectionOrder);

  const entries = pkg.entries();
  const architectures = uniqueSorted(
    entries
      .map((entry) => /^lib\/([^/]+)\//i.exec(entry.path)?.[1])
      .filter((value): value is string => Boolean(value)),
  );
  const dexEntries = entries.filter((entry) => /(?:^|\/)classes\d*\.dex$/i.test(entry.path));
  const nativeLibraries = uniqueSorted(
    entries
      .map((entry) => /^lib\/[^/]+\/([^/]+\.so)$/i.exec(entry.path)?.[1])
      .filter((value): value is string => Boolean(value)),
  );
  const manifest = pkg.manifest;
  const warnings = [...pkg.warnings];

  if (!manifest) {
    warnings.push("AndroidManifest.xml could not be parsed; application metadata may be incomplete.");
  }
  if (detections.length === 0) {
    warnings.push("No supported application framework was identified with the current signatures.");
  }

  return {
    schemaVersion: "1.0",
    generatedAt: new Date().toISOString(),
    application: {
      input: pkg.inputPath,
      format: pkg.format,
      fileName: basename(pkg.inputPath),
      size: pkg.size,
      sha256: pkg.sha256,
      ...(manifest?.packageName ? { packageName: manifest.packageName } : {}),
      ...(manifest?.versionName ? { versionName: manifest.versionName } : {}),
      ...(manifest?.versionCode !== undefined ? { versionCode: manifest.versionCode } : {}),
    },
    android: {
      ...(manifest?.minSdk !== undefined ? { minSdk: manifest.minSdk } : {}),
      ...(manifest?.targetSdk !== undefined ? { targetSdk: manifest.targetSdk } : {}),
      ...(manifest?.debuggable !== undefined ? { debuggable: manifest.debuggable } : {}),
      ...(manifest?.allowBackup !== undefined ? { allowBackup: manifest.allowBackup } : {}),
      ...(manifest?.usesCleartextTraffic !== undefined ? { usesCleartextTraffic: manifest.usesCleartextTraffic } : {}),
      ...(manifest?.networkSecurityConfig !== undefined ? { networkSecurityConfig: manifest.networkSecurityConfig } : {}),
      permissions: manifest?.permissions ?? [],
      activities: manifest?.activities ?? [],
      services: manifest?.services ?? [],
      receivers: manifest?.receivers ?? [],
      providers: manifest?.providers ?? [],
      metaData: manifest?.metaData ?? [],
      architectures,
      dexFiles: dexEntries.length,
      multiDex: dexEntries.length > 1,
      nativeLibraries,
      parts: pkg.partSummaries,
    },
    detections,
    workflow: buildRecommendedWorkflow(detections, nativeLibraries.length > 0),
    warnings,
  };
}

function detectionOrder(left: Detection, right: Detection): number {
  const categoryOrder = ["framework", "backend", "sdk", "toolchain"];
  const categoryDifference =
    categoryOrder.indexOf(left.category) - categoryOrder.indexOf(right.category);
  if (categoryDifference !== 0) {
    return categoryDifference;
  }
  return right.confidence - left.confidence || left.name.localeCompare(right.name);
}

