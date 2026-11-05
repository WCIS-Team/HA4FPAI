#!/usr/bin/env python3
"""Offline contract tests. Names map to docs/CI-FAILURE-MODES.md."""
import base64
import io
import json
from pathlib import Path
import random
import socket
import sys
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import wave
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "lang/server"))
sys.path.insert(0, str(ROOT / "klar"))
sys.path.insert(0, str(ROOT / "ci"))
from cfg import Config, parse
from core import Chain
from backends import REGISTRY, audioio
import app
import klar_executor


class Contracts(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(str(ROOT / "ci/fixtures"))
        self.chain = Chain(self.cfg)
        original = socket.socket.connect

        def local_only(sock, address):
            if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "::1", "localhost"):
                raise AssertionError("Offline tests attempted an external connection")
            return original(sock, address)
        self.network_guard = patch.object(socket.socket, "connect", local_only)
        self.network_guard.start()
        self.addCleanup(self.network_guard.stop)

    def inject(self, stage, value):
        self.chain._stages[(stage, self.chain.active(stage))] = value

    def test_FM01_configuration_overlay_and_registry(self):
        obj = parse('routes:\n  小克: "我在 # 原样"\nchain:\n  asr: echo # comment\n')
        self.assertEqual(obj["routes"]["小克"], "我在 # 原样")
        self.assertEqual(obj["chain"]["asr"], "echo")
        self.assertEqual(set(Chain.STAGES), set(REGISTRY))
        self.assertEqual(set(self.cfg.section("chain")), set(Chain.STAGES))
        for stage in Chain.STAGES:
            active = self.chain.active(stage)
            if active not in ("none", "off", ""):
                with self.subTest(stage=stage):
                    self.assertIn(active, REGISTRY[stage])
                    self.assertIsNotNone(self.chain.stage(stage))
        self.assertNotIn("sherpa_onnx", sys.modules)
        self.assertNotIn("onnxruntime", sys.modules)

    def test_FM02_audio_and_parameter_matrix(self):
        rng = random.Random(20261105)
        samples = [rng.uniform(-0.9, 0.9) for _ in range(1600)]
        for rate in (8000, 16000, 48000):
            with self.subTest(rate=rate):
                encoded = audioio.encode_wav(samples, rate)
                decoded, actual = audioio.decode_payload(encoded)
                self.assertEqual(actual, rate)
                self.assertEqual(len(decoded), len(samples))
                self.assertLess(max(abs(a - b) for a, b in zip(samples, decoded)), 0.0001)
        text = "中文标点。EnglishURLshttps://example.com/123！" * 4
        for cap in (1, 7, 12, 20, 40):
            with self.subTest(cap=cap):
                self.cfg.data["tokenizer_profiles"]["tts_split"]["max_chars"] = str(cap)
                chunks = Chain(self.cfg).stage("tokenizer").run(text)
                self.assertTrue(chunks)
                self.assertTrue(all(0 < len(chunk) <= cap for chunk in chunks))
                self.assertEqual("".join(chunks), text)

    def test_FM03_wake_gate_and_silence_stop_downstream(self):
        wav = audioio.encode_wav(audioio.sine(16000, 0.25), 16000)
        class Miss:
            lock = threading.Lock()
            def run(self, segments):
                return False, "no-hit"
        self.inject("kws", Miss())
        result = self.chain.run_audio(wav)
        self.assertIs(result["wake"], False)
        self.assertIn("kws", result["stages_ms"])
        self.assertNotIn("kws_route", result["stages_ms"])
        self.assertNotIn("asr", result["stages_ms"])
        self.assertNotIn("audio_b64", result)
        silent = self.chain.run_audio(audioio.encode_wav([0.0] * 1600, 16000))
        self.assertIs(silent["wake"], False)
        self.assertNotIn("kws", silent["stages_ms"])
        self.assertEqual(self.chain.gate_snapshot()["running"], 0)

    def test_FM04_transient_persistent_oom_io_and_compound_failure(self):
        wav = audioio.encode_wav(audioio.sine(16000, 0.25), 16000)
        for stage in ("asr", "llm", "tts"):
            for fault in (TimeoutError, OSError, MemoryError):
                for persistent in (False, True):
                    with self.subTest(stage=stage, fault=fault.__name__, persistent=persistent):
                        chain = Chain(self.cfg)
                        original = chain.stage(stage)
                        calls = [0]
                        class Failed:
                            lock = threading.Lock()
                            def run(self, *args, **kwargs):
                                calls[0] += 1
                                if persistent or calls[0] == 1:
                                    raise fault("injected resource boundary failure")
                                return original.run(*args, **kwargs)
                        chain._stages[(stage, chain.active(stage))] = Failed()
                        first = chain.run_audio(wav)
                        self.assertIn(stage, first["stages_error"])
                        self.assertEqual(chain.gate_snapshot()["running"], 0)
                        second = chain.run_audio(wav)
                        self.assertEqual(stage in second["stages_error"], persistent)
                        self.assertGreaterEqual(calls[0], 2)
                        self.assertEqual(chain.gate_snapshot()["running"], 0)
        class Broken:
            lock = threading.Lock()
            def run(self, *args, **kwargs):
                raise OSError("compound LLM and TTS failure")
        self.inject("llm", Broken())
        self.inject("tts", Broken())
        result = self.chain.run_text("保留文本")
        self.assertEqual(result["text"], "保留文本")
        self.assertEqual(set(result["stages_error"]), {"llm", "tts"})
        self.assertNotIn("audio_b64", result)

    def test_FM05_gate_timeout_and_exception_release(self):
        with self.chain.admit():
            out = self.chain.run_text("busy", {"tts": False})
            self.assertEqual(out["code"], 429)
            self.assertEqual(self.chain.gate_snapshot()["timeouts"], 1)
        with self.assertRaises(OSError):
            with self.chain.admit():
                raise OSError("caller disconnected")
        self.assertEqual(self.chain.gate_snapshot()["running"], 0)
        self.assertEqual(self.chain.run_text("recovered", {"tts": False})["code"], 0)

    def test_FM06_http_content_and_compatibility(self):
        app._CHAIN = self.chain
        server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        server.daemon_threads = True
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(lambda: worker.join(timeout=5))
        self.addCleanup(server.shutdown)
        base = "http://127.0.0.1:%s" % server.server_address[1]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for path in ("/health", "/components", "/stats"):
            with opener.open(base + path, timeout=5) as response:
                obj = json.load(response)
                self.assertIsInstance(obj, dict)
                if path == "/health":
                    self.assertIs(obj["ok"], True)
                    self.assertEqual(obj["service"], "lang-server")
        raw = audioio.encode_wav(audioio.sine(16000, 0.25), 16000)
        for path, key in (("/api/v1/chain", "asr_text"), ("/api/v1/asr", "text"),
                          ("/v1/audio/transcriptions", "text")):
            request = urllib.request.Request(base + path, data=raw,
                                             headers={"Content-Type": "audio/wav"})
            with opener.open(request, timeout=10) as response:
                result = json.load(response)
            self.assertTrue(result[key])
            self.assertFalse(result.get("stages_error"))
            if path == "/api/v1/chain":
                self.assertEqual(result["code"], 0)
                with wave.open(io.BytesIO(base64.b64decode(result["audio_b64"]))) as audio:
                    self.assertEqual(audio.getframerate(), 48000)
                    self.assertGreater(audio.getnframes(), 0)
        request = urllib.request.Request(base + "/tts", data=b'{"text":""}',
                                         headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as error:
            opener.open(request, timeout=5)
        self.assertEqual(error.exception.code, 400)
        error.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as error:
            opener.open(base + "/unknown-ci-route", timeout=5)
        self.assertEqual(error.exception.code, 404)
        error.exception.close()

    def test_FM07_executor_dry_run_and_partial_outage(self):
        with patch.object(klar_executor, "http", side_effect=AssertionError("unexpected action")):
            result = klar_executor.run_direct("开始录制", dry_run=True)
            self.assertTrue(result.get("dry_run"))
        healthy = [(200, {"ok": True}), (200, {"mode": "off"}), (200, {"ready": True})]
        with patch.object(klar_executor, "http", side_effect=healthy):
            self.assertIs(klar_executor.act_status({})[0], True)
        for failure in ((None, "disconnected"), (503, {"error": "not ready"})):
            with self.subTest(status=failure[0]):
                with patch.object(klar_executor, "http", side_effect=[failure] + healthy[1:]):
                    result = klar_executor.act_status({})
                self.assertIs(result[0], False)
                self.assertTrue(result[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
