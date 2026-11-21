"""Check dirty-tile detection against a byte-wise reference, without Chrome.

NumPy and Pillow are the sender's existing runtime dependencies. This is a
correctness fixture, not a benchmark of the browser, Wi-Fi or panel.
PORTALL_TEST_REPORT optionally selects the JSON report path.
"""

import ast
import hashlib
import io
import json
import os
import tempfile
from pathlib import Path

if not __debug__:
    raise RuntimeError("Run this fixture without -O: its checks must stay enabled")

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, __version__ as pillow_version

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "components/portall/ha_send.py"
OUTPUT = (Path(os.environ["PORTALL_TEST_REPORT"])
          if os.environ.get("PORTALL_TEST_REPORT")
          else Path(tempfile.mkdtemp(prefix="portall-frame-changes-")) / "report.json")
report = {"offline_correctness_fixture": True, "performance_measured": False,
          "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
          "numpy_version": np.__version__, "pillow_version": pillow_version,
          "checks": {}}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def load_helpers():
    """Load only the three pure functions; no browser or sender is started."""
    namespace = {}
    wanted = {"changed_rectangles", "tile_rectangles", "choose_rectangles"}
    tree = ast.parse(SOURCE.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {"TILE", "MIN_RECT"}:
                    namespace[target.id] = ast.literal_eval(node.value)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), namespace)
    require(wanted <= namespace.keys(), "Missing sender helpers")
    return namespace


def reference_mask(previous, current, tile):
    """Compare every tile's pixels directly; no word views or row reductions."""
    h, w = current.shape[:2]
    mask = np.zeros(((h + tile - 1) // tile, (w + tile - 1) // tile), dtype=bool)
    for ty in range(mask.shape[0]):
        for tx in range(mask.shape[1]):
            region = (slice(ty * tile, min((ty + 1) * tile, h)),
                      slice(tx * tile, min((tx + 1) * tile, w)))
            mask[ty, tx] = np.not_equal(previous[region], current[region]).any()
    return mask


def compare(previous, current, helpers, tile=64):
    h, w = current.shape[:2]
    mask = reference_mask(previous, current, tile)
    expected = helpers["tile_rectangles"](mask, w, h, tile)
    actual = helpers["changed_rectangles"](previous, current, tile)
    require(actual == expected, f"Rectangle mismatch: {current.shape}, {current.dtype}, tile={tile}")
    require(bool(actual) == bool(mask.any()), "Empty/nonempty result mismatch")
    for x, y, rw, rh in actual:
        require(0 <= x < x + rw <= w and 0 <= y < y + rh <= h,
                "Rectangle exceeds the image")
    for ty, tx in np.argwhere(mask):
        left, top = tx * tile, ty * tile
        right, bottom = min(left + tile, w), min(top + tile, h)
        require(any(x <= left and y <= top and x + rw >= right and y + rh >= bottom
                    for x, y, rw, rh in actual), "Changed tile is not fully covered")


def general_cases(helpers):
    rng = np.random.default_rng(818)
    count = 0
    for h, w in [(1, 1), (63, 65), (64, 64), (65, 63), (128, 192),
                 (800, 1280), (1280, 800), (71, 97)]:
        for channels in [1, 3, 4]:
            base = rng.integers(0, 256, (h, w, channels), dtype=np.uint8)
            for mode in ["same", "corners", "sparse", "all", "strided"]:
                previous = base
                current = base.copy()
                if mode == "corners":
                    current[0, 0, 0] ^= 1
                    current[-1, -1, -1] ^= 1
                elif mode == "sparse":
                    current[rng.integers(0, h, 25), rng.integers(0, w, 25),
                            rng.integers(0, channels, 25)] ^= 1
                elif mode == "all":
                    current ^= 255
                elif mode == "strided":
                    previous = previous[:, ::-1]
                    current = current[:, ::-1]
                    current[-1, -1, -1] ^= 1
                tiles = [1, 8, 63, 64, 128] if h * w < 65536 else [8, 63, 64, 128]
                for tile in tiles:
                    compare(previous, current, helpers, tile)
                    count += 1
    for shape in [(0, 64, 3), (64, 0, 3), (0, 0, 3)]:
        empty = np.empty(shape, dtype=np.uint8)
        compare(empty, empty, helpers)
        count += 1
    return count


def boundary_layout_cases(helpers):
    rng = np.random.default_rng(8181)
    count = 0
    for h, w in [(64, 64), (69, 96), (800, 1280), (1280, 800)]:
        base = rng.integers(0, 256, (h, w, 3), dtype=np.uint8)
        xs = sorted({0, 1, 2, 3, 7, 8, min(62, w - 1), min(63, w - 1),
                     min(64, w - 1), w - 1})
        ys = sorted({0, min(63, h - 1), min(64, h - 1), h - 1})
        for x in xs:
            for y in ys:
                for color in range(3):
                    current = base.copy()
                    current[y, x, color] ^= 1
                    for tile in [8, 64]:
                        compare(base, current, helpers, tile)
                        count += 1
        for dtype in [np.uint8, np.uint16, np.float32]:
            previous = base.astype(dtype)
            current = previous.copy()
            current[-1, -1, -1] += 1
            variants = [(previous, current),
                        (np.asfortranarray(previous), np.asfortranarray(current)),
                        (previous[::-1, ::-1], current[::-1, ::-1]),
                        (previous[:, ::2], current[:, ::2])]
            for left, right in variants:
                left.setflags(write=False)
                right.setflags(write=False)
                compare(left, right, helpers)
                count += 1
        # NumPy accepts a contiguous byte array starting at an odd address.
        raw = bytearray(1 + base.nbytes)
        previous = np.frombuffer(raw, dtype=np.uint8, count=base.size, offset=1).reshape(base.shape)
        previous[:] = base
        current = previous.copy()
        current[-1, -1, -1] ^= 1
        previous.setflags(write=False)
        current.setflags(write=False)
        compare(previous, current, helpers)
        count += 1
    return count


def make_shots(case):
    rng = np.random.default_rng(818)
    base = Image.fromarray(rng.integers(0, 256, (800, 1280, 3), dtype=np.uint8)).filter(ImageFilter.GaussianBlur(2.5))
    shots = []
    for frame in range(12):
        if case == "video_motion":
            image = Image.fromarray(np.roll(np.asarray(base), frame * 7, axis=1))
            ImageDraw.Draw(image).rectangle((frame * 20, 180, frame * 20 + 350, 520), fill=(50, 100, 180))
        else:
            image = base.copy()
            ImageDraw.Draw(image).rectangle((20 + frame * 10, 20, 140 + frame * 10, 55), fill=(50, 100, 180))
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=90)
        shots.append(output.getvalue())
    return shots


def pipeline(shots, helpers, rotate, reference):
    """The sender's JPEG/NumPy/rectangle/JPEG stages with bounded state."""
    previous = None
    signatures = []
    for shot in shots:
        image = Image.open(io.BytesIO(shot))
        if rotate:
            image = image.transpose(Image.Transpose.ROTATE_90)
        current = np.asarray(image)
        require(not current.flags.writeable, "Pillow array should exercise read-only input")
        w, h = image.size
        if previous is None:
            rectangles = [(0, 0, w, h)]
        elif reference:
            rectangles = helpers["tile_rectangles"](reference_mask(previous, current, 64), w, h)
        else:
            rectangles = helpers["changed_rectangles"](previous, current)
        rectangles = helpers["choose_rectangles"](rectangles, w, h, 0.106)
        signatures.append(rectangles)
        for x, y, rw, rh in rectangles:
            part = image if (x, y, rw, rh) == (0, 0, w, h) else image.crop((x, y, x + rw, y + rh))
            output = io.BytesIO()
            part.save(output, format="JPEG", quality=60)
            signatures.append(hashlib.sha256(output.getvalue()).hexdigest())
        previous = current
    return signatures


try:
    helpers = load_helpers()
    report["checks"]["general_cases"] = general_cases(helpers)
    report["checks"]["boundary_layout_readonly_cases"] = boundary_layout_cases(helpers)
    pipelines = []
    for rotate in [False, True]:
        for case in ["small_rectangles", "video_motion"]:
            shots = make_shots(case)
            require(pipeline(shots, helpers, rotate, False) == pipeline(shots, helpers, rotate, True),
                    f"JPEG pipeline changed: {case}, rotation={rotate}")
            pipelines.append({"case": case, "rotated": rotate, "frames": len(shots),
                              "identical_rectangles_and_payload_hashes": True})
    report["checks"]["jpeg_pipelines"] = pipelines
    report["status"] = "PASS"
except Exception as exc:
    report["status"] = "FAIL"
    report["error"] = type(exc).__name__ + ": " + str(exc)
    raise
finally:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
