import assert from "node:assert/strict";
import { test } from "node:test";

import { addHook, removeHook, type Settings } from "../src/lib/settings.js";
import { HOOK_COMMAND, HOOK_EVENT, PACKAGE_NAME } from "../src/constants.js";

function ourEntries(settings: Settings) {
  const groups = settings.hooks?.[HOOK_EVENT] ?? [];
  return groups
    .flatMap((g) => g.hooks ?? [])
    .filter((h) => typeof h.command === "string" && h.command.includes(PACKAGE_NAME));
}

test("addHook installs a SessionEnd command into empty settings", () => {
  const settings: Settings = {};
  const changed = addHook(settings);
  assert.equal(changed, true);
  const ours = ourEntries(settings);
  assert.equal(ours.length, 1);
  assert.equal(ours[0]!.command, HOOK_COMMAND);
});

test("addHook is idempotent — no duplicate on re-install", () => {
  const settings: Settings = {};
  addHook(settings);
  const changedAgain = addHook(settings);
  assert.equal(changedAgain, false);
  assert.equal(ourEntries(settings).length, 1);
});

test("addHook preserves unrelated settings and other hook events", () => {
  const settings: Settings = {
    model: "claude-opus-4-8",
    permissions: { allow: ["Bash(git*)"] },
    hooks: {
      PreToolUse: [{ hooks: [{ type: "command", command: "other-tool run" }] }],
      SessionEnd: [{ hooks: [{ type: "command", command: "someones-cleanup.sh" }] }],
    },
  };
  addHook(settings);
  assert.equal((settings as Record<string, unknown>).model, "claude-opus-4-8");
  assert.deepEqual(settings.hooks!.PreToolUse, [
    { hooks: [{ type: "command", command: "other-tool run" }] },
  ]);
  // Third-party SessionEnd entry survives alongside ours.
  const commands = settings.hooks!.SessionEnd!.flatMap((g) => g.hooks ?? []).map((h) => h.command);
  assert.ok(commands.includes("someones-cleanup.sh"));
  assert.ok(commands.includes(HOOK_COMMAND));
});

test("removeHook removes only our entry, leaving others intact", () => {
  const settings: Settings = {
    hooks: {
      SessionEnd: [
        { hooks: [{ type: "command", command: "someones-cleanup.sh" }] },
        { hooks: [{ type: "command", command: HOOK_COMMAND, timeout: 15 }] },
      ],
    },
  };
  const changed = removeHook(settings);
  assert.equal(changed, true);
  assert.equal(ourEntries(settings).length, 0);
  const commands = settings.hooks!.SessionEnd!.flatMap((g) => g.hooks ?? []).map((h) => h.command);
  assert.deepEqual(commands, ["someones-cleanup.sh"]);
});

test("removeHook prunes empty containers when we were the only hook", () => {
  const settings: Settings = { model: "x" };
  addHook(settings);
  const changed = removeHook(settings);
  assert.equal(changed, true);
  assert.equal(settings.hooks, undefined);
  assert.equal((settings as Record<string, unknown>).model, "x");
});

test("removeHook is a no-op when we were never installed", () => {
  const settings: Settings = { hooks: { Stop: [{ hooks: [{ type: "command", command: "x" }] }] } };
  const changed = removeHook(settings);
  assert.equal(changed, false);
  assert.deepEqual(settings.hooks!.Stop, [{ hooks: [{ type: "command", command: "x" }] }]);
});
