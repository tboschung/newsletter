# Newsletter

A Raspberry Pi service that collects the previous 24 hours of AI news and jobs,
ranks and archives them, and sends a personalized digest to each subscriber.

Collection is split into two declarative engines:

- `config/engines/newsletter.toml` contains news and research sources.
- `config/engines/jobs.toml` contains job sources.

Both use the same `ContentEngine`; adding, removing, reweighting, or recategorizing
a source does not require changing the engine code.

## Setup

Requires Python 3.11 or newer. The project runs directly from `main.py`; it is
not installed as a Python package.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
sudo install -m 640 -o root -g "$(id -gn)" .env.example /etc/newsletter.env
# Edit /etc/newsletter.env and replace the placeholder credentials.
python main.py check-config
python main.py run --dry-run
```

`/etc/newsletter.env` is required and is read directly by the script. A Gemini
API key is optional; without it, the digest uses source excerpts. An SMTP app
password is only required when sending email. `check-config --smtp` tests Gmail
authentication without sending.

## Raspberry Pi deployment

The supplied systemd units assume the project is at `/opt/apps/newsletter`
and runs as user `pi`. Change those values if needed.

```bash
sudo mkdir -p /opt/apps/newsletter/data
sudo chown -R pi:pi /opt/apps/newsletter
# Copy this repository into /opt/apps/newsletter, then:
python -m venv /opt/apps/newsletter/.venv
/opt/apps/newsletter/.venv/bin/pip install -r /opt/apps/newsletter/requirements.txt
sudo install -m 640 -o root -g pi /opt/apps/newsletter/.env.example /etc/newsletter.env
sudo install -m 644 deploy/morning-digest.service /etc/systemd/system/
sudo install -m 644 deploy/morning-digest.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now morning-digest.timer
```

Before enabling delivery, generate a preview and then run one manual send:

```bash
sudo -u pi /opt/apps/newsletter/.venv/bin/python /opt/apps/newsletter/main.py run --dry-run
sudo systemctl start morning-digest.service
sudo journalctl -u morning-digest.service -n 100 --no-pager
```

The timer runs at 06:00 Europe/Zurich, catches up after downtime, and SQLite
prevents the same cutoff from being delivered twice.

## Configuration and commands

Engine definitions are under `config/engines`. To use another
directory, set `DIGEST_ENGINES_DIR` in `/etc/newsletter.env`; to test one engine,
pass `--engine /path/to/engine.toml`. Job recommendation profiles remain under
`data/job_profiles`.

```bash
python main.py check-config
python main.py check-config --smtp
python main.py run
python main.py run --dry-run
python main.py run --as-of 2026-08-12T06:00:00+02:00 --dry-run
python main.py --engine /path/to/engine.toml run --dry-run
```

## Subscribers and personalization

Each TOML file in `config/subscribers` defines one subscriber.
Copy `default.toml`, give it a unique `id`, and choose any combination of
`news`, `technology`, and `jobs`:

```toml
id = "alice"
email = "alice@example.com"
content = ["news", "technology"]
job_profile = "cv"
```

For addresses kept outside the repository, use `email_env = "ALICE_EMAIL"`
and define `ALICE_EMAIL` in `/etc/newsletter.env`. Set
`DIGEST_SUBSCRIBERS_DIR` to keep all subscriber files elsewhere. A subscriber
who includes jobs can select a different profile directory with `job_profile`.

Collection runs once, after which ranking, rendering, previews, and delivery are
isolated per subscriber. The archive stores fetched items, summaries, source
health, rendered digests, and per-subscriber delivery state in SQLite. Existing
single-recipient archives are migrated automatically to the `default` subscriber.

## Signup API

The signup page, preset catalog, and JSON API can run as one small process:

```bash
python main.py serve --host 127.0.0.1 --port 8080
```

For a persistent local service, install `deploy/morning-digest-web.service` as
`/etc/systemd/system/morning-digest-web.service`, reload systemd, and enable it.
Keep the full `ExecStart` command on one line.

`GET /api/presets` returns the public preset catalog. `POST /api/subscribers`
accepts an `email` and `preset_id` JSON object. Repeating a signup updates the
existing address instead of creating a duplicate. Put the process behind an HTTPS
reverse proxy before exposing it publicly.

Public presets are TOML files in `config/presets`. Web signups are stored in the
same SQLite database used by delivery. Each record stores a preset plus an empty
`config_overrides` JSON object; the delivery resolver already merges validated
`content` and `job_profile` overrides, so custom per-user configuration can be
added later without changing the table or delivery loop.

The remaining subscriber-lifecycle, confirmation, unsubscribe, custom-config,
privacy, and operational work is documented in [`NEXT_STEPS.md`](NEXT_STEPS.md).
