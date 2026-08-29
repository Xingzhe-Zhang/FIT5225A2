import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { AuthContext, type AuthContextValue } from "../../src/auth/AuthContext";
import { MediaGallery } from "../../src/library/MediaGallery";

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

test("renders image thumbnail links and video poster cards from signed media results", async () => {
  const list = vi.fn().mockResolvedValue({
    results: [
      {
        media_id: "image-1",
        media_type: "image",
        status: "ready",
        original_url: "https://downloads.example.test/originals/camera.jpg",
        thumbnail_url: "https://downloads.example.test/derived/camera.jpg",
        tag_counts: { dingo: 2 },
        manual_tags: ["night"],
      },
      {
        media_id: "video-1",
        media_type: "video",
        status: "prepared",
        original_url: "https://downloads.example.test/originals/clip.mp4",
        thumbnail_url: "https://downloads.example.test/derived/clip.jpg",
        tag_counts: { wombat: 1 },
      },
      {
        media_id: "processing-1",
        media_type: "image",
        status: "processing",
        original_url: null,
        thumbnail_url: null,
        tag_counts: {},
      },
    ],
  });
  const deleteMediaById = vi.fn();

  render(
    <AuthContext.Provider value={authenticated}>
      <MediaGallery client={{ list, deleteMediaById }} />
    </AuthContext.Provider>,
  );

  expect(screen.getByRole("status")).toHaveTextContent("Loading media");
  expect(await screen.findByAltText("Image media thumbnail")).toHaveAttribute(
    "src",
    "https://downloads.example.test/derived/camera.jpg",
  );
  expect(screen.getByRole("link", { name: "Open image original" })).toHaveAttribute(
    "href",
    "https://downloads.example.test/originals/camera.jpg",
  );
  expect(screen.getByLabelText("Video media preview")).toHaveAttribute(
    "poster",
    "https://downloads.example.test/derived/clip.jpg",
  );
  expect(screen.getByRole("link", { name: "Open video original" })).toHaveAttribute(
    "href",
    "https://downloads.example.test/originals/clip.mp4",
  );
  expect(screen.getByText("Processing")).toBeInTheDocument();
  expect(screen.getByText("dingo × 2")).toBeInTheDocument();
  expect(screen.getByText("night · manual")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "All 3" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Videos 1" }));
  expect(screen.queryByAltText("Image media thumbnail")).not.toBeInTheDocument();
  expect(screen.getByLabelText("Video media preview")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Refresh library" }));
  await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
  expect(screen.queryByRole("link", { name: "Open image original processing-1" })).not.toBeInTheDocument();
  expect(list).toHaveBeenLastCalledWith("access-token");
});

test("confirms card deletion and keeps a failed item with the API error", async () => {
  const failed = {
    media_id: "failed-1",
    media_type: "image" as const,
    status: "failed" as const,
    original_url: null,
    thumbnail_url: null,
    tag_counts: {},
    failure_code: "IMAGE_CORRUPT",
    failure_message: "Image could not be decoded",
  };
  const list = vi.fn().mockResolvedValue({ results: [failed] });
  const deleteMediaById = vi.fn().mockResolvedValue({
    result: { media_id: failed.media_id, status: "failed", error: "processing record is locked" },
  });

  render(
    <AuthContext.Provider value={authenticated}>
      <MediaGallery client={{ list, deleteMediaById }} />
    </AuthContext.Provider>,
  );

  await screen.findByText("Failed");
  expect(screen.getByText("Processing failed")).toBeInTheDocument();
  expect(screen.getByText("Image could not be decoded")).toBeInTheDocument();
  expect(screen.getByText("IMAGE_CORRUPT")).toBeInTheDocument();
  expect(screen.queryByText("Preview is being prepared")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Delete image" }));
  expect(screen.getByRole("dialog", { name: "Confirm deletion" })).toBeInTheDocument();
  expect(deleteMediaById).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

  await waitFor(() => expect(deleteMediaById).toHaveBeenCalledWith("failed-1", "access-token"));
  expect(await screen.findByRole("alert")).toHaveTextContent("processing record is locked");
  expect(screen.getByText("Failed")).toBeInTheDocument();
});

test("keeps polling while media is prepared but tagging is not ready", async () => {
  const interval = vi.spyOn(window, "setInterval");
  const prepared = {
    media_id: "prepared-1",
    media_type: "image" as const,
    status: "prepared" as const,
    original_url: "https://downloads.example.test/prepared.jpg",
    thumbnail_url: "https://downloads.example.test/prepared-thumb.jpg",
    tag_counts: {},
  };
  const list = vi.fn().mockResolvedValue({ results: [prepared] });

  render(
    <AuthContext.Provider value={authenticated}>
      <MediaGallery client={{ list, deleteMediaById: vi.fn() }} />
    </AuthContext.Provider>,
  );

  await screen.findByText("Prepared");
  expect(interval).toHaveBeenCalled();
  interval.mockRestore();
});

test("removes a card only after the single-delete outcome is deleted", async () => {
  const media = {
    media_id: "ready-1",
    media_type: "image" as const,
    status: "ready" as const,
    original_url: "https://downloads.example.test/ready.jpg",
    thumbnail_url: null,
    tag_counts: {},
  };
  const list = vi.fn().mockResolvedValue({ results: [media] });
  const deleteMediaById = vi.fn().mockResolvedValue({
    result: { media_id: media.media_id, status: "deleted", error: null },
  });

  render(
    <AuthContext.Provider value={authenticated}>
      <MediaGallery client={{ list, deleteMediaById }} />
    </AuthContext.Provider>,
  );
  await screen.findByText("Ready");
  fireEvent.click(screen.getByRole("button", { name: "Delete image" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

  await waitFor(() => expect(screen.queryByText("Ready")).not.toBeInTheDocument());
  expect(screen.getByRole("status")).toHaveTextContent("Media deleted.");
});

test("closes the confirmation dialog when single deletion fails", async () => {
  const media = {
    media_id: "network-failure-1",
    media_type: "image" as const,
    status: "ready" as const,
    original_url: null,
    thumbnail_url: null,
    tag_counts: {},
  };
  const list = vi.fn().mockResolvedValue({ results: [media] });
  const deleteMediaById = vi.fn().mockRejectedValue(new Error("network unavailable"));

  render(
    <AuthContext.Provider value={authenticated}>
      <MediaGallery client={{ list, deleteMediaById }} />
    </AuthContext.Provider>,
  );
  await screen.findByText("Ready");
  fireEvent.click(screen.getByRole("button", { name: "Delete image" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("network unavailable");
  expect(screen.queryByRole("dialog", { name: "Confirm deletion" })).not.toBeInTheDocument();
  expect(screen.getByText("Ready")).toBeInTheDocument();
});
