# Subscriber system: next steps

This document describes the work needed to turn the current private signup page into a reliable multi-user newsletter service.

## Current baseline

The service currently provides:

- predefined TOML presets under `config/presets`;
- neutral `POST /api/subscribers` signup responses;
- one SQLite subscriber record per normalized email address;
- `preset_id` plus a reserved `config_overrides` JSON object;
- pending, active, unsubscribed, and disabled subscription states;
- hashed, expiring, single-use email confirmation tokens;
- scanner-safe confirmation through a GET page followed by a POST action;
- confirmation resends with a 10-minute cooldown;
- versioned, transactional SQLite migrations with WAL and a busy timeout;
- per-subscriber delivery attempts, failure counters, and delivery tracking;
- one transient retry and isolated recipient failures;
- a private Caddy route protected by the existing authentication service.

Only confirmed, active database subscribers enter the delivery loop. Existing TOML
subscribers remain active and supported. Scanner-safe unsubscribe, immediate
preset switching, and passwordless management sessions are now implemented.
There is not yet a web UI for custom settings.

## Completed milestone

Steps 1, 2, and 4 were implemented together: subscription lifecycle and explicit
schema migrations, email ownership confirmation, and isolated recipient delivery
failures. Existing database subscribers migrate as confirmed and active. Active
subscribers retain their current preset until a requested change is confirmed, and
disabled subscribers cannot reactivate themselves through signup or an old token.

## Recommended delivery order

### 1. Add a proper subscription lifecycle — completed

Expand `subscribers.status` from `active | unsubscribed` to:

- `pending`: signup received, email not confirmed;
- `active`: confirmed and eligible for delivery;
- `unsubscribed`: user opted out;
- `disabled`: delivery was stopped administratively or after repeated permanent failures.

Add timestamps such as `confirmed_at`, `unsubscribed_at`, and `last_delivery_at`. Only `active` subscribers should enter the digest delivery loop.

This is implemented through ordered, repeatable SQLite migrations and a
`schema_version` table. Migration covers fresh databases, the previous subscriber
schema, and the legacy digest schema.

### 2. Send confirmation email — completed

Signup now creates or updates a pending confirmation and sends a confirmation link:

1. The user submits an email and preset.
2. The API always returns a neutral response so it does not reveal whether an address already exists.
3. Generate a cryptographically random, single-purpose token with a short expiry, for example 24 hours.
4. Store only a SHA-256 token hash, its purpose, subscriber ID, expiry, and consumption time.
5. Email a link to a confirmation page.
6. Confirmation consumes the token idempotently and changes the subscriber to `active`.
7. A new signup invalidates older unconsumed confirmation tokens.

Separate HTML and plain-text templates are used. `DIGEST_PUBLIC_URL` supplies the
absolute base URL, including an optional proxy subpath. Token pages use
`Cache-Control: no-store`, query strings are redacted from request logs, and the
resend endpoint enforces a 10-minute cooldown.

### 3. Add unsubscribe support before broader use — completed

Every digest should contain a visible preference and unsubscribe link. Use another random, scoped token rather than exposing the subscriber ID or email.

Recommended endpoints:

- `GET /manage?token=...`: display the subscriber's current preferences;
- `POST /api/subscription/unsubscribe`: mark the subscriber unsubscribed;
- `POST /api/subscription/resubscribe`: require a fresh confirmation email;
- `POST /api/subscription/preferences`: update validated preferences.

The unsubscribe page should show a confirmation screen before changing state, because email security scanners may automatically open links. The operation must be idempotent. Once unsubscribed, no later digest run may reactivate the address; only an explicit new signup and confirmation may do so.

Also add standard `List-Unsubscribe` and `List-Unsubscribe-Post` headers when building messages. Test the complete flow from a rendered digest rather than testing the API alone.

### 4. Isolate delivery failures per subscriber — completed

The delivery loop records a failed recipient and continues with the remaining
subscribers.

Track at least:

- delivery attempt time;
- success or error category;
- consecutive failure count;
- last successful delivery;
- provider message ID when available.

Permanent recipient failures are classified separately from transient errors.
Transient failures retry once. A permanent rejection or three consecutive failures
disables a database subscriber, while success resets the counter. Authentication
and configuration failures abort immediately; other failures are reported after the
remaining recipients have been processed.

### 5. Provide preset management — completed (basic presets)

Presets should remain version-controlled TOML files and continue to describe shared defaults. Add a stable `version` field if changing a preset should be distinguishable from the version originally selected.

Decide explicitly whether preset changes:

- update every subscriber using that preset immediately; or
- create a new preset version while existing subscribers remain pinned.

For this small service, applying current preset values at delivery time is the simplest policy. Document significant changes and avoid deleting a preset while a subscriber still references it. Configuration validation should report orphaned preset IDs before a digest run begins.

### 6. Introduce custom configuration through the existing override boundary

Keep `preset_id` as the base configuration and store only differences in `config_overrides`. Resolve settings as:

```text
validated preset defaults
        +
validated subscriber overrides
        =
effective subscriber configuration
```

Start with a small versioned override schema, for example:

```json
{
  "schema_version": 1,
  "content": ["news", "technology"],
  "job_profile": "cv",
  "max_items_per_section": 5
}
```

Requirements:

- accept only known fields and bounded values;
- reject unknown categories and profile IDs;
- validate on write and again when resolving delivery configuration;
- expose the effective configuration in the management page;
- provide a “reset to preset” action that replaces overrides with `{}`;
- keep preset changes separate from override changes in tests and audit output.

Do not initially allow arbitrary feed URLs or adapter names. Fetching user-provided URLs creates server-side request forgery, network access, parsing, and abuse risks. If custom sources are added later, use a strict URL policy, block private/link-local networks, limit redirects and response sizes, and isolate collection failures.

If overrides grow into complex objects or need querying, move them into normalized preference tables. The current JSON column is appropriate while the schema is small and always resolved as one object.

### 7. Add passwordless preference management — completed (preset scope)

Full user accounts are unnecessary for the current product. A scoped, expiring management link can allow a subscriber to:

- see the email address and current preset;
- switch presets;
- edit supported overrides;
- pause or unsubscribe;
- request a fresh management link.

Use separate token purposes for confirmation, management, and unsubscribe. A token for one action must never authorize another. For longer-lived access, prefer a short session cookie created after consuming a one-time link rather than keeping a durable token in every page URL.

### 8. Privacy and security hardening

Before adding more users:

- define how long subscriber, token, and delivery-history records are retained;
- decide whether unsubscribe records are retained as a suppression list or deleted after a period;
- back up the SQLite database and test restoration;
- keep the database, backups, and environment file readable only by the service account;
- avoid logging email addresses and all access tokens;
- add request-size limits, signup/resend throttling, and CSRF protection for cookie-authenticated management actions;
- preserve loopback-only binding and the authenticated Caddy route;
- add `Cache-Control: no-store` to token and preference pages;
- rotate compromised tokens without changing subscriber IDs.

Even for a private service, confirmation is useful because it prevents typos and proves that delivery is going to an address controlled by the intended user.

### 9. Operations and observability

Add lightweight operational visibility:

- counts of pending, active, unsubscribed, and disabled subscribers;
- digest successes and failures per cutoff;
- stale pending signups and expired tokens;
- source failures separated from recipient delivery failures;
- a command to inspect a subscriber without printing tokens;
- a command to disable, reactivate, or remove a subscriber deliberately;
- database backup, integrity-check, and restore instructions.

SQLite remains sufficient for this service while signup and delivery volume are low. Enable and test an appropriate busy timeout or WAL mode if the web API and morning delivery process regularly write concurrently.

## Implemented lifecycle schema

The current schema includes these lifecycle concepts:

```text
subscribers
  id, normalized_email, preset_id, config_overrides, status,
  created_at, updated_at, confirmed_at, unsubscribed_at,
  consecutive_failures, last_delivery_at

subscriber_tokens
  id, subscriber_id, purpose, token_hash,
  created_at, expires_at, consumed_at

delivery_attempts
  id, subscriber_id, digest_id, attempted_at,
  status, error_category, error_message, provider_message_id
```

Keep token records and delivery attempts separate from the subscriber row: they have different retention rules and there may be many of each per subscriber.

## Remaining minimum test coverage

Lifecycle and confirmation coverage now includes duplicate signup, pending delivery
exclusion, token hashing and one-time consumption, active preset-change confirmation,
resend cooldown, disabled-address protection, and current/legacy schema migration.
Lifecycle coverage now includes scanner-safe unsubscribe and resubscription,
session and CSRF enforcement, immediate preset switching, scoped digest links,
token-free archives, and unsubscribe headers. Remaining coverage should focus on:

- invalid, unknown, and oversized overrides;
- concurrent signup and digest reads against SQLite;

## Next practical milestone

The next useful milestone is a small, versioned custom-override schema and UI.
Retention, backup/restore, and deliberate subscriber-administration commands
remain pending and should follow without weakening the suppression-list behavior.
