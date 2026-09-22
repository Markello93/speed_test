#!/usr/bin/env python3
"""Тесты для speedtest.py. Запуск: python -m unittest -v"""

import argparse
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from speedtest import (
    BYTES_PER_MIB,
    RequestResult,
    download_once,
    format_report,
    positive_int,
    run_requests,
    summarize,
)


class SummarizeTest(unittest.TestCase):
    def test_throughput_is_total_bytes_over_total_time(self):
        """Скорость считается как объём/время, а не как среднее скоростей запросов.

        Среднее арифметическое по скоростям дало бы (1.0 + 1/3) / 2 ≈ 0.667 МиБ/с,
        что не равно фактической пропускной способности: 2 МиБ за 4 с = 0.5 МиБ/с.
        """
        results = [
            RequestResult(bytes_read=BYTES_PER_MIB, elapsed_s=1.0),
            RequestResult(bytes_read=BYTES_PER_MIB, elapsed_s=3.0),
        ]

        stats = summarize(results, attempts=2)

        self.assertAlmostEqual(stats.mib_per_s, 0.5)

    def test_mbit_per_s_conversion(self):
        results = [RequestResult(bytes_read=BYTES_PER_MIB, elapsed_s=1.0)]

        stats = summarize(results, attempts=1)

        self.assertAlmostEqual(stats.mbit_per_s, BYTES_PER_MIB * 8 / 1_000_000)

    def test_median_is_robust_to_outlier(self):
        results = [
            RequestResult(bytes_read=100, elapsed_s=1.0),
            RequestResult(bytes_read=100, elapsed_s=1.0),
            RequestResult(bytes_read=100, elapsed_s=100.0),
        ]

        stats = summarize(results, attempts=3)

        self.assertAlmostEqual(stats.median_time_s, 1.0)
        self.assertAlmostEqual(stats.avg_time_s, 34.0)

    def test_failed_attempts_are_reported_but_not_averaged(self):
        results = [RequestResult(bytes_read=100, elapsed_s=2.0)]

        stats = summarize(results, attempts=10)

        self.assertEqual(stats.successes, 1)
        self.assertEqual(stats.attempts, 10)
        self.assertAlmostEqual(stats.avg_time_s, 2.0)

    def test_totals(self):
        results = [
            RequestResult(bytes_read=300, elapsed_s=1.5),
            RequestResult(bytes_read=700, elapsed_s=2.5),
        ]

        stats = summarize(results, attempts=2)

        self.assertEqual(stats.total_bytes, 1000)
        self.assertAlmostEqual(stats.total_time_s, 4.0)


class FormatReportTest(unittest.TestCase):
    def test_report_contains_key_metrics(self):
        stats = summarize([RequestResult(bytes_read=BYTES_PER_MIB, elapsed_s=1.0)], attempts=2)

        report = format_report(stats)

        self.assertIn("1/2", report)
        self.assertIn("1.00 МиБ/с", report)
        self.assertIn("Мбит/с", report)


class PositiveIntTest(unittest.TestCase):
    def test_accepts_positive(self):
        self.assertEqual(positive_int("10"), 10)

    def test_rejects_zero_and_negative(self):
        for raw in ("0", "-5"):
            with self.subTest(raw=raw), self.assertRaises(argparse.ArgumentTypeError):
                positive_int(raw)


class LocalServerTest(unittest.TestCase):
    """Интеграционные тесты против локального HTTP-сервера — без внешней сети."""

    PAYLOAD = b"x" * (256 * 1024)

    def setUp(self):
        payload = self.PAYLOAD

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/heavy.jpg"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_download_once_counts_every_byte(self):
        result = download_once(self.url, timeout=5.0, chunk_size=4096)

        self.assertEqual(result.bytes_read, len(self.PAYLOAD))
        self.assertGreater(result.elapsed_s, 0.0)

    def test_run_requests_performs_requested_count(self):
        results = run_requests(self.url, count=3, timeout=5.0, chunk_size=4096)

        self.assertEqual(len(results), 3)

    def test_failed_request_does_not_abort_the_series(self):
        self.server.shutdown()
        self.server.server_close()

        with self.assertLogs("speedtest", level="ERROR"):
            results = run_requests(self.url, count=2, timeout=2.0, chunk_size=4096)

        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
