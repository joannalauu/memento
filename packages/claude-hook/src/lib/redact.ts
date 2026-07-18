/**
 * Best-effort secret scrubbing for a raw transcript. This is a defence-in-depth
 * text filter, NOT a guarantee — the server should treat ingested transcripts as
 * potentially sensitive regardless. Replacements are short, quote-free tokens so
 * that JSON string values in the JSONL stay parseable after substitution.
 */

interface Rule {
  type: string;
  pattern: RegExp;
  /** Replacement; use `$<name>` groups to preserve surrounding structure. */
  replace: string;
}

// Ordered: structural/multiline and highly-specific formats first, generic last.
const RULES: Rule[] = [
  {
    type: "private-key",
    pattern:
      /-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----/g,
    replace: "[REDACTED:private-key]",
  },
  {
    type: "anthropic-key",
    pattern: /sk-ant-[A-Za-z0-9_-]{20,}/g,
    replace: "[REDACTED:anthropic-key]",
  },
  {
    type: "openai-key",
    pattern: /sk-(?:proj-)?[A-Za-z0-9_-]{20,}/g,
    replace: "[REDACTED:openai-key]",
  },
  {
    type: "github-token",
    pattern: /gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}/g,
    replace: "[REDACTED:github-token]",
  },
  {
    type: "slack-token",
    pattern: /xox[baprs]-[A-Za-z0-9-]{10,}/g,
    replace: "[REDACTED:slack-token]",
  },
  {
    type: "aws-access-key-id",
    pattern: /\b(?:AKIA|ASIA)[0-9A-Z]{16}\b/g,
    replace: "[REDACTED:aws-access-key-id]",
  },
  {
    type: "google-api-key",
    pattern: /AIza[0-9A-Za-z_-]{35}/g,
    replace: "[REDACTED:google-api-key]",
  },
  {
    type: "jwt",
    pattern: /eyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}/g,
    replace: "[REDACTED:jwt]",
  },
  {
    // `Authorization: Bearer <token>` / `Authorization: Basic <token>`
    type: "auth-header",
    pattern: /(?<prefix>authorization"?\s*[:=]\s*"?(?:bearer|basic)\s+)[A-Za-z0-9._~+/=-]{8,}/gi,
    replace: "$<prefix>[REDACTED:auth-header]",
  },
  {
    // Credentials embedded in a URL: scheme://user:pass@host
    type: "url-credentials",
    pattern: /(?<scheme>[a-z][a-z0-9+.-]*:\/\/)(?<user>[^\s:@/]+):[^\s@/]+@/gi,
    replace: "$<scheme>$<user>:[REDACTED:url-credentials]@",
  },
  {
    // Generic `.env`-style assignment. The optional opening quote (plain or a
    // JSON-escaped `\"`) is kept in the key group so the output stays balanced;
    // the value stops at whitespace, a quote, a backslash, or a comma.
    type: "secret-assignment",
    pattern:
      /(?<key>\b(?:[A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|PASSPHRASE|API[_-]?KEY|ACCESS[_-]?KEY|PRIVATE[_-]?KEY)[A-Z0-9_]*)\s*[:=]\s*(?:\\?["'])?)(?<val>[^\s"'\\,]{6,})/gi,
    replace: "$<key>[REDACTED:secret-assignment]",
  },
];

/** Escape a literal string for safe use inside a RegExp. */
function escapeRegExp(literal: string): string {
  return literal.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Scrub secrets from `text`. If `ownApiKey` is supplied, any verbatim occurrence
 * of it is masked too (defensive — it should never appear in a transcript).
 */
export function redact(text: string, ownApiKey?: string): string {
  let out = text;
  for (const rule of RULES) {
    out = out.replace(rule.pattern, rule.replace);
  }
  if (ownApiKey && ownApiKey.length >= 6) {
    out = out.replace(new RegExp(escapeRegExp(ownApiKey), "g"), "[REDACTED:memento-api-key]");
  }
  return out;
}
