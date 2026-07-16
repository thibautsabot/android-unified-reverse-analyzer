import type { Detection, WorkflowStep } from "./types.js";

export function buildRecommendedWorkflow(detections: Detection[], hasNativeCode: boolean): WorkflowStep[] {
  const ids = new Set(detections.map((detection) => detection.id));
  const steps: Array<Omit<WorkflowStep, "order">> = [];
  const add = (tool: string, purpose: string): void => {
    if (!steps.some((step) => step.tool === tool)) {
      steps.push({ tool, purpose });
    }
  };

  add("MobSF", "Review permissions, exported components and common Android security findings.");
  add("JADX", "Inspect the Android layer, entry points, SDK integrations and DEX code.");
  add("apktool", "Decode resources and Smali, then rebuild controlled static patches.");

  if (ids.has("unity")) {
    add("AssetRipper", "Recover Unity scenes, prefabs, serialized assets and object hierarchies.");
    if (ids.has("il2cpp")) {
      add("Il2CppDumper or Cpp2IL", "Recover managed types, methods, fields and native method addresses.");
      add("Ghidra, IDA or Binary Ninja", "Analyze the native implementations inside libil2cpp.so.");
    } else if (ids.has("unity-mono")) {
      add("dnSpyEx or ILSpy", "Inspect the managed Unity assemblies, including Assembly-CSharp.dll.");
    }
  }

  if (ids.has("flutter")) {
    add("Flutter asset inspection", "Inspect assets/flutter_assets and the application's Dart snapshots.");
    add("Ghidra, IDA or Binary Ninja", "Analyze libapp.so and native Flutter libraries when required.");
  }

  if (ids.has("react-native")) {
    add("JavaScript or Hermes bundle inspection", "Inspect index.android.bundle or Hermes bytecode.");
  }

  if (ids.has("unreal-engine")) {
    add("UnrealPak-compatible tooling", "Inspect packaged Unreal Engine assets and Pak archives.");
    add("Ghidra, IDA or Binary Ninja", "Analyze the Unreal native runtime and application code.");
  }

  if (ids.has("godot")) {
    add("Godot project inspection", "Inspect exported Godot resources and project data.");
  }

  if (ids.has("xamarin")) {
    add("dnSpyEx or ILSpy", "Inspect packaged .NET assemblies.");
  }

  if (ids.has("cordova")) {
    add("Web asset inspection", "Inspect assets/www, JavaScript bundles and the Cordova bridge.");
  }

  if (hasNativeCode && !steps.some((step) => step.tool.includes("Ghidra"))) {
    add("Ghidra, IDA or Binary Ninja", "Inspect native shared libraries and exported functions.");
  }

  add("Frida", "Observe and instrument Java and native behavior while the application runs.");

  return steps.map((step, index) => ({ order: index + 1, ...step }));
}
