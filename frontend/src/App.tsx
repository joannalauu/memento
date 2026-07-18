import { useState } from "react"
import { Button } from "@/components/ui/button"

function App() {
  const [count, setCount] = useState(0)

  return (
    <div className="flex min-h-svh flex-col items-center justify-center gap-6 bg-background text-foreground">
      <h1 className="text-4xl font-bold tracking-tight">Vite + React + Tailwind + shadcn</h1>
      <p className="text-muted-foreground">Edit <code className="rounded bg-muted px-1.5 py-0.5">src/App.tsx</code> and save to test HMR.</p>
      <Button onClick={() => setCount((c) => c + 1)}>Count is {count}</Button>
    </div>
  )
}

export default App
