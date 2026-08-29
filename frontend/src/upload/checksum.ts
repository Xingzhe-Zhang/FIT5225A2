export function checksumFileInWorker(file: File, signal?: AbortSignal): Promise<string> {
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL("./checksum.worker.ts", import.meta.url), { type: "module" });
    const finish = () => worker.terminate();
    const abort = () => {
      finish();
      reject(new DOMException("Upload cancelled", "AbortError"));
    };
    if (signal?.aborted) return abort();
    signal?.addEventListener("abort", abort, { once: true });
    worker.onerror = () => {
      finish();
      reject(new Error("Checksum calculation failed."));
    };
    worker.onmessage = ({ data }: MessageEvent<{ sha256?: string; error?: string }>) => {
      signal?.removeEventListener("abort", abort);
      finish();
      if (data.sha256) resolve(data.sha256);
      else reject(new Error(data.error || "Checksum calculation failed."));
    };
    worker.postMessage({ file });
  });
}
