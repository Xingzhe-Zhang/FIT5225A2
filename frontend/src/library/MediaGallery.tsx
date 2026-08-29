import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { BulkDeleteResponse, SingleDeleteResponse, TagUpdateResponse } from "../api/mediaTypes";
import { useAuth } from "../auth/AuthContext";
import { Icon } from "../ui/Icon";
import { MediaTable, mediaFileName } from "./MediaTable";

export interface MediaResult {
  media_id: string;
  file_name?: string | null;
  media_type: "image" | "video";
  status: "reserved" | "uploaded" | "processing" | "prepared" | "ready" | "deleting" | "failed";
  original_url: string | null;
  thumbnail_url: string | null;
  tag_counts: Record<string, number>;
  manual_tags?: string[];
  failure_code?: string | null;
  failure_message?: string | null;
}

export interface LocalMediaPreview {
  file_name: string;
  url: string;
}

export interface MediaLibraryClient {
  list(accessToken: string): Promise<{ results: MediaResult[] }>;
  updateTags(urls: string[], tags: string[], operation: 0 | 1, accessToken: string): Promise<TagUpdateResponse>;
  deleteMedia(urls: string[], accessToken: string): Promise<BulkDeleteResponse>;
  deleteMediaById(mediaId: string, accessToken: string): Promise<SingleDeleteResponse>;
}

type MediaFilter = "all" | "image" | "video";
type StatusFilter = "all" | "ready" | "processing" | "failed";

const PROCESSING_STATUSES = new Set<MediaResult["status"]>([
  "reserved", "uploaded", "processing", "prepared", "deleting",
]);

function matchesStatus(media: MediaResult, filter: StatusFilter): boolean {
  if (filter === "all") return true;
  if (filter === "processing") return PROCESSING_STATUSES.has(media.status);
  return media.status === filter;
}

export function MediaGallery({
  client,
  refreshVersion = 0,
  localPreviews = {},
  onResultsChange,
}: {
  client: MediaLibraryClient;
  refreshVersion?: number;
  localPreviews?: Record<string, LocalMediaPreview>;
  onResultsChange?(results: MediaResult[]): void;
}) {
  const auth = useAuth();
  const [results, setResults] = useState<MediaResult[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState<string | null>(null);
  const [filter, setFilter] = useState<MediaFilter>("all");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [tags, setTags] = useState("");
  const [deleting, setDeleting] = useState<MediaResult | null>(null);
  const [confirmingBulkDelete, setConfirmingBulkDelete] = useState(false);
  const pollTimer = useRef<number | undefined>(undefined);

  const loadMedia = useCallback(async (background = false) => {
    if (!auth.accessToken) {
      setResults(null);
      setLoading(false);
      setError(null);
      setMessage(null);
      return;
    }
    if (!background) setLoading(true);
    if (!background) setError(null);
    try {
      const response = await client.list(auth.accessToken);
      setResults(response.results);
      onResultsChange?.(response.results);
      setSelected((current) => new Set([...current].filter((id) => response.results.some((item) => item.media_id === id))));
      const pending = response.results.some((item) => PROCESSING_STATUSES.has(item.status));
      if (pending && pollTimer.current === undefined) {
        pollTimer.current = window.setInterval(() => void loadMedia(true), 5000);
      } else if (!pending && pollTimer.current !== undefined) {
        window.clearInterval(pollTimer.current);
        pollTimer.current = undefined;
      }
    } catch (caught) {
      if (!background) setResults(null);
      if (!background) setError(caught instanceof Error ? caught.message : "Media library is unavailable.");
    } finally {
      if (!background) setLoading(false);
    }
  }, [auth.accessToken, client, onResultsChange]);

  useEffect(() => {
    void loadMedia();
    return () => {
      if (pollTimer.current !== undefined) window.clearInterval(pollTimer.current);
      pollTimer.current = undefined;
    };
  }, [loadMedia, refreshVersion]);

  useEffect(() => setPage(1), [filter, statusFilter]);

  const counts = useMemo(() => ({
    all: results?.length ?? 0,
    image: results?.filter((item) => item.media_type === "image").length ?? 0,
    video: results?.filter((item) => item.media_type === "video").length ?? 0,
    ready: results?.filter((item) => item.status === "ready").length ?? 0,
    processing: results?.filter((item) => PROCESSING_STATUSES.has(item.status)).length ?? 0,
    failed: results?.filter((item) => item.status === "failed").length ?? 0,
  }), [results]);
  const filtered = results?.filter((item) =>
    (filter === "all" || item.media_type === filter) && matchesStatus(item, statusFilter),
  ) ?? [];
  const selectedResults = results?.filter((item) => selected.has(item.media_id)) ?? [];
  const selectedUrls = selectedResults.flatMap((item) => item.original_url ? [item.original_url] : []);

  function replaceResults(next: MediaResult[]) {
    setResults(next);
    onResultsChange?.(next);
  }

  function toggle(mediaId: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(mediaId)) next.delete(mediaId);
      else next.add(mediaId);
      return next;
    });
  }

  function togglePage(mediaIds: string[], checked: boolean) {
    setSelected((current) => {
      const next = new Set(current);
      mediaIds.forEach((id) => checked ? next.add(id) : next.delete(id));
      return next;
    });
  }

  function normalizedTags(): string[] {
    return [...new Set(tags.split(",").map((tag) => tag.trim().toLowerCase()).filter(Boolean))];
  }

  async function updateSelectedTags(operation: 0 | 1) {
    if (!auth.accessToken || selectedUrls.length === 0) return;
    const nextTags = normalizedTags();
    setMessage(null);
    if (nextTags.length === 0) {
      setError("Enter at least one tag.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const response = await client.updateTags(selectedUrls, nextTags, operation, auth.accessToken);
      const updatedIds = new Set(response.results.filter((outcome) => outcome.status === "updated").map((outcome) => outcome.media_id));
      const next = (results ?? []).map((media) => {
        if (!updatedIds.has(media.media_id)) return media;
        const manual = new Set(media.manual_tags ?? []);
        nextTags.forEach((tag) => operation === 1 ? manual.add(tag) : manual.delete(tag));
        return { ...media, manual_tags: [...manual].sort() };
      });
      replaceResults(next);
      const failures = response.results.filter((outcome) => !["updated", "unchanged"].includes(outcome.status));
      if (failures.length > 0) setError(`Tags were not updated for ${failures.length} item(s).`);
      if (response.results.length > failures.length) setMessage("Selected media tags updated.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The tag update failed.");
    } finally {
      setLoading(false);
    }
  }

  async function deleteSelected() {
    if (!auth.accessToken || selectedResults.length === 0) return;
    setLoading(true);
    setError(null);
    setMessage(null);
    try {
      const deletedIds = new Set<string>();
      const failures: string[] = [];
      if (selectedUrls.length > 0) {
        const response = await client.deleteMedia(selectedUrls, auth.accessToken);
        response.results.forEach((outcome) => {
          if (outcome.status === "deleted" && outcome.media_id) deletedIds.add(outcome.media_id);
          else failures.push(outcome.error || outcome.status);
        });
      }
      const withoutUrls = selectedResults.filter((item) => !item.original_url);
      const responses = await Promise.all(withoutUrls.map((item) => client.deleteMediaById(item.media_id, auth.accessToken!)));
      responses.forEach((response) => {
        if (response.result.status === "deleted" && response.result.media_id) deletedIds.add(response.result.media_id);
        else failures.push(response.result.error || response.result.status);
      });
      const next = (results ?? []).filter((item) => !deletedIds.has(item.media_id));
      replaceResults(next);
      setSelected((current) => new Set([...current].filter((id) => !deletedIds.has(id))));
      setConfirmingBulkDelete(false);
      if (deletedIds.size > 0) setMessage(`${deletedIds.size} selected item(s) deleted.`);
      if (failures.length > 0) setError(`Could not delete ${failures.length} item(s): ${failures.join("; ")}`);
    } catch (caught) {
      setConfirmingBulkDelete(false);
      setError(caught instanceof Error ? caught.message : "The selected media could not be deleted.");
    } finally {
      setLoading(false);
    }
  }

  async function confirmDelete() {
    if (!deleting || !auth.accessToken) return;
    setLoading(true);
    setError(null);
    setMessage(null);
    try {
      const response = await client.deleteMediaById(deleting.media_id, auth.accessToken);
      if (response.result.status !== "deleted") {
        setError(response.result.error || `Media could not be deleted (${response.result.status}).`);
        setDeleting(null);
        return;
      }
      const next = (results ?? []).filter((item) => item.media_id !== deleting.media_id);
      replaceResults(next);
      setSelected((current) => {
        const updated = new Set(current);
        updated.delete(deleting.media_id);
        return updated;
      });
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
        <div><p className="panel-kicker">Observation collection</p><h2 id="media-gallery-heading">Your media library</h2></div>
        <button type="button" className="button-link refresh-library icon-label" onClick={() => void loadMedia()} disabled={loading}><Icon name="refresh" />Refresh</button>
      </div>
      <p className="panel-description">Review processing progress, open originals and organise the species evidence found in each file.</p>
      {loading && <p className="inline-loading" role="status">Updating library…</p>}
      {error && <p role="alert">{error}</p>}
      {message && <p role="status">{message}</p>}
      {!loading && results?.length === 0 && <p className="empty-state">Your media library is empty. Upload a field observation to begin.</p>}
      {results && results.length > 0 && (
        <>
          <div className="library-filters">
            <div className="filter-bar" role="group" aria-label="Filter by media type">
              {(["all", "image", "video"] as const).map((value) => (
                <button key={value} type="button" className={filter === value ? "active" : "secondary"} aria-pressed={filter === value} onClick={() => setFilter(value)}>
                  {value === "all" ? `All ${counts.all}` : value === "image" ? `Images ${counts.image}` : `Videos ${counts.video}`}
                </button>
              ))}
            </div>
            <div className="filter-bar filter-bar-status" role="group" aria-label="Filter by processing status">
              {(["all", "ready", "processing", "failed"] as const).map((value) => (
                <button key={value} type="button" className={statusFilter === value ? "active" : "secondary"} aria-pressed={statusFilter === value} onClick={() => setStatusFilter(value)}>
                  {value === "all" ? "Any status" : `${value.charAt(0).toUpperCase()}${value.slice(1)} ${counts[value]}`}
                </button>
              ))}
            </div>
          </div>
          {selected.size > 0 && (
            <div className="library-bulk-actions" aria-label="Bulk media actions">
              <strong>{selected.size} selected</strong>
              <label className="visually-hidden" htmlFor="library-tags">Tags</label>
              <input id="library-tags" value={tags} onChange={(event) => setTags(event.target.value)} placeholder="Tags: night, field-note" />
              <button className="icon-label" type="button" disabled={loading || selectedUrls.length === 0} onClick={() => void updateSelectedTags(1)}><Icon name="add" />Add tags</button>
              <button type="button" className="secondary icon-label" disabled={loading || selectedUrls.length === 0} onClick={() => void updateSelectedTags(0)}><Icon name="remove" />Remove tags</button>
              <button type="button" className="button-danger-subtle icon-label" disabled={loading} onClick={() => setConfirmingBulkDelete(true)}><Icon name="delete" />Delete selected</button>
              <button type="button" className="button-link icon-label" onClick={() => setSelected(new Set())}><Icon name="clear" />Clear</button>
            </div>
          )}
          {filtered.length === 0 ? <p className="empty-state">No media matches these filters.</p> : (
            <MediaTable
              items={filtered}
              label="Media library"
              page={page}
              onPageChange={setPage}
              selected={selected}
              onToggle={toggle}
              onTogglePage={togglePage}
              onDelete={setDeleting}
              localPreviews={localPreviews}
              disabled={loading}
            />
          )}
        </>
      )}
      {(deleting || confirmingBulkDelete) && (
        <div className="modal-backdrop">
          <div className="dialog" role="dialog" aria-label="Confirm deletion" aria-modal="true">
            <p>{deleting ? `Delete ${mediaFileName(deleting, localPreviews[deleting.media_id])} from your library?` : `Delete ${selectedResults.length} selected item(s)?`} This cannot be undone.</p>
            <div className="dialog-actions">
              <button className="secondary" type="button" disabled={loading} onClick={() => { setDeleting(null); setConfirmingBulkDelete(false); }}>Cancel</button>
              <button className="button-danger" type="button" disabled={loading} onClick={() => void (deleting ? confirmDelete() : deleteSelected())}>Confirm delete</button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
