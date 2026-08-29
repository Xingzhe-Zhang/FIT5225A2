import { useEffect, useRef, useState } from "react";

import { useAuth } from "../auth/AuthContext";
import { PlatformClient } from "../api/platformClient";
import { MediaGallery, type LocalMediaPreview } from "../library/MediaGallery";
import { ManagementPanel } from "../manage/ManagementPanel";
import { QueryPanel } from "../query/QueryPanel";
import { SubscriptionPanel } from "../subscriptions/SubscriptionPanel";
import { UploadPanel } from "../upload/UploadPanel";
import { Icon } from "../ui/Icon";

const platformClient = new PlatformClient();
const uploadClient = {
  reserve: platformClient.reserve.bind(platformClient),
  cancelReservation: platformClient.cancelReservation.bind(platformClient),
};
const mediaClient = {
  list: platformClient.listMedia.bind(platformClient),
  updateTags: platformClient.updateTags.bind(platformClient),
  deleteMedia: (urls: string[], accessToken: string) => platformClient.deleteMedia(urls, accessToken),
  deleteMediaById: platformClient.deleteMediaById.bind(platformClient),
};

export function LibraryPage() {
  const auth = useAuth();
  const [libraryVersion, setLibraryVersion] = useState(0);
  const [localPreviews, setLocalPreviews] = useState<Record<string, LocalMediaPreview>>({});
  const previewUrls = useRef<Set<string>>(new Set());

  useEffect(() => () => {
    previewUrls.current.forEach((url) => URL.revokeObjectURL(url));
    previewUrls.current.clear();
  }, []);

  async function refreshLibrary() {
    setLibraryVersion((current) => current + 1);
  }

  function rememberLocalPreview(mediaId: string, file: File) {
    const url = URL.createObjectURL(file);
    previewUrls.current.add(url);
    setLocalPreviews((current) => {
      const previous = current[mediaId]?.url;
      if (previous) {
        URL.revokeObjectURL(previous);
        previewUrls.current.delete(previous);
      }
      return { ...current, [mediaId]: { file_name: file.name, url } };
    });
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <a className="brand" href="#top" aria-label="Pacific BioArchive home">
          <span className="brand-mark" aria-hidden="true">PB</span>
          <span>
            <strong>Pacific BioArchive</strong>
            <small>Research workspace</small>
          </span>
        </a>
        <div className="header-actions">
          <span className="connection-status"><i aria-hidden="true" /> Secure session</span>
          <button type="button" className="button button-quiet icon-label" onClick={auth.logout}><Icon name="logout" />Sign out</button>
        </div>
      </header>
      <main id="top" className="library-shell">
        <section className="workspace-hero" aria-labelledby="workspace-title">
          <div>
            <p className="eyebrow">Wildlife observation archive</p>
            <h1 id="workspace-title">From field capture to searchable evidence</h1>
            <p className="hero-copy">Upload, review and organise wildlife observations in one secure workspace.</p>
          </div>
          <div className="workflow-summary" aria-label="Archive workflow">
            <span><strong>01</strong> Capture</span>
            <span><strong>02</strong> Identify</span>
            <span><strong>03</strong> Share</span>
          </div>
        </section>

        <nav className="section-nav" aria-label="Application sections">
          <a href="#upload"><Icon name="upload" /><span>01</span> Upload</a>
          <a href="#library"><Icon name="library" /><span>02</span> Library</a>
          <a href="#search"><Icon name="search" /><span>03</span> Search</a>
          <a href="#manage"><Icon name="manage" /><span>04</span> Manage</a>
          <a href="#subscriptions"><Icon name="bell" /><span>05</span> Subscriptions</a>
        </nav>

        <div className="workspace-grid">
          <section id="upload" className="workspace-card workspace-card-compact">
            <UploadPanel client={uploadClient} refreshLibrary={refreshLibrary} onUploadAccepted={rememberLocalPreview} />
          </section>
          <aside className="workspace-card field-notes" aria-labelledby="field-notes-heading">
            <div>
              <p className="panel-kicker">Field checklist</p>
              <h2 id="field-notes-heading">Before you upload</h2>
              <p>Keep the original field file intact. The archive handles preview generation and species tagging after transfer.</p>
            </div>
            <ol>
              <li><span>01</span><div><strong>Use a supported format</strong><small>JPG, PNG, MP4 or MOV</small></div></li>
              <li><span>02</span><div><strong>Duplicates are detected</strong><small>Matching file hashes reuse the existing record</small></div></li>
              <li><span>03</span><div><strong>Watch the status</strong><small>Processing becomes ready when previews and tags finish</small></div></li>
            </ol>
          </aside>
          <section id="library" className="workspace-card workspace-card-wide">
            <MediaGallery client={mediaClient} refreshVersion={libraryVersion} localPreviews={localPreviews} />
          </section>
          <section id="search" className="workspace-card workspace-card-wide">
            <QueryPanel client={platformClient} />
          </section>
          <section id="manage" className="workspace-card">
            <ManagementPanel client={platformClient} onLibraryChanged={refreshLibrary} />
          </section>
          <section id="subscriptions" className="workspace-card">
            <SubscriptionPanel client={platformClient} />
          </section>
        </div>
      </main>
      <footer className="app-footer">
        <span>Pacific BioArchive</span>
        <span>Built for responsible biodiversity research</span>
      </footer>
    </div>
  );
}
