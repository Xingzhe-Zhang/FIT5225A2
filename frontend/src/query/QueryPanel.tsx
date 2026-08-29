import { useEffect, useMemo, useState, type FormEvent } from "react";

import { useAuth } from "../auth/AuthContext";
import type { BulkDeleteResponse, SingleDeleteResponse, TagUpdateResponse } from "../api/mediaTypes";
import { MediaTable } from "../library/MediaTable";
import type { MediaResult } from "../library/MediaGallery";
import { Icon } from "../ui/Icon";

export type QueryMode = "tags" | "species" | "thumbnail";

export interface QueryResult {
  media_id: string;
  file_name?: string | null;
  media_type: "image" | "video";
  status: MediaResult["status"];
  original_url: string | null;
  thumbnail_url: string | null;
  tag_counts: Record<string, number>;
  manual_tags?: string[];
  failure_code?: string | null;
  failure_message?: string | null;
}

export interface QueryResponse {
  results: QueryResult[];
}

export interface QueryClient {
  search(
    mode: QueryMode,
    payload: Record<string, unknown>,
    accessToken: string,
  ): Promise<QueryResponse>;
  updateTags(
    urls: string[],
    tags: string[],
    operation: 0 | 1,
    accessToken: string,
  ): Promise<TagUpdateResponse>;
  deleteMedia(urls: string[], accessToken: string): Promise<BulkDeleteResponse>;
  deleteMediaById(mediaId: string, accessToken: string): Promise<SingleDeleteResponse>;
}

interface TagRow {
  species: string;
  count: string;
}

function querySpeciesName(value: string): string {
  return value.trim().replace(/[\s_]+/g, "_");
}

function validateThumbnailUrl(value: string): string | null {
  try {
    const url = new URL(value);
    if (url.protocol !== "https:") return "Use an HTTPS thumbnail URL.";
    if (!url.pathname.startsWith("/derived/")) {
      return "Paste a thumbnail URL containing /derived/. A URL containing /originals/ is the original media, not its thumbnail.";
    }
    return null;
  } catch {
    return "Enter a valid thumbnail URL.";
  }
}

export function QueryPanel({
  client,
  onLibraryChanged,
}: {
  client: QueryClient;
  onLibraryChanged?(): Promise<void>;
}) {
  const auth = useAuth();
  const [mode, setMode] = useState<QueryMode>("tags");
  const [rows, setRows] = useState<TagRow[]>([{ species: "", count: "1" }]);
  const [species, setSpecies] = useState("");
  const [thumbnailUrl, setThumbnailUrl] = useState("");
  const [results, setResults] = useState<QueryResult[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState<"all" | "image" | "video">("all");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [actionTags, setActionTags] = useState("");
  const [actionBusy, setActionBusy] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const filteredResults = useMemo(
    () => (results ?? []).filter((item) => filter === "all" || item.media_type === filter),
    [filter, results],
  );
  const counts = useMemo(() => ({
    all: results?.length ?? 0,
    image: results?.filter((item) => item.media_type === "image").length ?? 0,
    video: results?.filter((item) => item.media_type === "video").length ?? 0,
  }), [results]);
  const selectedResults = (results ?? []).filter((item) => selected.has(item.media_id));
  const selectedUrls = selectedResults.flatMap((item) => item.original_url ? [item.original_url] : []);

  useEffect(() => setPage(1), [filter]);

  function updateRow(index: number, field: keyof TagRow, value: string) {
    setRows((current) =>
      current.map((row, rowIndex) => (rowIndex === index ? { ...row, [field]: value } : row)),
    );
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!auth.accessToken) {
      setError("Sign in before running a query.");
      return;
    }

    let payload: Record<string, unknown>;
    if (mode === "tags") {
      payload = Object.fromEntries(
        rows
          .map((row) => [querySpeciesName(row.species).toLocaleLowerCase(), Number(row.count)] as const)
          .filter(([tag, count]) => tag.length > 0 && Number.isInteger(count) && count > 0),
      );
      if (Object.keys(payload).length !== rows.length) {
        setError("Every tag needs a species and a positive whole-number count.");
        return;
      }
    } else if (mode === "species") {
      if (!species.trim()) {
        setError("Enter a species name.");
        return;
      }
      payload = { species: querySpeciesName(species) };
    } else {
      if (!thumbnailUrl.trim()) {
        setError("Enter a thumbnail URL.");
        return;
      }
      const thumbnailError = validateThumbnailUrl(thumbnailUrl.trim());
      if (thumbnailError) {
        setError(thumbnailError);
        return;
      }
      payload = { thumbnail_url: thumbnailUrl.trim() };
    }

    setLoading(true);
    setError(null);
    try {
      const response = await client.search(mode, payload, auth.accessToken);
      setResults(response.results);
      setSelected(new Set());
      setActionMessage(null);
      setPage(1);
    } catch (caught) {
      setResults(null);
      setError(caught instanceof Error ? caught.message : "Query failed.");
    } finally {
      setLoading(false);
    }
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

  async function updateSelectedTags(operation: 0 | 1) {
    if (!auth.accessToken || selectedUrls.length === 0) return;
    const normalized = [...new Set(
      actionTags.split(",").map((tag) => tag.trim().toLowerCase()).filter(Boolean),
    )];
    setActionMessage(null);
    if (normalized.length === 0) {
      setError("Enter at least one tag.");
      return;
    }
    setActionBusy(true);
    setError(null);
    try {
      const response = await client.updateTags(selectedUrls, normalized, operation, auth.accessToken);
      const updatedIds = new Set(
        response.results
          .filter((outcome) => outcome.status === "updated" && outcome.media_id)
          .map((outcome) => outcome.media_id as string),
      );
      setResults((current) => (current ?? []).map((result) => {
        if (!updatedIds.has(result.media_id)) return result;
        const manual = new Set(result.manual_tags ?? []);
        normalized.forEach((tag) => operation === 1 ? manual.add(tag) : manual.delete(tag));
        return { ...result, manual_tags: [...manual].sort() };
      }));
      const failures = response.results.filter(
        (outcome) => outcome.status !== "updated" && outcome.status !== "unchanged",
      );
      if (updatedIds.size > 0) {
        setActionMessage(`Tags updated for ${updatedIds.size} item(s).`);
        await onLibraryChanged?.();
      }
      if (failures.length > 0) {
        setError(`Tags were not updated for ${failures.length} item(s).`);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The tag update failed.");
    } finally {
      setActionBusy(false);
    }
  }

  async function deleteSelected() {
    if (!auth.accessToken || selectedResults.length === 0) return;
    setActionBusy(true);
    setError(null);
    setActionMessage(null);
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
      const responses = await Promise.all(
        withoutUrls.map((item) => client.deleteMediaById(item.media_id, auth.accessToken!)),
      );
      responses.forEach((response) => {
        if (response.result.status === "deleted" && response.result.media_id) {
          deletedIds.add(response.result.media_id);
        } else {
          failures.push(response.result.error || response.result.status);
        }
      });
      setResults((current) => (current ?? []).filter((item) => !deletedIds.has(item.media_id)));
      setSelected((current) => new Set([...current].filter((id) => !deletedIds.has(id))));
      setConfirmingDelete(false);
      if (deletedIds.size > 0) {
        setActionMessage(`${deletedIds.size} selected item(s) deleted.`);
        await onLibraryChanged?.();
      }
      if (failures.length > 0) {
        setError(`Could not delete ${failures.length} item(s): ${failures.join("; ")}`);
      }
    } catch (caught) {
      setConfirmingDelete(false);
      setError(caught instanceof Error ? caught.message : "The selected media could not be deleted.");
    } finally {
      setActionBusy(false);
    }
  }

  return (
    <section aria-labelledby="query-heading">
      <div className="panel-heading">
        <div>
          <p className="panel-kicker">Explore the archive</p>
          <h2 id="query-heading">Search wildlife media</h2>
        </div>
        <span className="panel-number" aria-hidden="true">03</span>
      </div>
      <p className="panel-description">Combine detected species counts, browse one species, or use a generated thumbnail URL to locate its source observation.</p>
      <form className="query-form" onSubmit={submit}>
        <fieldset className="mode-switcher">
          <legend>Query type</legend>
          <label className={mode === "tags" ? "active" : ""}>
            <input aria-label="Tag counts" name="query-mode" type="radio" checked={mode === "tags"} onChange={() => setMode("tags")} />
            <span><strong>Tag counts</strong><small>Match a combination</small></span>
          </label>
          <label className={mode === "species" ? "active" : ""}>
            <input aria-label="Species" name="query-mode" type="radio" checked={mode === "species"} onChange={() => setMode("species")} />
            <span><strong>Species</strong><small>Browse one label</small></span>
          </label>
          <label className={mode === "thumbnail" ? "active" : ""}>
            <input
              name="query-mode"
              aria-label="Thumbnail URL"
              type="radio"
              checked={mode === "thumbnail"}
              onChange={() => setMode("thumbnail")}
            />
            <span><strong>Thumbnail URL</strong><small>Locate its source media</small></span>
          </label>
        </fieldset>

        {mode === "tags" && (
          <div className="tag-query-builder">
            {rows.map((row, index) => (
              <div className="tag-query-row" key={index}>
                <label>
                  Species {index + 1}
                  <input
                    aria-label={`Species ${index + 1}`}
                    value={row.species}
                    onChange={(event) => updateRow(index, "species", event.target.value)}
                  />
                </label>
                <label>
                  Minimum count {index + 1}
                  <input
                    aria-label={`Minimum count ${index + 1}`}
                    type="number"
                    min="1"
                    step="1"
                    value={row.count}
                    onChange={(event) => updateRow(index, "count", event.target.value)}
                  />
                </label>
                {rows.length > 1 && (
                  <button className="secondary icon-label" type="button" onClick={() => setRows((current) => current.filter((_, i) => i !== index))}>
                    <Icon name="remove" />Remove tag {index + 1}
                  </button>
                )}
              </div>
            ))}
            <button className="secondary add-row-button icon-label" type="button" onClick={() => setRows((current) => [...current, { species: "", count: "1" }])}>
              <Icon name="add" />Add tag
            </button>
          </div>
        )}

        {mode === "species" && (
          <label>
            Species name
            <input aria-label="Species name" value={species} onChange={(event) => setSpecies(event.target.value)} />
          </label>
        )}

        {mode === "thumbnail" && (
          <label>
            Thumbnail URL
            <input
              aria-label="Thumbnail URL"
              type="url"
              value={thumbnailUrl}
              onChange={(event) => setThumbnailUrl(event.target.value)}
            />
            <small className="field-help">Use the HTTPS URL opened by the thumbnail icon in a media table row. Its path contains <code>/derived/</code>, not <code>/originals/</code>.</small>
          </label>
        )}

        <button className="query-submit icon-label" type="submit" disabled={loading}><Icon name="search" />{loading ? "Searching…" : "Search"}</button>
      </form>

      {error && <p role="alert">{error}</p>}
      {actionMessage && <p role="status">{actionMessage}</p>}
      {results?.length === 0 && <p className="empty-state">No matching media found.</p>}
      {results && results.length > 0 && (
        <div className="query-results">
          <p className="result-summary" role="status">{`${results.length} ${results.length === 1 ? "match" : "matches"} found`}</p>
          <div className="library-filters query-result-filters">
            <div className="filter-bar" role="group" aria-label="Filter query results by media type">
              {(["all", "image", "video"] as const).map((value) => (
                <button key={value} type="button" className={filter === value ? "active" : "secondary"} aria-pressed={filter === value} onClick={() => setFilter(value)}>
                  {value === "all" ? `All ${counts.all}` : value === "image" ? `Images ${counts.image}` : `Videos ${counts.video}`}
                </button>
              ))}
            </div>
          </div>
          {selected.size > 0 && (
            <div className="library-bulk-actions" aria-label="Query result actions">
              <strong>{selected.size} selected</strong>
              <label className="visually-hidden" htmlFor="query-result-tags">Query result tags</label>
              <input
                id="query-result-tags"
                value={actionTags}
                onChange={(event) => setActionTags(event.target.value)}
                placeholder="Tags: night, field-note"
              />
              <button className="icon-label" type="button" disabled={actionBusy || selectedUrls.length === 0} onClick={() => void updateSelectedTags(1)}><Icon name="add" />Add tags</button>
              <button className="secondary icon-label" type="button" disabled={actionBusy || selectedUrls.length === 0} onClick={() => void updateSelectedTags(0)}><Icon name="remove" />Remove tags</button>
              <button type="button" className="button-danger-subtle icon-label" disabled={actionBusy} onClick={() => setConfirmingDelete(true)}><Icon name="delete" />Delete selected</button>
              <button type="button" className="button-link icon-label" disabled={actionBusy} onClick={() => setSelected(new Set())}><Icon name="clear" />Clear</button>
            </div>
          )}
          {filteredResults.length === 0 ? <p className="empty-state">No matching media in this filter.</p> : (
            <MediaTable
              items={filteredResults}
              label="Query results"
              page={page}
              onPageChange={setPage}
              selected={selected}
              onToggle={toggle}
              onTogglePage={togglePage}
            />
          )}
        </div>
      )}
      {confirmingDelete && (
        <div className="modal-backdrop">
          <div className="dialog" role="dialog" aria-label="Confirm deletion" aria-modal="true">
            <p>{`Delete ${selectedResults.length} selected item(s)?`} This cannot be undone.</p>
            <div className="dialog-actions">
              <button className="secondary" type="button" disabled={actionBusy} onClick={() => setConfirmingDelete(false)}>Cancel</button>
              <button className="button-danger" type="button" disabled={actionBusy} onClick={() => void deleteSelected()}>Confirm delete</button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
