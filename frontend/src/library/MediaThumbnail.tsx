export interface ThumbnailMedia {
  media_id: string;
  media_type: "image" | "video";
  original_url: string | null;
  thumbnail_url: string | null;
}

export function MediaThumbnail({
  media,
  name,
  localUrl,
}: {
  media: ThumbnailMedia;
  name: string;
  localUrl?: string;
}) {
  const imageSource = media.thumbnail_url ?? localUrl ?? (media.media_type === "image" ? media.original_url : null);
  const videoSource = localUrl ?? media.original_url;

  if (media.media_type === "image" && imageSource) {
    const image = <img className="media-thumbnail" src={imageSource} alt={`${name} thumbnail`} />;
    return media.original_url ? (
      <a href={media.original_url} target="_blank" rel="noreferrer" aria-label={`View original ${name}`}>
        {image}
      </a>
    ) : image;
  }

  if (media.media_type === "video" && (media.thumbnail_url || videoSource)) {
    const video = (
      <video
        className="media-thumbnail media-video-thumbnail"
        aria-label={`${name} preview`}
        poster={media.thumbnail_url ?? undefined}
        src={media.thumbnail_url ? undefined : videoSource ?? undefined}
        muted
        preload="metadata"
      />
    );
    return media.original_url ? (
      <a href={media.original_url} target="_blank" rel="noreferrer" aria-label={`View original ${name}`}>
        {video}
      </a>
    ) : video;
  }

  return (
    <span className="preview-placeholder" aria-label={`Preview unavailable ${media.media_id}`}>
      <span className="preview-placeholder-icon" aria-hidden="true">{media.media_type === "image" ? "▧" : "▷"}</span>
      <strong>{media.media_type === "image" ? "Image" : "Video"}</strong>
      <small>Preview unavailable</small>
    </span>
  );
}
