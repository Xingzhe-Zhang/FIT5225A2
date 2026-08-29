import { useEffect, useRef, useState, type FormEvent } from "react";

import { useAuth } from "../auth/AuthContext";
import { checksumFileInWorker } from "./checksum";
import { maxBytesFor } from "./mediaLimits";

export interface UploadReservationRequest {
  file_name: string;
  media_type: "image" | "video";
  size_bytes: number;
  sha256: string;
}

export interface UploadReservationResponse {
  media_id: string;
  duplicate: boolean;
  status: string;
  upload_url: string | null;
  object_key: string | null;
  expires_in_seconds: number | null;
  upload_headers: Record<string, string> | null;
}

export interface UploadClient {
  reserve(request: UploadReservationRequest, accessToken: string): Promise<UploadReservationResponse>;
  cancelReservation(mediaId: string, sha256: string, accessToken: string): Promise<{ status: "cancelled" | "already_cancelled" }>;
}

function mediaTypeFor(file: File): "image" | "video" | null {
  const extension = file.name.toLocaleLowerCase().match(/\.[^.]+$/)?.[0];
  if (extension === ".jpg" || extension === ".jpeg" || extension === ".png") return "image";
  if (extension === ".mp4" || extension === ".mov") return "video";
  return null;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function UploadPanel({
  client,
  refreshLibrary,
  calculateChecksum = checksumFileInWorker,
}: {
  client: UploadClient;
  refreshLibrary(): Promise<void>;
  calculateChecksum?(file: File, signal?: AbortSignal): Promise<string>;
}) {
  const auth = useAuth();
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  useEffect(() => () => abortRef.current?.abort(), []);

  function clearSelection() {
    setFile(null);
    if (inputRef.current) inputRef.current.value = "";
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!auth.accessToken) {
      setError("Sign in before uploading media.");
      return;
    }
    if (!file) {
      setError("Choose a media file first.");
      return;
    }
    const mediaType = mediaTypeFor(file);
    if (!mediaType) {
      setError("Choose an image or video file.");
      return;
    }
    if (file.size > maxBytesFor(mediaType)) {
      setError(`${mediaType === "image" ? "Image" : "Video"} exceeds the ${formatBytes(maxBytesFor(mediaType))} upload limit.`);
      return;
    }

    setUploading(true);
    setError(null);
    setMessage(null);
    const controller = new AbortController();
    abortRef.current = controller;
    let pendingReservation: { mediaId: string; sha256: string } | null = null;
    try {
      const sha256 = await calculateChecksum(file, controller.signal);
      if (controller.signal.aborted) throw new DOMException("Upload cancelled", "AbortError");
      const reservation = await client.reserve(
        {
          file_name: file.name,
          media_type: mediaType,
          size_bytes: file.size,
          sha256,
        },
        auth.accessToken,
      );
      if (reservation.duplicate) {
        await refreshLibrary();
        setMessage("This file is already in your library.");
        clearSelection();
        return;
      }
      pendingReservation = { mediaId: reservation.media_id, sha256 };
      if (!reservation.upload_url || !reservation.upload_headers) {
        throw new Error("Upload reservation did not include its signed transport contract.");
      }
      const upload = await fetch(reservation.upload_url, {
        method: "PUT",
        headers: reservation.upload_headers,
        body: file,
        signal: controller.signal,
      });
      if (!upload.ok) {
        throw new Error("Direct upload failed.");
      }
      pendingReservation = null;
      await refreshLibrary();
      setMessage("Upload complete.");
      clearSelection();
    } catch (caught) {
      let cancellationError: string | null = null;
      if (pendingReservation && auth.accessToken) {
        try {
          await client.cancelReservation(
            pendingReservation.mediaId,
            pendingReservation.sha256,
            auth.accessToken,
          );
        } catch (cancelCaught) {
          cancellationError = cancelCaught instanceof Error
            ? cancelCaught.message
            : "The unused reservation could not be cancelled.";
          await refreshLibrary().catch(() => undefined);
        }
      }
      const failure = caught instanceof DOMException && caught.name === "AbortError"
        ? "Upload cancelled."
        : caught instanceof Error ? caught.message : "Upload failed.";
      setError(cancellationError ? `${failure} ${cancellationError}` : failure);
    } finally {
      abortRef.current = null;
      setUploading(false);
    }
  }

  return (
    <section aria-labelledby="upload-heading">
      <div className="panel-heading">
        <div>
          <p className="panel-kicker">New observation</p>
          <h2 id="upload-heading">Upload wildlife media</h2>
        </div>
        <span className="panel-number" aria-hidden="true">01</span>
      </div>
      <p className="panel-description">Add field images or video. The archive creates a lightweight preview and prepares species tags after upload.</p>
      <form className="upload-form" onSubmit={submit}>
        <label className="file-picker">
          <span className="file-picker-title">Choose media file</span>
          <span className="file-picker-help">JPG, PNG, MP4 or MOV</span>
          <input
            ref={inputRef}
            aria-label="Choose media file"
            type="file"
            accept=".jpg,.jpeg,.png,.mp4,.mov,image/jpeg,image/png,video/mp4,video/quicktime"
            onChange={(event) => {
              const selected = event.target.files?.[0] ?? null;
              const selectedType = selected ? mediaTypeFor(selected) : null;
              if (selected && selectedType && selected.size > maxBytesFor(selectedType)) {
                setFile(null);
                setError(`${selectedType === "image" ? "Image" : "Video"} exceeds the ${formatBytes(maxBytesFor(selectedType))} upload limit.`);
                event.target.value = "";
                return;
              }
              setFile(selected);
              setError(null);
              setMessage(null);
            }}
          />
        </label>
        {file && (
          <div className="file-summary" aria-label="Selected file">
            <span className="file-type-icon" aria-hidden="true">{mediaTypeFor(file) === "video" ? "▶" : "◇"}</span>
            <span>
              <strong>{file.name}</strong>
              <small>{`${formatBytes(file.size)} · ${mediaTypeFor(file) === "video" ? "Video" : "Image"}`}</small>
            </span>
            <button
              type="button"
              className="button-link"
              onClick={() => uploading ? abortRef.current?.abort() : clearSelection()}
            >{uploading ? "Cancel upload" : "Remove"}</button>
          </div>
        )}
        <button type="submit" aria-label="Upload" disabled={uploading || !file}>{uploading ? "Preparing upload…" : "Upload to archive"}</button>
      </form>
      {message && <p role="status">{message}</p>}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
