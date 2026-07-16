import {
  createDetection,
  evidence,
} from "../detection.js";
import type {
  Detection,
  Detector,
  DetectorContext,
  Evidence,
} from "../types.js";

interface SdkSignature {
  id: string;
  name: string;

  dexNeedles: Array<{
    value: string;
    weight: number;
    summary: string;
  }>;

  filePatterns?: Array<{
    pattern: RegExp;
    weight: number;
    summary: string;
  }>;

  manifestPatterns?: Array<{
    pattern: RegExp;
    weight: number;
    summary: string;
  }>;
}

const SDK_SIGNATURES: SdkSignature[] = [
  {
    id: "firebase",
    name: "Firebase",

    dexNeedles: [
      {
        value: "com/google/firebase",
        weight: 70,
        summary: "Firebase Android classes",
      },
    ],

    filePatterns: [
      {
        pattern: /^lib\/[^/]+\/libFirebaseCppApp[^/]*\.so$/i,
        weight: 20,
        summary: "Firebase C++ library",
      },
    ],

    manifestPatterns: [
      {
        pattern: /com\.google\.firebase/i,
        weight: 10,
        summary: "Firebase manifest component",
      },
    ],
  },

  {
    id: "facebook-sdk",
    name: "Meta / Facebook SDK",

    /*
     * Do not use the generic com/facebook/ namespace here.
     *
     * React Native classes live under com/facebook/react and would otherwise
     * cause a false-positive Facebook SDK detection.
     */
    dexNeedles: [
      {
        value: "com/facebook/FacebookSdk",
        weight: 55,
        summary: "Facebook SDK bootstrap class",
      },
      {
        value: "com/facebook/appevents",
        weight: 30,
        summary: "Facebook App Events classes",
      },
      {
        value: "com/facebook/login",
        weight: 25,
        summary: "Facebook Login classes",
      },
      {
        value: "com/facebook/AccessToken",
        weight: 25,
        summary: "Facebook access token class",
      },
    ],

    manifestPatterns: [
      {
        pattern:
          /com\.facebook\.(?:FacebookActivity|CustomTabActivity|CustomTabMainActivity)/i,
        weight: 25,
        summary: "Facebook SDK manifest activity",
      },
      {
        pattern: /com\.facebook\.app\.FacebookContentProvider/i,
        weight: 25,
        summary: "Facebook SDK content provider",
      },
    ],
  },

  {
    id: "google-mobile-ads",
    name: "Google Mobile Ads",

    dexNeedles: [
      {
        value: "com/google/android/gms/ads",
        weight: 80,
        summary: "Google Mobile Ads classes",
      },
    ],

    manifestPatterns: [
      {
        pattern: /com\.google\.android\.gms\.ads/i,
        weight: 20,
        summary: "Google Mobile Ads manifest component",
      },
    ],
  },

  {
    id: "appsflyer",
    name: "AppsFlyer",

    dexNeedles: [
      {
        value: "com/appsflyer",
        weight: 100,
        summary: "AppsFlyer Android classes",
      },
    ],
  },

  {
    id: "adjust",
    name: "Adjust",

    dexNeedles: [
      {
        value: "com/adjust/sdk",
        weight: 100,
        summary: "Adjust Android classes",
      },
    ],
  },

  {
    id: "onesignal",
    name: "OneSignal",

    dexNeedles: [
      {
        value: "com/onesignal",
        weight: 100,
        summary: "OneSignal Android classes",
      },
    ],
  },
];

export class ThirdPartySdkDetector implements Detector {
  readonly id = "third-party-sdks";

  async detect(context: DetectorContext): Promise<Detection[]> {
    const detections: Detection[] = [];

    for (const signature of SDK_SIGNATURES) {
      const evidences: Evidence[] = [];

      for (const needle of signature.dexNeedles) {
        const locations = await context.searchDex(needle.value);

        if (locations.length === 0) {
          continue;
        }

        evidences.push(
          evidence({
            id: `${signature.id}-dex-${needle.value}`,
            summary: needle.summary,
            detail: `DEX bytecode contains the signature ${needle.value}.`,
            weight: needle.weight,
            source: "dex",
            locations,
          }),
        );
      }

      for (const file of signature.filePatterns ?? []) {
        const matches = context.findEntries(file.pattern);

        if (matches.length === 0) {
          continue;
        }

        evidences.push(
          evidence({
            id: `${signature.id}-file-${file.summary}`,
            summary: file.summary,
            detail: "A package file matches a known SDK signature.",
            weight: file.weight,
            source: "file",
            locations: matches
              .slice(0, 5)
              .map((entry) => context.location(entry)),
          }),
        );
      }

      for (const manifest of signature.manifestPatterns ?? []) {
        const values = context.manifestStrings.filter((value) =>
          manifest.pattern.test(value),
        );

        if (values.length === 0) {
          continue;
        }

        evidences.push(
          evidence({
            id: `${signature.id}-manifest-${manifest.summary}`,
            summary: manifest.summary,
            detail: "The Android manifest contains a known SDK component.",
            weight: manifest.weight,
            source: "manifest",
            locations: [...new Set(values)].slice(0, 5),
          }),
        );
      }

      const detection = createDetection({
        id: signature.id,
        name: signature.name,
        category: "sdk",
        evidence: evidences,
      });

      if (detection) {
        detections.push(detection);
      }
    }

    return detections;
  }
}
