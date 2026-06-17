import { readFile, readdir } from "node:fs/promises";
import { join } from "node:path";
import { canonicalizeJson, parseJsonStructural, structuralJsonEqual } from "../../src/index.js";
import { goGeneratedRoot, rustGeneratedRoot, typescriptGeneratedRoot } from "./paths.js";

const EXPECTED_RUNTIME = 224;
const EXPECTED_JSON_ONLY = 8;
const EXPECTED_TOTAL = EXPECTED_RUNTIME + EXPECTED_JSON_ONLY;

type Generated = {
  label: "TypeScript" | "Go";
  root: string;
};

const rustFiles = await jsonFiles(rustGeneratedRoot);
assertExpectedCounts("Rust", rustFiles);

const generated: Generated[] = [
  { label: "TypeScript", root: typescriptGeneratedRoot },
  { label: "Go", root: goGeneratedRoot },
];

for (const candidate of generated) {
  await compareGenerated(candidate, rustFiles);
}

console.log(`request JSON parity passed for ${rustFiles.length} fixture(s) across Rust, TypeScript, and Go`);

async function compareGenerated(candidate: Generated, rustFiles: string[]) {
  const candidateFiles = await jsonFiles(candidate.root);
  assertExpectedCounts(candidate.label, candidateFiles);

  const rustSet = new Set(rustFiles);
  const candidateSet = new Set(candidateFiles);
  const missingInCandidate = rustFiles.filter((file) => !candidateSet.has(file));
  const extraInCandidate = candidateFiles.filter((file) => !rustSet.has(file));
  if (missingInCandidate.length || extraInCandidate.length) {
    throw new Error(
      [
        missingInCandidate.length ? `missing ${candidate.label} fixtures:\n${missingInCandidate.join("\n")}` : "",
        extraInCandidate.length ? `extra ${candidate.label} fixtures:\n${extraInCandidate.join("\n")}` : "",
      ]
        .filter(Boolean)
        .join("\n\n"),
    );
  }

  const mismatches: string[] = [];
  for (const file of rustFiles) {
    const rustJson = await readFile(join(rustGeneratedRoot, file), "utf8");
    const candidateJson = await readFile(join(candidate.root, file), "utf8");
    if (!structuralJsonEqual(rustJson, candidateJson)) {
      mismatches.push(
        `${file}\nRust: ${JSON.stringify(canonicalizeJson(parseJsonStructural(rustJson)))}\n${candidate.label}: ${JSON.stringify(canonicalizeJson(parseJsonStructural(candidateJson)))}`,
      );
    }
  }

  if (mismatches.length) {
    throw new Error(`request JSON parity failed for ${mismatches.length} ${candidate.label} fixture(s):\n\n${mismatches.join("\n\n")}`);
  }
}

function assertExpectedCounts(label: string, files: string[]) {
  const runtime = files.filter((file) => file.startsWith("runtime/")).length;
  const jsonOnly = files.filter((file) => file.startsWith("json-only/")).length;
  if (runtime !== EXPECTED_RUNTIME || jsonOnly !== EXPECTED_JSON_ONLY || files.length !== EXPECTED_TOTAL) {
    throw new Error(`${label} fixture count mismatch: runtime=${runtime}, json-only=${jsonOnly}, total=${files.length}`);
  }
}

async function jsonFiles(root: string, dir = ""): Promise<string[]> {
  const entries = await readdir(join(root, dir), { withFileTypes: true });
  const files = await Promise.all(
    entries.map(async (entry) => {
      const rel = join(dir, entry.name);
      if (entry.isDirectory()) return jsonFiles(root, rel);
      if (entry.isFile() && entry.name.endsWith(".json")) return [rel];
      return [];
    }),
  );
  return files.flat().sort((a, b) => a.localeCompare(b));
}
