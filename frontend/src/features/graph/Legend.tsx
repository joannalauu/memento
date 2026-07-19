import { currentTheme, NODE_TYPE_LABELS, NODE_TYPE_ORDER } from "./palette";

// themeKey re-reads colors after a light/dark toggle.
export function Legend({ themeKey }: { themeKey: number }) {
  void themeKey;
  const theme = currentTheme();
  return (
    <div className="bg-card/80 absolute bottom-4 left-4 rounded-lg border p-3 text-xs backdrop-blur">
      <ul className="space-y-1.5">
        {NODE_TYPE_ORDER.map((t) => (
          <li key={t} className="flex items-center gap-2">
            <span
              className="inline-block size-3 rounded-full"
              style={{ backgroundColor: theme.node[t] }}
            />
            <span>{NODE_TYPE_LABELS[t]}</span>
          </li>
        ))}
        <li className="flex items-center gap-2 pt-1">
          <svg width="16" height="6" aria-hidden>
            <line
              x1="0"
              y1="3"
              x2="16"
              y2="3"
              stroke={theme.linkStrong}
              strokeWidth="1.5"
              strokeDasharray="4 3"
            />
          </svg>
          <span className="text-muted-foreground">superseded by</span>
        </li>
        {/* Live-traversal highlight legend (T4.6). */}
        <li className="flex items-center gap-2 pt-1">
          <span
            className="inline-block size-3 rounded-full"
            style={{ boxShadow: `0 0 0 2px ${theme.highlightEntry}` }}
          />
          <span className="text-muted-foreground">entry (landing)</span>
        </li>
        <li className="flex items-center gap-2">
          <span
            className="inline-block size-3 rounded-full"
            style={{ boxShadow: `0 0 0 2px ${theme.highlightHop}` }}
          />
          <span className="text-muted-foreground">hop</span>
        </li>
      </ul>
    </div>
  );
}
