#!/usr/bin/env python3
"""
CheckWarranty - GLPI ITAM Hardware Warranty & Specifications Synchronizer
Synchronizes hardware warranty dates, SLA tiers, and purchase dates into GLPI ITAM.
Zero external dependencies required (uses standard library urllib/json).
"""

import os
import calendar
import hashlib
import tempfile
from pathlib import Path
import sys
import argparse
import logging
import time
import uuid
import json
import re
from typing import Dict, Any, List, Optional
from datetime import datetime, date
import urllib.request
import urllib.error
import urllib.parse

# Attempt to load .env if python-dotenv is available, otherwise read OS environment
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("checkwarranty-glpi")


def detect_brand(serial: str, model_name: str = "", manufacturer_name: str = "") -> Optional[str]:
    """Infer manufacturer from manufacturer name, model name, or serial number syntax."""
    mfg_lower = (manufacturer_name or "").strip().lower()
    mod_lower = (model_name or "").strip().lower()
    s_upper = (serial or "").strip().upper()

    if any(k in mfg_lower for k in ("dell", "latitude", "optiplex", "precision", "vostro", "xps", "poweredge")):
        return "dell"
    if any(k in mfg_lower for k in ("lenovo", "thinkpad", "thinkcentre", "thinkstation", "thinksystem", "ideapad")):
        return "lenovo"
    if re.search(r"\b(hp|hpe|hewlett[\s-]packard|elitebook|probook|zbook|proliant)\b", mfg_lower):
        return "hp"
    if any(k in mfg_lower for k in ("apple", "macbook", "imac", "mac mini", "mac studio", "mac pro")):
        return "apple"

    # Model name check
    if any(k in mod_lower for k in ("dell", "latitude", "optiplex", "precision", "vostro", "xps", "poweredge")):
        return "dell"
    if any(k in mod_lower for k in ("lenovo", "thinkpad", "thinkcentre", "thinkstation", "thinksystem", "ideapad")):
        return "lenovo"
    if re.search(r"\b(hp|hpe|hewlett[\s-]packard|elitebook|probook|zbook|proliant)\b", mod_lower):
        return "hp"
    if any(k in mod_lower for k in ("apple", "macbook", "imac", "mac mini", "mac studio", "mac pro")):
        return "apple"

    # Serial syntax heuristics
    if len(s_upper) == 7 and s_upper.isalnum():
        return "dell"
    if len(s_upper) == 8 and s_upper.isalnum():
        return "lenovo"
    if len(s_upper) == 10 and (s_upper.startswith("5CG") or s_upper.startswith("5CD") or s_upper.startswith("CND") or s_upper.startswith("CZ")):
        return "hp"
    if len(s_upper) == 12 and s_upper.startswith("C02"):
        return "apple"

    return None


def format_iso_to_date(iso_str: Optional[str]) -> Optional[str]:
    """Convert ISO 8601 timestamp string to YYYY-MM-DD for GLPI date fields."""
    if not isinstance(iso_str, str) or not iso_str:
        return None
    try:
        clean = iso_str.split("T")[0]
        datetime.strptime(clean, "%Y-%m-%d")
        return clean
    except ValueError:
        return None


def calculate_warranty_months(start_date_str: Optional[str], end_date_str: Optional[str]) -> Optional[int]:
    """Calculate warranty duration in months between start and end dates for GLPI Infocom."""
    if not start_date_str or not end_date_str:
        return None
    try:
        d1 = datetime.strptime(start_date_str.split("T")[0], "%Y-%m-%d")
        d2 = datetime.strptime(end_date_str.split("T")[0], "%Y-%m-%d")
        if d2 <= d1:
            return 0
        diff_days = (d2 - d1).days
        months = round(diff_days / 30.4375)
        return max(1, months)
    except ValueError:
        return None


def should_recheck_warranty(existing_date_str: Optional[Any], recheck_days: int) -> bool:
    """Determine if a warranty expiration should be refreshed."""
    if not existing_date_str:
        return True
    try:
        clean = str(existing_date_str).split(" ")[0].split("T")[0]
        exp_date = datetime.strptime(clean, "%Y-%m-%d").date()
        today = date.today()
        days_remaining = (exp_date - today).days
        return days_remaining <= recheck_days
    except ValueError:
        return True


class GlpiClient:
    """Handles communication with the GLPI REST API (/apirest.php/)."""

    def __init__(self, base_url: str, app_token: str, user_token: str):
        url = (base_url or "").strip().rstrip("/")
        if not url.endswith("/apirest.php"):
            url = f"{url}/apirest.php"
        self.api_url = url
        self.app_token = app_token.strip()
        self.user_token = user_token.strip()
        self.session_token: Optional[str] = None

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "App-Token": self.app_token,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "CheckWarranty-GLPI-Sync/1.0",
        }
        if self.session_token:
            headers["Session-Token"] = self.session_token
        return headers

    def _request(
        self,
        method: str,
        path: str,
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        custom_headers: Optional[Dict[str, str]] = None
    ) -> Any:
        url = f"{self.api_url}{path}"
        if params:
            query = urllib.parse.urlencode(params, doseq=True)
            url = f"{url}?{query}"

        headers = self._get_headers()
        if custom_headers:
            headers.update(custom_headers)

        body = json.dumps(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.build_opener(_NoRedirect()).open(req, timeout=30) as resp:
                res_body = resp.read().decode("utf-8")
                return json.loads(res_body) if res_body else {}
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            logger.error(f"GLPI HTTP {e.code} error on {method} {url}")
            raise
        except urllib.error.URLError as e:
            logger.error(f"GLPI connection error: {e}")
            raise

    def init_session(self) -> bool:
        """Authenticate with GLPI and obtain a session token."""
        headers = {
            "Authorization": f"user_token {self.user_token}",
            "App-Token": self.app_token,
        }
        try:
            res = self._request("GET", "/initSession", custom_headers=headers)
            if res and res.get("session_token"):
                self.session_token = res.get("session_token")
                logger.info("GLPI session successfully initialized.")
                return True
            logger.error("Failed to obtain session token from GLPI.")
            return False
        except Exception as e:
            logger.error(f"Failed to initialize GLPI session: {e}")
            return False

    def kill_session(self) -> None:
        """Close GLPI session."""
        if not self.session_token:
            return
        try:
            self._request("GET", "/killSession")
            self.session_token = None
            logger.info("GLPI session killed.")
        except Exception:
            pass

    def get_computers(self, total_limit: int = 100, page_size: int = 50, start_offset: int = 0) -> List[Dict[str, Any]]:
        """Fetch active Computer assets from GLPI with pagination."""
        all_computers: List[Dict[str, Any]] = []
        offset = start_offset

        while len(all_computers) < total_limit:
            fetch_count = min(page_size, total_limit - len(all_computers))
            range_header = f"{offset}-{offset + fetch_count - 1}"

            params = {
                "expand_dropdowns": "true",
                "is_deleted": 0,
                "range": range_header,
            }
            res = self._request("GET", "/Computer", params=params)
            if not res or not isinstance(res, list):
                break

            all_computers.extend(res)
            offset += len(res)
            if len(res) < fetch_count:
                break

        return all_computers

    def get_computer_infocom(self, computer_id: int) -> Optional[Dict[str, Any]]:
        """Fetch existing financial/warranty information (Infocom) for a Computer."""
        try:
            res = self._request("GET", f"/Computer/{computer_id}/Infocom")
            if isinstance(res, list) and len(res) > 0:
                return res[0]
            if isinstance(res, dict) and res.get("id"):
                return res
            return None
        except Exception:
            return None

    def upsert_infocom(self, computer_id: int, infocom_data: Dict[str, Any]) -> bool:
        """Create or update Infocom record for a Computer."""
        existing = self.get_computer_infocom(computer_id)
        if existing and existing.get("id"):
            infocom_id = existing["id"]
            payload = {
                "input": {
                    "id": infocom_id,
                    **infocom_data
                }
            }
            try:
                res = self._request("PUT", f"/Infocom/{infocom_id}", data=payload)
                return bool(res)
            except Exception as e:
                logger.error(f"Failed to update Infocom #{infocom_id} for Computer #{computer_id}: {e}")
                return False
        else:
            payload = {
                "input": {
                    "itemtype": "Computer",
                    "items_id": computer_id,
                    **infocom_data
                }
            }
            try:
                res = self._request("POST", "/Infocom", data=payload)
                return bool(res)
            except Exception as e:
                logger.error(f"Failed to create Infocom for Computer #{computer_id}: {e}")
                return False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class CheckWarrantyClient:
    """Handles communication with the CheckWarranty B2B REST API using urllib."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = (base_url or "").strip().rstrip("/")
        parts = urllib.parse.urlsplit(self.base_url)
        local_test = parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost", "::1")
        if not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or (parts.scheme != "https" and not local_test):
            raise ValueError("CheckWarranty API URL must use HTTPS (HTTP is allowed only on loopback for tests)")
        self.headers = {
            "X-API-Key": api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "CheckWarranty-GLPI-Sync/1.0",
        }

    def _request(self, method: str, path: str, data: Optional[Dict[str, Any]] = None, extra_headers: Optional[Dict[str, str]] = None) -> Any:
        url = f"{self.base_url}{path}"
        body = json.dumps(data).encode("utf-8") if data is not None else None
        headers = dict(self.headers)
        if extra_headers:
            headers.update(extra_headers)

        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.build_opener(_NoRedirect()).open(req, timeout=35) as resp:
                res_body = resp.read().decode("utf-8")
                return json.loads(res_body) if res_body else {}
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 402:
                logger.error("CheckWarranty credit balance exhausted (HTTP 402). Top up credits at checkwarranty.info/dashboard/billing")
                sys.exit(2)
            logger.warning(f"CheckWarranty HTTP {e.code} for {path}")
            return None
        except urllib.error.URLError as e:
            logger.error(f"CheckWarranty connection error: {e}")
            return None

    def lookup_device(self, serial: str, brand: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Query CheckWarranty for a single serial number."""
        idempotency_key = str(uuid.uuid4())
        extra = {"Idempotency-Key": idempotency_key}

        if brand:
            payload = {"serialNumber": serial, "brand": brand}
            res = self._request("POST", "/devices/lookup", payload, extra_headers=extra)
        else:
            payload = {"serialNumber": serial}
            res = self._request("POST", "/devices/auto-detect", payload, extra_headers=extra)

        if not res:
            return None

        # Handle async 202 accepted response
        if res.get("status") in ("in_progress", "pending"):
            batch_id = res.get("batchId")
            if batch_id:
                return self._poll_batch_result(batch_id)

        return res

    def _poll_batch_result(self, batch_id: str, max_retries: int = 6) -> Optional[Dict[str, Any]]:
        """Poll batch status endpoint if scan returned 202 Accepted."""
        for _ in range(max_retries):
            time.sleep(4)
            res = self._request("GET", f"/devices/scans/{batch_id}")
            if not res:
                continue
            status = res.get("status")
            if status in ("completed", "partial"):
                items = res.get("items", [])
                return items[0] if items and isinstance(items[0], dict) else None
            if status in ("failed", "error", "not_found"):
                logger.warning(f"CheckWarranty async scan ended with status: {status}")
                return res
        return None



def extract_result(result):
    """Read the public B2B DTO, including specs-only completed results."""
    if not isinstance(result, dict) or result.get("status") not in (None, "completed"):
        return {}, {}
    warranty = result.get("warranty")
    if not isinstance(warranty, dict):
        return {}, {}
    details = warranty.get("details") or {}
    if not isinstance(details, dict):
        details = {}
    specs = {}
    for source, target in (("processor", "cpu"), ("memory", "ram"), ("hardDrive", "storage"), ("display", "display")):
        value = details.get(source)
        if isinstance(value, str) and value.strip().lower() not in ("", "unknown", "n/a", "none", "not found", "-"):
            specs[target] = value.strip()
    device = result.get("device") or {}
    if isinstance(device, dict) and warranty.get("status") != "not_found":
        for source, target in (("model", "model"), ("productNumber", "product_number"), ("description", "description")):
            value = device.get(source)
            if isinstance(value, str) and value.strip() and not re.match(r"^(unknown( device)?|[a-z]+ device|n/a|none|-)$", value.strip(), re.I):
                specs[target] = value.strip()
    if warranty.get("status") == "not_found" and not specs:
        return {}, {}
    return warranty, specs


def exact_warranty_months(start, end):
    """Return whole calendar months only when the interval matches exactly."""
    start, end = format_iso_to_date(start), format_iso_to_date(end)
    if not start or not end:
        return None
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    months = (last.year - first.year) * 12 + last.month - first.month
    if months <= 0:
        return None
    day = min(first.day, calendar.monthrange(last.year, last.month)[1])
    return months if last.day == day else None



def build_infocom_payload(warranty, specs, existing, sync_specs=True):
    """Preserve purchases and store exact expiry even without a warranty start."""
    start = format_iso_to_date(warranty.get("startDate"))
    end = format_iso_to_date(warranty.get("endDate"))
    payload = {}
    months = exact_warranty_months(start, end)
    if months and warranty.get("startDateSource") != "estimated":
        payload.update(warranty_date=start, warranty_duration=months)
    parts = []
    if end:
        parts.append("expiry=" + end)
    if start:
        parts.append("start=" + start + (" (estimated)" if warranty.get("startDateSource") == "estimated" else ""))
    if warranty.get("status") in ("active", "expired"):
        parts.append("status=" + warranty["status"])
    tier = warranty.get("coverageType")
    if isinstance(tier, str) and tier.strip():
        parts.append("SLA=" + tier.strip().replace("\n", " "))
    if sync_specs:
        parts.extend(key.upper() + "=" + value.replace("\n", " ") for key, value in specs.items())
    if parts:
        manual = re.sub(r"(?:\n)?\[CheckWarranty\][^\n]*", "", existing.get("warranty_info") or "").rstrip()
        previous = re.search(r"\[CheckWarranty\] ([^\n]*)", existing.get("warranty_info") or "")
        merged = {}
        for part in (previous.group(1).split("; ") if previous else []) + parts:
            if "=" in part:
                key, value = part.split("=", 1)
                if key in {"expiry", "start", "status", "SLA", "CPU", "RAM", "STORAGE", "DISPLAY", "MODEL", "PRODUCT_NUMBER", "DESCRIPTION"} and value:
                    merged[key] = value
        if "expiry" in merged:
            merged = {"expiry": merged.pop("expiry"), **merged}
        note = "[CheckWarranty] " + "; ".join(key + "=" + value for key, value in merged.items())
        available = 255 - len(manual) - (1 if manual else 0)
        essential = "[CheckWarranty] " + ("expiry=" + end if end else parts[0])
        if available >= len(essential):
            payload["warranty_info"] = (manual + "\n" if manual else "") + note[:available]
        else:
            logger.warning("Infocom notes are full; existing manual notes preserved")
    return payload


def infocom_expiration(infocom):
    match = re.search(r"\[CheckWarranty\] expiry=(\d{4}-\d{2}-\d{2})", infocom.get("warranty_info") or "")
    if match:
        return format_iso_to_date(match.group(1))
    start = format_iso_to_date(infocom.get("warranty_date"))
    months = infocom.get("warranty_duration")
    try:
        months = int(months)
        if not start or months <= 0 or months > 1200:
            return None
        first = date.fromisoformat(start)
        total = first.year * 12 + first.month - 1 + months
        year, month = total // 12, total % 12 + 1
        return date(year, month, min(first.day, calendar.monthrange(year, month)[1])).isoformat()
    except (ValueError, TypeError):
        return None



def read_cursor(args, target):
    """Cursor is scoped to the target URL and contains no credentials or results."""
    filename = getattr(args, "state_file", None)
    if not filename:
        return 0
    try:
        state = json.loads(Path(filename).expanduser().read_text())
        if not isinstance(state, dict):
            return 0
        return max(0, int(state.get("offset", 0))) if state.get("target") == hashlib.sha256(target.encode()).hexdigest() else 0
    except (OSError, ValueError, TypeError):
        return 0


def write_cursor(args, target, offset):
    filename = getattr(args, "state_file", None)
    if not filename or args.dry_run or getattr(args, "plan", False):
        return
    path = Path(filename).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".cw-cursor-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump({"target": hashlib.sha256(target.encode()).hexdigest(), "offset": offset}, stream)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run_sync(args: argparse.Namespace) -> None:
    glpi_url = os.getenv("GLPI_URL")
    glpi_app_token = os.getenv("GLPI_APP_TOKEN")
    glpi_user_token = os.getenv("GLPI_USER_TOKEN")
    cw_key = os.getenv("CHECKWARRANTY_API_KEY")
    cw_url = os.getenv("CHECKWARRANTY_API_URL", "https://checkwarranty.info/api/v1")

    if not glpi_url or not glpi_app_token or not glpi_user_token:
        logger.error("Missing GLPI_URL, GLPI_APP_TOKEN, or GLPI_USER_TOKEN in environment.")
        sys.exit(1)
    if not cw_key and not getattr(args, "plan", False):
        logger.error("Missing CHECKWARRANTY_API_KEY in environment variables.")
        sys.exit(1)

    glpi = GlpiClient(glpi_url, glpi_app_token, glpi_user_token)
    cw = CheckWarrantyClient(cw_url, cw_key)

    if not glpi.init_session():
        sys.exit(1)

    try:
        logger.info(
            f"Starting GLPI Sync (dry_run={args.dry_run}, limit={args.limit}, force={args.force}, "
            f"recheck_days={args.recheck_days})"
        )

        cursor = read_cursor(args, glpi_url)
        computers = glpi.get_computers(total_limit=args.limit, start_offset=cursor)
        logger.info(f"Fetched {len(computers)} computers from GLPI.")

        stats = {"processed": 0, "updated": 0, "skipped": 0, "not_found": 0, "errors": 0}

        for comp in computers:
            comp_id = comp.get("id")
            serial = (comp.get("serial") or "").strip()
            comp_name = comp.get("name") or f"Computer #{comp_id}"
            mfg_val = comp.get("manufacturers_id")
            mfg_name = mfg_val if isinstance(mfg_val, str) else ""
            model_val = comp.get("computermodels_id")
            model_name = model_val if isinstance(model_val, str) else ""

            if not comp_id or not serial:
                if args.verbose:
                    logger.info(f"Skipping {comp_name}: missing ID or empty serial.")
                stats["skipped"] += 1
                continue

            # Check existing Infocom
            infocom = glpi.get_computer_infocom(comp_id)
            existing_warranty_date = None
            if infocom:
                existing_warranty_date = infocom_expiration(infocom)

            if existing_warranty_date and not args.force:
                if not should_recheck_warranty(str(existing_warranty_date), args.recheck_days):
                    if args.verbose:
                        logger.info(f"Skipping {comp_name} ({serial}) - warranty valid until {existing_warranty_date}")
                    stats["skipped"] += 1
                    continue

            brand = args.brand or detect_brand(serial, model_name, mfg_name)
            stats["processed"] += 1

            logger.info(f"Querying CheckWarranty: {comp_name} [Serial: {serial}, Brand: {brand or 'auto-detect'}]...")
            if getattr(args, "plan", False):
                logger.info("[PLAN] Lookup required; no CheckWarranty request sent")
                stats["skipped"] += 1
                continue
            result = cw.lookup_device(serial, brand)

            if not result or result.get("status") == "not_found":
                logger.warning(f"Device not found on vendor portal: {serial}")
                stats["not_found"] += 1
                continue

            warranty, specs = extract_result(result)
            infocom_payload = build_infocom_payload(warranty, specs, infocom or {}, args.sync_specs)
            if not infocom_payload:
                stats["not_found"] += 1
                continue

            logger.info(f" -> Plan update {comp_name} ({serial}): {infocom_payload}")

            if args.dry_run:
                stats["updated"] += 1
                continue

            success = glpi.upsert_infocom(comp_id, infocom_payload)
            if success:
                logger.info(f" -> Successfully updated GLPI Infocom for #{comp_id}")
                stats["updated"] += 1
            else:
                logger.error(f" -> Failed to update GLPI Infocom for #{comp_id}")
                stats["errors"] += 1

        write_cursor(args, glpi_url, cursor + len(computers) if len(computers) == args.limit else 0)
        logger.info("=" * 60)
        logger.info(
            f"GLPI Sync Complete. Processed: {stats['processed']}, "
            f"Updated: {stats['updated']}, Skipped: {stats['skipped']}, "
            f"Not Found: {stats['not_found']}, Errors: {stats['errors']}"
        )

    finally:
        glpi.kill_session()


def main():
    parser = argparse.ArgumentParser(
        description="Synchronize hardware warranty status from CheckWarranty to GLPI ITAM."
    )
    parser.add_argument("--dry-run", action="store_true", help="Simulate execution without modifying GLPI.")
    parser.add_argument("--limit", type=int, default=50, help="Max computers to fetch and inspect (default: 50).")
    parser.add_argument("--force", action="store_true", help="Update warranty even if already populated.")
    parser.add_argument(
        "--recheck-days",
        type=int,
        default=0,
        help="Recheck warranty if expired or expiring within N days (default: 0)."
    )
    parser.add_argument("--brand", type=str, choices=["dell", "lenovo", "hp", "apple"], help="Override brand detection.")
    parser.add_argument("--sync-specs", action="store_true", default=True, help="Include specs in warranty_info.")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose debug logging.")

    parser.add_argument("--plan", action="store_true", help="List assets needing lookup without calling CheckWarranty or writing changes.")
    parser.add_argument("--state-file", default=os.environ.get("CHECKWARRANTY_STATE_FILE", str(Path.home() / ".local" / "state" / "checkwarranty" / "glpi.json")), help="Persistent cursor file; schedule one process per file.")
    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    run_sync(args)


if __name__ == "__main__":
    main()
