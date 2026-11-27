/* Collects microphone samples off the main thread in ~42 ms batches and flushes any remaining tail samples on stop. */
class PipkinCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Float32Array(2048);
    this.length = 0;
    this.stopped = false;
    this.port.onmessage = ({ data }) => {
      if (data === "flush" || data?.type === "flush") {
        this.flush(true);
        this.stopped = true;
      }
    };
  }

  process(inputs) {
    if (this.stopped) return false;
    const channel = inputs[0]?.[0];
    if (channel) {
      for (let i = 0; i < channel.length; i += 1) {
        this.buffer[this.length++] = channel[i];
        if (this.length === this.buffer.length) this.flush(false);
      }
    }
    return true;
  }

  flush(finalChunk) {
    if (this.length > 0) {
      const chunk = this.buffer.slice(0, this.length);
      this.length = 0;
      this.port.postMessage(chunk, [chunk.buffer]);
    }
    if (finalChunk) {
      this.port.postMessage({ type: "flushed" });
    }
  }
}

registerProcessor("pipkin-capture", PipkinCapture);
