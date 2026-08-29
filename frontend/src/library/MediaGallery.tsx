import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useAuth } from "../auth/AuthContext";
import type { SingleDeleteResponse } from "../api/mediaTypes";

export interface MediaResult {
  media_id: string;
  media_type: "image" | "video";
  status: "reserved" | "uploaded" | "processing" | "prepared" | "ready" | "deleting" | "failed";
  original_url: string | null;
  thumbnail_url: string | null;
  tag_counts: Record<string, number>;
  manual_tags?: string[];
  failure_code?: string | null;
  failure_message?: string | null;
}

export interface MediaLibraryClient {
  list(accessToken: string): Promise<{ results: MediaResult[] }>;
  deleteMediaById(mediaId: string, accessToken: string): Promise<SingleDeleteResponse>;
}

type MediaFilter = "all" | "image" | "video";

function titleCase(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

export function MediaGallery({
  client,
  refreshVersion = 0,
}: {
  client: MediaLibraryClient;
  refreshVersion?: number;
}) {
  const auth = useAuth();
  const [results, setResults] = useState<MediaResult[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState<string | null>(null);
  const [filter, setFilter] = useState<MediaFilter>("all");
  const [deleting, setDeleting] = useState<MediaResult | null>(null);
  const pollTimer = useRef<number | undefined>(undefined);

  const loadMedia = useCallback(async () => {
    if (!auth.accessToken) {
      setResults(null);
      setLoading(false);
      setError(null);
      setMessage(null);
      return;
    }
    setLoading(true);
    setError(null);
    setMessage(null);
    try {
      const response = await client.list(auth.accessToken);
      setResults(response.results);
      const pending = response.results.some((item) =>
        item.status === "reserved" || item.status === "uploaded" || item.status === "processing" || item.status === "prepared",
      );
      if (pending && pollTimer.current === undefined) {
        pollTimer.current = window.setInterval(() => void loadMedia(), 5000);
      } else if (!pending && pollTimer.current !== undefined) {
        window.clearInterval(pollTimer.current);
        pollTimer.current = undefined;
      }
    } catch (caught) {
      setResults(null);
      setError(caught instanceof Error ? caught.message : "Media library is unavailable.");
    } finally {
      setLoading(false);
    }
  }, [auth.accessToken, client]);

  useEffect(() => {
    void loadMedia();
    return () => {
      if (pollTimer.current !== undefined) {
        window.clearInterval(pollTimer.current);
        pollTimer.current = undefined;
      }
    };
  }, [loadMedia, refreshVersion]);

  const counts = useMemo(() => ({
    all: results?.length ?? 0,
    image: results?.filter((item) => item.media_type === "image").length ?? 0,
    video: results?.filter((item) => item.media_type === "video").length ?? 0,
  }), [results]);
  const filtered = results?.filter((item) => filter === "all" || item.media_type === filter) ?? [];

  async function confirmDelete() {
    if (!deleting || !auth.accessToken) return;
    setLoading(true);
    setError(null);
    setMessage(null);
    try {
      const response = await client.deleteMediaById(deleting.media_id, auth.accessToken);
      const outcome = response.result;
      if (outcome.status !== "deleted") {
        setError(outcome.error || `Media could not be deleted (${outcome.status}).`);
        setDeleting(null);
        return;
      }
      setResults((current) => current?.filter((item) => item.media_id !== deleting.media_id) ?? current);
      setMessage("Media deleted.");
      setDeleting(null);
    } catch (caught) {
      setDeleting(null);
      setError(caught instanceof Error ? caught.message : "The media could not be deleted.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section aria-labelledby="media-gallery-heading">
      <div className="panel-heading panel-heading-row">
        <div>
          <p className="panel-kicker">Observation collection</p>
          <h2 id="media-gallery-heading">Your media library</h2>
        </div>
        <button type="button" className="secondary" onClick={() => void loadMedia()} disabled={loading}>Refresh library</button>
      </div>
      <p className="panel-description">Review processing progress, open originals and browse the species evidence found in each file.</p>
      {loading && <p role="status">Loading media…</p>}
      {error && <p role="alert">{error}</p>}
      {message && <p role="status">{message}</p>}
      {!loading && results?.length === 0 && <p className="empty-state">Your media library is empty. Upload a field observation to begin.</p>}
      {results && results.length > 0 && (
        <>
          <div className="filter-bar" role="group" aria-label="Filter media library">
            <button type="button" className={filter === "all" ? "active" : "secondary"} aria-pressed={filter === "all"} onClick={() => setFilter("all")}>{`All ${counts.all}`}</button>
            <button type="button" className={filter === "image" ? "active" : "secondary"} aria-pressed={filter === "image"} onClick={() => setFilter("image")}>{`Images ${counts.image}`}</button>
            <button type="button" className={filter === "video" ? "active" : "secondary"} aria-pressed={filter === "video"} onClick={() => setFilter("video")}>{`Videos ${counts.video}`}</button>
          </div>
          {filtered.length === 0 ? (
            <p className="empty-state">No {filter} media is available in this view.</p>
          ) : (
            <ul className="media-grid" aria-label="Media library">
              {filtered.map((media) => (
                <li className="media-card" key={media.media_id}>
                  <div className="media-preview">
                    {media.status === "failed" ? (
                      <span className="preview-placeholder preview-failed" aria-label={`Media processing failed ${media.media_id}`}>Processing failed</span>
                    ) : !media.original_url ? (
                      <span className="preview-placeholder" aria-label={`Media unavailable ${media.media_id}`}>Preview is being prepared</span>
                    ) : media.media_type === "image" ? (
                      <a href={media.original_url} target="_blank" rel="noreferrer" aria-label="Open image original">
                        {media.thumbnail_url ? (
                          <img className="media-thumbnail" src={media.thumbnail_url} alt="Image media thumbnail" />
                        ) : (
                          <span className="preview-placeholder">Image preview unavailable</span>
                        )}
                      </a>
                    ) : (
                      <a href={media.original_url} target="_blank" rel="noreferrer" aria-label="Open video original">
                        <video className="media-thumbnail media-video-thumbnail" aria-label="Video media preview" poster={media.thumbnail_url ?? undefined} muted preload="metadata" />
                        <span className="play-indicator" aria-hidden="true">▶</span>
                      </a>
                    )}
                    <span className={`status-chip status-${media.status}`}>{titleCase(media.status)}</span>
                  </div>
                  <div className="media-card-body">
                    <div className="media-card-title">
                      <strong>{media.media_type === "image" ? "Field image" : "Field video"}</strong>
                      <span>{media.media_id.slice(0, 8)}</span>
                    </div>
                    {Object.keys(media.tag_counts).length > 0 || (media.manual_tags ?? []).length > 0 ? (
                      <div className="tag-list" aria-label="Detected tags">
                        {Object.entries(media.tag_counts).map(([tag, count]) => <span className="tag-chip" key={`detected-${tag}`}>{`${tag} × ${count}`}</span>)}
                        {(media.manual_tags ?? []).map((tag) => <span className="tag-chip tag-chip-manual" key={`manual-${tag}`}>{`${tag} · manual`}</span>)}
                      </div>
                    ) : <span className="tag-empty">{media.status === "failed" ? "No tags were produced" : "No tags yet"}</span>}
                    {media.status === "failed" && (
                      <p className="media-failure">
                        {media.failure_message ?? "Processing stopped before this media could be prepared."}
                        {media.failure_code && <small>{media.failure_code}</small>}
                      </p>
                    )}
                    <button
                      type="button"
                      className="button-danger"
                      disabled={loading}
                      onClick={() => setDeleting(media)}
                    >
                      {`Delete ${media.media_type}`}
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      {deleting && (
        <div className="modal-backdrop">
          <div className="dialog" role="dialog" aria-label="Confirm deletion" aria-modal="true">
            <p>Delete this {deleting.media_type} from your library? This cannot be undone.</p>
            <div className="dialog-actions">
              <button className="secondary" type="button" disabled={loading} onClick={() => setDeleting(null)}>Cancel</button>
              <button className="button-danger" type="button" disabled={loading} onClick={() => void confirmDelete()}>Confirm delete</button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
