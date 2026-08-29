import type { ReactNode } from "react";

export type IconName =
  | "image"
  | "video"
  | "view"
  | "delete"
  | "refresh"
  | "search"
  | "tag"
  | "add"
  | "remove"
  | "clear"
  | "previous"
  | "next"
  | "upload"
  | "library"
  | "manage"
  | "bell"
  | "logout"
  | "edit"
  | "filter";

const paths: Record<IconName, ReactNode> = {
  image: <><rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="8.5" cy="10" r="1.5"/><path d="m21 15-5-5L5 19"/></>,
  video: <><rect x="3" y="6" width="14" height="12" rx="2"/><path d="m17 10 4-2v8l-4-2z"/></>,
  view: <><path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z"/><circle cx="12" cy="12" r="2.5"/></>,
  delete: <><path d="M4 7h16M9 7V4h6v3m3 0-1 14H7L6 7m4 4v6m4-6v6"/></>,
  refresh: <><path d="M20 6v5h-5"/><path d="M19 11a7 7 0 1 0 1 5"/></>,
  search: <><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></>,
  tag: <><path d="M20 13 13 20l-9-9V4h7z"/><circle cx="8.5" cy="8.5" r="1"/></>,
  add: <path d="M12 5v14M5 12h14"/>,
  remove: <path d="M5 12h14"/>,
  clear: <path d="m6 6 12 12M18 6 6 18"/>,
  previous: <path d="m15 18-6-6 6-6"/>,
  next: <path d="m9 18 6-6-6-6"/>,
  upload: <><path d="M12 16V4m0 0L7 9m5-5 5 5"/><path d="M5 14v5h14v-5"/></>,
  library: <><path d="M4 5h6v14H4zM14 5h6v14h-6z"/><path d="M7 9h0m10 0h0"/></>,
  manage: <><path d="M4 7h10M18 7h2M4 17h2m4 0h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/></>,
  bell: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"/><path d="M10 21h4"/></>,
  logout: <><path d="M10 5H5v14h5"/><path d="m14 8 4 4-4 4m-6-4h10"/></>,
  edit: <><path d="m4 20 4-1 11-11-3-3L5 16z"/><path d="m14 7 3 3"/></>,
  filter: <path d="M4 6h16l-6 7v5l-4 2v-7z"/>,
};

export function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
  return (
    <svg
      aria-hidden="true"
      className="ui-icon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {paths[name]}
    </svg>
  );
}
