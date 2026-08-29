import { afterEach, describe, expect, it, vi } from "vitest";

import { PlatformClient } from "../src/api/platformClient";
import { AUTHENTICATION_REQUIRED_EVENT } from "../src/auth/authClient";

const result = {
  media_id: "11111111-1111-4111-8111-111111111111",
  media_type: "image" as const,
  original_url: "https://media.example.test/original.jpg",
  thumbnail_url: "https://media.example.test/thumbnail.jpg",
  tag_counts: { dingo: 2 },
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("PlatformClient", () => {
  it("sends query payloads to the configured API with Bearer JSON authentication", async () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://api.example.test/");
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ results: [result] }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await new PlatformClient().search("tags", { dingo: 2 }, "access-token");

    expect(response.results).toEqual([result]);
    expect(fetchMock).toHaveBeenCalledWith("https://api.example.test/queries/tags", {
      method: "POST",
      headers: {
        Authorization: "Bearer access-token",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ dingo: 2 }),
    });
  });

  it("uploads a query file as multipart without overriding the browser boundary", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ results: [result] }));
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["image"], "camera.jpg", { type: "image/jpeg" });

    const matches = await new PlatformClient("https://api.example.test").queryByFile(file, "access-token");

    expect(matches).toEqual([result]);
    const [url, request] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.example.test/queries/by-file");
    expect(request.headers).toEqual({ Authorization: "Bearer access-token" });
    expect(request.body).toBeInstanceOf(FormData);
    expect((request.body as FormData).get("file")).toBe(file);
  });

  it("uses JSON routes for media management and subscription CRUD", async () => {
    const subscription = {
      subscription_id: "22222222-2222-4222-8222-222222222222",
      email: "researcher@example.com",
      tags: ["dingo"],
      status: "active" as const,
      version: 1,
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ results: [] }))
      .mockResolvedValueOnce(jsonResponse({ results: [] }))
      .mockResolvedValueOnce(jsonResponse({ results: [] }))
      .mockResolvedValueOnce(jsonResponse(subscription, 201))
      .mockResolvedValueOnce(jsonResponse({ ...subscription, version: 2 }))
      .mockResolvedValueOnce(jsonResponse({}));
    vi.stubGlobal("fetch", fetchMock);
    const client = new PlatformClient("https://api.example.test");

    const tagResponse = { results: [{ url: result.original_url, media_id: result.media_id, status: "updated" }] };
    const deleteResponse = { results: [{ url: result.original_url, media_id: result.media_id, status: "deleted", error: null }] };
    fetchMock.mockReset();
    fetchMock
      .mockResolvedValueOnce(jsonResponse(tagResponse))
      .mockResolvedValueOnce(jsonResponse(deleteResponse))
      .mockResolvedValueOnce(jsonResponse({ results: [] }))
      .mockResolvedValueOnce(jsonResponse(subscription, 201))
      .mockResolvedValueOnce(jsonResponse({ ...subscription, version: 2 }))
      .mockResolvedValueOnce(jsonResponse({}));
    await expect(client.updateTags([result.original_url], ["dingo"], 1, "access-token")).resolves.toEqual(tagResponse);
    await expect(client.deleteMedia([result.original_url], "access-token")).resolves.toEqual(deleteResponse);
    await client.list("access-token");
    await client.create(subscription.email, subscription.tags, "access-token");
    await client.update(subscription.subscription_id, subscription.email, subscription.tags, 1, "access-token");
    await client.delete(subscription.subscription_id, "access-token");

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "https://api.example.test/media/tags",
      "https://api.example.test/media",
      "https://api.example.test/subscriptions",
      "https://api.example.test/subscriptions",
      `https://api.example.test/subscriptions/${subscription.subscription_id}`,
      `https://api.example.test/subscriptions/${subscription.subscription_id}`,
    ]);
    expect(fetchMock.mock.calls[4][1]).toMatchObject({
      method: "PUT",
      body: JSON.stringify({ email: subscription.email, tags: subscription.tags, expected_version: 1 }),
    });
  });

  it("deletes one media record by id and preserves the wrapped outcome", async () => {
    const response = { result: { media_id: result.media_id, status: "deleted", error: null } };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(response));
    vi.stubGlobal("fetch", fetchMock);

    await expect(new PlatformClient("https://api.example.test").deleteMediaById(result.media_id, "access-token"))
      .resolves.toEqual(response);
    expect(fetchMock).toHaveBeenCalledWith(`https://api.example.test/media/${result.media_id}`, {
      method: "DELETE",
      headers: { Authorization: "Bearer access-token" },
    });
  });

  it("reserves uploads and lists media through the assignment API", async () => {
    const reservation = {
      media_id: "33333333-3333-4333-8333-333333333333",
      duplicate: false,
      status: "reserved",
      upload_url: "https://uploads.example.test/object",
      object_key: "originals/object.jpg",
      expires_in_seconds: 900,
      upload_headers: {
        "Content-Type": "image/jpeg",
        "x-amz-meta-sha256": "a".repeat(64),
      },
    };
    const mediaResponse = { results: [{ ...result, status: "ready" }] };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(reservation))
      .mockResolvedValueOnce(jsonResponse(mediaResponse))
      .mockResolvedValueOnce(jsonResponse({ media_id: reservation.media_id, status: "cancelled" }));
    vi.stubGlobal("fetch", fetchMock);
    const client = new PlatformClient("https://api.example.test");

    await expect(client.reserve({
      file_name: "camera.jpg",
      media_type: "image",
      size_bytes: 10,
      sha256: "a".repeat(64),
    }, "access-token")).resolves.toEqual(reservation);
    await expect(client.listMedia("access-token")).resolves.toEqual(mediaResponse);
    await expect(client.cancelReservation(reservation.media_id, "a".repeat(64), "access-token"))
      .resolves.toMatchObject({ status: "cancelled" });

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "https://api.example.test/uploads/reservations",
      "https://api.example.test/media",
      `https://api.example.test/uploads/reservations/${reservation.media_id}`,
    ]);
    expect(fetchMock.mock.calls[2][1]).toMatchObject({
      method: "DELETE",
      body: JSON.stringify({ sha256: "a".repeat(64) }),
    });
  });

  it("surfaces JSON API error messages", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ error: { message: "Access denied" } }, 403)));

    await expect(new PlatformClient("https://api.example.test").list("access-token"))
      .rejects.toThrow("Access denied");
  });

  it("signals the auth provider to clear an expired session after a 401", async () => {
    const authenticationRequired = vi.fn();
    window.addEventListener(AUTHENTICATION_REQUIRED_EVENT, authenticationRequired, { once: true });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ error: { message: "Token expired" } }, 401)));

    await expect(new PlatformClient("https://api.example.test").list("expired-token"))
      .rejects.toThrow("Token expired");
    expect(authenticationRequired).toHaveBeenCalledOnce();
  });
});
