"""Offline check: python scripts/test_download_history.py"""

import copy
import io
import json
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch

import download_history as history


class DownloadHistoryCheck(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(history.DEFAULT_HISTORY.read_text(encoding="utf-8"))

    def test_verified_baseline_and_exact_asset_filter(self):
        snapshot = json.loads(history.DEFAULT_HISTORY.with_name("release-downloads.json").read_text(encoding="utf-8"))
        counts = history.count_assets(a for r in snapshot["releases"] for a in r["assets"])
        self.assertEqual(self.data["samples"][0], {"collected_at": snapshot["as_of_utc"], **counts, "total": sum(counts.values())})
        self.assertEqual(counts, {"exe": 1606, "zip": 243})
        excluded = ["latest.json", "setup.exe.sig", "portable.zip.sig", "package.whl", "SHA256SUMS.txt", "source.zip", "sbom.cdx.json"]
        self.assertEqual(history.count_assets({"name": name, "download_count": 999} for name in excluded), {"exe": 0, "zip": 0})
        self.assertEqual(history.count_assets([{"name": "SHARPpy-Reimagined-v1-windows-x64-setup.exe.sig", "download_count": 999}]), {"exe": 0, "zip": 0})
        with self.assertRaises(ValueError):
            history.count_assets([{"name": "SHARPpy-Reimagined-v1.exe", "download_count": -1}])

    def test_pagination_and_fixed_host_without_redirects(self):
        responses = [io.BytesIO(json.dumps(list(range(100))).encode()), io.BytesIO(b'[100]')]
        for response in responses:
            response.status = 200
        connection = MagicMock()
        connection.getresponse.side_effect = responses
        with patch.object(history, "HTTPSConnection", return_value=connection) as https:
            self.assertEqual(list(history.api_items("releases", "test-token")), list(range(101)))
        self.assertEqual(https.call_count, 2)
        https.assert_called_with("api.github.com", timeout=30)
        self.assertIn("page=2", connection.request.call_args.args[1])
        response = io.BytesIO(b'[]')
        response.status = 302
        connection.getresponse.side_effect = None
        connection.getresponse.return_value = response
        with patch.object(history, "HTTPSConnection", return_value=connection), self.assertRaises(RuntimeError):
            list(history.api_items("releases", "test-token"))

    def test_collector_skips_drafts_and_fetches_asset_pages(self):
        def items(resource, token):
            if resource == "releases":
                return iter([{"id": 1, "draft": False}, {"id": 2, "draft": True}])
            self.assertEqual(resource, "releases/1/assets")
            return iter([{"name": "SHARPpy-Reimagined-v1.exe", "download_count": 4}, {"name": "latest.json", "download_count": 1000}])
        with patch.object(history, "api_items", side_effect=items):
            sample = history.collect("")
        self.assertEqual((sample["exe"], sample["zip"], sample["total"]), (4, 0, 4))
        history.sample_time(sample)

    def test_daily_append_preserves_baseline_and_real_decreases(self):
        original = copy.deepcopy(self.data)
        baseline = original["samples"][0]
        history.add_sample(self.data, {**baseline, "collected_at": "2026-10-25T23:00:00Z", "exe": 1607, "total": 1850})
        self.assertEqual(self.data, original)
        later = {"collected_at": "2026-10-26T00:17:05Z", "exe": 1607, "zip": 244, "total": 1851}
        history.add_sample(self.data, later)
        self.assertEqual(self.data["samples"], [baseline, later])
        with self.assertRaises(ValueError):
            history.add_sample(self.data, baseline)
        history.add_sample(self.data, {"collected_at": "2026-10-27T00:17:05Z", "exe": 0, "zip": 0, "total": 0})
        self.assertEqual(self.data["samples"][-1]["total"], 0)

    def test_single_point_and_later_line_are_measured_dates_only(self):
        namespace = {"s": "http://www.w3.org/2000/svg"}
        svg = ET.fromstring(history.render_svg(self.data))
        self.assertEqual(len(svg.findall(".//s:circle", namespace)), 1)
        self.assertEqual(svg.findall(".//s:polyline", namespace), [])
        self.assertEqual(json.loads(svg.find("s:metadata", namespace).text), self.data["samples"])
        self.assertIn("History starts 25 Oct 2026", " ".join(svg.itertext()))
        visible_text = [node.text for node in svg.findall(".//s:text", namespace)]
        self.assertEqual(svg.attrib["viewBox"], "0 0 760 420")
        self.assertTrue({"Download History", history.REPOSITORY, "Date", "Downloads", "1,849"}.issubset(visible_text))
        self.assertFalse(any("Source:" in text or "snapshot" in text for text in visible_text))
        history.add_sample(self.data, {"collected_at": "2026-10-27T00:17:05Z", "exe": 1607, "zip": 244, "total": 1851})
        svg = ET.fromstring(history.render_svg(self.data))
        self.assertEqual(len(svg.findall(".//s:circle", namespace)), 2)
        self.assertEqual(len(svg.findall(".//s:polyline", namespace)), 1)
        invalid = copy.deepcopy(self.data)
        invalid["samples"][0]["total"] = 1
        with self.assertRaises(ValueError):
            history.render_svg(invalid)


if __name__ == "__main__":
    unittest.main()
