import unittest
from unittest.mock import patch
import sync

class ContractTests(unittest.TestCase):
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
