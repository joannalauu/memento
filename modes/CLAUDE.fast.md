# Fast Mode

Operate autonomously. Minimize confirmation prompts and move fast.

- Proceed with file edits, feature scaffolding, and standard CLI commands without asking for confirmation.
- Skip explanatory preamble — make changes and report results concisely.
- Only pause for genuinely irreversible actions (dropping features, dropping database tables, force-push).

## Testing

Write tests only if the user asks or the feature warrants it. Run with `uv run pytest`.

## Before Finishing

```bash
hackplate run       # verify server starts clean, then Ctrl+C
hackplate precommit # lint and format
```

Skip `uv run pytest` unless tests exist or were added as part of the task.
