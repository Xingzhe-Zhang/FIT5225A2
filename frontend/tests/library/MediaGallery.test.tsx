import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { AuthContext, type AuthContextValue } from "../../src/auth/AuthContext";
import { MediaGallery, type MediaLibraryClient, type MediaResult } from "../../src/library/MediaGallery";

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

function client(results: MediaResult[], overrides: Partial<MediaLibraryClient> = {}): MediaLibraryClient {
  return {
    list: vi.fn().mockResolvedValue({ results }),
    updateTags: vi.fn().mockResolvedValue({ results: [] }),
    deleteMedia: vi.fn().mockResolvedValue({ results: [] }),
    deleteMediaById: vi.fn().mockResolvedValue({ result: { media_id: null, status: "deleted", error: null } }),
    ...overrides,
  };
}

function renderGallery(api: MediaLibraryClient, props: Record<string, unknown> = {}) {
  render(
    <AuthContext.Provider value={authenticated}>
      <MediaGallery client={api} {...props} />
    </AuthContext.Provider>,
  );
}

test("renders consistent previews, filenames, readable species and status filters", async () => {
  const results: MediaResult[] = [
    {
      media_id: "image-1",
      media_type: "image",
      status: "ready",
      original_url: "https://downloads.example.test/originals/camera.jpg",
      thumbnail_url: "https://downloads.example.test/derived/camera.jpg",
      tag_counts: { Casuarius_casuarius: 2 },
      manual_tags: ["night_camera"],
    },
    {
      media_id: "video-1",
      media_type: "video",
      status: "prepared",
      original_url: "https://downloads.example.test/originals/clip.mp4",
      thumbnail_url: "https://downloads.example.test/derived/clip.jpg",
      tag_counts: {},
    },
    {
      media_id: "processing-1",
      media_type: "image",
      status: "processing",
      original_url: null,
      thumbnail_url: null,
      tag_counts: {},
    },
  ];
  const api = client(results);

  renderGallery(api, {
    localPreviews: { "processing-1": { file_name: "new-field-image.jpg", url: "blob:local-preview" } },
  });

  expect(await screen.findByAltText("camera.jpg thumbnail")).toHaveAttribute("src", "https://downloads.example.test/derived/camera.jpg");
  expect(screen.getByAltText("new-field-image.jpg thumbnail")).toHaveAttribute("src", "blob:local-preview");
  expect(screen.getByLabelText("clip.mp4 preview")).toHaveAttribute("poster", "https://downloads.example.test/derived/clip.jpg");
  expect(screen.getByText("Casuarius casuarius × 2")).toBeInTheDocument();
  expect(screen.getByText("night camera")).toBeInTheDocument();
  expect(screen.getByText("Analysis complete")).toBeInTheDocument();
  expect(screen.getAllByText("Detecting species").length).toBeGreaterThan(0);
  expect(screen.getByText("Preparing preview")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Failed 0" }));
  expect(screen.getByText("No media matches these filters.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Any status" }));
  fireEvent.click(screen.getByRole("button", { name: "Videos 1" }));
  expect(screen.queryByAltText("camera.jpg thumbnail")).not.toBeInTheDocument();
  expect(screen.getByLabelText("clip.mp4 preview")).toBeInTheDocument();
});

test("keeps failed details collapsed and uses a calm summary with the error code", async () => {
  const failed: MediaResult = {
    media_id: "failed-1",
    file_name: "failed-field-image.jpg",
    media_type: "image",
    status: "failed",
    original_url: null,
    thumbnail_url: null,
    tag_counts: {},
    failure_code: "TAGGING_INPUT_INVALID",
    failure_message: "Unrecognized model at /tmp/private-cache and s3://internal/key",
  };
  const deleteMediaById = vi.fn().mockResolvedValue({
    result: { media_id: failed.media_id, status: "failed", error: "processing record is locked" },
  });
  renderGallery(client([failed], { deleteMediaById }));

  expect(await screen.findByText("failed-field-image.jpg")).toBeInTheDocument();
  expect(screen.getByLabelText("Preview unavailable failed-1")).toBeInTheDocument();
  expect(screen.getByText("Species detection failed.")).toBeInTheDocument();
  expect(screen.getByText("TAGGING_INPUT_INVALID")).toBeInTheDocument();
  const details = screen.getByText("View technical details").closest("details");
  expect(details).not.toHaveAttribute("open");
  expect(within(details!).getByText(/\/tmp\/private-cache/)).toBeInTheDocument();
  expect(screen.queryByText("Preview is being prepared")).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
  await waitFor(() => expect(deleteMediaById).toHaveBeenCalledWith("failed-1", "access-token"));
  expect(await screen.findByRole("alert")).toHaveTextContent("processing record is locked");
});

test("supports bulk tag editing and deletion with per-item outcomes", async () => {
  const first: MediaResult = {
    media_id: "first-ready",
    media_type: "image",
    status: "ready",
    original_url: "https://downloads.example.test/originals/first.jpg",
    thumbnail_url: null,
    tag_counts: {},
  };
  const second: MediaResult = {
    ...first,
    media_id: "second-ready",
    original_url: "https://downloads.example.test/originals/second.jpg",
  };
  const updateTags = vi.fn().mockResolvedValue({
    results: [
      { media_id: first.media_id, url: first.original_url, status: "updated" },
      { media_id: second.media_id, url: second.original_url, status: "updated" },
    ],
  });
  const deleteMedia = vi.fn().mockResolvedValue({
    results: [
      { media_id: first.media_id, url: first.original_url, status: "deleted", error: null },
      { media_id: second.media_id, url: second.original_url, status: "deleted", error: null },
    ],
  });
  renderGallery(client([first, second], { updateTags, deleteMedia }));

  await screen.findByText("first.jpg");
  fireEvent.click(screen.getByRole("checkbox", { name: "Select all visible media" }));
  expect(screen.getByText("2 selected")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Tags"), { target: { value: "Night, field_note" } });
  fireEvent.click(screen.getByRole("button", { name: "Add tags" }));
  await waitFor(() => expect(updateTags).toHaveBeenCalledWith(
    [first.original_url, second.original_url], ["night", "field_note"], 1, "access-token",
  ));
  expect(screen.getAllByText("field note")).toHaveLength(2);

  fireEvent.click(screen.getByRole("button", { name: "Delete selected" }));
  expect(screen.getByRole("dialog", { name: "Confirm deletion" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
  await waitFor(() => expect(deleteMedia).toHaveBeenCalledWith(
    [first.original_url, second.original_url], "access-token",
  ));
  expect(screen.getByRole("status")).toHaveTextContent("2 selected item(s) deleted");
});

test("keeps polling while media is prepared", async () => {
  let poll: (() => void) | undefined;
  const interval = vi.spyOn(window, "setInterval").mockImplementation((handler) => {
    poll = handler as () => void;
    return 123;
  });
  const prepared: MediaResult = {
    media_id: "prepared-1",
    media_type: "image",
    status: "prepared",
    original_url: "https://downloads.example.test/prepared.jpg",
    thumbnail_url: "https://downloads.example.test/prepared-thumb.jpg",
    tag_counts: {},
  };
  const list = vi.fn()
    .mockResolvedValueOnce({ results: [prepared] })
    .mockImplementationOnce(() => new Promise(() => undefined));
  renderGallery(client([prepared], { list }));

  await screen.findByText("prepared.jpg");
  expect(interval).toHaveBeenCalled();
  act(() => poll?.());
  expect(screen.queryByText("Updating library…")).not.toBeInTheDocument();
  interval.mockRestore();
});

test("removes a card only after the single-delete outcome is deleted", async () => {
  const media: MediaResult = {
    media_id: "ready-1",
    media_type: "image",
    status: "ready",
    original_url: "https://downloads.example.test/ready.jpg",
    thumbnail_url: null,
    tag_counts: {},
  };
  const deleteMediaById = vi.fn().mockResolvedValue({
    result: { media_id: media.media_id, status: "deleted", error: null },
  });
  renderGallery(client([media], { deleteMediaById }));
  await screen.findByText("ready.jpg");
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

  await waitFor(() => expect(screen.queryByText("ready.jpg")).not.toBeInTheDocument());
  expect(screen.getByRole("status")).toHaveTextContent("Media deleted.");
});

test("paginates ten rows, truncates manual tags, and resets pagination after filtering", async () => {
  const results: MediaResult[] = Array.from({ length: 12 }, (_, index) => ({
    media_id: `media-${index}`,
    media_type: index === 11 ? "video" : "image",
    status: "ready",
    original_url: `https://downloads.example.test/originals/file-${index}.${index === 11 ? "mp4" : "jpg"}`,
    thumbnail_url: null,
    tag_counts: {},
    manual_tags: index === 0 ? ["one", "two", "three", "four", "five"] : [],
  }));
  renderGallery(client(results));

  expect(await screen.findByText("1–10 of 12")).toBeInTheDocument();
  expect(screen.getByText("+3 more")).toBeInTheDocument();
  expect(screen.queryByText("file-10.jpg")).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  expect(await screen.findByText("11–12 of 12")).toBeInTheDocument();
  expect(screen.getByText("file-10.jpg")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Videos 1" }));
  expect(await screen.findByText("1–1 of 1")).toBeInTheDocument();
  expect(screen.getByText("file-11.mp4")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Next page" })).not.toBeInTheDocument();
});
