"""Local end-to-end experiment; does not change the plugin or production runtime.

Run: python tools/benchmark_plugin_threads.py --output .runtime/plugin-thread-benchmark

direct: original hook on the client event loop.
thread1: one shared plugin instance on one dedicated worker.
thread2_locked: two workers, with the shared instance protected by a hook-wide lock.
thread2/3/4_isolated: one plugin instance per worker; an opt-in state ownership change.
"""

# ruff: noqa: E402 -- allow running from a source checkout without installation

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import importlib.util
import io
import json
import math
import platform
import random
import statistics
import struct
import sys
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image, ImageDraw
from PIL import __version__ as pillow_version

from image_downloader.configuration.models import AppConfig
from image_downloader.media.artifact_pipeline import ArtifactPipeline
from image_downloader.media.image_processor import ImageProcessor
from image_downloader.models import Chapter, DownloadManifest, ImageArtifact, ImageResource, RequestSpec
from image_downloader.observability.logging import DownloadLogger
from image_downloader.plugins.plugin_invoker import PluginInvoker
from image_downloader.storage.filesystem import FileSystem
from image_downloader.storage.transactions import finish_task
from image_downloader.transport.gateway import RequestGateway


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


class TransformAdapter:
    def __init__(self, cls, mode):
        self.cls, self.mode = cls, mode
        self.shared = cls()
        self.local = threading.local()
        self.lock = threading.Lock()
        self.durations = []
        self.queue_waits = []
        self.observation_lock = threading.Lock()
        self.worker_threads = set()
        self.active = self.peak_active = 0
        workers = int(mode[6]) if mode != "direct" else 0
        self.pool = None if mode == "direct" else ThreadPoolExecutor(max_workers=workers)

    async def transform_image(self, artifact, context):
        submitted = time.perf_counter()

        async def invoke(plugin):
            start = time.perf_counter()
            self.queue_waits.append(start - submitted)
            with self.observation_lock:
                self.worker_threads.add(threading.get_ident())
                self.active += 1
                self.peak_active = max(self.peak_active, self.active)
            try:
                return await plugin.transform_image(artifact, context)
            finally:
                self.durations.append(time.perf_counter() - start)
                with self.observation_lock:
                    self.active -= 1

        if self.pool is None:
            return await invoke(self.shared)

        def work():
            # The supplied hook has no awaits. asyncio.run preserves its async signature
            # inside a worker; this is an experiment, not a generic async-plugin runner.
            if self.mode.endswith("_isolated"):
                if not hasattr(self.local, "plugin"):
                    self.local.plugin = self.cls()
                return asyncio.run(invoke(self.local.plugin))
            with self.lock:
                return asyncio.run(invoke(self.shared))

        return await finish_task(asyncio.get_running_loop().run_in_executor(self.pool, work))

    def close(self):
        if self.pool:
            self.pool.shutdown(wait=True, cancel_futures=True)


def percentile(values, fraction):
    return sorted(values)[min(len(values) - 1, math.ceil(len(values) * fraction) - 1)]


async def trial(args, cls, core, base_url, placeholder, mode, delay, repeat, expected):
    config = AppConfig()
    gateway = RequestGateway(config)
    adapter = TransformAdapter(cls, mode)
    resources = tuple(
        ImageResource(
            f"{base_url}/{i}",
            index=i + 1,
            metadata={"iv": "benchmark-iv", "salt": "benchmark-salt", "placeholder": placeholder},
        )
        for i in range(args.images)
    )
    chapter = Chapter(1, title="Synthetic chapter", chapter_id="synthetic", images=resources)
    manifest = DownloadManifest(title="Local benchmark", chapters=(chapter,))
    record = types.SimpleNamespace(id="benchmark", manifest=types.SimpleNamespace(value={}), catalog=None)
    registry = types.SimpleNamespace(effective_config=lambda *args: {})
    pipeline = ArtifactPipeline(config, registry, record, None, core, DownloadLogger(), PluginInvoker("benchmark"), ())
    directory = args.output / f"delay{delay}_{mode}_r{repeat}"
    directory.mkdir(parents=True, exist_ok=True)
    filesystem = FileSystem(directory)
    slots = asyncio.Semaphore(args.concurrency)
    fetch_times, save_times, lags = [], [], []
    outputs = []
    stop = asyncio.Event()

    async def heartbeat():
        while not stop.is_set():
            target = time.perf_counter() + 0.01
            await asyncio.sleep(0.01)
            lags.append(max(0, time.perf_counter() - target))

    async def image_job(i, image):
        async with slots:
            start = time.perf_counter()
            response = await gateway.execute(RequestSpec(image.url))
            fetch_times.append(time.perf_counter() - start)
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status}")
            artifact = ImageArtifact(response.body, "image/jpeg", image.url)
            final = await pipeline.process(adapter, artifact, image, manifest, chapter)
            start = time.perf_counter()
            path = await finish_task(asyncio.to_thread(filesystem.write_bytes_atomic, f"{i:03}.webp", final.data))
            save_times.append(time.perf_counter() - start)
            outputs.append((i, path, hashlib.sha256(final.data).hexdigest()))

    ticker = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)
    start = time.perf_counter()
    jobs = [asyncio.create_task(image_job(i, image)) for i, image in enumerate(resources)]
    try:
        await asyncio.gather(*jobs)
        elapsed = time.perf_counter() - start
    finally:
        for job in jobs:
            if not job.done():
                job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        stop.set()
        await ticker
        await gateway.close()
        await asyncio.to_thread(adapter.close)
    # Correctness checks deliberately excluded from timed section.
    hashes = []
    for i, path, digest in sorted(outputs):
        data = path.read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest, "saved bytes differ"
        with Image.open(io.BytesIO(data)) as decoded:
            assert decoded.format == "WEBP"
            assert hashlib.sha256(decoded.convert("RGB").tobytes()).hexdigest() == expected[i % 4], "pixel mismatch"
        hashes.append(digest)
    return dict(
        mode=mode,
        delay_ms=delay,
        repeat=repeat,
        elapsed_s=elapsed,
        images_per_s=args.images / elapsed,
        fetch_mean_ms=statistics.mean(fetch_times) * 1000,
        transform_mean_ms=statistics.mean(adapter.durations) * 1000,
        transform_queue_mean_ms=statistics.mean(adapter.queue_waits) * 1000,
        save_mean_ms=statistics.mean(save_times) * 1000,
        loop_lag_p95_ms=percentile(lags, 0.95) * 1000,
        loop_lag_max_ms=max(lags) * 1000,
        hashes=hashes,
        verified_images=len(outputs),
        transform_threads_used=len(adapter.worker_threads),
        peak_simultaneous_transforms=adapter.peak_active,
    )


async def main(args):
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    cls = load_plugin(args.plugin)
    bodies, placeholder, expected = fixtures(args.images, args.width, args.height)
    core = ImageProcessor()
    results = []
    try:
        # Exclude first process spawn and decoder initialization from every timed run.
        await asyncio.to_thread(core.inspect, bodies[0])
        for delay in args.delays:
            server = make_server(bodies, delay / 1000)
            try:
                baseline_hashes = None
                for repeat in range(args.repeats):
                    modes = list(args.modes)
                    random.Random(repeat + 42).shuffle(modes)
                    for mode in modes:
                        result = await trial(
                            args,
                            cls,
                            core,
                            f"http://127.0.0.1:{server.server_port}",
                            placeholder,
                            mode,
                            delay,
                            repeat,
                            expected,
                        )
                        if baseline_hashes is None:
                            baseline_hashes = result["hashes"]
                        assert result["hashes"] == baseline_hashes, "mode output differs"
                        results.append(result)
                        print(json.dumps({k: v for k, v in result.items() if k != "hashes"}), flush=True)
                        payload = dict(
                            environment=dict(python=sys.version, pillow=pillow_version, platform=platform.platform()),
                            parameters={k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
                            input_bytes=list(map(len, bodies)),
                            runs=results,
                        )
                        (args.output / "results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
            finally:
                await asyncio.to_thread(server.shutdown)
                server.server_close()
    finally:
        await asyncio.to_thread(core.close)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin", type=Path, default=Path("C:/work/site_plugins/plugin_a"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--images", type=int, default=8)
    parser.add_argument("--width", type=int, default=768)
    parser.add_argument("--height", type=int, default=1024)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--delays", type=int, nargs="+", default=[0, 100])
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=["direct", "thread1", "thread2_locked", "thread2_isolated", "thread3_isolated", "thread4_isolated"],
        default=["direct", "thread1", "thread2_locked", "thread2_isolated", "thread3_isolated", "thread4_isolated"],
    )
    arguments = parser.parse_args()
    if arguments.width % 128 or arguments.height % 128:
        parser.error("dimensions must be multiples of 128")
    asyncio.run(main(arguments))
