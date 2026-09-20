#!/usr/bin/env node
/**
 * Bring up an isolated stack for the E2E run.
 *
 * It uses TEST_DATABASE_URL and its own ports, so it never touches the demo database or
 * a running dev server. The schema is migrated and seeded explicitly before the services
 * start, and every child is stopped on exit.
 */

import { spawn, spawnSync } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const frontendRoot = resolve(here, "..");
const repoRoot = resolve(frontendRoot, "..");
const backendRoot = join(repoRoot, "backend");

const WEB_PORT = process.env.E2E_WEB_PORT ?? "3100";
const API_PORT = process.env.E2E_API_PORT ?? "8100";
const API_ORIGIN = `http://127.0.0.1:${API_PORT}`;
const WEB_ORIGIN = `http://127.0.0.1:${WEB_PORT}`;

const testDatabaseUrl =
  process.env.TEST_DATABASE_URL ??
  "postgresql+asyncpg://torob:torob@127.0.0.1:5438/torob_repair_test";

const backendEnv = {
  ...process.env,
  APP_MODE: "demo",
  AI_MODE: "mock",
  // The E2E stack runs entirely against the test database.
  DATABASE_URL: testDatabaseUrl,
  WEB_ORIGIN,
  API_PORT,
  ATTACHMENT_DIR: join(backendRoot, "var", "e2e-attachments"),
};

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: backendRoot,
    env: backendEnv,
    encoding: "utf8",
    ...options,
  });
  if (result.status !== 0) {
    console.error(`[e2e] ${command} ${args.join(" ")} failed`);
    console.error(result.stdout ?? "");
    console.error(result.stderr ?? "");
    process.exit(1);
  }
  return result;
}

console.log("[e2e] resetting the test schema");
run("uv", ["run", "python", "-m", "app.e2e_reset"]);
console.log("[e2e] applying migrations");
run("uv", ["run", "alembic", "upgrade", "head"]);
console.log("[e2e] seeding sample data");
run("uv", ["run", "python", "-m", "app.seed"]);

const children = [];

function start(name, command, args, cwd, env) {
  const child = spawn(command, args, { cwd, env, stdio: "inherit" });
  child.on("exit", (code) => {
    if (code !== 0 && !stopping) {
      console.error(`[e2e] ${name} exited with ${code}`);
      shutdown(1);
    }
  });
  children.push(child);
  return child;
}

let stopping = false;
function shutdown(code = 0) {
  if (stopping) return;
  stopping = true;
  for (const child of children) child.kill("SIGTERM");
  setTimeout(() => process.exit(code), 500);
}

process.on("SIGINT", () => shutdown(0));
process.on("SIGTERM", () => shutdown(0));

start(
  "api",
  "uv",
  ["run", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", API_PORT],
  backendRoot,
  backendEnv,
);
start("worker", "uv", ["run", "python", "-m", "app.worker"], backendRoot, backendEnv);
start(
  "web",
  "npm",
  ["run", "dev", "--", "--hostname", "127.0.0.1", "--port", WEB_PORT],
  frontendRoot,
  { ...process.env, BACKEND_ORIGIN: API_ORIGIN, PORT: WEB_PORT },
);
