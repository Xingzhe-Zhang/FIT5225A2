/** Per-record result returned by the bulk manual-tag endpoint. */
export type TagUpdateStatus = "invalid_url" | "not_found" | "conflict" | "unchanged" | "updated";

export interface TagUpdateOutcome {
  url: string;
  media_id: string | null;
  status: TagUpdateStatus;
  /** Some deployments include an explanatory error for a failed item. */
  error?: string | null;
}

export interface TagUpdateResponse {
  results: TagUpdateOutcome[];
}

export type MediaDeleteStatus = "invalid_url" | "not_found" | "failed" | "deleted";

/** Per-record result returned by either media deletion endpoint. */
export interface MediaDeleteOutcome {
  url?: string;
  media_id: string | null;
  operation_id?: string | null;
  status: MediaDeleteStatus;
  error: string | null;
}

export interface BulkDeleteResponse {
  results: MediaDeleteOutcome[];
}

export interface SingleDeleteResponse {
  result: MediaDeleteOutcome;
}
