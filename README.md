# CheckWarranty &bull; GLPI ITAM Hardware Warranty Sync

> **Dry-run and API credits:** `--dry-run` prevents writes to the target ITAM,
> but still sends device serials to CheckWarranty and may consume live API credits.
> Use a sandbox API key and `TEST-` serials for acceptance testing.

Automate computer and workstation warranty tracking in [GLPI](https://glpi-project.org/) using the [CheckWarranty B2B API](https://checkwarranty.info/docs/api).

![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)
![Python: 3.8+](https://img.shields.io/badge/Python-3.8%2B-green.svg)
![Integration: GLPI](https://img.shields.io/badge/ITAM-GLPI-orange.svg)

---

## Overview

GLPI is the dominant open-source IT asset management and service desk solution in Europe (France, Germany, UK, and public sector organizations). While GLPI tracks thousands of computers and laptops, warranty expiration dates and purchase dates often stay blank or are entered manually from paper invoices.

**`checkwarranty-glpi-sync`** automatically:
1. Queries GLPI for Computer assets (Dell, Lenovo, HP, Apple).
2. Looks up live OEM warranty coverage and factory hardware specifications (CPU, RAM, SSD) using CheckWarranty's B2B REST API.
3. Automatically creates or updates GLPI **`Infocom`** financial & warranty records:
   - **`warranty_date`**: Confirmed warranty start, only with an exact native month interval.
   - **`buy_date`**: Preserved; never inferred from warranty or manufacture dates.
   - **`warranty_duration`**: Duration of OEM coverage in months (12, 24, 36, etc.).
   - **`warranty_info`**: Support tier (e.g. *ProSupport*, *Care Pack Next Business Day*) and factory specs summary.

---

## Workflow Diagram

```
┌──────────────────┐       1. Fetch computers       ┌────────────────────────┐
│    GLPI ITAM     │ ◄────────────────────────────  │  checkwarranty-glpi    │
│  (Self-hosted)   │                                │         (CLI)          │
└────────┬─────────┘                                └───────────┬────────────┘
         │ 2. Return serials & Infocom data                     │
         └──────────────────────────────────────────►           │ 3. Lookup warranty & specs
                                                                ▼
                                                    ┌────────────────────────┐
                                                    │   CheckWarranty API    │
                                                    │ (Dell, Lenovo, HP, Mac)│
                                                    └───────────┬────────────┘
         ┌─────────────────┐ 5. Upsert Infocom record           │
         │    GLPI ITAM    │ ◄──────────────────────────────────┘ 4. Return verified data
         │   (Enriched)    │   (Warranty date, months, SLA, specs)
         └─────────────────┘
```

---

## GLPI API Configuration (One-Time Setup)

Before running the synchronization, ensure the GLPI REST API is enabled:

1. Log into GLPI as Administrator.
2. Go to **Setup &rarr; General &rarr; API**:
   - Check **Enable REST API** &rarr; Yes.
   - Check **Enable login with external application token** &rarr; Yes.
   - Click **Add API client**:
     - Name: `CheckWarranty Sync`
     - Active: Yes
     - Regenerate / Copy the **Application Token (`GLPI_APP_TOKEN`)**.
3. Go to **Administration &rarr; Users &rarr; (Your User Account)**:
   - Navigate to the **Remote access keys** tab.
   - Generate an **API token (`GLPI_USER_TOKEN`)** and save your user settings.

---

## Quick Start (3 Minutes)

### 1. Clone & Setup

```bash
cd integrations/glpi
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Environment

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Edit `.env` with your GLPI and CheckWarranty credentials:

```ini
GLPI_URL=https://glpi.yourcompany.com
GLPI_APP_TOKEN=your_glpi_application_token
GLPI_USER_TOKEN=your_glpi_user_token

CHECKWARRANTY_API_KEY=cw_live_your_api_key_here
CHECKWARRANTY_API_URL=https://checkwarranty.info/api/v1
```

### 3. Run Dry-Run Simulation

Verify asset discovery and warranty calculations without writing to GLPI:

```bash
python sync.py --dry-run
```

### 4. Execute Live Synchronization

```bash
python sync.py
```

---

## CLI Options & Filters

| Flag | Default | Description |
| :--- | :--- | :--- |
| `--dry-run` | `False` | Simulate execution without modifying GLPI records. |
| `--limit` | `50` | Maximum number of computers to fetch and inspect. |
| `--force` | `False` | Update warranty even if an active date is already set in Infocom. |
| `--recheck-days` | `0` | Re-query devices whose warranty expires within N days (or is expired). |
| `--brand` | `auto` | Override brand auto-detection (`dell`, `hp`, `lenovo`, `apple`). |
| `--sync-specs` | `True` | Include factory hardware specs (CPU, RAM, Disk) in `warranty_info`. |
| `--verbose` | `False` | Enable debug logs for troubleshooting. |

---

## Credit Protection & Smart Caching

To prevent unnecessary API credit consumption:
* **Existing Infocom Protection:** Computers with an active warranty date in the future are skipped automatically.
* **Smart Renewal Buffer (`--recheck-days`):** Set `--recheck-days 30` in monthly cron jobs to only refresh records nearing expiration.
* **Idempotency Keys:** Every request carries a unique UUID idempotency key to prevent double-billing on network retries.

---

## Docker & Scheduled Cron

Run as an isolated container:

```bash
docker build -t checkwarranty-glpi-sync .
docker run --rm --env-file .env checkwarranty-glpi-sync --limit 100
```

Or using `docker-compose`:

```bash
docker-compose run --rm checkwarranty-glpi-sync
```

To schedule a weekly cron job on your GLPI server:

```bash
0 3 * * 0 /usr/bin/docker run --rm --env-file /opt/glpi-sync/.env checkwarranty-glpi-sync --recheck-days 30
```

---

## License

MIT &copy; CheckWarranty Team

## Installable CLI (1.0.1)

Build and install locally with `python -m pip install .`, or install the wheel
from a GitHub release. PyPI publication is pending account/publisher setup.
The CLI command is `checkwarranty-glpi-sync`.
Use `--plan` to discover candidates without sending requests to CheckWarranty.
`--dry-run` still queries CheckWarranty but suppresses writes to the target ITAM.

Warranty start, purchase and manufacture dates are distinct. This connector never
populates a purchase date from a warranty start. Exact expiration dates use the
configured expiration custom field (Snipe-IT), NetBox date custom field, or a
`[CheckWarranty] expiry=YYYY-MM-DD` marker in GLPI warranty notes. Native month
intervals are written only when they reproduce the exact calendar expiration.
A new CLI run creates a new idempotency key; this is not a cross-run cache.

Scheduled runs rotate through target assets using `--state-file` (default:
`~/.local/state/checkwarranty/glpi.json`). Run one scheduler process per
state file. Plan/dry-run do not advance it. For Docker, preserve `/state` using a
named volume, for example `-v checkwarranty-glpi-state:/state`; the supplied
Compose file does this. The cursor stores only a target URL hash and an offset,
not API keys. Removing it restarts discovery at the beginning.
