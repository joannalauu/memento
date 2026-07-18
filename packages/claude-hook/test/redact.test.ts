import assert from "node:assert/strict";
import { test } from "node:test";

import { redact } from "../src/lib/redact.js";

test("masks a PEM private key block", () => {
  const secret = [
    "-----BEGIN RSA PRIVATE KEY-----",
    "MIIEpAIBAAKCAQEA0Z3VS5JJcds3xfn/ygWyFUL4=",
    "-----END RSA PRIVATE KEY-----",
  ].join("\n");
  const out = redact(secret);
  assert.equal(out, "[REDACTED:private-key]");
});

test("masks provider API keys", () => {
  const cases: Array<[string, string]> = [
    ["sk-ant-api03-abcdefghijklmnopqrstuvwxyz012345", "anthropic-key"],
    ["sk-proj-abcdefghijklmnopqrstuvwxyz012345", "openai-key"],
    ["ghp_abcdefghijklmnopqrstuvwxyz0123456789", "github-token"],
    ["github_pat_abcdefghijklmnopqrstuvwxyz0123456789", "github-token"],
    // Split literal so this fixture doesn't trip secret scanners; redact() sees the joined string.
    ["xoxb-" + "123456789012-abcdefghijklmnop", "slack-token"],
    ["AKIAIOSFODNN7EXAMPLE", "aws-access-key-id"],
    ["AIzaSyA1234567890abcdefghijklmnopqrstuvw", "google-api-key"],
  ];
  for (const [secret, type] of cases) {
    const out = redact(`token is ${secret} ok`);
    assert.ok(out.includes(`[REDACTED:${type}]`), `expected ${type} to be masked, got: ${out}`);
    assert.ok(!out.includes(secret), `raw secret leaked: ${secret}`);
  }
});

test("masks a JWT", () => {
  const jwt =
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U";
  const out = redact(`Authorization header: ${jwt}`);
  assert.ok(out.includes("[REDACTED:jwt]"));
  assert.ok(!out.includes(jwt));
});

test("masks bearer tokens while keeping the header key", () => {
  const out = redact('"authorization": "Bearer abcdef0123456789xyz"');
  assert.ok(out.includes("[REDACTED:auth-header]"));
  assert.ok(out.toLowerCase().includes("authorization"));
  assert.ok(!out.includes("abcdef0123456789xyz"));
});

test("masks credentials embedded in a URL but keeps host", () => {
  const out = redact("postgres://admin:sup3rs3cret@db.internal:5432/app");
  assert.ok(out.includes("[REDACTED:url-credentials]"));
  assert.ok(out.includes("admin"));
  assert.ok(out.includes("db.internal:5432/app"));
  assert.ok(!out.includes("sup3rs3cret"));
});

test("masks generic .env-style secret assignments", () => {
  const out = redact('DATABASE_PASSWORD="hunter2hunter2"\nMY_API_KEY=abcdef123456');
  assert.ok(!out.includes("hunter2hunter2"));
  assert.ok(!out.includes("abcdef123456"));
  assert.equal((out.match(/\[REDACTED:secret-assignment\]/g) ?? []).length, 2);
});

test("masks secret assignments with JSON-escaped quotes", () => {
  // As secrets actually appear inside a JSONL string value.
  const line = JSON.stringify({ text: 'export DATABASE_PASSWORD="hunter2hunter2" now' });
  const out = redact(line);
  assert.ok(!out.includes("hunter2hunter2"), `secret leaked: ${out}`);
  assert.ok(out.includes("[REDACTED:secret-assignment]"));
  // Output is still valid JSON.
  assert.doesNotThrow(() => JSON.parse(out));
});

test("masks the tool's own api key verbatim", () => {
  const out = redact("leaked mk_live_abcdef into logs", "mk_live_abcdef");
  assert.ok(out.includes("[REDACTED:memento-api-key]"));
  assert.ok(!out.includes("mk_live_abcdef"));
});

test("leaves ordinary text untouched", () => {
  const clean = "the quick brown fox refactored the parser at line 42";
  assert.equal(redact(clean), clean);
});

test("redacted JSONL stays parseable", () => {
  const line = JSON.stringify({ role: "user", text: "my key is sk-ant-api03-" + "a".repeat(30) });
  const out = redact(line);
  const parsed = JSON.parse(out);
  assert.equal(parsed.role, "user");
  assert.ok(parsed.text.includes("[REDACTED:anthropic-key]"));
});
