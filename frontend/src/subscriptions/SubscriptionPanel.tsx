import { useEffect, useState } from "react";

import { useAuth } from "../auth/AuthContext";
import { Icon } from "../ui/Icon";


export interface SubscriptionView {
  subscription_id: string;
  email: string;
  tags: string[];
  status: "active" | "pending_confirmation";
  version: number;
}

export interface SubscriptionClient {
  list(accessToken: string): Promise<SubscriptionView[]>;
  create(email: string, tags: string[], accessToken: string): Promise<SubscriptionView>;
  update(
    subscriptionId: string,
    email: string,
    tags: string[],
    expectedVersion: number,
    accessToken: string,
  ): Promise<SubscriptionView>;
  delete(subscriptionId: string, accessToken: string): Promise<void>;
}

export function SubscriptionPanel({ client }: { client: SubscriptionClient }) {
  const { accessToken } = useAuth();
  const [subscriptions, setSubscriptions] = useState<SubscriptionView[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [tags, setTags] = useState("");
  const [editing, setEditing] = useState<SubscriptionView | null>(null);
  const [deleting, setDeleting] = useState<SubscriptionView | null>(null);

  useEffect(() => {
    let active = true;
    if (!accessToken) {
      setLoading(false);
      setError("Authentication is required.");
      return () => { active = false; };
    }
    setLoading(true);
    client.list(accessToken)
      .then((items) => {
        if (active) setSubscriptions(items);
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "Subscriptions could not be loaded");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [accessToken, client]);

  function normalizedTags(): string[] {
    return [...new Set(
      tags.split(",").map((tag) => tag.trim().toLowerCase()).filter(Boolean),
    )].sort();
  }

  function clearForm() {
    setEmail("");
    setTags("");
    setEditing(null);
  }

  async function save() {
    if (!accessToken) return;
    const watchedTags = normalizedTags();
    if (!email.trim() || watchedTags.length === 0) {
      setError("Enter an email and at least one watched tag.");
      return;
    }
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      if (editing) {
        const updated = await client.update(
          editing.subscription_id,
          email.trim().toLowerCase(),
          watchedTags,
          editing.version,
          accessToken,
        );
        setSubscriptions((current) =>
          current.map((item) => item.subscription_id === updated.subscription_id ? updated : item),
        );
        setMessage("Subscription updated.");
      } else {
        const created = await client.create(
          email.trim().toLowerCase(),
          watchedTags,
          accessToken,
        );
        setSubscriptions((current) => [...current, created]);
        setMessage("Subscription created.");
      }
      clearForm();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The subscription could not be saved");
    } finally {
      setBusy(false);
    }
  }

  function beginEdit(subscription: SubscriptionView) {
    setEditing(subscription);
    setEmail(subscription.email);
    setTags(subscription.tags.join(", "));
    setError(null);
    setMessage(null);
  }

  async function confirmDelete() {
    if (!accessToken || !deleting) return;
    setBusy(true);
    setError(null);
    try {
      await client.delete(deleting.subscription_id, accessToken);
      setSubscriptions((current) =>
        current.filter((item) => item.subscription_id !== deleting.subscription_id),
      );
      setDeleting(null);
      setMessage("Subscription deleted.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The subscription could not be deleted");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="subscriptions-heading">
      <div className="panel-heading">
        <div><p className="panel-kicker">Detection alerts</p><h2 id="subscriptions-heading">Tag subscriptions</h2></div>
        <span className="panel-number" aria-hidden="true">05</span>
      </div>
      <p className="panel-description">Choose the species tags you want to follow. Notification delivery begins after the email subscription is confirmed.</p>
      {loading && <p role="status">Loading subscriptions...</p>}
      {error && <p role="alert">{error}</p>}
      {message && <p role="status">{message}</p>}
      {!loading && subscriptions.length === 0 && <p className="empty-state compact-empty">No tag subscriptions yet.</p>}

      {subscriptions.length > 0 && (
        <ul className="subscription-list" aria-label="Current subscriptions">
          {subscriptions.map((subscription) => (
            <li key={subscription.subscription_id}>
              <div className="subscription-title"><strong>{subscription.email}</strong><span className={`subscription-status ${subscription.status}`}>{subscription.status === "active" ? "Active" : "Pending confirmation"}</span></div>
              <span>{subscription.tags.join(", ")}</span>
              {subscription.status === "pending_confirmation" && <small>Confirm the SNS email before notifications can arrive.</small>}
              <div className="action-row"><button className="secondary icon-label" type="button" onClick={() => beginEdit(subscription)}><Icon name="edit" />{`Edit ${subscription.email}`}</button><button className="button-link danger-link icon-label" type="button" onClick={() => setDeleting(subscription)}><Icon name="delete" />{`Delete ${subscription.email}`}</button></div>
            </li>
          ))}
        </ul>
      )}

      <div className="subscription-form">
        <div className="form-title"><strong>{editing ? `Editing ${editing.email}` : "Create an alert"}</strong><small>Separate multiple tags with commas.</small></div>
        <label htmlFor="subscription-email">Notification email</label>
        <input id="subscription-email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="researcher@example.com" />
        <label htmlFor="subscription-tags">Watched tags</label>
        <input id="subscription-tags" value={tags} onChange={(event) => setTags(event.target.value)} placeholder="dingo, wombat" />
        <div className="action-row"><button className="icon-label" type="button" disabled={busy} onClick={() => void save()}><Icon name="bell" />{editing ? "Update subscription" : "Create subscription"}</button>{editing && <button className="secondary icon-label" type="button" onClick={clearForm}><Icon name="clear" />Cancel edit</button>}</div>
      </div>

      {deleting && (
        <div role="dialog" aria-label="Confirm subscription deletion" aria-modal="true">
          <p>{`Delete the subscription for ${deleting.email}?`}</p>
          <div className="dialog-actions"><button className="secondary" type="button" onClick={() => setDeleting(null)}>Cancel</button><button className="button-danger" type="button" disabled={busy} onClick={() => void confirmDelete()}>Confirm delete</button></div>
        </div>
      )}
    </section>
  );
}
