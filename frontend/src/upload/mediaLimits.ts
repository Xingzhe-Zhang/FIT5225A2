export const MAX_IMAGE_BYTES = 25 * 1024 * 1024;
export const MAX_VIDEO_BYTES = 512 * 1024 * 1024;

export function maxBytesFor(mediaType: "image" | "video"): number {
  return mediaType === "image" ? MAX_IMAGE_BYTES : MAX_VIDEO_BYTES;
}
