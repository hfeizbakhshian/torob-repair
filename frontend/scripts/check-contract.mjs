#!/usr/bin/env node
/**
 * Fails when the delivered contract, the backend code and the generated TypeScript types
 * have drifted apart.
 *
 * Two independent checks:
 *   1. the backend re-exports its OpenAPI document and compares it with
 *      `contracts/openapi.json`;
 *   2. the types are regenerated from that contract and compared with the committed
 *      `src/lib/api-types.ts`, which is why that file must never be hand-edited.
 */

import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const frontendRoot = resolve(here, "..");
const repoRoot = resolve(frontendRoot, "..");
const contract = join(repoRoot, "contracts", "openapi.json");
const committedTypes = join(frontendRoot, "src", "lib", "api-types.ts");

let failed = false;

function fail(message) {
  console.error(`✗ ${message}`);
  failed = true;
}

// 1. backend code vs the delivered contract
const backendCheck = spawnSync(
  "uv",
  ["run", "python", "-m", "app.export_openapi", "--check"],
  { cwd: join(repoRoot, "backend"), encoding: "utf8" },
);

if (backendCheck.error?.code === "ENOENT") {
  fail(
    "ابزار uv در دسترس نیست، بنابراین انطباق قرارداد با کد بک‌اند بررسی نشد. " +
      "uv را نصب کنید یا این بررسی را از ریشهٔ بک‌اند اجرا کنید.",
  );
} else if (backendCheck.status !== 0) {
  fail(
    "قرارداد contracts/openapi.json با کد بک‌اند هم‌خوان نیست. " +
      "`uv run python -m app.export_openapi` را اجرا کنید.\n" +
      (backendCheck.stderr || "").trim(),
  );
} else {
  console.log("✓ قرارداد OpenAPI با کد بک‌اند هم‌خوان است.");
}

// 2. the delivered contract vs the generated types
const scratch = mkdtempSync(join(tmpdir(), "torob-contract-"));
const generated = join(scratch, "api-types.ts");
try {
  const generateResult = spawnSync(
    process.execPath,
    [join(frontendRoot, "node_modules", "openapi-typescript", "bin", "cli.js"), contract, "-o", generated],
    { encoding: "utf8" },
  );
  if (generateResult.status !== 0) {
    fail(`تولید typeها از قرارداد ناموفق بود.\n${(generateResult.stderr || "").trim()}`);
  } else if (readFileSync(generated, "utf8") !== readFileSync(committedTypes, "utf8")) {
    fail(
      "typeهای تولیدشده با قرارداد هم‌خوان نیستند. `npm run api:types` را اجرا کنید " +
        "و فایل src/lib/api-types.ts را دستی ویرایش نکنید.",
    );
  } else {
    console.log("✓ typeهای TypeScript با قرارداد هم‌خوان‌اند.");
  }
} finally {
  rmSync(scratch, { recursive: true, force: true });
}

process.exit(failed ? 1 : 0);
