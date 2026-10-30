#!/usr/bin/env python3
"""
Unit tests for GLPI ITAM sync script.
Tests brand detection, duration calculation, date formatting, API mapping, and dry-run execution.
"""

import unittest
from unittest.mock import MagicMock, patch
import os
import sys
from datetime import date, timedelta
import argparse

sys.path.insert(0, os.path.dirname(__file__))

from sync import (
    detect_brand,
    format_iso_to_date,
    calculate_warranty_months,
    should_recheck_warranty,
    GlpiClient,
    CheckWarrantyClient,
    run_sync,
)


class TestGlpiSync(unittest.TestCase):

    def test_detect_brand_by_manufacturer(self):
        self.assertEqual(detect_brand("XYZ1234", manufacturer_name="Dell Inc."), "dell")
        self.assertEqual(detect_brand("XYZ1234", manufacturer_name="Hewlett-Packard"), "hp")
        self.assertEqual(detect_brand("XYZ1234", manufacturer_name="Lenovo"), "lenovo")
        self.assertEqual(detect_brand("XYZ1234", manufacturer_name="Apple"), "apple")

    def test_detect_brand_by_model(self):
        self.assertEqual(detect_brand("XYZ1234", model_name="Latitude 5520"), "dell")
        self.assertEqual(detect_brand("XYZ1234", model_name="EliteBook 840 G8"), "hp")
        self.assertEqual(detect_brand("XYZ1234", model_name="ThinkPad T14 Gen 2"), "lenovo")
        self.assertEqual(detect_brand("XYZ1234", model_name="MacBook Pro 14"), "apple")

    def test_detect_brand_by_serial(self):
        self.assertEqual(detect_brand("8HG76F2"), "dell")
        self.assertEqual(detect_brand("PF4A1234"), "lenovo")
        self.assertEqual(detect_brand("5CG1234567"), "hp")
        self.assertEqual(detect_brand("C02G12345678"), "apple")
        self.assertIsNone(detect_brand("UNKNOWN_SERIAL_123"))

    def test_calculate_warranty_months(self):
        self.assertEqual(calculate_warranty_months("2024-01-01", "2027-01-01"), 36)
        self.assertEqual(calculate_warranty_months("2024-06-01", "2025-06-01"), 12)
        self.assertEqual(calculate_warranty_months("2024-01-01", "2024-01-01"), 0)
        self.assertIsNone(calculate_warranty_months(None, "2025-01-01"))

    def test_format_iso_to_date(self):
        self.assertEqual(format_iso_to_date("2026-12-31T23:59:59.000Z"), "2026-12-31")
        self.assertEqual(format_iso_to_date("2025-05-01"), "2025-05-01")
        self.assertIsNone(format_iso_to_date(None))
        self.assertIsNone(format_iso_to_date("invalid-date"))

    def test_should_recheck_warranty(self):
        today = date.today()
        self.assertTrue(should_recheck_warranty(None, recheck_days=30))
        self.assertTrue(should_recheck_warranty("", recheck_days=30))

        # Expired warranty
        yesterday_str = (today - timedelta(days=1)).isoformat()
        self.assertTrue(should_recheck_warranty(yesterday_str, recheck_days=0))

        # Expiring soon
        soon_str = (today + timedelta(days=10)).isoformat()
        self.assertTrue(should_recheck_warranty(soon_str, recheck_days=30))

        # Expiring later
        future_str = (today + timedelta(days=90)).isoformat()
        self.assertFalse(should_recheck_warranty(future_str, recheck_days=30))

    def test_glpi_client_url_clean(self):
        glpi = GlpiClient("https://glpi.company.local/ ", "app_tok", "user_tok")
        self.assertEqual(glpi.api_url, "https://glpi.company.local/apirest.php")
        self.assertEqual(glpi.app_token, "app_tok")

    @patch("sync.GlpiClient._request")
    def test_glpi_session_lifecycle(self, mock_request):
        mock_request.side_effect = [
            {"session_token": "sess_xyz123"},
            {},
        ]
        glpi = GlpiClient("https://glpi.test", "app_tok", "user_tok")
        self.assertTrue(glpi.init_session())
        self.assertEqual(glpi.session_token, "sess_xyz123")

        glpi.kill_session()
        self.assertIsNone(glpi.session_token)

    @patch("sync.GlpiClient._request")
    def test_glpi_upsert_infocom_create(self, mock_request):
        # 1. get_computer_infocom -> 404/None
        # 2. POST /Infocom
        mock_request.side_effect = [
            None,
            {"id": 42, "message": "Infocom created"},
        ]
        glpi = GlpiClient("https://glpi.test", "app_tok", "user_tok")
        glpi.session_token = "sess_1"

        success = glpi.upsert_infocom(10, {"buy_date": "2024-01-01", "warranty_duration": 36})
        self.assertTrue(success)
        mock_request.assert_called_with(
            "POST",
            "/Infocom",
            data={"input": {"itemtype": "Computer", "items_id": 10, "buy_date": "2024-01-01", "warranty_duration": 36}}
        )

    @patch("sync.GlpiClient._request")
    def test_glpi_upsert_infocom_update(self, mock_request):
        # 1. get_computer_infocom -> returns existing id=42
        # 2. PUT /Infocom/42
        mock_request.side_effect = [
            [{"id": 42, "itemtype": "Computer", "items_id": 10}],
            {"id": 42, "message": "Infocom updated"},
        ]
        glpi = GlpiClient("https://glpi.test", "app_tok", "user_tok")
        glpi.session_token = "sess_1"

        success = glpi.upsert_infocom(10, {"warranty_duration": 36})
        self.assertTrue(success)
        mock_request.assert_called_with(
            "PUT",
            "/Infocom/42",
            data={"input": {"id": 42, "warranty_duration": 36}}
        )

    @patch.dict(os.environ, {
        "GLPI_URL": "https://glpi.test",
        "GLPI_APP_TOKEN": "app_token_test",
        "GLPI_USER_TOKEN": "user_token_test",
        "CHECKWARRANTY_API_KEY": "cw_key_test",
    })
    @patch("sync.GlpiClient.init_session")
    @patch("sync.GlpiClient.kill_session")
    @patch("sync.GlpiClient.get_computers")
    @patch("sync.GlpiClient.get_computer_infocom")
    @patch("sync.CheckWarrantyClient.lookup_device")
    @patch("sync.GlpiClient.upsert_infocom")
    def test_run_sync_dry_run(self, mock_upsert, mock_lookup, mock_get_infocom, mock_get_comps, mock_kill, mock_init):
        mock_init.return_value = True
        mock_get_comps.return_value = [
            {
                "id": 101,
                "name": "PC-FIN-01",
                "serial": "8HG76F2",
                "manufacturers_id": "Dell",
                "computermodels_id": "Latitude 5420",
            }
        ]
        mock_get_infocom.return_value = None
        mock_lookup.return_value = {
            "status": "completed",
            "warranty": {
                    "startDate": "2024-01-10T00:00:00Z",
                    "endDate": "2027-01-10T00:00:00Z",
                    "status": "active",
                    "coverageType": "ProSupport",
                },

        }

        args = argparse.Namespace(
            dry_run=True,
            limit=10,
            force=False,
            recheck_days=0,
            brand=None,
            sync_specs=True,
            verbose=False,
        )

        run_sync(args)
        mock_init.assert_called_once()
        mock_kill.assert_called_once()
        mock_lookup.assert_called_once_with("8HG76F2", "dell")
        mock_upsert.assert_not_called()


if __name__ == "__main__":
    unittest.main()
