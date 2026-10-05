# Subscriber system: next steps

This document describes the work needed to turn the current private signup page into a reliable multi-user newsletter service.

## Current baseline

The service currently provides:

- predefined TOML presets under `config/presets`;
- `POST /api/subscribers` for creating or updating a subscriber;
- one SQLite subscriber record per normalized email address;
- `preset_id` plus a reserved `config_overrides` JSON object;
- per-subscriber rendering and delivery tracking;
- a private Caddy route protected by the existing authentication service.

New signups currently become active immediately. There is no confirmation email, preference-management link, unsubscribe flow, bounce handling, or web UI for custom settings.

## Recommended delivery order

### 1. Add a proper subscription lifecycle

Expand `subscribers.status` from `active | unsubscribed` to:

- `pending`: signup received, email not confirmed;
- `active`: confirmed and eligible for delivery;
- `unsubscribed`: user opted out;
- `disabled`: delivery was stopped administratively or after repeated permanent failures.

Add timestamps such as `confirmed_at`, `unsubscribed_at`, and `last_delivery_at`. Only `active` subscribers should enter the digest delivery loop.

Do this through explicit, repeatable SQLite migrations rather than adding more ad-hoc checks during startup. Keep a schema-version table and test migration from the current database.

### 2. Send confirmation email

Change signup to create or update a `pending` subscriber and send a confirmation link. A practical flow is:

1. The user submits an email and preset.
2. The API always returns a neutral response so it does not reveal whether an address already exists.
3. Generate a cryptographically random, single-purpose token with a short expiry, for example 24 hours.
4. Store only a SHA-256 token hash, its purpose, subscriber ID, expiry, and consumption time.
5. Email a link to a confirmation page.
6. Confirmation consumes the token idempotently and changes the subscriber to `active`.
7. A new signup invalidates older unconsumed confirmation tokens.

Use separate HTML and plain-text confirmation templates. Do not place the token in logs, analytics, toast messages, or the database in plaintext. Add a resend endpoint with a cooldown.

### 3. Add unsubscribe support before broader use

Every digest should contain a visible preference and unsubscribe link. Use another random, scoped token rather than exposing the subscriber ID or email.

Recommended endpoints:

- `GET /manage?token=...`: display the subscriber's current preferences;
- `POST /api/subscription/unsubscribe`: mark the subscriber unsubscribed;
- `POST /api/subscription/resubscribe`: require a fresh confirmation email;
- `POST /api/subscription/preferences`: update validated preferences.

The unsubscribe page should show a confirmation screen before changing state, because email security scanners may automatically open links. The operation must be idempotent. Once unsubscribed, no later digest run may reactivate the address; only an explicit new signup and confirmation may do so.

Also add standard `List-Unsubscribe` and `List-Unsubscribe-Post` headers when building messages. Test the complete flow from a rendered digest rather than testing the API alone.

### 4. Isolate delivery failures per subscriber

The current delivery loop stops when one email send raises an exception. Change it so one failed recipient is recorded and processing continues for the remaining subscribers.

Track at least:

- delivery attempt time;
- success or error category;
- consecutive failure count;
- last successful delivery;
- provider message ID when available.

Classify permanent failures separately from transient SMTP errors. Retry transient failures with a bounded policy. Disable an address only after a defined permanent failure or repeated failures, and record why.

### 5. Provide preset management

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

### 7. Add passwordless preference management

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

## Suggested schema additions

The exact migration can evolve, but the next schema should include the following concepts:

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

## Minimum test coverage

Add tests for:

- duplicate signup and case-insensitive email handling;
- pending subscribers being excluded from delivery;
- confirmation token success, expiry, reuse, and invalidation;
- unsubscribe idempotency and exclusion from later runs;
- resubscription requiring confirmation;
- preset switching without losing valid overrides;
- invalid, unknown, and oversized overrides;
- one subscriber's SMTP failure not blocking another;
- concurrent signup and digest reads against SQLite;
- management and unsubscribe links beneath the Caddy subpath;
- plain-text and HTML emails containing correct preference links.

## Practical milestone

The next useful milestone is complete when a person can:

1. choose a preset;
2. confirm ownership of the email address;
3. receive a digest without another subscriber's failure affecting delivery;
4. open a protected preference page;
5. switch presets or unsubscribe;
6. remain excluded from all future sends after unsubscribing.

Custom configuration should follow that lifecycle milestone, not precede it.
