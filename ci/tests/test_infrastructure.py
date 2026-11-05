#!/usr/bin/env python3
"""Regression checks for the CI tools themselves (FM08-FM10, FM14-FM15)."""
import base64
from copy import deepcopy
import io
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
import subprocess
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import device_probe as probe
import experiment
import remote
import report


def measurement(value=100.0):
    return {"schema_version": 1, "status": "passed",
            "context": {"kind": "host-mock", "environment": {"device_id": "test"},
                        "workload_sha256": "work", "harness_sha256": "harness"},
            "correctness": {"text": "valid"},
            "metrics": {"chain.latency_ms": {"n": 3, "p50": value, "p95": value}},
            "samples": {"chain.latency_ms": [value] * 3}}


def wav_data(channels=1, width=2, frames=400):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(width)
        audio.setframerate(16000)
        audio.writeframes(bytes(frames * width * channels))
    return stream.getvalue()


class Infrastructure(unittest.TestCase):
    gates = {"latency_ms.p95": {"direction": "lower", "relative": 0.2, "absolute": 10}}

    def test_FM08_regression_and_tolerances(self):
        baseline = measurement()
        for value, expected in ((100, "passed"), (119, "passed"), (121, "regression")):
            self.assertEqual(report.compare(measurement(value), baseline, self.gates)["status"], expected)
        # An absolute noise floor still applies at tiny baseline values.
        self.assertEqual(report.compare(measurement(5), measurement(1), self.gates)["status"], "passed")
        result = report.compare(measurement(11), measurement(0), self.gates)
        self.assertEqual(result["status"], "regression")
        self.assertIsNone(result["rows"][0]["percent"])
        self.assertEqual(report.compare(baseline, None, self.gates)["status"], "no-baseline")
        higher = {"latency_ms.p95": {"direction": "higher", "relative": 0.1, "absolute": 1}}
        self.assertEqual(report.compare(measurement(80), baseline, higher)["status"], "regression")

    def test_FM08_report_outputs_cannot_replace_inputs(self):
        from contextlib import redirect_stderr
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "measurement.json"
            path.write_text(json.dumps(measurement()), encoding="utf-8")
            original = path.read_bytes()
            cases = [(report.main, ["report.py", "--candidate", str(path)]),
                     (experiment.main, ["experiment.py", "--measurement", str(path), "--baseline-variant", "a", "--candidate-variant", "b"])]
            for function, arguments in cases:
                with patch.object(sys, "argv", arguments + ["--output", str(path.with_suffix(""))]), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                    function()
                self.assertEqual(error.exception.code, 2)
                self.assertEqual(path.read_bytes(), original)

    def test_FM08_missing_nonfinite_and_incomparable(self):
        baseline = measurement()
        for key in ("environment", "workload_sha256", "harness_sha256", "kind"):
            candidate = deepcopy(baseline)
            candidate["context"][key] = {"changed": True} if key == "environment" else "changed"
            self.assertEqual(report.compare(candidate, baseline, self.gates)["status"], "incomparable")
        variants = []
        candidate = deepcopy(baseline)
        candidate["metrics"] = {}
        variants.append(candidate)
        for field, value in (("p95", float("nan")), ("p50", -1), ("n", 2)):
            candidate = deepcopy(baseline)
            candidate["metrics"]["chain.latency_ms"][field] = value
            variants.append(candidate)
        candidate = deepcopy(baseline)
        candidate.pop("correctness")
        variants.append(candidate)
        candidate = deepcopy(baseline)
        candidate["samples"]["chain.latency_ms"].pop()
        variants.append(candidate)
        for candidate in variants:
            with self.subTest(candidate=candidate):
                self.assertEqual(report.compare(candidate, baseline, self.gates)["status"], "invalid")
        self.assertEqual(report.compare(baseline, baseline, {"missing.p95": self.gates["latency_ms.p95"]})["status"], "invalid")
        candidate = deepcopy(baseline)
        candidate["status"] = "failed"
        self.assertEqual(report.compare(candidate, None, self.gates)["status"], "invalid")

    def test_FM14_config_change_is_explicit_and_html_is_escaped(self):
        baseline, candidate = measurement(), measurement()
        baseline["context"]["configuration_sha256"] = "old"
        candidate["context"]["configuration_sha256"] = "new"
        self.assertEqual(report.compare(candidate, baseline, self.gates)["status"], "incomparable")
        result = report.compare(candidate, baseline, self.gates, allow_config_change=True)
        self.assertEqual(result["status"], "passed")
        result["reason"] = "<script>bad()</script>"
        markdown, document = report.render(result, candidate)
        self.assertIn("<script>", markdown)
        self.assertNotIn("<script>", document)
        self.assertIn("&lt;script&gt;", document)

    def test_FM14_adapter_pin_requires_gitlink(self):
        import actions
        settings = {"adapter_sources": [{"repository": "example/model", "path": "external/model"}]}
        with tempfile.TemporaryDirectory() as name:
            output = Path(name) / "github-output"
            completed = subprocess.CompletedProcess([], 0, "160000 commit " + "a" * 40 + "\texternal/model\n", "")
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}), patch.object(actions, "config", return_value=settings), patch.object(actions.subprocess, "run", return_value=completed):
                actions.adapter_pin()
                self.assertIn("ref=" + "a" * 40, output.read_text(encoding="utf-8"))
                completed.stdout = "100644 blob " + "b" * 40 + "\texternal/model\n"
                with self.assertRaises(ValueError):
                    actions.adapter_pin()

    def test_FM14_parameter_experiment_has_explicit_output_contract(self):
        record = measurement()
        stats = record["metrics"].pop("chain.latency_ms")
        samples = record["samples"].pop("chain.latency_ms")
        record["parameters"], record["correctness"] = {}, {}
        for name, chunks, frames in (("short", ["你", "好"], 200), ("long", ["你好"], 100)):
            record["parameters"][name] = {"max_chars": len(chunks), "out_rate": 16000}
            record["correctness"][name] = {"text": "你好", "chunks": chunks, "audio_rate": 16000, "frames": frames}
            record["metrics"][name + ".latency_ms"] = dict(stats)
            record["samples"][name + ".latency_ms"] = list(samples)
        result, _ = experiment.experiment(record, "short", "long", self.gates)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["experiment"]["baseline"]["chunks"], 2)
        self.assertEqual(result["experiment"]["candidate"]["frames"], 100)
        record["correctness"]["long"]["audio_rate"] = 48000
        self.assertEqual(experiment.experiment(record, "short", "long", self.gates)[0]["status"], "incomparable")
        record["correctness"]["long"].update(text="不同", chunks=["不同"], audio_rate=16000)
        self.assertEqual(experiment.experiment(record, "short", "long", self.gates)[0]["status"], "invalid")

    def test_FM09_http_contract_redirect_and_allowlist(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path == "/health":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"ok":true,"service":"lang-server"}')
                elif self.path == "/status":
                    self.send_response(302)
                    self.send_header("Location", "/action/open")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(b"<html>not JSON</html>")

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        service = {"port": server.server_port}
        try:
            obj, elapsed, _ = probe.http(service, "/health")
            probe.require_fields(obj, {"ok": True, "service": "lang-server"})
            self.assertGreaterEqual(elapsed, 0)
            for path in ("/action/open", "/health?redirect=x", "/status"):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    probe.http(service, path)
            html, _, _ = probe.http(service, "/components")
            with self.assertRaises(ValueError):
                probe.require_fields(html, {})
            with self.assertRaises(ValueError):
                probe.require_fields({"ok": 1}, {"ok": True})
            with self.assertRaises(ValueError):
                probe.http({"host": "example.com", "port": 80}, "/health")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)
            self.assertFalse(thread.is_alive())

    def test_FM09_wyoming_complete_and_truncated_frames(self):
        def query(frame, passed):
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(3)
            def send():
                with listener:
                    connection, _ = listener.accept()
                    with connection:
                        connection.recv(1024)
                        connection.sendall(frame)
            thread = threading.Thread(target=send, daemon=True)
            thread.start()
            spec = {"port": listener.getsockname()[1], "capability": "asr", "timeout_s": 2}
            try:
                if passed:
                    probe.wyoming(spec)
                else:
                    with self.assertRaises(ValueError):
                        probe.wyoming(spec)
            finally:
                thread.join(3)
                self.assertFalse(thread.is_alive())
        query(b'{"type":"info","data":{"asr":[{"name":"mock"}]}}\n', True)
        query(b'{"type":"info","data_length":100}\n{}', False)
        query(b'{"type":"info","data":{"tts":[{}]}}\n', False)

    def test_FM09_ssh_identity_transport_and_cleanup(self):
        with tempfile.TemporaryDirectory() as name:
            known = Path(name) / "known_hosts"
            known.write_text("fixture-host-key\n", encoding="utf-8")
            options = SimpleNamespace(target="tester@board", remote_python="python3", known_hosts=known,
                                      identity=None, port=22, mode="health", timeout=30, allow_inference=False)
            calls = []
            def completed(command, **kwargs):
                self.assertNotIn("shell", kwargs)
                calls.append(command)
                data = b'{"schema_version":1,"status":"passed"}'
                return subprocess.CompletedProcess(command, 0, data, b"")
            with patch.object(remote.subprocess, "run", side_effect=completed):
                self.assertEqual(remote.transport(options, {"schema_version": 1})["status"], "passed")
            self.assertEqual(len(calls), 3)
            self.assertEqual(calls[0][0], "scp")
            for command in calls:
                self.assertIn("StrictHostKeyChecking=yes", command)
                self.assertIn("BatchMode=yes", command)
            self.assertIn("--deadline 30", calls[1][-1])
            self.assertIn("rm -- /tmp/ha4fpai-ci-", calls[2][-1])
            self.assertNotIn("password", str(calls).lower())
            options.target = "-oProxyCommand=anything"
            with self.assertRaises(ValueError):
                remote.transport(options, {})

    def test_FM10_corpus_cer_and_bad_inference(self):
        self.assertEqual(probe.character_error_rate("你好 世界", "你好世界"), 0)
        self.assertEqual(probe.character_error_rate("你好", "你们"), 0.5)
        with self.assertRaises(ValueError):
            probe.character_error_rate(" ", "text")
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            spec = {"id": "asr", "audio_file": "sample.wav"}
            for data in (wav_data(channels=2), wav_data(width=1), wav_data(frames=0)):
                (root / "sample.wav").write_bytes(data)
                with self.assertRaises(ValueError):
                    remote.prepare({"schema_version": 1, "workloads": [dict(spec)]}, "benchmark", root)
            (root / "sample.wav").write_bytes(wav_data())
            prepared = remote.prepare({"schema_version": 1, "workloads": [dict(spec)]}, "benchmark", root)
            self.assertEqual(base64.b64decode(prepared["workloads"][0]["audio_base64"]), wav_data())
            self.assertEqual(prepared["workloads"][0]["input_seconds"], 0.025)
            (root / "sample.b64").write_bytes(base64.b64encode(wav_data()) + b"\n")
            (root / "metadata.json").write_text(json.dumps({"schema_version": 1, "text": "test", "sha256": "0" * 64}), encoding="utf-8")
            with self.assertRaises(ValueError):
                remote.prepare({"schema_version": 1, "workloads": [{"id": "bad-hash", "audio_file": "sample.b64", "audio_encoding": "base64", "corpus_metadata": "metadata.json"}]}, "benchmark", root)
        corpus = {"id": "speech", "audio_file": "ci/fixtures/asr-zh.wav.b64", "audio_encoding": "base64",
                  "corpus_metadata": "ci/fixtures/asr-zh.json", "max_cer": 0.2}
        speech = remote.prepare({"schema_version": 1, "workloads": [corpus]}, "benchmark", Path(__file__).resolve().parents[2])["workloads"][0]
        self.assertEqual(speech["expected_text"], "这是一条自动化测试。")
        self.assertLess(speech["input_seconds"], 10)
        self.assertGreater(speech["input_seconds"], 1)
        asr = {"kind": "asr", "path": "/api/v1/asr", "audio_base64": base64.b64encode(wav_data()).decode()}
        for response in ({"code": 1, "text": "looks good"}, {"code": False, "text": "x"}, {"code": 0.0, "text": "x"}, {"text": ""}, {"text": "x", "stages_error": {"asr": "oops"}}):
            with patch.object(probe, "http", return_value=(response, 1.0, "json")), self.assertRaises(ValueError):
                probe.inference({}, asr)
        for audio in (wav_data(frames=0), wav_data(frames=400)):
            with patch.object(probe, "http", return_value=(audio, 1.0, "audio/wav")), self.assertRaises(ValueError):
                probe.inference({}, {"kind": "tts", "path": "/tts", "text": "test"})

    def test_FM15_partial_evidence_drift_restart_and_memory(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "source.py").write_text("pass\n", encoding="utf-8")
            (root / "config.yaml").write_text("server: test\n", encoding="utf-8")
            manifest = {"schema_version": 1, "source_root": name,
                        "source_files": ["source.py"], "configuration_files": ["config.yaml"],
                        "services": [{"id": "lang", "port": 1, "checks": [{"path": "/health", "expect": {"ok": True}}]}],
                        "workloads": [{"id": "say", "service": "lang", "kind": "tts", "path": "/tts", "text": "test"}],
                        "process_patterns": ["server"], "samples": 3, "warmup": 0, "soak_iterations": 3,
                        "max_rss_growth_kib": 100}
            separate = root / "separate-config"
            separate.mkdir()
            (separate / "config.yaml").write_text("server: test\n", encoding="utf-8")
            (root / "config.yaml").unlink()
            manifest["configuration_root"] = str(separate)
            empty = {"processes": [], "rss_kib": 0, "cpu_ticks": 0, "pattern_counts": {"server": 0}}
            before = {"processes": [{"pid": 10, "rss_kib": 100}], "rss_kib": 100, "cpu_ticks": 1,
                      "pattern_counts": {"server": 1}}
            with patch.object(probe, "http", return_value=({"ok": True}, 1, "json")), patch.object(probe, "resources", return_value=before):
                self.assertEqual(probe.probe(manifest, "health")["status"], "passed")
                (root / "source.py").unlink()
                failed = probe.probe(manifest, "health")
                self.assertEqual(failed["status"], "failed")
                self.assertTrue(any(r["id"] == "lang/health" and r["status"] == "passed" for r in failed["checks"]))
                self.assertTrue(any(r["id"] == "source-fingerprint" for r in failed["checks"]))
                (root / "source.py").write_text("pass\n", encoding="utf-8")
            def changed(*args):
                (root / "source.py").write_text("changed = True\n", encoding="utf-8")
                return {"ok": True}, 1, "json"
            with patch.object(probe, "http", side_effect=changed), patch.object(probe, "resources", return_value=before):
                result = probe.probe(manifest, "health")
                self.assertTrue(any(r["id"] == "source-changed-during-run" for r in result["checks"]))
            after = deepcopy(before)
            after["processes"][0]["pid"] = 20
            after["rss_kib"] = 300
            with patch.object(probe, "http", return_value=({"ok": True}, 1, "json")), patch.object(probe, "resources", side_effect=[before, after]), patch.object(probe, "inference", return_value=(1, {"rtf": 0.5})):
                result = probe.probe(manifest, "soak", True)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["metrics"]["say.latency_ms"]["n"], 3)
                self.assertTrue({"process-restart", "memory-growth"} <= {r["id"] for r in result["checks"]})
            with patch.object(probe, "http", return_value=({"ok": True}, 1, "json")), patch.object(probe, "resources", return_value=empty):
                self.assertEqual(probe.probe(manifest, "health")["status"], "failed")
            for change in ({"services": []}, {"samples": 0}, {"workloads": []}):
                invalid = deepcopy(manifest)
                invalid.update(change)
                with self.assertRaises(ValueError):
                    probe.probe(invalid, "benchmark", True)
            manifest["warmup"] = 1
            warmed = deepcopy(before)
            warmed["rss_kib"] = 1000
            settled = deepcopy(warmed)
            settled["rss_kib"] = 1010
            with patch.object(probe, "http", return_value=({"ok": True}, 1, "json")), patch.object(probe, "resources", side_effect=[before, warmed, settled]), patch.object(probe, "inference", return_value=(1, {})) as invoke:
                result = probe.probe(manifest, "soak", True)
                self.assertEqual(result["status"], "passed")
                self.assertEqual(result["metrics"]["services.rss_growth_kib"]["value"], 10)
                self.assertEqual(invoke.call_count, 4)
            versions = []
            for active in ("echo", "remote-api"):
                with patch.object(probe, "http", return_value=({"ok": True, "stages": {"asr": active}}, 1, "json")), patch.object(probe, "resources", return_value=before):
                    versions.append(probe.probe(manifest, "health"))
            self.assertNotEqual(versions[0]["context"]["configuration_sha256"], versions[1]["context"]["configuration_sha256"])
            self.assertEqual(report.compare(versions[1], versions[0], {})["status"], "incomparable")
            self.assertEqual(report.compare(versions[1], versions[0], {}, allow_config_change=True)["status"], "passed")
            with self.assertRaises(ValueError):
                probe.probe(manifest, "benchmark", False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
