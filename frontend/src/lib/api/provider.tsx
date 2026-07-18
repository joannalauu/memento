/**
 * App-level API provider: wires the QueryClient and an optional 401 handler.
 *
 * Wrap the app once (see main.tsx). Pass `onUnauthorized` to redirect through
 * login when any request 401s — defaults to the Auth0 login flow.
 */
import { useEffect, useRef, type ReactNode } from "react"
import { QueryClientProvider } from "@tanstack/react-query"
import { ReactQueryDevtools } from "@tanstack/react-query-devtools"

import { login } from "./auth"
import { setUnauthorizedHandler } from "./http"
import { createQueryClient } from "./query-client"

interface ApiProviderProps {
  children: ReactNode
  /** Called when any request 401s. Defaults to redirecting through login. */
  onUnauthorized?: () => void
  /** Show the React Query devtools panel (dev only by default). */
  devtools?: boolean
}

export function ApiProvider({
  children,
  onUnauthorized = login,
  devtools = import.meta.env.DEV,
}: ApiProviderProps) {
  // One QueryClient for the app's lifetime.
  const clientRef = useRef(createQueryClient())

  useEffect(() => {
    setUnauthorizedHandler(onUnauthorized)
    return () => setUnauthorizedHandler(null)
  }, [onUnauthorized])

  return (
    <QueryClientProvider client={clientRef.current}>
      {children}
      {devtools && <ReactQueryDevtools initialIsOpen={false} />}
    </QueryClientProvider>
  )
}
