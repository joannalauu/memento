/**
 * Repo scoping for the graph. Every repo is selected by default (the graph is
 * org-wide); the user can pare that down to a subset. Selection is tracked as
 * the set of *deselected* repos so "all selected" is the empty-set default and
 * needs no async initialization against the repo list.
 */
import { ChevronDown } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

interface RepoFilterProps {
  // All repos in the org, full `owner/name`, in a stable order.
  repos: string[];
  // Repos the user has turned OFF. Empty = all selected (default).
  deselected: Set<string>;
  onToggle: (repo: string) => void;
  onSelectAll: () => void;
  onClear: () => void;
}

export function RepoFilter({
  repos,
  deselected,
  onToggle,
  onSelectAll,
  onClear,
}: RepoFilterProps) {
  // Nothing to scope by until the org's repos load.
  if (repos.length === 0) return null;

  const selectedCount = repos.length - deselected.size;
  const label =
    deselected.size === 0
      ? "All repos"
      : `${selectedCount} of ${repos.length} repos`;

  return (
    <div className="absolute top-4 left-4 z-10">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="sm" className="bg-card/80 backdrop-blur">
            {label}
            <ChevronDown data-icon="inline-end" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-64">
          <div className="flex items-center justify-between px-2 py-1.5">
            <DropdownMenuLabel className="p-0">Repositories</DropdownMenuLabel>
            <div className="flex gap-1">
              <Button
                variant="ghost"
                size="xs"
                onClick={onSelectAll}
                disabled={deselected.size === 0}
              >
                All
              </Button>
              <Button
                variant="ghost"
                size="xs"
                onClick={onClear}
                disabled={selectedCount === 0}
              >
                Clear
              </Button>
            </div>
          </div>
          <DropdownMenuSeparator />
          <div className="max-h-72 overflow-y-auto">
            {repos.map((repo) => (
              <DropdownMenuCheckboxItem
                key={repo}
                checked={!deselected.has(repo)}
                // Keep the menu open so several repos can be toggled at once.
                onSelect={(e) => e.preventDefault()}
                onCheckedChange={() => onToggle(repo)}
              >
                <span className="truncate">{repo}</span>
              </DropdownMenuCheckboxItem>
            ))}
          </div>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
