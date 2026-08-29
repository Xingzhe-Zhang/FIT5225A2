import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { AuthContext, type AuthContextValue } from "../../src/auth/AuthContext";
import { UploadPanel, type UploadClient } from "../../src/upload/UploadPanel";
import { MAX_IMAGE_BYTES } from "../../src/upload/mediaLimits";

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

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function mediaFile(name: string, type = "image/jpeg"): File {
  return new File(["camera-bytes"], name, { type });
}

test("hashes a selected image, reserves it, PUTs it, and refreshes the library", async () => {
  const reserve = vi.fn().mockResolvedValue({
    media_id: "11111111-1111-4111-8111-111111111111",
    duplicate: false,
    status: "reserved",
    upload_url: "https://uploads.example.test/originals/hash/camera.jpg",
    object_key: "originals/hash/camera.jpg",
    expires_in_seconds: 900,
    upload_headers: {
      "Content-Type": "image/jpeg",
      "x-amz-meta-sha256": "ab".repeat(32),
    },
  });
  const directPut = vi.fn().mockResolvedValue({ ok: true });
  const refreshLibrary = vi.fn().mockResolvedValue(undefined);
  const client: UploadClient = { reserve, cancelReservation: vi.fn() };
  const calculateChecksum = vi.fn().mockResolvedValue("ab".repeat(32));
  const onUploadAccepted = vi.fn();
  vi.stubGlobal("fetch", directPut);

  render(
    <AuthContext.Provider value={authenticated}>
      <UploadPanel client={client} refreshLibrary={refreshLibrary} onUploadAccepted={onUploadAccepted} calculateChecksum={calculateChecksum} />
    </AuthContext.Provider>,
  );
  const file = mediaFile("Camera.JPG", "video/mp4");
  fireEvent.change(screen.getByLabelText("Choose media file"), { target: { files: [file] } });
  expect(screen.getByText("Camera.JPG")).toBeInTheDocument();
  expect(screen.getByText("12 B · Image")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Upload" }));

  await waitFor(() => expect(reserve).toHaveBeenCalledOnce());
  expect(reserve).toHaveBeenCalledWith(
    {
      file_name: "Camera.JPG",
      media_type: "image",
      size_bytes: file.size,
      sha256: "ab".repeat(32),
    },
    "access-token",
  );
  expect(directPut).toHaveBeenCalledWith("https://uploads.example.test/originals/hash/camera.jpg", expect.objectContaining({
    method: "PUT",
    headers: {
      "Content-Type": "image/jpeg",
      "x-amz-meta-sha256": "ab".repeat(32),
    },
    body: file,
  }));
  expect(refreshLibrary).toHaveBeenCalledOnce();
  expect(onUploadAccepted).toHaveBeenCalledWith("11111111-1111-4111-8111-111111111111", file);
  expect(screen.getByText("Upload complete.")).toBeInTheDocument();
  expect(screen.queryByText("Camera.JPG")).not.toBeInTheDocument();
});

test("reports a duplicate without PUTting the file and still refreshes the library", async () => {
  const reserve = vi.fn().mockResolvedValue({
    media_id: "11111111-1111-4111-8111-111111111111",
    duplicate: true,
    status: "ready",
    upload_url: null,
    object_key: null,
    expires_in_seconds: null,
    upload_headers: null,
  });
  const directPut = vi.fn().mockResolvedValue({ ok: true });
  const refreshLibrary = vi.fn().mockResolvedValue(undefined);
  const calculateChecksum = vi.fn().mockResolvedValue("00".repeat(32));
  vi.stubGlobal("fetch", directPut);

  render(
    <AuthContext.Provider value={authenticated}>
      <UploadPanel client={{ reserve, cancelReservation: vi.fn() }} refreshLibrary={refreshLibrary} calculateChecksum={calculateChecksum} />
    </AuthContext.Provider>,
  );
  const file = mediaFile("camera.jpg");
  fireEvent.change(screen.getByLabelText("Choose media file"), { target: { files: [file] } });
  fireEvent.click(screen.getByRole("button", { name: "Upload" }));

  expect(await screen.findByText("This file is already in your library.")).toBeInTheDocument();
  expect(directPut).not.toHaveBeenCalled();
  expect(refreshLibrary).toHaveBeenCalledOnce();
});

test("uses the canonical reservation headers when the browser MIME type is empty", async () => {
  const reserve = vi.fn().mockResolvedValue({
    media_id: "22222222-2222-4222-8222-222222222222",
    duplicate: false,
    status: "reserved",
    upload_url: "https://uploads.example.test/originals/hash/clip.mp4",
    object_key: "originals/hash/clip.mp4",
    expires_in_seconds: 900,
    upload_headers: {
      "Content-Type": "video/mp4",
      "x-amz-meta-sha256": "ab".repeat(32),
    },
  });
  const directPut = vi.fn().mockResolvedValue({ ok: true });
  const calculateChecksum = vi.fn().mockResolvedValue("ab".repeat(32));
  vi.stubGlobal("fetch", directPut);
  render(
    <AuthContext.Provider value={authenticated}>
      <UploadPanel client={{ reserve, cancelReservation: vi.fn() }} refreshLibrary={vi.fn().mockResolvedValue(undefined)} calculateChecksum={calculateChecksum} />
    </AuthContext.Provider>,
  );
  const file = mediaFile("clip.mp4", "");
  fireEvent.change(screen.getByLabelText("Choose media file"), { target: { files: [file] } });
  fireEvent.click(screen.getByRole("button", { name: "Upload" }));

  await waitFor(() => expect(reserve).toHaveBeenCalledOnce());
  expect(reserve.mock.calls[0][0].media_type).toBe("video");
  expect(directPut).toHaveBeenCalledWith(
    "https://uploads.example.test/originals/hash/clip.mp4",
    expect.objectContaining({
      headers: {
        "Content-Type": "video/mp4",
        "x-amz-meta-sha256": "ab".repeat(32),
      },
    }),
  );
});

test("cancels the checksum reservation when the direct PUT fails", async () => {
  const sha256 = "ab".repeat(32);
  const reserve = vi.fn().mockResolvedValue({
    media_id: "33333333-3333-4333-8333-333333333333",
    duplicate: false,
    status: "reserved",
    upload_url: "https://uploads.example.test/originals/hash/camera.jpg",
    object_key: "originals/hash/camera.jpg",
    expires_in_seconds: 900,
    upload_headers: { "Content-Type": "image/jpeg", "x-amz-meta-sha256": sha256 },
  });
  const cancelReservation = vi.fn().mockResolvedValue({ status: "cancelled" });
  const calculateChecksum = vi.fn().mockResolvedValue(sha256);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));

  render(
    <AuthContext.Provider value={authenticated}>
      <UploadPanel
        client={{ reserve, cancelReservation }}
        refreshLibrary={vi.fn().mockResolvedValue(undefined)}
        calculateChecksum={calculateChecksum}
      />
    </AuthContext.Provider>,
  );
  fireEvent.change(screen.getByLabelText("Choose media file"), { target: { files: [mediaFile("camera.jpg")] } });
  fireEvent.click(screen.getByRole("button", { name: "Upload" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Direct upload failed.");
  expect(cancelReservation).toHaveBeenCalledWith(
    "33333333-3333-4333-8333-333333333333",
    sha256,
    "access-token",
  );
});

test("rejects an oversized image before hashing or reserving", async () => {
  const reserve = vi.fn();
  const calculateChecksum = vi.fn();
  const oversized = mediaFile("too-large.jpg");
  Object.defineProperty(oversized, "size", { value: MAX_IMAGE_BYTES + 1 });

  render(
    <AuthContext.Provider value={authenticated}>
      <UploadPanel
        client={{ reserve, cancelReservation: vi.fn() }}
        refreshLibrary={vi.fn()}
        calculateChecksum={calculateChecksum}
      />
    </AuthContext.Provider>,
  );
  fireEvent.change(screen.getByLabelText("Choose media file"), { target: { files: [oversized] } });

  expect(await screen.findByRole("alert")).toHaveTextContent("Image exceeds the 25.0 MB upload limit.");
  expect(calculateChecksum).not.toHaveBeenCalled();
  expect(reserve).not.toHaveBeenCalled();
});
