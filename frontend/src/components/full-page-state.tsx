/**
 * Full-viewport loading and error states, shared by the auth gate and route
 * dispatchers.
 */
import { Loader2 } from "lucide-react"

export function FullPageSpinner() {
  return (
    <div className="bg-background text-muted-foreground grid h-screen place-items-center">
      <Loader2 className="size-6 animate-spin" aria-label="Loading" />
    </div>
  )
}

export function FullPageError({ message }: { message?: string }) {
  return (
    <div className="bg-background grid h-screen place-items-center p-8">
      <p className="text-destructive max-w-md text-center text-sm">
        {message ?? "Something went wrong. Please try again."}
      </p>
    </div>
  )
}
