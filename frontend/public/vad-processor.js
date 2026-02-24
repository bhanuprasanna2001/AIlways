/**
 * AudioWorklet processor for live transcription.
 *
 * Runs on the audio rendering thread and forwards raw PCM samples
 * to the main thread via postMessage. The main thread then sends
 * these samples over the WebSocket to the backend.
 *
 * Audio is buffered to ~256 ms chunks (4096 samples at 16 kHz)
 * to reduce WebSocket message rate from ~125/s to ~4/s.  This
 * dramatically lowers event-loop overhead on both browser and
 * backend while keeping latency imperceptible for live use.
 */

const BUFFER_SIZE = 4096; // samples (~256 ms at 16 kHz)

class VadProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buffer = new Float32Array(BUFFER_SIZE);
    this._offset = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0] || input[0].length === 0) {
      return true;
    }

    const samples = input[0];
    let srcOffset = 0;

    while (srcOffset < samples.length) {
      const remaining = BUFFER_SIZE - this._offset;
      const toCopy = Math.min(remaining, samples.length - srcOffset);

      this._buffer.set(
        samples.subarray(srcOffset, srcOffset + toCopy),
        this._offset,
      );
      this._offset += toCopy;
      srcOffset += toCopy;

      if (this._offset >= BUFFER_SIZE) {
        // Buffer full — send to main thread
        this.port.postMessage(new Float32Array(this._buffer));
        this._offset = 0;
      }
    }

    return true;
  }
}

registerProcessor("vad-processor", VadProcessor);
