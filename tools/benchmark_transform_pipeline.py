"""Measure a frozen baseline or the managed synchronous transform pipeline.

Requires psutil for sampled parent + image-worker RSS. See implementation report.
"""

# ruff: noqa: E402
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import importlib.util
import io
import json
import platform
import random
import struct
import sys
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil
from PIL import Image, ImageDraw

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--runtime-root", type=Path, required=True)
parser.add_argument("--plugin", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--stage", required=True)
parser.add_argument("--legacy-runtime", action="store_true")
parser.add_argument("--sync-plugin", action="store_true")
parser.add_argument("--workers", type=int, default=2)
parser.add_argument("--images", type=int, default=8)
parser.add_argument("--repeats", type=int, default=5)
parser.add_argument("--delay", type=int, default=0)
parser.add_argument("--repeat-offset", type=int, default=0)
args = parser.parse_args()
sys.path.insert(0, str(args.runtime_root.resolve() / "src"))
from image_downloader.configuration.models import AppConfig
from image_downloader.media.artifact_pipeline import ArtifactPipeline
from image_downloader.media.image_processor import ImageProcessor
from image_downloader.models import Chapter, DownloadManifest, ImageArtifact, ImageResource, RequestSpec
from image_downloader.observability.logging import DownloadLogger
from image_downloader.plugins.plugin_invoker import PluginInvoker
from image_downloader.storage.filesystem import FileSystem
from image_downloader.storage.transactions import finish_task
from image_downloader.transport.gateway import RequestGateway

if not args.legacy_runtime:
    from image_downloader.media import TransformExecutor


def load_plugin(directory):
    package = types.ModuleType("benchmark_external_plugin")
    package.__path__ = [str(directory)]
    sys.modules[package.__name__] = package
    spec = importlib.util.spec_from_file_location(package.__name__ + ".plugin", directory / "plugin.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.ADownloadPlugin


def fixtures(count, width, height):
    """Valid synthetic placeholder, including rotation and reflection records."""
    span, margin = 128, 1
    nx, ny = width // span, height // span
    inner = span - 2 * margin
    records = []
    for y in range(ny):
        for x in range(nx):
            rotation, flip = (x + y) % 4, (x + y) % 2
            word = (1 << 27) | (x * inner << 15) | (y * inner << 3) | rotation << 1 | flip
            metadata = span << 24 | x << 16 | y << 8
            records.append(struct.pack("<II", word, metadata))
    group = b"".join(records)
    placeholder = "A" * 66 + base64.b64encode((struct.pack("<H", len(records)) + group) * count).decode()
    bodies, expected = [], []
    rng = random.Random(20261102)
    for variant in range(4):
        image = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(image)
        for _ in range(500):
            x, y = rng.randrange(width), rng.randrange(height)
            draw.line(
                (x, y, min(width - 1, x + rng.randrange(5, 180)), min(height - 1, y + rng.randrange(5, 120))),
                fill=(variant * 35, rng.randrange(180), rng.randrange(180)),
                width=3,
            )
        for y in range(20, height, 70):
            draw.text((20, y), f"Synthetic page {variant}: benchmark 0123456789", fill="black")
        encoded = io.BytesIO()
        image.save(encoded, "JPEG", quality=90)
        bodies.append(encoded.getvalue())
        # Independent geometric reference, operating on the actual downloaded JPEG pixels.
        with Image.open(io.BytesIO(bodies[-1])) as source:
            reference = Image.new("RGB", (nx * inner, ny * inner))
            for y in range(ny):
                for x in range(nx):
                    tile = source.crop(
                        (x * span + margin, y * span + margin, (x + 1) * span - margin, (y + 1) * span - margin)
                    )
                    if (x + y) % 2:
                        tile = tile.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
                    rotation = (x + y) % 4
                    if rotation:
                        tile = tile.transpose(
                            {
                                1: Image.Transpose.ROTATE_90,
                                2: Image.Transpose.ROTATE_180,
                                3: Image.Transpose.ROTATE_270,
                            }[rotation]
                        )
                    reference.paste(tile, (x * inner, y * inner))
            expected.append(hashlib.sha256(reference.tobytes()).hexdigest())
    return bodies, placeholder, expected


def make_server(bodies, delay):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            body = bodies[int(self.path.rsplit("/", 1)[-1]) % len(bodies)]
            time.sleep(delay)
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            # Real sockets; server runs independently of the client's event loop.
            self.wfile.write(body)
            self.wfile.flush()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class MemorySampler:
    def __init__(self):
        self.stop = threading.Event()
        self.parent = psutil.Process()
        self.samples = []
        self.thread = threading.Thread(target=self.collect, daemon=True)

    def snapshot(self):
        total = self.parent.memory_info().rss
        for child in self.parent.children(recursive=True):
            try:
                total += child.memory_info().rss
            except psutil.NoSuchProcess:
                pass
        return total

    def collect(self):
        while not self.stop.is_set():
            self.samples.append(self.snapshot())
            self.stop.wait(0.02)

    def start(self):
        self.samples.append(self.snapshot())
        self.thread.start()

    def finish(self):
        self.samples.append(self.snapshot())
        self.stop.set()
        self.thread.join()


async def trial(core, cls, base_url, placeholder, bodies, expected, repeat):
    plugin = cls()
    if args.legacy_runtime and args.sync_plugin:

        class LegacyAdapter:
            async def transform_image(self, artifact, context):
                return plugin.transform_image(artifact, context)

        selected = LegacyAdapter()
    else:
        selected = plugin
    executor = None if args.legacy_runtime else TransformExecutor(args.workers)
    config = AppConfig()
    gateway = RequestGateway(config)
    images = tuple(
        ImageResource(
            f"{base_url}/{i}",
            index=i + 1,
            metadata={"iv": "benchmark-iv", "salt": "benchmark-salt", "placeholder": placeholder},
        )
        for i in range(args.images)
    )
    chapter = Chapter(1, "synthetic", images=images, chapter_id="synthetic")
    manifest = DownloadManifest("benchmark", (chapter,))
    record = types.SimpleNamespace(id="benchmark", manifest=types.SimpleNamespace(value={}), catalog=None)
    registry = types.SimpleNamespace(effective_config=lambda *a: {})
    kwargs = {} if executor is None else {"transform_executor": executor}
    pipeline = ArtifactPipeline(
        config, registry, record, None, core, DownloadLogger(), PluginInvoker("benchmark"), (), **kwargs
    )
    directory = args.output.resolve() / f"{args.stage}_d{args.delay}_n{args.images}_r{repeat}"
    directory.mkdir(parents=True, exist_ok=True)
    fs = FileSystem(directory)
    slots = asyncio.Semaphore(8)
    stop = asyncio.Event()
    lags = []
    outputs = []

    async def heartbeat():
        while not stop.is_set():
            target = time.perf_counter() + 0.01
            await asyncio.sleep(0.01)
            lags.append(max(0, time.perf_counter() - target))

    async def job(i):
        async with slots:
            response = await gateway.execute(RequestSpec(images[i].url))
            assert response.status == 200
            artifact = ImageArtifact(response.body, "image/jpeg", images[i].url)
            result = await pipeline.process(selected, artifact, images[i], manifest, chapter)
            path = await finish_task(asyncio.to_thread(fs.write_bytes_atomic, f"{i:03}.webp", result.data))
            outputs.append((i, path, hashlib.sha256(result.data).hexdigest()))

    sampler = MemorySampler()
    sampler.start()
    ticker = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)
    start = time.perf_counter()
    jobs = [asyncio.create_task(job(i)) for i in range(args.images)]
    try:
        await asyncio.gather(*jobs)
        elapsed = time.perf_counter() - start
    finally:
        for item in jobs:
            if not item.done():
                item.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        sampler.finish()
        stop.set()
        await ticker
        await gateway.close()
        if executor is not None:
            await executor.close()
    for i, path, digest in outputs:
        data = path.read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest
        with Image.open(io.BytesIO(data)) as decoded:
            assert hashlib.sha256(decoded.convert("RGB").tobytes()).hexdigest() == expected[i % 4]
    if hasattr(plugin, "cleanup_after_use"):
        plugin.cleanup_after_use()
    return dict(
        stage=args.stage,
        delay_ms=args.delay,
        images=args.images,
        repeat=repeat,
        workers=args.workers if executor is not None else 0,
        elapsed_s=elapsed,
        loop_lag_max_ms=max(lags) * 1000,
        rss_initial_mib=sampler.samples[0] / 2**20,
        rss_peak_mib=max(sampler.samples) / 2**20,
        rss_end_mib=sampler.samples[-1] / 2**20,
        memory_samples=len(sampler.samples),
        verified_images=len(outputs),
        hashes=[digest for i, path, digest in sorted(outputs)],
    )


async def main():
    args.output.mkdir(parents=True, exist_ok=True)
    cls = load_plugin(args.plugin.resolve())
    bodies, placeholder, expected = fixtures(args.images, 768, 1024)
    core = ImageProcessor()
    results = []
    server = make_server(bodies, args.delay / 1000)
    try:
        await asyncio.to_thread(core.inspect, bodies[0])
        for repeat in range(args.repeat_offset, args.repeat_offset + args.repeats):
            result = await trial(
                core, cls, f"http://127.0.0.1:{server.server_port}", placeholder, bodies, expected, repeat
            )
            results.append(result)
            print(json.dumps({k: v for k, v in result.items() if k != "hashes"}), flush=True)
            payload = dict(environment=dict(python=sys.version, platform=platform.platform()), runs=results)
            path = args.output / f"{args.stage}_d{args.delay}_n{args.images}_r{args.repeat_offset}.json"
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        await asyncio.to_thread(core.close)


if __name__ == "__main__":
    asyncio.run(main())
