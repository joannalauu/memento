/**
 * A tiny shared signal so the gap-review dialog (mounted in the app shell) knows
 * a document was just uploaded and the backend is now enriching it + detecting
 * doc↔code gaps. The uploader (DocumentsSection) calls `notifyUpload()`; the
 * dialog reads `lastUploadAt` to open immediately with a "processing" spinner
 * while the questions are still being generated, and calls `clearUpload()` once
 * they arrive or the wait times out.
 *
 * State only — no persistence. A full reload clears it, which is fine: any
 * questions already generated show on their own via the open-chat poll.
 */
import { createContext, useContext, useMemo, useState, type ReactNode } from "react"

interface UploadSignalValue {
  /** Epoch ms of the most recent upload, or null if none is being awaited. */
  lastUploadAt: number | null
  notifyUpload: () => void
  clearUpload: () => void
}

const UploadSignalContext = createContext<UploadSignalValue | null>(null)

export function UploadSignalProvider({ children }: { children: ReactNode }) {
  const [lastUploadAt, setLastUploadAt] = useState<number | null>(null)
  const value = useMemo<UploadSignalValue>(
    () => ({
      lastUploadAt,
      notifyUpload: () => setLastUploadAt(Date.now()),
      clearUpload: () => setLastUploadAt(null),
    }),
    [lastUploadAt],
  )
  return <UploadSignalContext value={value}>{children}</UploadSignalContext>
}

export function useUploadSignal(): UploadSignalValue {
  const ctx = useContext(UploadSignalContext)
  if (!ctx)
    throw new Error("useUploadSignal must be used within an UploadSignalProvider")
  return ctx
}
