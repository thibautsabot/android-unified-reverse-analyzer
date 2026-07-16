import type {
  AndroidPackageFormat,
  ManifestActivity,
  ManifestComponent,
  ManifestMetaData,
  ManifestProvider,
  PackagePartSummary,
} from "../apk/types.js";

export type DetectionCategory = "framework" | "backend" | "sdk" | "toolchain";
export type DetectionStatus = "confirmed" | "likely" | "possible";
export type EvidenceSource = "file" | "manifest" | "dex" | "content";

export interface Evidence {
  id: string;
  summary: string;
  detail: string;
  weight: number;
  source: EvidenceSource;
  locations: string[];
}

export interface Detection {
  id: string;
  name: string;
  category: DetectionCategory;
  confidence: number;
  status: DetectionStatus;
  evidence: Evidence[];
  details: Record<string, string | number | boolean | string[]>;
}

export interface WorkflowStep {
  order: number;
  tool: string;
  purpose: string;
}

export interface ApplicationSummary {
  input: string;
  format: AndroidPackageFormat;
  fileName: string;
  size: number;
  sha256: string;
  packageName?: string;
  versionName?: string;
  versionCode?: number;
}

export interface AndroidSummary {
  minSdk?: number | string;
  targetSdk?: number | string;
  debuggable?: boolean;
  allowBackup?: boolean;
  usesCleartextTraffic?: boolean;
  networkSecurityConfig?: string;
  permissions: string[];
  activities: ManifestActivity[];
  services: ManifestComponent[];
  receivers: ManifestComponent[];
  providers: ManifestProvider[];
  metaData: ManifestMetaData[];
  architectures: string[];
  dexFiles: number;
  multiDex: boolean;
  nativeLibraries: string[];
  parts: PackagePartSummary[];
}

export interface InspectionReport {
  schemaVersion: "1.0";
  generatedAt: string;
  application: ApplicationSummary;
  android: AndroidSummary;
  detections: Detection[];
  workflow: WorkflowStep[];
  warnings: string[];
}

export interface Detector {
  readonly id: string;
  detect(context: DetectorContext): Promise<Detection[]>;
}

export interface DetectorContext {
  readonly manifestStrings: readonly string[];
  findEntries(pattern: RegExp): import("../apk/types.js").PackageEntry[];
  location(entry: import("../apk/types.js").PackageEntry): string;
  read(entry: import("../apk/types.js").PackageEntry): Promise<Buffer>;
  searchDex(needle: string): Promise<string[]>;
}
