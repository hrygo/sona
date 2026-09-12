/** Play one Blob, releasing its URL on completion, error or explicit cancellation. */
export function playAudioBlob(blob: Blob, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return Promise.reject(new DOMException("Playback aborted", "AbortError"));
  const audioUrl = URL.createObjectURL(blob);
  const audio = new Audio(audioUrl);
  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = (error?: Error) => {
      if (settled) return;
      settled = true;
      audio.onended = null; audio.onerror = null;
      signal?.removeEventListener("abort", abort);
      if (error) audio.pause?.();
      URL.revokeObjectURL(audioUrl);
      if (error) reject(error); else resolve();
    };
    const abort = () => finish(new DOMException("Playback aborted", "AbortError"));
    signal?.addEventListener("abort", abort, { once: true });
    audio.onended = () => finish();
    audio.onerror = () => finish(new Error("audio playback failed"));
    try {
      audio.play().catch((error: unknown) => {
        finish(error instanceof Error ? error : new Error("audio playback was rejected"));
      });
    } catch (error) { finish(error instanceof Error ? error : new Error("audio playback failed")); }
  });
}
