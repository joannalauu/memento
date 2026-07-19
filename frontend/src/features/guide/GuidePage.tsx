/**
 * Guide: a static docs page explaining how to use Memento. It has an anchor bar
 * at the top that jumps to in-page sections. Because the app shell gives each
 * page its own scroll container (see AppShell — the Outlet wrapper is
 * overflow-hidden), plain `#hash` links don't scroll reliably; instead the
 * anchors call scrollIntoView on the section element, and each section carries
 * `scroll-mt` so its heading isn't flush against the top edge.
 */
import { useState } from "react"
import { Link } from "react-router-dom"
import { Check, Copy, GitBranch, KeyRound, Plug, Webhook } from "lucide-react"

import { cn } from "@/lib/utils"
import { useActiveOrg } from "@/components/app-shell/org-context"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"

const MCP_COMMAND = `claude mcp add --transport http memento https://api.memento-ai.ca/mcp \\
  --header "Authorization: Bearer YOUR_API_KEY"`

const HOOK_COMMAND = `npx -y @memento-ai/hook install --api-key YOUR_API_KEY`

export function GuidePage() {
  const { role } = useActiveOrg()
  const isAdmin = role === "admin"
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-4xl flex-col gap-6 p-6">
        <div>
          <h1 className="text-xl font-semibold">Guide</h1>
          <p className="text-muted-foreground text-sm">
            Everything you need to get Memento wired into your workflow.
          </p>
        </div>

        <McpSection />
        <ClaudeHookSection />
        {isAdmin && <GithubSection />}
      </div>
    </div>
  )
}

function McpSection() {
  return (
    <section id="connect-mcp" className="scroll-mt-6">
      <Card>
        <CardHeader>
          <div className="flex items-center gap-3">
            <div className="bg-primary/10 text-primary grid size-10 place-items-center rounded-lg">
              <Plug className="size-5" />
            </div>
            <div className="space-y-1">
              <CardTitle>Connect to the MCP</CardTitle>
              <CardDescription>
                Give Claude Code (or any MCP client) access to your Memento
                knowledge graph.
              </CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <p className="text-sm">
            Run this in your terminal to register the Memento MCP server.
            Replace <code className="text-foreground font-mono">YOUR_API_KEY</code>{" "}
            with a key from your{" "}
            <Link to="/home" className="text-primary underline underline-offset-2">
              home page
            </Link>
            .
          </p>

          <CodeBlock code={MCP_COMMAND} />

          <div className="text-muted-foreground flex items-start gap-2 text-sm">
            <KeyRound className="mt-0.5 size-4 shrink-0" />
            <p>
              Don't have an API key yet? Create one from the{" "}
              <span className="text-foreground font-medium">API keys</span>{" "}
              section on your home page, then paste it into the command above.
            </p>
          </div>
        </CardContent>
      </Card>
    </section>
  )
}

function ClaudeHookSection() {
  return (
    <section id="install-claude-hook" className="scroll-mt-6">
      <Card>
        <CardHeader>
          <div className="flex items-center gap-3">
            <div className="bg-primary/10 text-primary grid size-10 place-items-center rounded-lg">
              <Webhook className="size-5" />
            </div>
            <div className="space-y-1">
              <CardTitle>Install Claude hook</CardTitle>
              <CardDescription>
                Automatically capture your Claude Code sessions into your
                knowledge graph when they end.
              </CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <p className="text-sm">
            The Memento hook plugs into Claude Code's{" "}
            <code className="text-foreground font-mono">SessionEnd</code> event.
            Once installed, every session you run in a project is distilled and
            added to your graph automatically — no manual export needed.
          </p>

          <p className="text-sm">
            Run this from the root of the repository you want to capture. Replace{" "}
            <code className="text-foreground font-mono">YOUR_API_KEY</code> with a
            key from your{" "}
            <Link
              to="/home"
              className="text-primary underline underline-offset-2"
            >
              home page
            </Link>
            .
          </p>

          <CodeBlock code={HOOK_COMMAND} />

          <p className="text-sm">
            This writes the hook into{" "}
            <code className="text-foreground font-mono">
              ./.claude/settings.json
            </code>{" "}
            and saves your API key to{" "}
            <code className="text-foreground font-mono">
              ~/.claude/memento-hook/config.json
            </code>{" "}
            (never committed). To remove it later, run{" "}
            <code className="text-foreground font-mono">
              npx @memento-ai/hook uninstall
            </code>
            .
          </p>

          <div className="text-muted-foreground flex items-start gap-2 text-sm">
            <KeyRound className="mt-0.5 size-4 shrink-0" />
            <p>
              You'll need Node.js (which provides{" "}
              <code className="text-foreground font-mono">npx</code>) installed.
              The same API key you use for the MCP works here.
            </p>
          </div>
        </CardContent>
      </Card>
    </section>
  )
}

function GithubSection() {
  return (
    <section id="sync-repositories" className="scroll-mt-6">
      <Card>
        <CardHeader>
          <div className="flex items-center gap-3">
            <div className="bg-primary/10 text-primary grid size-10 place-items-center rounded-lg">
              <GitBranch className="size-5" />
            </div>
            <div className="space-y-1">
              <CardTitle>Sync your repositories</CardTitle>
              <CardDescription>
                Automatically distill merged pull requests into your knowledge
                graph.
              </CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <p className="text-sm">
            Memento connects to GitHub through a GitHub App. Once it's installed
            on your repositories, every pull request that gets merged is
            automatically captured — the transcript and PR context are turned
            into structured decision records and added to your graph. No YAML,
            no workflow files, no CI config to maintain.
          </p>

          <ol className="flex flex-col gap-3 text-sm">
            <Step n={1}>
              Open the{" "}
              <Link
                to="/admin"
                className="text-primary underline underline-offset-2"
              >
                admin dashboard
              </Link>{" "}
              and find the <span className="font-medium">GitHub</span> card.
              (Connecting GitHub is an admin-only action.)
            </Step>
            <Step n={2}>
              Click <span className="font-medium">Connect GitHub</span>. You'll
              be taken to GitHub to install the Memento App.
            </Step>
            <Step n={3}>
              Choose which repositories Memento can access, then approve the
              installation. GitHub sends you back to Memento and your repos sync
              automatically.
            </Step>
            <Step n={4}>
              That's it. Merge a pull request into a connected repo, and Memento
              picks it up from there.
            </Step>
          </ol>

          <div className="bg-muted/50 text-muted-foreground rounded-lg border p-3 text-sm">
            Need to add or remove repositories later? Use{" "}
            <span className="text-foreground font-medium">
              Manage repositories
            </span>{" "}
            on the GitHub card to update which repos Memento can see.
          </div>
        </CardContent>
      </Card>
    </section>
  )
}

function Step({ n, children }: { n: number; children: React.ReactNode }) {
  return (
    <li className="flex gap-3">
      <span className="bg-primary/10 text-primary grid size-6 shrink-0 place-items-center rounded-full text-xs font-semibold">
        {n}
      </span>
      <span className="pt-0.5">{children}</span>
    </li>
  )
}

function CodeBlock({ code }: { code: string }) {
  const [copied, setCopied] = useState(false)

  const onCopy = async () => {
    await navigator.clipboard.writeText(code)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="group relative">
      <pre className="bg-muted text-foreground overflow-x-auto rounded-lg border p-4 pr-12 font-mono text-xs leading-relaxed">
        <code>{code}</code>
      </pre>
      <Button
        variant="ghost"
        size="icon-sm"
        className="absolute top-2 right-2"
        onClick={onCopy}
        aria-label="Copy command"
      >
        {copied ? (
          <Check className={cn("size-4", "text-emerald-500")} />
        ) : (
          <Copy className="size-4" />
        )}
      </Button>
    </div>
  )
}
