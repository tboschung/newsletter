# Newsletter

A Raspberry Pi script that collects the previous 24 hours of AI news,
technology/research, and early-career AI/ML/data jobs; ranks and archives them;
and emails a morning digest.

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

RSS sources are in `morning_digest/default_sources.toml`. To override them, set
`DIGEST_SOURCES_FILE` in `/etc/newsletter.env` or pass `--sources` before the
command. Job profiles are under `data/job_profiles`; `cv` is the default mode.

```bash
python main.py check-config
python main.py check-config --smtp
python main.py run
python main.py run --dry-run
python main.py run --recommendation-mode specification --dry-run
python main.py run --as-of 2026-08-12T06:00:00+02:00 --dry-run
python main.py --sources /path/to/sources.toml run --dry-run
```

The archive stores fetched selections, summaries, source health, rendered
digests, and delivery state in SQLite.
