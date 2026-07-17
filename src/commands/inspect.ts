import { resolve } from "node:path";
import { analyzeAndroidPackage } from "../analysis/analyzer.js";
import type { Detection, InspectionReport } from "../analysis/types.js";
import { AndroidPackage } from "../apk/android-package.js";

export async function runInspectCommand(
  input: string,
  options: { verbose?: boolean } = {},
): Promise<string> {
  const packagePath = resolve(input);
  const pkg = await AndroidPackage.open(packagePath);
  try {
    const report = await analyzeAndroidPackage(pkg);
    return renderConsoleReport(report, options.verbose ?? false);
  } finally {
    await pkg.close();
  }
}

const categoryLabels: Record<Detection["category"], string> = {
  framework: "Frameworks",
  backend: "Backends",
  sdk: "SDKs",
  toolchain: "Toolchain",
};

function renderConsoleReport(report: InspectionReport, verbose: boolean): string {
  const c = colors();
  const lines: string[] = [];

  lines.push(c.bold("AURA APK Inspection"));
  lines.push(
    c.dim(
      "An Android reverse-engineering toolkit for package inspection, environment diagnostics, and runtime analysis.",
    ),
  );
  lines.push("");

  section(lines, "Application", c);
  field(lines, "File", report.application.fileName);
  field(lines, "Format", report.application.format.toUpperCase());
  field(lines, "Size", formatBytes(report.application.size));
  field(lines, "SHA-256", report.application.sha256);
  field(lines, "Package", report.application.packageName ?? "Unknown");
  field(
    lines,
    "Version",
    formatVersion(report.application.versionName, report.application.versionCode),
  );

  section(lines, "Android", c);
  field(lines, "Min SDK", valueOrUnknown(report.android.minSdk));
  field(lines, "Target SDK", valueOrUnknown(report.android.targetSdk));
  field(lines, "Architectures", joinOrNone(report.android.architectures));
  field(
    lines,
    "DEX files",
    `${report.android.dexFiles}${report.android.multiDex ? " (MultiDEX)" : ""}`,
  );
  field(lines, "Native libraries", String(report.android.nativeLibraries.length));
  if (report.android.debuggable !== undefined) {
    field(lines, "Debuggable", report.android.debuggable ? c.red("yes") : "no");
  }
  if (report.android.allowBackup !== undefined) {
    field(lines, "Allow backup", report.android.allowBackup ? c.yellow("yes") : "no");
  }
  if (report.android.usesCleartextTraffic !== undefined) {
    field(
      lines,
      "Cleartext traffic",
      report.android.usesCleartextTraffic ? c.red("allowed") : "blocked",
    );
  }
  if (report.android.networkSecurityConfig !== undefined) {
    field(lines, "Net security cfg", report.android.networkSecurityConfig);
  }

  const categories: Detection["category"][] = ["framework", "backend", "sdk", "toolchain"];
  for (const category of categories) {
    const detections = report.detections.filter((detection) => detection.category === category);
    if (detections.length === 0) {
      continue;
    }

    section(lines, categoryLabels[category], c);
    for (const detection of detections) {
      renderDetection(lines, detection, c, verbose);
    }
  }

  section(lines, "Recommended workflow", c);
  for (const step of report.workflow) {
    lines.push(`${c.cyan(String(step.order).padStart(2, " "))}. ${c.bold(step.tool)}`);
    lines.push(`    ${step.purpose}`);
  }

  if (verbose) {
    if (report.android.permissions.length > 0) {
      section(lines, `Permissions (${report.android.permissions.length})`, c);
      for (const permission of report.android.permissions) {
        lines.push(`  ${permission}`);
      }
    }

    if (report.android.activities.length > 0) {
      section(lines, `Activities (${report.android.activities.length})`, c);
      for (const activity of report.android.activities) {
        const markers = [
          activity.launcher ? "launcher" : undefined,
          activity.exported === true ? "exported" : undefined,
        ].filter(Boolean);
        lines.push(`  ${activity.name}${markers.length > 0 ? ` (${markers.join(", ")})` : ""}`);
      }
    }

    if (report.android.services.length > 0) {
      section(lines, `Services (${report.android.services.length})`, c);
      for (const service of report.android.services) {
        const markers = [
          service.exported === true ? "exported" : undefined,
          service.permission ? `permission: ${service.permission}` : undefined,
        ].filter(Boolean);
        lines.push(`  ${service.name}${markers.length > 0 ? ` (${markers.join(", ")})` : ""}`);
      }
    }

    if (report.android.receivers.length > 0) {
      section(lines, `Receivers (${report.android.receivers.length})`, c);
      for (const receiver of report.android.receivers) {
        const markers = [
          receiver.exported === true ? "exported" : undefined,
          receiver.permission ? `permission: ${receiver.permission}` : undefined,
        ].filter(Boolean);
        lines.push(`  ${receiver.name}${markers.length > 0 ? ` (${markers.join(", ")})` : ""}`);
      }
    }

    if (report.android.providers.length > 0) {
      section(lines, `Providers (${report.android.providers.length})`, c);
      for (const provider of report.android.providers) {
        const markers = [
          provider.exported === true ? "exported" : undefined,
          provider.authorities ? `authorities: ${provider.authorities}` : undefined,
          provider.permission ? `permission: ${provider.permission}` : undefined,
        ].filter(Boolean);
        lines.push(`  ${provider.name}${markers.length > 0 ? ` (${markers.join(", ")})` : ""}`);
      }
    }
  }

  if (report.android.metaData.length > 0) {
    section(lines, `Metadata (${report.android.metaData.length})`, c);
    for (const meta of report.android.metaData) {
      lines.push(`  ${meta.name}${meta.value !== undefined ? `: ${meta.value}` : ""}`);
    }
  }

  if (report.android.parts.length > 1) {
    section(lines, `Package parts (${report.android.parts.length})`, c);
    for (const part of report.android.parts) {
      lines.push(
        `  ${part.base ? c.green("base") : "split"}  ${part.name}${part.splitName ? ` [${part.splitName}]` : ""}`,
      );
    }
  }

  if (report.warnings.length > 0) {
    section(lines, "Warnings", c);
    for (const warning of report.warnings) {
      lines.push(`  ${c.yellow("!")} ${warning}`);
    }
  }

  return `${lines.join("\n")}\n`;
}

function renderDetection(
  lines: string[],
  detection: Detection,
  c: ReturnType<typeof colors>,
  verbose = false,
): void {
  const status = detection.status.toUpperCase();
  const statusText =
    detection.status === "confirmed"
      ? c.green(status)
      : detection.status === "likely"
        ? c.yellow(status)
        : c.dim(status);

  lines.push(
    `${c.bold(detection.name)}  ${statusText}  ${c.dim(`score ${detection.confidence}/100`)}`,
  );

  for (const [key, value] of Object.entries(detection.details)) {
    lines.push(`  ${key}: ${Array.isArray(value) ? value.join(", ") : String(value)}`);
  }

  lines.push("  Evidence:");
  for (const evidence of detection.evidence) {
    const weight = c.dim(` (+${evidence.weight})`);
    lines.push(`    ${c.green("✓")} ${evidence.summary}${weight}`);
    if (verbose && evidence.locations.length > 0) {
      for (const location of evidence.locations) {
        lines.push(`      ${c.dim(location)}`);
      }
    }
    lines.push(`      ${evidence.detail}`);
  }
  lines.push("");
}

function section(lines: string[], title: string, c: ReturnType<typeof colors>): void {
  if (lines.length > 0 && lines.at(-1) !== "") {
    lines.push("");
  }
  lines.push(c.bold(title));
  lines.push(c.dim("-".repeat(Math.max(12, title.length))));
}

function field(lines: string[], label: string, value: string): void {
  lines.push(`${label.padEnd(18, " ")} ${value}`);
}

function formatVersion(name: string | undefined, code: number | undefined): string {
  if (name && code !== undefined) {
    return `${name} (${code})`;
  }
  if (name) {
    return name;
  }
  if (code !== undefined) {
    return String(code);
  }
  return "Unknown";
}

function valueOrUnknown(value: string | number | undefined): string {
  return value === undefined ? "Unknown" : String(value);
}

function joinOrNone(values: string[]): string {
  return values.length > 0 ? values.join(", ") : "None detected";
}

function formatBytes(value: number): string {
  if (value < 1024) {
    return `${value} B`;
  }
  const units = ["KiB", "MiB", "GiB"];
  let amount = value / 1024;
  let index = 0;
  while (amount >= 1024 && index < units.length - 1) {
    amount /= 1024;
    index += 1;
  }
  return `${amount.toFixed(2)} ${units[index]}`;
}

function colors() {
  const wrap =
    (code: number) =>
    (value: string): string =>
      `\u001b[${code}m${value}\u001b[0m`;
  return {
    bold: wrap(1),
    dim: wrap(2),
    red: wrap(31),
    green: wrap(32),
    yellow: wrap(33),
    cyan: wrap(36),
  };
}
