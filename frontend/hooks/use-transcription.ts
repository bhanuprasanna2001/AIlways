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

  const wsRef = useRef<WebSocket | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
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
  }

  /** Start a live transcription session. */
  const start = useCallback(async (vaultId: string) => {
    setError(null);
    setSegments([]);
    setClaims([]);
    setSessionId(null);
    setDuration(null);
    setElapsed(0);
    setStatus("connecting");

    try {
      // 1. Get one-time WS ticket
      const { ticket } = await apiFetch<{ ticket: string }>(
        "/api/auth/ws-ticket",
        { method: "POST" },
      );

      // 2. Get microphone access
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      mediaStreamRef.current = stream;

      // 3. Set up AudioContext + AudioWorklet for raw PCM
      const audioCtx = new AudioContext({ sampleRate: 16000 });
      audioCtxRef.current = audioCtx;

      await audioCtx.audioWorklet.addModule("/vad-processor.js");

      const source = audioCtx.createMediaStreamSource(stream);
      const workletNode = new AudioWorkletNode(audioCtx, "vad-processor");
      workletNodeRef.current = workletNode;
      source.connect(workletNode);
      workletNode.connect(audioCtx.destination);

      // 4. Open WebSocket — connect directly to backend (Next.js API routes
      //    don't support WebSocket upgrade, so we bypass the BFF for WS).
      const backendWsUrl =
        process.env.NEXT_PUBLIC_WS_URL
          ? `${process.env.NEXT_PUBLIC_WS_URL}/vaults/${vaultId}/transcribe/live?ticket=${ticket}&sample_rate=${audioCtx.sampleRate}`
          : `ws://localhost:8080/vaults/${vaultId}/transcribe/live?ticket=${ticket}&sample_rate=${audioCtx.sampleRate}`;

      const ws = new WebSocket(backendWsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        setStatus("recording");

        // Forward audio chunks from worklet to WS
        workletNode.port.onmessage = (event) => {
          if (ws.readyState === WebSocket.OPEN && event.data) {
            // event.data is a Float32Array from the worklet
            const float32 = event.data as Float32Array;
            // Convert to 16-bit PCM
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
    if (audioCtxRef.current) {
      audioCtxRef.current.close().catch(() => {});
      audioCtxRef.current = null;
    }

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
          ...prev,
          {
            id: msg.claim_id,
            text: msg.text,
            speaker: msg.speaker,
            verdict: "verifying",
            confidence: 0,
            explanation: "",
            evidence: [],
          },
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
    start,
    stop,
    reset,
  };
}
