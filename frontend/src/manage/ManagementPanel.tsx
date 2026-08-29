import { useState } from "react";

import { useAuth } from "../auth/AuthContext";
import type { BulkDeleteResponse, TagUpdateResponse } from "../api/mediaTypes";


export interface ManagementResult {
  media_id: string;
  media_type: "image" | "video";
  original_url: string;
  thumbnail_url: string | null;
  tag_counts: Record<string, number>;
  manual_tags?: string[];
}

export interface ManagementClient {
  queryByFile(file: File, accessToken: string): Promise<ManagementResult[]>;
  updateTags(urls: string[], tags: string[], operation: 0 | 1, accessToken: string): Promise<TagUpdateResponse>;
  deleteMedia(urls: string[], accessToken: string): Promise<BulkDeleteResponse>;
}

function resultName(result: ManagementResult): string {
  const path = new URL(result.original_url).pathname;
  return decodeURIComponent(path.split("/").at(-1) || result.media_id);
}

function outcomeFor<T extends { media_id: string | null; url?: string }>(
  result: ManagementResult,
  outcomes: T[],
): T | undefined {
  return outcomes.find((outcome) => outcome.media_id === result.media_id)
    ?? outcomes.find((outcome) => outcome.url === result.original_url);
}

function statusSummary(statuses: string[]): string {
  const counts = new Map<string, number>();
  statuses.forEach((status) => counts.set(status, (counts.get(status) ?? 0) + 1));
  return [...counts.entries()].map(([status, count]) => `${count} ${status.replaceAll("_", " ")}`).join(", ");
}


export function ManagementPanel({ client }: { client: ManagementClient }) {
  const { accessToken } = useAuth();
  const [file, setFile] = useState<File | null>(null);
  const [results, setResults] = useState<ManagementResult[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [tags, setTags] = useState("");
  const [busy, setBusy] = useState(false);
  const [searched, setSearched] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const selectedUrls = results
    .filter((result) => selected.has(result.media_id))
    .map((result) => result.original_url);

  async function runQuery() {
    if (!file || !accessToken) return;
    setBusy(true);
    setError(null);
    setMessage("Searching...");
    try {
      const matches = await client.queryByFile(file, accessToken);
      setResults(matches);
      setSelected(new Set());
      setSearched(true);
      setMessage(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The query failed");
      setMessage(null);
    } finally {
      setBusy(false);
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

  async function update(operation: 0 | 1) {
    if (!accessToken || selectedUrls.length === 0) return;
    const normalized = [...new Set(
      tags.split(",").map((tag) => tag.trim().toLowerCase()).filter(Boolean),
    )];
    setMessage(null);
    if (normalized.length === 0) {
      setError("Enter at least one tag.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const response = await client.updateTags(selectedUrls, normalized, operation, accessToken);
      const successfulIds = new Set(
        results
          .filter((result) => selected.has(result.media_id))
          .filter((result) => outcomeFor(result, response.results)?.status === "updated")
          .map((result) => result.media_id),
      );
      setResults((current) => current.map((result) => {
        if (!successfulIds.has(result.media_id)) return result;
        const existing = new Set(result.manual_tags ?? []);
        normalized.forEach((tag) => operation === 1 ? existing.add(tag) : existing.delete(tag));
        return { ...result, manual_tags: [...existing].sort() };
      }));
      const selectedResults = results.filter((result) => selected.has(result.media_id));
      const failed = selectedResults
        .map((result) => outcomeFor(result, response.results))
        .filter((outcome) => !outcome || (outcome.status !== "updated" && outcome.status !== "unchanged"));
      const succeeded = selectedResults.length - failed.length;
      if (succeeded > 0) setMessage(`Tags updated. ${succeeded} item(s) succeeded.`);
      else setMessage(null);
      if (failed.length > 0) {
        const statuses = failed.map((outcome) => outcome?.status ?? "missing result");
        setError(`Tags were not updated for ${failed.length} item(s): ${statusSummary(statuses)}.`);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The tag update failed");
    } finally {
      setBusy(false);
    }
  }

  async function confirmDelete() {
    if (!accessToken || selectedUrls.length === 0) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const response = await client.deleteMedia(selectedUrls, accessToken);
      const selectedResults = results.filter((result) => selected.has(result.media_id));
      const deletedIds = new Set(
        selectedResults
          .filter((result) => outcomeFor(result, response.results)?.status === "deleted")
          .map((result) => result.media_id),
      );
      setResults((current) => current.filter((result) => !deletedIds.has(result.media_id)));
      setSelected((current) => new Set([...current].filter((mediaId) => !deletedIds.has(mediaId))));
      setConfirmingDelete(false);
      const failed = selectedResults
        .map((result) => outcomeFor(result, response.results))
        .filter((outcome) => !outcome || outcome.status !== "deleted");
      if (deletedIds.size > 0) setMessage(`${deletedIds.size} item(s) deleted.`);
      else setMessage(null);
      if (failed.length > 0) {
        const details = failed.map((outcome) => outcome?.error || outcome?.status || "missing result");
        setError(`Could not delete ${failed.length} item(s): ${details.join("; ")}`);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The deletion failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="management-heading">
      <div className="panel-heading">
        <div><p className="panel-kicker">Curate records</p><h2 id="management-heading">Media management</h2></div>
        <span className="panel-number" aria-hidden="true">04</span>
      </div>
      <p className="panel-description">Upload a reference file to find similar records, then apply tags or delete selected items in one action.</p>
      <div className="management-search">
        <label htmlFor="query-file">Query file</label>
        <input id="query-file" type="file" accept="image/jpeg,image/png,video/mp4,video/quicktime,.mov" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
        <button type="button" disabled={!file || busy || !accessToken} onClick={() => void runQuery()}>Find matching media</button>
      </div>

      {error && <p role="alert">{error}</p>}
      {message && <p role="status">{message}</p>}
      {searched && results.length === 0 && <p className="empty-state">No matching media found.</p>}

      {results.length > 0 && (
        <div className="management-results">
          <div className="selection-toolbar">
            <strong>{`${selected.size} of ${results.length} selected`}</strong>
            <span>
              <button type="button" className="button-link" onClick={() => setSelected(new Set(results.map((result) => result.media_id)))}>Select all</button>
              <button type="button" className="button-link" onClick={() => setSelected(new Set())}>Clear selection</button>
            </span>
          </div>
          <ul className="management-list" aria-label="Matching media">
            {results.map((result) => {
              const name = resultName(result);
              return (
                <li className={selected.has(result.media_id) ? "selected" : ""} key={result.media_id}>
                  {result.thumbnail_url ? <img src={result.thumbnail_url} alt="" /> : <span className="management-placeholder" aria-hidden="true">◇</span>}
                  <div className="management-record">
                    <label><input type="checkbox" checked={selected.has(result.media_id)} onChange={() => toggle(result.media_id)} />{`Select ${name}`}</label>
                    <a href={result.original_url} target="_blank" rel="noreferrer">{`Open ${name}`}</a>
                    <div className="tag-list">
                      {Object.entries(result.tag_counts).map(([tag, count]) => <span className="tag-chip" key={`detected-${tag}`}>{`${tag}: ${count}`}</span>)}
                      {(result.manual_tags ?? []).map((tag) => <span className="tag-chip tag-chip-manual" key={`manual-${tag}`}>{`${tag} · manual`}</span>)}
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      <div className="bulk-actions">
        <label htmlFor="management-tags">Tags</label>
        <input id="management-tags" value={tags} onChange={(event) => setTags(event.target.value)} placeholder="dingo, night" />
        <div className="action-row">
          <button type="button" disabled={busy || selectedUrls.length === 0} onClick={() => void update(1)}>Add tags</button>
          <button className="secondary" type="button" disabled={busy || selectedUrls.length === 0} onClick={() => void update(0)}>Remove tags</button>
          <button className="button-danger" type="button" disabled={busy || selectedUrls.length === 0} onClick={() => setConfirmingDelete(true)}>Delete selected</button>
        </div>
      </div>

      {confirmingDelete && (
        <div role="dialog" aria-label="Confirm deletion" aria-modal="true">
          <p>Delete {selectedUrls.length} selected item(s)? This cannot be undone.</p>
          <div className="dialog-actions"><button className="secondary" type="button" onClick={() => setConfirmingDelete(false)}>Cancel</button><button className="button-danger" type="button" disabled={busy} onClick={() => void confirmDelete()}>Confirm delete</button></div>
        </div>
      )}
    </section>
  );
}
