import type {
  Detection,
  DetectionCategory,
  DetectionStatus,
  Evidence,
} from "./types.js";

export function createDetection(options: {
  id: string;
  name: string;
  category: DetectionCategory;
  evidence: Evidence[];
  details?: Record<string, string | number | boolean | string[]>;
}): Detection | null {
  if (options.evidence.length === 0) {
    return null;
  }

  const confidence = Math.min(
    100,
    options.evidence.reduce((sum, evidence) => sum + evidence.weight, 0),
  );

  return {
    id: options.id,
    name: options.name,
    category: options.category,
    confidence,
    status: statusFor(confidence, options.evidence),
    evidence: options.evidence,
    details: options.details ?? {},
  };
}

export function evidence(options: {
  id: string;
  summary: string;
  detail: string;
  weight: number;
  source: Evidence["source"];
  locations: string[];
}): Evidence {
  return options;
}

function statusFor(
  confidence: number,
  evidence: readonly Evidence[],
): DetectionStatus {
  /*
   * A single signature should not be presented as independently confirmed,
   * even when its evidence score reaches 100.
   *
   * Confirmation requires at least two pieces of evidence.
   */
  if (confidence >= 80 && evidence.length >= 2) {
    return "confirmed";
  }

  if (confidence >= 50) {
    return "likely";
  }

  return "possible";
}
