import {
  createDetection,
  evidence,
} from "../detection.js";
import type { Detection, Detector, DetectorContext, Evidence } from "../types.js";

export class FlutterDetector implements Detector {
  readonly id = "flutter";

  async detect(context: DetectorContext): Promise<Detection[]> {
    const evidences: Evidence[] = [];
    addFileEvidence(context, evidences, {
      pattern: /^lib\/[^/]+\/libflutter\.so$/i,
      id: "flutter-engine",
      summary: "Flutter engine library",
      detail: "libflutter.so contains the Flutter engine.",
      weight: 40,
    });
    addFileEvidence(context, evidences, {
      pattern: /^lib\/[^/]+\/libapp\.so$/i,
      id: "flutter-app",
      summary: "Flutter application library",
      detail: "libapp.so commonly contains AOT-compiled Dart application code.",
      weight: 30,
    });
    addFileEvidence(context, evidences, {
      pattern: /^assets\/flutter_assets\//i,
      id: "flutter-assets",
      summary: "Flutter asset bundle",
      detail: "The package contains Flutter's generated asset directory.",
      weight: 30,
      limit: 5,
    });

    return single(
      createDetection({
        id: "flutter",
        name: "Flutter",
        category: "framework",
        evidence: evidences,
      }),
    );
  }
}

export class ReactNativeDetector implements Detector {
  readonly id = "react-native";

  async detect(context: DetectorContext): Promise<Detection[]> {
    const evidences: Evidence[] = [];
    addFileEvidence(context, evidences, {
      pattern: /(?:^|\/)index\.android\.bundle$/i,
      id: "react-native-bundle",
      summary: "React Native JavaScript bundle",
      detail: "index.android.bundle is a conventional React Native production bundle.",
      weight: 40,
    });
    addFileEvidence(context, evidences, {
      pattern: /^lib\/[^/]+\/libreactnativejni\.so$/i,
      id: "react-native-jni",
      summary: "React Native JNI library",
      detail: "libreactnativejni.so bridges the Android and React Native runtimes.",
      weight: 25,
    });
    addFileEvidence(context, evidences, {
      pattern: /^lib\/[^/]+\/libhermes\.so$/i,
      id: "hermes-runtime",
      summary: "Hermes JavaScript runtime",
      detail: "Hermes is commonly used by React Native Android applications.",
      weight: 15,
    });
    const dexLocations = await context.searchDex("com/facebook/react");
    if (dexLocations.length > 0) {
      evidences.push(
        evidence({
          id: "react-native-dex",
          summary: "React Native Android classes",
          detail: "DEX bytecode references the com.facebook.react package.",
          weight: 20,
          source: "dex",
          locations: dexLocations,
        }),
      );
    }

    return single(
      createDetection({
        id: "react-native",
        name: "React Native",
        category: "framework",
        evidence: evidences,
      }),
    );
  }
}

export class UnrealDetector implements Detector {
  readonly id = "unreal-engine";

  async detect(context: DetectorContext): Promise<Detection[]> {
    const evidences: Evidence[] = [];
    addFileEvidence(context, evidences, {
      pattern: /^lib\/[^/]+\/(?:libUE4|libUnreal)\.so$/i,
      id: "unreal-library",
      summary: "Unreal Engine native library",
      detail: "The package contains a native Unreal Engine runtime library.",
      weight: 55,
    });
    addFileEvidence(context, evidences, {
      pattern: /(?:^|\/)[^/]+\.pak$/i,
      id: "unreal-pak",
      summary: "Unreal Pak archive",
      detail: "Pak archives are commonly used to package Unreal Engine content.",
      weight: 25,
      limit: 5,
    });
    const dexLocations = [
      ...(await context.searchDex("com/epicgames/unreal")),
      ...(await context.searchDex("GameActivity")),
    ];
    if (dexLocations.length > 0) {
      evidences.push(
        evidence({
          id: "unreal-android",
          summary: "Unreal Android bootstrap",
          detail: "DEX bytecode contains Unreal Engine Android bootstrap classes.",
          weight: 20,
          source: "dex",
          locations: [...new Set(dexLocations)],
        }),
      );
    }

    return single(
      createDetection({
        id: "unreal-engine",
        name: "Unreal Engine",
        category: "framework",
        evidence: evidences,
      }),
    );
  }
}

export class GodotDetector implements Detector {
  readonly id = "godot";

  async detect(context: DetectorContext): Promise<Detection[]> {
    const evidences: Evidence[] = [];
    addFileEvidence(context, evidences, {
      pattern: /^lib\/[^/]+\/libgodot_android\.so$/i,
      id: "godot-library",
      summary: "Godot Android library",
      detail: "libgodot_android.so contains the Godot Android runtime.",
      weight: 55,
    });
    addFileEvidence(context, evidences, {
      pattern: /^assets\/(?:\.godot\/|project\.binary$|_cl_)/i,
      id: "godot-assets",
      summary: "Godot project data",
      detail: "The package contains paths associated with exported Godot projects.",
      weight: 25,
      limit: 5,
    });
    const dexLocations = await context.searchDex("org/godotengine");
    if (dexLocations.length > 0) {
      evidences.push(
        evidence({
          id: "godot-dex",
          summary: "Godot Android classes",
          detail: "DEX bytecode references the org.godotengine package.",
          weight: 20,
          source: "dex",
          locations: dexLocations,
        }),
      );
    }

    return single(
      createDetection({
        id: "godot",
        name: "Godot",
        category: "framework",
        evidence: evidences,
      }),
    );
  }
}

export class XamarinDetector implements Detector {
  readonly id = "xamarin";

  async detect(context: DetectorContext): Promise<Detection[]> {
    const evidences: Evidence[] = [];
    addFileEvidence(context, evidences, {
      pattern: /^lib\/[^/]+\/libmonodroid\.so$/i,
      id: "xamarin-monodroid",
      summary: "Xamarin Android runtime",
      detail: "libmonodroid.so is used by Xamarin and .NET Android applications.",
      weight: 45,
    });
    addFileEvidence(context, evidences, {
      pattern: /(?:^|\/)assemblies\/.*\.dll$/i,
      id: "xamarin-assemblies",
      summary: ".NET Android assemblies",
      detail: "The package contains managed assemblies in an assemblies directory.",
      weight: 35,
      limit: 5,
    });
    const mauiLocations = await context.searchDex("Microsoft.Maui");
    if (mauiLocations.length > 0) {
      evidences.push(
        evidence({
          id: "dotnet-maui",
          summary: ".NET MAUI references",
          detail: "DEX bytecode contains .NET MAUI identifiers.",
          weight: 20,
          source: "dex",
          locations: mauiLocations,
        }),
      );
    }

    return single(
      createDetection({
        id: "xamarin",
        name: "Xamarin / .NET Android",
        category: "framework",
        evidence: evidences,
      }),
    );
  }
}

export class CordovaDetector implements Detector {
  readonly id = "cordova";

  async detect(context: DetectorContext): Promise<Detection[]> {
    const evidences: Evidence[] = [];
    addFileEvidence(context, evidences, {
      pattern: /^assets\/www\/cordova(?:\.min)?\.js$/i,
      id: "cordova-js",
      summary: "Cordova JavaScript runtime",
      detail: "The web asset bundle contains Cordova's JavaScript bridge.",
      weight: 60,
    });
    const dexLocations = await context.searchDex("org/apache/cordova");
    if (dexLocations.length > 0) {
      evidences.push(
        evidence({
          id: "cordova-dex",
          summary: "Cordova Android classes",
          detail: "DEX bytecode references the org.apache.cordova package.",
          weight: 40,
          source: "dex",
          locations: dexLocations,
        }),
      );
    }

    return single(
      createDetection({
        id: "cordova",
        name: "Apache Cordova / Ionic",
        category: "framework",
        evidence: evidences,
      }),
    );
  }
}

export class Cocos2dDetector implements Detector {
  readonly id = "cocos2d";

  async detect(context: DetectorContext): Promise<Detection[]> {
    const evidences: Evidence[] = [];
    addFileEvidence(context, evidences, {
      pattern: /^lib\/[^/]+\/libcocos2dcpp\.so$/i,
      id: "cocos2d-library",
      summary: "Cocos2d-x native library",
      detail: "libcocos2dcpp.so is the Cocos2d-x C++ runtime.",
      weight: 55,
    });
    addFileEvidence(context, evidences, {
      pattern: /^assets\/(?:res|src|scripts)\//i,
      id: "cocos2d-assets",
      summary: "Cocos2d-x asset layout",
      detail: "The assets/res or assets/src layout is conventional for Cocos2d-x projects.",
      weight: 20,
      limit: 5,
    });
    const dexLocations = await context.searchDex("org/cocos2dx");
    if (dexLocations.length > 0) {
      evidences.push(evidence({
        id: "cocos2d-dex",
        summary: "Cocos2d-x Android classes",
        detail: "DEX bytecode references the org.cocos2dx package.",
        weight: 25,
        source: "dex",
        locations: dexLocations,
      }));
    }

    return single(createDetection({ id: "cocos2d", name: "Cocos2d-x", category: "framework", evidence: evidences }));
  }
}

export class LibGDXDetector implements Detector {
  readonly id = "libgdx";

  async detect(context: DetectorContext): Promise<Detection[]> {
    const evidences: Evidence[] = [];
    addFileEvidence(context, evidences, {
      pattern: /^lib\/[^/]+\/libgdx(?:backend)?\.so$/i,
      id: "libgdx-native",
      summary: "LibGDX native library",
      detail: "libgdx.so or libgdxbackend.so contains the LibGDX native runtime.",
      weight: 55,
    });
    const dexLocations = await context.searchDex("com/badlogic/gdx");
    if (dexLocations.length > 0) {
      evidences.push(evidence({
        id: "libgdx-dex",
        summary: "LibGDX classes",
        detail: "DEX bytecode references the com.badlogic.gdx package.",
        weight: 45,
        source: "dex",
        locations: dexLocations,
      }));
    }

    return single(createDetection({ id: "libgdx", name: "LibGDX", category: "framework", evidence: evidences }));
  }
}

export class DefoldDetector implements Detector {
  readonly id = "defold";

  async detect(context: DetectorContext): Promise<Detection[]> {
    const evidences: Evidence[] = [];
    addFileEvidence(context, evidences, {
      pattern: /^lib\/[^/]+\/libdmengine\.so$/i,
      id: "defold-library",
      summary: "Defold engine library",
      detail: "libdmengine.so is the Defold native engine runtime.",
      weight: 70,
    });
    addFileEvidence(context, evidences, {
      pattern: /(?:^|\/)game\.arcd?$/i,
      id: "defold-archive",
      summary: "Defold game archive",
      detail: "game.arc or game.arcd is the Defold packed game resource archive.",
      weight: 30,
    });

    return single(createDetection({ id: "defold", name: "Defold", category: "framework", evidence: evidences }));
  }
}

export class CapacitorDetector implements Detector {
  readonly id = "capacitor";

  async detect(context: DetectorContext): Promise<Detection[]> {
    const evidences: Evidence[] = [];
    addFileEvidence(context, evidences, {
      pattern: /^assets\/public\/index\.html$/i,
      id: "capacitor-html",
      summary: "Capacitor web entry point",
      detail: "assets/public/index.html is the conventional Capacitor web app entry point.",
      weight: 30,
    });
    addFileEvidence(context, evidences, {
      pattern: /^assets\/public\/.*capacitor(?:\.min)?\.js$/i,
      id: "capacitor-js",
      summary: "Capacitor JavaScript runtime",
      detail: "The web asset bundle contains the Capacitor JavaScript bridge.",
      weight: 50,
    });
    const dexLocations = await context.searchDex("com/getcapacitor");
    if (dexLocations.length > 0) {
      evidences.push(evidence({
        id: "capacitor-dex",
        summary: "Capacitor Android classes",
        detail: "DEX bytecode references the com.getcapacitor package.",
        weight: 40,
        source: "dex",
        locations: dexLocations,
      }));
    }

    return single(createDetection({ id: "capacitor", name: "Capacitor", category: "framework", evidence: evidences }));
  }
}

function addFileEvidence(
  context: DetectorContext,
  evidences: Evidence[],
  options: {
    pattern: RegExp;
    id: string;
    summary: string;
    detail: string;
    weight: number;
    limit?: number;
  },
): void {
  const matches = context.findEntries(options.pattern);
  if (matches.length === 0) {
    return;
  }
  evidences.push(
    evidence({
      id: options.id,
      summary: options.summary,
      detail: options.detail,
      weight: options.weight,
      source: "file",
      locations: matches
        .slice(0, options.limit ?? matches.length)
        .map((entry) => context.location(entry)),
    }),
  );
}

function single(detection: Detection | null): Detection[] {
  return detection ? [detection] : [];
}
