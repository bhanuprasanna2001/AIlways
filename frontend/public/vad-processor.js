/**
 * AudioWorklet processor for live transcription.
 *
 * Runs on the audio rendering thread and forwards raw PCM samples
 * to the main thread via postMessage. The main thread then sends
 * these samples over the WebSocket to the backend.
 *
 * Supports mono (mic-only) and stereo (mic + system audio) modes.
 * Channel count is detected automatically from the first process()
 * call. In stereo mode, samples are interleaved [L, R, L, R, …]
 * matching DeepGram's expected multichannel linear16 format.
 *
 * Audio is buffered to ~256 ms chunks (4096 frames at 16 kHz)
 * to reduce WebSocket message rate from ~125/s to ~4/s.  This
 * dramatically lowers event-loop overhead on both browser and
 * backend while keeping latency imperceptible for live use.
 */

const FRAMES_PER_BUFFER = 4096; // frames per channel (~256 ms at 16 kHz)

class VadProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._channels = 0; // detected on first process() call
    this._buffer = null;
    this._frameOffset = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0] || input[0].length === 0) {
      return true;
    }

    const channels = input.length; // 1 = mono, 2 = stereo
    const frameCount = input[0].length; // typically 128

    // (Re-)initialise buffer when channel count changes or on first call
    if (this._channels !== channels) {
      this._channels = channels;
      this._buffer = new Float32Array(FRAMES_PER_BUFFER * channels);
      this._frameOffset = 0;
    }

    let srcFrame = 0;

    while (srcFrame < frameCount) {
      const remainingFrames = FRAMES_PER_BUFFER - this._frameOffset;
      const framesToCopy = Math.min(remainingFrames, frameCount - srcFrame);

      if (channels === 1) {
        // Mono — direct copy (same as original behaviour)
        this._buffer.set(
          input[0].subarray(srcFrame, srcFrame + framesToCopy),
          this._frameOffset,
        );
      } else {
        // Stereo — interleave ch0 (mic) and ch1 (system) per frame
        for (let f = 0; f < framesToCopy; f++) {
          const bufIdx = (this._frameOffset + f) * 2;
          this._buffer[bufIdx] = input[0][srcFrame + f];
          this._buffer[bufIdx + 1] = input[1][srcFrame + f];
        }
      }

      this._frameOffset += framesToCopy;
      srcFrame += framesToCopy;

      if (this._frameOffset >= FRAMES_PER_BUFFER) {
        // Buffer full — send to main thread
        this.port.postMessage(new Float32Array(this._buffer));
        this._frameOffset = 0;
      }
    }

    return true;
  }
}

registerProcessor("vad-processor", VadProcessor);
