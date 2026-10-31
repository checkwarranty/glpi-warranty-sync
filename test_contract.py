import unittest
from unittest.mock import patch
import sync
import io
import urllib.error

class ContractTests(unittest.TestCase):
    def test_cursor_beyond_reduced_inventory_ends_page(self):
        client = sync.GlpiClient('http://localhost', 'app', 'user')
        error = urllib.error.HTTPError('http://localhost/Computer', 400, 'range', {},
            io.BytesIO(b'["ERROR_RANGE_EXCEED_TOTAL", "range exceeds total"]'))
        with patch.object(client, '_request', side_effect=error):
            self.assertEqual(client.get_computers(start_offset=100), [])

    def test_other_bad_requests_are_not_treated_as_empty_inventory(self):
        client = sync.GlpiClient('http://localhost', 'app', 'user')
        error = urllib.error.HTTPError('http://localhost/Computer', 400, 'invalid', {},
            io.BytesIO(b'["ERROR_BAD_ARRAY", "invalid query"]'))
        with patch.object(client, '_request', side_effect=error):
            with self.assertRaises(urllib.error.HTTPError):
                client.get_computers(start_offset=100)

    def test_real_api_specs_only_result(self):
        warranty, specs = sync.extract_result({"status": "completed", "warranty": {"status": "unknown", "details": {"processor": "Xeon", "memory": "16 GB"}}})
        self.assertEqual(specs, {"cpu": "Xeon", "ram": "16 GB"})

    def test_errors_and_not_found_do_not_become_success(self):
        for value in ({"status":"failed"}, {"status":"pending"}, {"status":"completed","warranty":{"status":"not_found","details":{"message":"missing"}}}, None):
            self.assertEqual(sync.extract_result(value), ({}, {}))

    def test_async_partial_returns_completed_item(self):
        client = sync.CheckWarrantyClient("https://checkwarranty.info/api/v1", "sandbox")
        item = {"status":"completed","warranty":{"endDate":"2027-01-01"}}
        with patch.object(client, "_request", return_value={"status":"partial","items":[item]}), patch.object(sync.time, "sleep"):
            self.assertEqual(client._poll_batch_result("batch", max_retries=1), item)

    def test_malformed_date_is_ignored(self):
        self.assertIsNone(sync.format_iso_to_date({"bad":"date"}))

    def test_glpi_preserves_purchase_and_exact_end_date(self):
        payload = sync.build_infocom_payload({"startDate":"2024-01-01","endDate":"2027-01-01"}, {}, {"buy_date":"2022-03-01","warranty_info":"Manual SLA"})
        self.assertNotIn("buy_date",payload)
        self.assertEqual(payload["warranty_date"],"2024-01-01")
        self.assertEqual(payload["warranty_duration"],36)
        self.assertIn("Manual SLA",payload["warranty_info"])
        self.assertIn("expiry=2027-01-01",payload["warranty_info"])

    def test_dell_without_start_does_not_fabricate_dates(self):
        payload = sync.build_infocom_payload({"endDate":"2027-01-05"}, {}, {})
        self.assertNotIn("buy_date",payload)
        self.assertNotIn("warranty_date",payload)
        self.assertNotIn("warranty_duration",payload)
        self.assertIn("expiry=2027-01-05",payload["warranty_info"])

    def test_unknown_start_does_not_use_old_warranty_start_as_expiry(self):
        self.assertEqual(sync.infocom_expiration({"warranty_date":"2024-01-01","warranty_duration":36}),"2027-01-01")

    def test_estimated_or_inexact_start_never_sets_native_interval(self):
        for warranty in ({"startDate":"2024-01-01","endDate":"2027-01-05"},{"startDate":"2024-01-01","endDate":"2027-01-01","startDateSource":"estimated"}):
            payload=sync.build_infocom_payload(warranty,{}, {})
            self.assertNotIn("warranty_date",payload)

    def test_auth_headers_are_not_forwarded_on_redirect(self):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        received = []
        class Sink(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                received.append(self.headers.get("X-API-Key"))
                body=b'{"status":"completed"}'
                self.send_response(200); self.end_headers(); self.wfile.write(body)
        sink=HTTPServer(("127.0.0.1",0),Sink)
        class Redirect(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                self.send_response(302)
                self.send_header("Location","http://127.0.0.1:"+str(sink.server_port)+"/sink")
                self.send_header("Content-Length", "0")
                self.end_headers()
        source=HTTPServer(("127.0.0.1",0),Redirect)
        for server in (sink,source):
            threading.Thread(target=server.serve_forever,daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
        client=sync.CheckWarrantyClient("http://127.0.0.1:"+str(source.server_port),"test-only-api-key")
        self.assertIsNone(client.lookup_device("TEST-REDIRECT","dell"))
        self.assertEqual(received,[])

    def test_partial_refresh_preserves_verified_expiry(self):
        payload=sync.build_infocom_payload({"status":"unknown"},{"cpu":"Xeon"},{"warranty_info":"Manual note\n[CheckWarranty] expiry=2027-01-05; SLA=ProSupport"})
        self.assertIn("expiry=2027-01-05",payload["warranty_info"])
        self.assertIn("SLA=ProSupport",payload["warranty_info"])
        self.assertIn("CPU=Xeon",payload["warranty_info"])
