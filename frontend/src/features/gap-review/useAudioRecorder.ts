/**
 * Microphone capture for a gap-chat voice answer. Records with MediaRecorder and
 * hands back a single `File` when recording stops — the caller POSTs it to the
 * gap-chat `/answer/audio` endpoint, which transcribes (Backboard/ElevenLabs)
 * *and* resolves the memory in one shot, so this hook owns only capture, not
 * transcription.
 *
 * This is the recorder core extracted from features/graph/useVoiceInput.ts; that
 * hook keeps its own transcribe-then-return flow for the graph ask bar. `toggle`
 * drives idle → recording → (stop) → idle, and the mic stream is always released
 * when recording stops, on the success path and on unmount alike.
 */
import { useCallback, useEffect, useRef, useState } from "react"

export type RecorderStatus = "idle" | "recording"

export interface UseAudioRecorderOptions {
  /** The captured audio, once recording stops with non-empty data. */
  onAudio: (file: File) => void
  /** A user-facing failure (permission denied, empty capture). */
  onError?: (message: string) => void
}

export interface UseAudioRecorderResult {
  status: RecorderStatus
  /** Start recording when idle, stop-and-emit when recording. */
  toggle: () => void
  /** True when the browser can't record audio at all. */
  unsupported: boolean
}

/** MediaRecorder MIME → file extension, so the backend can infer the format. */
function extForMime(mime: string): string {
  if (mime.includes("webm")) return "webm"
  if (mime.includes("mp4")) return "mp4"
  if (mime.includes("ogg")) return "ogg"
  if (mime.includes("wav")) return "wav"
  return "webm"
}

export function useAudioRecorder({
  onAudio,
  onError,
}: UseAudioRecorderOptions): UseAudioRecorderResult {
  const [status, setStatus] = useState<RecorderStatus>("idle")
  const recorderRef = useRef<MediaRecorder | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const chunksRef = useRef<Blob[]>([])
  // Latest callbacks via ref so `toggle` stays referentially stable.
  const cbRef = useRef({ onAudio, onError })
  cbRef.current = { onAudio, onError }

  const unsupported =
    typeof window === "undefined" ||
    typeof window.MediaRecorder === "undefined" ||
    !navigator.mediaDevices?.getUserMedia

  const releaseStream = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
  }, [])

  const start = useCallback(async () => {
    if (unsupported) return
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      cbRef.current.onError?.("Microphone access was denied")
      return
    }
    streamRef.current = stream
    chunksRef.current = []
    const recorder = new MediaRecorder(stream)
    recorderRef.current = recorder

    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunksRef.current.push(e.data)
    }
    recorder.onstop = () => {
      releaseStream()
      const type = recorder.mimeType || "audio/webm"
      const blob = new Blob(chunksRef.current, { type })
      chunksRef.current = []
      setStatus("idle")
      if (blob.size === 0) {
        cbRef.current.onError?.("No audio was captured")
        return
      }
      cbRef.current.onAudio(
        new File([blob], `answer.${extForMime(type)}`, { type }),
      )
    }

    recorder.start()
    setStatus("recording")
  }, [unsupported, releaseStream])

  const toggle = useCallback(() => {
    if (status === "recording") {
      recorderRef.current?.stop() // fires onstop → onAudio
    } else {
      void start()
    }
  }, [status, start])

  // Unmount: stop any recorder and free the mic.
  useEffect(
    () => () => {
      if (recorderRef.current?.state === "recording") recorderRef.current.stop()
      releaseStream()
    },
    [releaseStream],
  )

  return { status, toggle, unsupported }
}
