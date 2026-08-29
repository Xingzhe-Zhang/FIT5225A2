import { IncrementalSha256 } from "./incrementalSha256";

const CHUNK_BYTES = 4 * 1024 * 1024;

type ChecksumRequest = { file: File };
type WorkerScope = {
  onmessage: ((event: MessageEvent<ChecksumRequest>) => void) | null;
  postMessage(message: { sha256?: string; error?: string }): void;
};

const scope = self as unknown as WorkerScope;
scope.onmessage = async ({ data }) => {
  try {
    const sha256 = new IncrementalSha256();
    for (let offset = 0; offset < data.file.size; offset += CHUNK_BYTES) {
      const chunk = await data.file.slice(offset, offset + CHUNK_BYTES).arrayBuffer();
      sha256.update(new Uint8Array(chunk));
    }
    scope.postMessage({ sha256: sha256.digestHex() });
  } catch (error) {
    scope.postMessage({ error: error instanceof Error ? error.message : "Checksum calculation failed" });
  }
};

export {};
