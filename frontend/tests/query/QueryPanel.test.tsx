import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { AuthContext, type AuthContextValue } from "../../src/auth/AuthContext";
import {
  QueryPanel,
  type QueryClient,
  type QueryResponse,
} from "../../src/query/QueryPanel";

const authenticated: AuthContextValue = {
  status: "authenticated",
  config: null,
  accessToken: "access-token",
  login: vi.fn(),
  signup: vi.fn(),
  confirmRegistration: vi.fn(),
  resendRegistration: vi.fn(),
  localLogin: vi.fn(),
  completeCallback: vi.fn(),
  logout: vi.fn(),
};

afterEach(cleanup);

function renderPanel(
  search: QueryClient["search"] = vi.fn().mockResolvedValue({ results: [] }),
  overrides: Record<string, unknown> = {},
  onLibraryChanged?: () => Promise<void>,
) {
  const client = {
    search,
    updateTags: vi.fn().mockResolvedValue({ results: [] }),
    deleteMedia: vi.fn().mockResolvedValue({ results: [] }),
    deleteMediaById: vi.fn().mockResolvedValue({
      result: { media_id: null, status: "deleted", error: null },
    }),
    ...overrides,
  };
  render(
    <AuthContext.Provider value={authenticated}>
      <QueryPanel client={client} onLibraryChanged={onLibraryChanged} />
    </AuthContext.Provider>,
  );
  return client;
}

test("deletes selected query results and retains partial failures", async () => {
  const first: QueryResponse["results"][number] = {
    media_id: "delete-first",
    media_type: "image",
    status: "ready",
    original_url: "https://signed.example.test/originals/delete-first.jpg",
    thumbnail_url: null,
    tag_counts: { dingo: 1 },
  };
  const second: QueryResponse["results"][number] = {
    ...first,
    media_id: "delete-second",
    original_url: "https://signed.example.test/originals/delete-second.jpg",
  };
  const deleteMedia = vi.fn().mockResolvedValue({
    results: [
      { media_id: first.media_id, url: first.original_url, status: "deleted", error: null },
      { media_id: second.media_id, url: second.original_url, status: "failed", error: "storage timeout" },
    ],
  });
  const onLibraryChanged = vi.fn().mockResolvedValue(undefined);
  renderPanel(
    vi.fn().mockResolvedValue({ results: [first, second] }),
    { deleteMedia },
    onLibraryChanged,
  );

  fireEvent.change(screen.getByLabelText("Species 1"), { target: { value: "dingo" } });
  fireEvent.click(screen.getByRole("button", { name: "Search" }));
  fireEvent.click(await screen.findByRole("checkbox", { name: "Select all visible media" }));
  fireEvent.click(screen.getByRole("button", { name: "Delete selected" }));
  expect(screen.getByRole("dialog", { name: "Confirm deletion" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

  await waitFor(() => expect(deleteMedia).toHaveBeenCalledWith(
    [first.original_url, second.original_url],
    "access-token",
  ));
  expect(screen.queryByText("delete-first.jpg")).not.toBeInTheDocument();
  expect(screen.getByText("delete-second.jpg")).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("storage timeout");
  expect(onLibraryChanged).toHaveBeenCalledTimes(1);
});

test("applies manual tags to selected query results", async () => {
  const first: QueryResponse["results"][number] = {
    media_id: "query-first",
    media_type: "image",
    status: "ready",
    original_url: "https://signed.example.test/originals/first.jpg",
    thumbnail_url: null,
    tag_counts: { dingo: 1 },
    manual_tags: [],
  };
  const second: QueryResponse["results"][number] = {
    ...first,
    media_id: "query-second",
    original_url: "https://signed.example.test/originals/second.jpg",
  };
  const updateTags = vi.fn().mockResolvedValue({
    results: [
      { media_id: first.media_id, url: first.original_url, status: "updated" },
      { media_id: second.media_id, url: second.original_url, status: "updated" },
    ],
  });
  renderPanel(vi.fn().mockResolvedValue({ results: [first, second] }), { updateTags });

  fireEvent.change(screen.getByLabelText("Species 1"), { target: { value: "dingo" } });
  fireEvent.click(screen.getByRole("button", { name: "Search" }));
  fireEvent.click(await screen.findByRole("checkbox", { name: "Select all visible media" }));
  fireEvent.change(screen.getByLabelText("Query result tags"), {
    target: { value: "Batch Demo, night" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Add tags" }));

  await waitFor(() => expect(updateTags).toHaveBeenCalledWith(
    [first.original_url, second.original_url],
    ["batch demo", "night"],
    1,
    "access-token",
  ));
  expect(screen.getAllByText("batch demo")).toHaveLength(2);

  fireEvent.click(screen.getByRole("button", { name: "Remove tags" }));
  await waitFor(() => expect(updateTags).toHaveBeenLastCalledWith(
    [first.original_url, second.original_url],
    ["batch demo", "night"],
    0,
    "access-token",
  ));
  expect(screen.queryByText("batch demo")).not.toBeInTheDocument();
});

test("submits every tag row as an AND minimum-count query", async () => {
  const search = vi.fn().mockResolvedValue({ results: [] });
  renderPanel(search);

  fireEvent.change(screen.getByLabelText("Species 1"), { target: { value: "dingo" } });
  fireEvent.change(screen.getByLabelText("Minimum count 1"), { target: { value: "2" } });
  fireEvent.click(screen.getByRole("button", { name: "Add tag" }));
  fireEvent.change(screen.getByLabelText("Species 2"), { target: { value: "wombat" } });
  fireEvent.change(screen.getByLabelText("Minimum count 2"), { target: { value: "1" } });
  fireEvent.click(screen.getByRole("button", { name: "Search" }));

  await waitFor(() =>
    expect(search).toHaveBeenCalledWith("tags", { dingo: 2, wombat: 1 }, "access-token"),
  );
  expect(screen.getByText("No matching media found." )).toBeInTheDocument();
});

test("switches between species and thumbnail query payloads", async () => {
  const search = vi.fn().mockResolvedValue({ results: [] });
  renderPanel(search);

  fireEvent.click(screen.getByRole("radio", { name: "Species" }));
  fireEvent.change(screen.getByLabelText("Species name"), { target: { value: "Dingo" } });
  fireEvent.click(screen.getByRole("button", { name: "Search" }));
  await waitFor(() =>
    expect(search).toHaveBeenCalledWith("species", { species: "Dingo" }, "access-token"),
  );

  fireEvent.click(screen.getByRole("radio", { name: "Thumbnail URL" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Thumbnail URL" }), {
    target: { value: "https://media.example.test/derived/1/thumbnail.jpg?signature=one" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Search" }));
  await waitFor(() =>
    expect(search).toHaveBeenCalledWith(
      "thumbnail",
      { thumbnail_url: "https://media.example.test/derived/1/thumbnail.jpg?signature=one" },
      "access-token",
    ),
  );
});

test("rejects an original media URL before running a thumbnail query", async () => {
  const search = vi.fn().mockResolvedValue({ results: [] });
  renderPanel(search);

  fireEvent.click(screen.getByRole("radio", { name: "Thumbnail URL" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Thumbnail URL" }), {
    target: { value: "https://media.example.test/originals/1/camera.jpg" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Search" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("/derived/");
  expect(search).not.toHaveBeenCalled();
});

test("treats spaces and underscores as equivalent species separators", async () => {
  const search = vi.fn().mockResolvedValue({ results: [] });
  renderPanel(search);

  fireEvent.change(screen.getByLabelText("Species 1"), { target: { value: "Alectura lathami" } });
  fireEvent.click(screen.getByRole("button", { name: "Search" }));
  await waitFor(() => expect(search).toHaveBeenCalledWith(
    "tags",
    { alectura_lathami: 1 },
    "access-token",
  ));

  fireEvent.click(screen.getByRole("radio", { name: "Species" }));
  fireEvent.change(screen.getByLabelText("Species name"), { target: { value: "Casuarius casuarius" } });
  fireEvent.click(screen.getByRole("button", { name: "Search" }));
  await waitFor(() => expect(search).toHaveBeenCalledWith(
    "species",
    { species: "Casuarius_casuarius" },
    "access-token",
  ));
});

test("renders image previews, video links, and request errors", async () => {
  const response: QueryResponse = {
    results: [
      {
        media_id: "image-1",
        media_type: "image",
        status: "ready",
        original_url: "https://signed.example.test/original.jpg",
        thumbnail_url: "https://signed.example.test/thumbnail.jpg",
        tag_counts: { dingo: 2 },
        manual_tags: ["night"],
      },
      {
        media_id: "video-1",
        media_type: "video",
        status: "ready",
        original_url: "https://signed.example.test/video.mp4",
        thumbnail_url: null,
        tag_counts: { wombat: 1 },
      },
    ],
  };
  const search = vi
    .fn()
    .mockResolvedValueOnce(response)
    .mockRejectedValueOnce(new Error("Query service unavailable"));
  renderPanel(search);

  fireEvent.change(screen.getByLabelText("Species 1"), { target: { value: "dingo" } });
  fireEvent.click(screen.getByRole("button", { name: "Search" }));
  expect(await screen.findByAltText("original.jpg thumbnail")).toHaveAttribute(
    "src",
    response.results[0].thumbnail_url,
  );
  expect(screen.getByRole("link", { name: "View original original.jpg" })).toHaveAttribute(
    "href",
    response.results[0].original_url,
  );
  expect(screen.getByRole("link", { name: "Open thumbnail URL" })).toHaveAttribute(
    "href",
    response.results[0].thumbnail_url,
  );
  expect(screen.getAllByRole("link", { name: "View" }).some((link) =>
    link.getAttribute("href") === response.results[1].original_url,
  )).toBe(true);
  expect(screen.getByRole("status")).toHaveTextContent("2 matches");
  expect(screen.getByText("dingo × 2")).toBeInTheDocument();
  expect(screen.getByText("night")).toBeInTheDocument();
  expect(screen.getByText("wombat × 1")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Search" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Query service unavailable");
});

test("uses the original as a processing thumbnail and renders a consistent failed placeholder", async () => {
  const search = vi.fn().mockResolvedValue({
    results: [
      {
        media_id: "processing-image",
        media_type: "image",
        status: "processing",
        original_url: "https://signed.example.test/Casuarius_casuarius.JPG",
        thumbnail_url: null,
        tag_counts: {},
      },
      {
        media_id: "failed-image",
        media_type: "image",
        status: "failed",
        original_url: null,
        thumbnail_url: null,
        tag_counts: {},
        failure_code: "TAGGING_INPUT_INVALID",
        failure_message: "Internal path /tmp/model-cache must stay hidden until expanded.",
      },
    ],
  });
  renderPanel(search);

  fireEvent.change(screen.getByLabelText("Species 1"), { target: { value: "cassowary" } });
  fireEvent.click(screen.getByRole("button", { name: "Search" }));

  expect(await screen.findByAltText("Casuarius_casuarius.JPG thumbnail")).toHaveAttribute(
    "src",
    "https://signed.example.test/Casuarius_casuarius.JPG",
  );
  expect(screen.getByText("Processing")).toBeInTheDocument();
  expect(screen.getByLabelText("Preview unavailable failed-image")).toBeInTheDocument();
  expect(screen.getByText("Species detection failed.")).toBeInTheDocument();
  expect(screen.getByText("TAGGING_INPUT_INVALID")).toBeInTheDocument();
  expect(screen.getByText("View technical details")).toBeInTheDocument();
});

test("paginates query results and resets to page one when media type changes", async () => {
  const results: QueryResponse["results"] = Array.from({ length: 11 }, (_, index) => ({
    media_id: `query-${index}`,
    media_type: index === 10 ? "video" : "image",
    status: "ready",
    original_url: `https://signed.example.test/query-${index}.${index === 10 ? "mp4" : "jpg"}`,
    thumbnail_url: null,
    tag_counts: {},
  }));
  renderPanel(vi.fn().mockResolvedValue({ results }));
  fireEvent.change(screen.getByLabelText("Species 1"), { target: { value: "dingo" } });
  fireEvent.click(screen.getByRole("button", { name: "Search" }));

  expect(await screen.findByText("1–10 of 11")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  expect(await screen.findByText("11–11 of 11")).toBeInTheDocument();
  expect(screen.getByText("query-10.mp4")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Videos 1" }));
  expect(await screen.findByText("1–1 of 1")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Next page" })).not.toBeInTheDocument();
});
