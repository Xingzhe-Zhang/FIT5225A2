import { useEffect, useMemo, useRef } from "react";

import { Icon } from "../ui/Icon";
import type { LocalMediaPreview, MediaResult } from "./MediaGallery";
import { MediaThumbnail } from "./MediaThumbnail";

const DEFAULT_PAGE_SIZE = 10;

export function displaySpecies(value: string): string {
  return value.replaceAll("_", " ");
}

export function mediaFileName(media: MediaResult, localPreview?: LocalMediaPreview): string {
  if (localPreview?.file_name) return localPreview.file_name;
  if (media.file_name) return media.file_name;
  if (media.original_url) {
    try {
      const path = new URL(media.original_url).pathname;
      const name = decodeURIComponent(path.split("/").at(-1) ?? "");
      if (name) return name;
    } catch {
      // A malformed signed URL should not prevent the row rendering.
    }
  }
  return `${media.media_type === "image" ? "Image" : "Video"} ${media.media_id.slice(0, 8)}`;
}

export function mediaStatusLabel(status: MediaResult["status"]): string {
  if (status === "ready") return "Ready";
  if (status === "failed") return "Failed";
  if (status === "prepared") return "Detecting species";
  if (status === "deleting") return "Deleting";
  return "Processing";
}

function mediaStatusDescription(status: MediaResult["status"]): string {
  if (status === "ready") return "Analysis complete";
  if (status === "prepared") return "Detecting species";
  if (status === "failed") return "Processing failed";
  if (status === "deleting") return "Deletion in progress";
  return "Preparing preview";
}

function failureSummary(code?: string | null): string {
  if (code?.startsWith("TAGGING_")) return "Species detection failed.";
  if (code?.startsWith("IMAGE_")) return "Image processing failed.";
  if (code?.startsWith("VIDEO_")) return "Video processing failed.";
  return "Media processing failed.";
}

function SelectPageCheckbox({
  checked,
  indeterminate,
  onChange,
}: {
  checked: boolean;
  indeterminate: boolean;
  onChange(checked: boolean): void;
}) {
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (input.current) input.current.indeterminate = indeterminate;
  }, [indeterminate]);
  return (
    <input
      ref={input}
      type="checkbox"
      checked={checked}
      aria-label="Select all visible media"
      onChange={(event) => onChange(event.target.checked)}
    />
  );
}

function CompactTags({
  entries,
  manual = false,
  empty,
}: {
  entries: Array<[string, number | null]>;
  manual?: boolean;
  empty: string;
}) {
  if (entries.length === 0) return <span className="table-empty-value">{empty}</span>;
  const visible = entries.slice(0, 3);
  const hidden = entries.slice(3);
  return (
    <div className="table-tag-list">
      {visible.map(([tag, count]) => (
        <span className={`tag-chip${manual ? " tag-chip-manual" : ""}`} key={`${manual ? "manual" : "detected"}-${tag}`}>
          {displaySpecies(tag)}{count === null ? "" : ` × ${count}`}
        </span>
      ))}
      {hidden.length > 0 && (
        <details className="table-popover">
          <summary aria-label={`Show ${hidden.length} more tags`}>+{hidden.length} more</summary>
          <div className="table-popover-panel">
            {entries.map(([tag, count]) => (
              <span className={`tag-chip${manual ? " tag-chip-manual" : ""}`} key={`all-${tag}`}>
                {displaySpecies(tag)}{count === null ? "" : ` × ${count}`}
              </span>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

export function MediaTable({
  items,
  label,
  page,
  onPageChange,
  selected,
  onToggle,
  onTogglePage,
  onDelete,
  localPreviews = {},
  disabled = false,
  pageSize = DEFAULT_PAGE_SIZE,
}: {
  items: MediaResult[];
  label: string;
  page: number;
  onPageChange(page: number): void;
  selected: Set<string>;
  onToggle(mediaId: string): void;
  onTogglePage(mediaIds: string[], checked: boolean): void;
  onDelete?(media: MediaResult): void;
  localPreviews?: Record<string, LocalMediaPreview>;
  disabled?: boolean;
  pageSize?: number;
}) {
  const pageCount = Math.max(1, Math.ceil(items.length / pageSize));
  const safePage = Math.min(Math.max(page, 1), pageCount);
  const start = (safePage - 1) * pageSize;
  const pageItems = useMemo(() => items.slice(start, start + pageSize), [items, pageSize, start]);
  const pageIds = pageItems.map((item) => item.media_id);
  const selectedOnPage = pageIds.filter((id) => selected.has(id)).length;

  useEffect(() => {
    if (page !== safePage) onPageChange(safePage);
  }, [onPageChange, page, safePage]);

  return (
    <div className="media-table-region">
      <div className="media-table-scroll">
        <table className="media-table" aria-label={label}>
          <thead>
            <tr>
              <th className="media-table-select" scope="col">
                <SelectPageCheckbox
                  checked={pageItems.length > 0 && selectedOnPage === pageItems.length}
                  indeterminate={selectedOnPage > 0 && selectedOnPage < pageItems.length}
                  onChange={(checked) => onTogglePage(pageIds, checked)}
                />
              </th>
              <th scope="col">Thumbnail</th>
              <th scope="col">Media</th>
              <th scope="col">Type</th>
              <th scope="col">Status</th>
              <th scope="col">Detected species</th>
              <th scope="col">Manual tags</th>
              <th className="media-table-actions-heading" scope="col">Actions</th>
            </tr>
          </thead>
          <tbody>
            {pageItems.map((media) => {
              const local = localPreviews[media.media_id];
              const name = mediaFileName(media, local);
              return (
                <tr className={selected.has(media.media_id) ? "selected" : ""} key={media.media_id}>
                  <td className="media-table-select">
                    <input
                      type="checkbox"
                      checked={selected.has(media.media_id)}
                      aria-label={`Select ${name}`}
                      onChange={() => onToggle(media.media_id)}
                    />
                  </td>
                  <td className="media-table-thumbnail">
                    <MediaThumbnail media={media} name={name} localUrl={local?.url} />
                  </td>
                  <td className="media-table-name">
                    <strong title={name}>{name}</strong>
                    <span title={media.media_id}>ID: {media.media_id.slice(0, 8)}</span>
                  </td>
                  <td>
                    <span className="media-type-label"><Icon name={media.media_type} size={16} />{media.media_type === "image" ? "Image" : "Video"}</span>
                  </td>
                  <td className="media-table-status">
                    <span className={`table-status-chip status-${media.status}`}>{mediaStatusLabel(media.status)}</span>
                    <span className="table-status-description">{mediaStatusDescription(media.status)}</span>
                    {media.status === "failed" && (
                      <div className="table-failure-copy">
                        <span>{failureSummary(media.failure_code)}</span>
                        {media.failure_code && <code>{media.failure_code}</code>}
                        {media.failure_message && (
                          <details className="table-popover technical-popover">
                            <summary>View technical details</summary>
                            <div className="table-popover-panel"><p>{media.failure_message}</p></div>
                          </details>
                        )}
                      </div>
                    )}
                  </td>
                  <td><CompactTags entries={Object.entries(media.tag_counts)} empty="None detected" /></td>
                  <td><CompactTags entries={(media.manual_tags ?? []).map((tag) => [tag, null])} manual empty="None" /></td>
                  <td className="media-table-actions">
                    {media.thumbnail_url && (
                      <a
                        className="button button-secondary icon-button"
                        href={media.thumbnail_url}
                        target="_blank"
                        rel="noreferrer"
                        aria-label="Open thumbnail URL"
                        title="Open thumbnail URL"
                      >
                        <Icon name="image" />
                      </a>
                    )}
                    {media.original_url && (
                      <a className="button button-secondary icon-label" href={media.original_url} target="_blank" rel="noreferrer">
                        <Icon name="view" />View
                      </a>
                    )}
                    {onDelete && (
                      <button
                        type="button"
                        className="button-danger-subtle icon-button"
                        aria-label="Delete"
                        title={`Delete ${name}`}
                        disabled={disabled}
                        onClick={() => onDelete(media)}
                      >
                        <Icon name="delete" />
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="media-table-pagination" aria-label={`${label} pagination`}>
        <span>{items.length === 0 ? "0 of 0" : `${start + 1}–${Math.min(start + pageSize, items.length)} of ${items.length}`}</span>
        {pageCount > 1 && (
          <div>
            <button type="button" className="icon-button" aria-label="Previous page" title="Previous page" disabled={safePage === 1} onClick={() => onPageChange(safePage - 1)}><Icon name="previous" /></button>
            <span>Page {safePage} of {pageCount}</span>
            <button type="button" className="icon-button" aria-label="Next page" title="Next page" disabled={safePage === pageCount} onClick={() => onPageChange(safePage + 1)}><Icon name="next" /></button>
          </div>
        )}
      </div>
    </div>
  );
}
