"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import { apiFetch } from "@/lib/api";
import type { Citation } from "@/lib/types";

// ---------------------------------------------------------------------------
// Types — live transcription state
// ---------------------------------------------------------------------------

export type LiveSegment = {
  text: string;
  speaker: number;
  start: number;
  end: number;
  confidence: number;
  is_final: boolean;
};

export type LiveClaim = {
  id: string;
  text: string;
  speaker: number;
  verdict: "verifying" | "supported" | "contradicted" | "unverifiable";
  confidence: number;
  explanation: string;
  evidence: Citation[];
};

export type TranscriptionStatus =
  | "idle"
  | "connecting"
  | "recording"
  | "stopping"
  | "error";

/** Audio capture mode — mic-only or meeting (mic + system audio). */
export type AudioMode = "mic" | "meeting";

// ---------------------------------------------------------------------------
// WS message types (mirrors backend schema literals)
// ---------------------------------------------------------------------------

type WSTranscript = {
  type: "transcript";
  text: string;
  speaker: number;
  start: number;
  end: number;
  confidence: number;
  is_final: boolean;
};

type WSClaimDetected = {
  type: "claim_detected";
  claim_id: string;
  text: string;
  speaker: number;
  status: "verifying";
};

type WSClaimVerified = {
  type: "claim_verified";
  claim_id: string;
  claim_text: string;
  verdict: "supported" | "contradicted" | "unverifiable";
  confidence: number;
  explanation: string;
  evidence: Citation[];
};

type WSSessionStarted = {
  type: "session_started";
  session_id: string;
};

type WSSessionEnded = {
  type: "session_ended";
  session_id: string;
  duration_seconds: number;
};

type WSError = {
  type: "error";
  message: string;
};

type WSMessage =
  | WSTranscript
  | WSClaimDetected
  | WSClaimVerified
  | WSSessionStarted
  | WSSessionEnded
  | WSError;

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useTranscription() {
  const [status, setStatus] = useState<TranscriptionStatus>("idle");
  const [segments, setSegments] = useState<LiveSegment[]>([]);
  const [claims, setClaims] = useState<LiveClaim[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [duration, setDuration] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  /** True when meeting-mode system audio is active. */
  const [systemAudioActive, setSystemAudioActive] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const systemStreamRef = useRef<MediaStream | null>(null);
  const workletNodeRef = useRef<AudioWorkletNode | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Elapsed timer
  useEffect(() => {
    if (status === "recording") {
      setElapsed(0);
      timerRef.current = setInterval(() => {
        setElapsed((prev) => prev + 1);
      }, 1000);
    } else {
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [status]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      _cleanup();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** Internal cleanup — close WS, stop media, close audio context. */
  function _cleanup() {
    if (wsRef.current) {
      try {
        wsRef.current.close();
      } catch {
        /* ignore */
      }
      wsRef.current = null;
    }
    if (workletNodeRef.current) {
      workletNodeRef.current.disconnect();
      workletNodeRef.current = null;
    }
    if (audioCtxRef.current) {
      audioCtxRef.current.close().catch(() => {});
      audioCtxRef.current = null;
    }
    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach((t) => t.stop());
      mediaStreamRef.current = null;
    }
    if (systemStreamRef.current) {
      systemStreamRef.current.getTracks().forEach((t) => t.stop());
      systemStreamRef.current = null;
    }
    setSystemAudioActive(false);
  }

  /** Start a live transcription session.
   *
   * @param vaultId  Vault to verify claims against.
   * @param audioMode  ``"mic"`` for microphone only (default),
   *                   ``"meeting"`` to also capture system/tab audio.
   */
  const start = useCallback(async (vaultId: string, audioMode: AudioMode = "mic") => {
    setError(null);
    setSegments([]);
    setClaims([]);
    setSessionId(null);
    setDuration(null);
    setElapsed(0);
    setSystemAudioActive(false);
    setStatus("connecting");

    try {
      // 1. Get one-time WS ticket
      const { ticket } = await apiFetch<{ ticket: string }>(
        "/api/auth/ws-ticket",
        { method: "POST" },
      );

      // 2. In meeting mode, capture system / tab audio FIRST so we
      //    can decide whether to keep echo cancellation on the mic.
      //    If system audio is available → 2-channel multichannel mode.
      //    If not (e.g. shared entire screen on macOS) → fall back to
      //    mic-only with echo cancellation OFF so the mic picks up
      //    meeting audio from speakers for better diarization.
      let systemStream: MediaStream | null = null;
      let channels = 1;
      let micEchoCancellation = true;

      if (audioMode === "meeting") {
        try {
          // getDisplayMedia requires video; we discard the video track.
          // Audio processing is disabled so we get the raw meeting audio.
          const displayStream = await navigator.mediaDevices.getDisplayMedia({
            audio: {
              echoCancellation: false,
              noiseSuppression: false,
              autoGainControl: false,
            },
            video: true,
          });

          // Discard video track — we only need audio
          displayStream.getVideoTracks().forEach((t) => t.stop());

          if (displayStream.getAudioTracks().length > 0) {
            systemStream = displayStream;
            systemStreamRef.current = systemStream;
            channels = 2;
            setSystemAudioActive(true);

            // If the user stops sharing, channel 1 goes silent but
            // the session continues with mic-only audio.
            displayStream.getAudioTracks()[0].addEventListener("ended", () => {
              setSystemAudioActive(false);
            });
          } else {
            // Entire screen / window share — no audio track available.
            // Disable echo cancellation so the mic picks up meeting
            // audio through speakers for better speaker diarization.
            micEchoCancellation = false;
            setError(
              "No audio in the shared source. " +
              "Tip: share a Chrome browser tab to capture meeting audio directly. " +
              "Continuing with microphone only.",
            );
          }
        } catch {
          // User denied screen share or browser doesn't support it.
          // Fall back to mic-only silently — not a fatal error.
        }
      }

      // 3. Get microphone access — constraints depend on whether
      //    system audio was captured.
      const micStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: micEchoCancellation,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      mediaStreamRef.current = micStream;

      // 4. Set up AudioContext + AudioWorklet for raw PCM
      const audioCtx = new AudioContext({ sampleRate: 16000 });
      audioCtxRef.current = audioCtx;

      // Ensure context is running — the original button-click gesture
      // may have expired after the preceding awaits (ticket fetch,
      // getUserMedia / getDisplayMedia).  Chrome's autoplay policy can
      // leave a new AudioContext in "suspended" state when no recent
      // user interaction is detected.
      if (audioCtx.state === "suspended") {
        await audioCtx.resume();
      }

      await audioCtx.audioWorklet.addModule("/vad-processor.js");

      const micSource = audioCtx.createMediaStreamSource(micStream);
      let workletNode: AudioWorkletNode;

      if (systemStream) {
        // Meeting mode: merge mic (ch 0) + system (ch 1) into stereo
        const sysSource = audioCtx.createMediaStreamSource(systemStream);
        const merger = audioCtx.createChannelMerger(2);
        micSource.connect(merger, 0, 0); // mic  → left channel
        sysSource.connect(merger, 0, 1); // sys  → right channel

        workletNode = new AudioWorkletNode(audioCtx, "vad-processor", {
          channelCount: 2,
          channelCountMode: "explicit",
        });
        merger.connect(workletNode);
      } else {
        // Mic-only mode — explicit mono to prevent the Web Audio API
        // from upmixing the single-channel mic into stereo.  The
        // AudioWorkletNode default is channelCount:2 / mode:"max",
        // which silently doubles every sample and sends garbled
        // interleaved stereo to Deepgram on a channels=1 stream.
        channels = 1;
        workletNode = new AudioWorkletNode(audioCtx, "vad-processor", {
          channelCount: 1,
          channelCountMode: "explicit",
        });
        micSource.connect(workletNode);
      }

      workletNodeRef.current = workletNode;
      workletNode.connect(audioCtx.destination);

      // 5. Open WebSocket — connect directly to backend (Next.js API routes
      //    don't support WebSocket upgrade, so we bypass the BFF for WS).
      const backendWsUrl =
        process.env.NEXT_PUBLIC_WS_URL
          ? `${process.env.NEXT_PUBLIC_WS_URL}/vaults/${vaultId}/transcribe/live?ticket=${ticket}&sample_rate=${audioCtx.sampleRate}&channels=${channels}`
          : `ws://localhost:8080/vaults/${vaultId}/transcribe/live?ticket=${ticket}&sample_rate=${audioCtx.sampleRate}&channels=${channels}`;

      const ws = new WebSocket(backendWsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        setStatus("recording");

        // Forward audio chunks from worklet to WS
        workletNode.port.onmessage = (event) => {
          if (ws.readyState === WebSocket.OPEN && event.data) {
            // event.data is a Float32Array from the worklet
            // (mono: 4096 samples, stereo: 8192 interleaved samples)
            const float32 = event.data as Float32Array;
            // Convert to 16-bit PCM (interleaved format preserved)
            const pcm16 = new Int16Array(float32.length);
            for (let i = 0; i < float32.length; i++) {
              const s = Math.max(-1, Math.min(1, float32[i]));
              pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
            }
            ws.send(pcm16.buffer);
          }
        };
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data) as WSMessage;
          _handleMessage(msg);
        } catch {
          /* ignore malformed messages */
        }
      };

      ws.onerror = () => {
        setError("WebSocket connection error");
        setStatus("error");
        _cleanup();
      };

      ws.onclose = (event) => {
        // Accept 1000 (normal), 1005 (no status), and 1006 (abnormal)
        // as non-error codes.  Uvicorn may send 1006 if the TCP
        // connection drops before the close handshake completes.
        const normalCodes = [1000, 1005, 1006];
        if (!normalCodes.includes(event.code)) {
          setStatus((prev) => {
            // Don't overwrite idle — session_ended already moved us there
            if (prev === "idle") return prev;
            setError(event.reason || "Connection closed unexpectedly");
            return "error";
          });
        }
        _cleanup();
      };
    } catch (err) {
      const msg =
        err instanceof Error ? err.message : "Failed to start transcription";
      setError(msg);
      setStatus("error");
      _cleanup();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** Stop the live transcription. */
  const stop = useCallback(() => {
    setStatus("stopping");

    // Send stop command
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "stop" }));
    }

    // Stop audio immediately
    if (workletNodeRef.current) {
      workletNodeRef.current.disconnect();
      workletNodeRef.current = null;
    }
    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach((t) => t.stop());
      mediaStreamRef.current = null;
    }
    if (systemStreamRef.current) {
      systemStreamRef.current.getTracks().forEach((t) => t.stop());
      systemStreamRef.current = null;
    }
    if (audioCtxRef.current) {
      audioCtxRef.current.close().catch(() => {});
      audioCtxRef.current = null;
    }
    setSystemAudioActive(false);

    // WS will close after backend drains
  }, []);

  /** Reset state for a new session. */
  const reset = useCallback(() => {
    _cleanup();
    setStatus("idle");
    setSegments([]);
    setClaims([]);
    setSessionId(null);
    setDuration(null);
    setError(null);
    setElapsed(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** Handle incoming WS messages. */
  function _handleMessage(msg: WSMessage) {
    switch (msg.type) {
      case "transcript":
        setSegments((prev) => {
          // Replace last segment if it was interim (not final)
          if (prev.length > 0 && !prev[prev.length - 1].is_final) {
            return [...prev.slice(0, -1), msg];
          }
          return [...prev, msg];
        });
        break;

      case "claim_detected":
        setClaims((prev) => [
          {
            id: msg.claim_id,
            text: msg.text,
            speaker: msg.speaker,
            verdict: "verifying",
            confidence: 0,
            explanation: "",
            evidence: [],
          },
          ...prev,
        ]);
        break;

      case "claim_verified":
        setClaims((prev) =>
          prev.map((c) =>
            c.id === msg.claim_id
              ? {
                  ...c,
                  verdict: msg.verdict,
                  confidence: msg.confidence,
                  explanation: msg.explanation,
                  evidence: msg.evidence,
                }
              : c,
          ),
        );
        break;

      case "session_started":
        setSessionId(msg.session_id);
        break;

      case "session_ended":
        setDuration(msg.duration_seconds);
        setStatus("idle");
        break;

      case "error":
        setError(msg.message);
        setStatus("error");
        break;
    }
  }

  return {
    status,
    segments,
    claims,
    sessionId,
    duration,
    error,
    elapsed,
    systemAudioActive,
    start,
    stop,
    reset,
  };
}
