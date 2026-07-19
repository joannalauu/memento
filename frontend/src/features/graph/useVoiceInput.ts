/**
 * Voice input for the ask bar: record a spoken question with MediaRecorder,
 * upload it to `POST /orgs/{orgId}/graph/transcribe`, and hand the transcript
 * back for the caller to drop into the input. Transcription runs server-side
 * through Backboard/ElevenLabs — the same STT path the gap-chat voice answers
 * use — so the browser only captures and uploads audio.
 *
 * `toggle` drives the whole cycle: idle → recording → (stop) → transcribing →
 * idle. The mic stream is always released when recording stops, on the success
 * path and on unmount alike.
 */
import { useCallback, useEffect, useRef, useState } from "react"

import { graphApi } from "@/lib/api"

export type VoiceStatus = "idle" | "recording" | "transcribing"

export interface UseVoiceInputOptions {
  orgId: string | null
  /** The transcript, once STT returns non-empty text. */
  onTranscript: (text: string) => void
  /** A user-facing failure (permission denied, no speech, upload error). */
  onError?: (message: string) => void
}

export interface UseVoiceInputResult {
  status: VoiceStatus
  /** Start recording when idle, stop-and-transcribe when recording. */
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

export function useVoiceInput({
  orgId,
  onTranscript,
  onError,
}: UseVoiceInputOptions): UseVoiceInputResult {
  const [status, setStatus] = useState<VoiceStatus>("idle")
  const recorderRef = useRef<MediaRecorder | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const chunksRef = useRef<Blob[]>([])
  // Latest callbacks via ref so `toggle` stays referentially stable.
  const cbRef = useRef({ onTranscript, onError })
  cbRef.current = { onTranscript, onError }

  const unsupported =
    typeof window === "undefined" ||
    typeof window.MediaRecorder === "undefined" ||
    !navigator.mediaDevices?.getUserMedia

  const releaseStream = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
  }, [])

  const start = useCallback(async () => {
    if (!orgId || unsupported) return
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
    recorder.onstop = async () => {
      releaseStream()
      const type = recorder.mimeType || "audio/webm"
      const blob = new Blob(chunksRef.current, { type })
      chunksRef.current = []
      if (blob.size === 0) {
        setStatus("idle")
        return
      }
      setStatus("transcribing")
      try {
        const { transcript } = await graphApi.transcribe(
          orgId,
          blob,
          `question.${extForMime(type)}`,
        )
        if (transcript.trim()) cbRef.current.onTranscript(transcript.trim())
      } catch (err) {
        cbRef.current.onError?.(
          err instanceof Error ? err.message : "Could not transcribe audio",
        )
      } finally {
        setStatus("idle")
      }
    }

    recorder.start()
    setStatus("recording")
  }, [orgId, unsupported, releaseStream])

  const toggle = useCallback(() => {
    if (status === "recording") {
      recorderRef.current?.stop() // fires onstop → transcribe
    } else if (status === "idle") {
      void start()
    }
    // transcribing: ignore — the upload is already in flight.
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
