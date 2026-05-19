"""App-action protocol map fetch and render helpers.

Implements the three-step app-action map path:
  1. MAPL  – list maps, find the current one
  2. MAPI  – retrieve map metadata (total byte size)
  3. MAPD  – fetch map JSON in chunks

All I/O is synchronous and blocking; callers must run this in an executor.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from io import BytesIO
from typing import Any

_LOGGER = logging.getLogger(__name__)

_APP_ACTION_SIID = 2
_APP_ACTION_AIID = 50
_APP_MAP_CHUNK_SIZE = 400


def fetch_app_map_png(action_fn: Callable[[dict[str, Any]], Any]) -> bytes:
    """Fetch the current mower map via app-action protocol and return PNG bytes.

    *action_fn* must call ``protocol.action(siid=2, aiid=50, parameters=[payload])``
    and return the raw result dict.  Raises on connection or protocol errors.
    Returns PNG bytes on success.
    """
    payload, _ = _fetch_app_map_payload(action_fn)
    image_bytes, _, _ = _render_app_map_payload_png(payload)
    return image_bytes


# ---------------------------------------------------------------------------
# Internal: protocol helpers
# ---------------------------------------------------------------------------


def _call_app_action(
    action_fn: Callable[[dict[str, Any]], Any],
    payload: dict[str, Any],
) -> Any:
    """Call action_fn with payload and extract the first output entry."""
    result = action_fn(payload)
    if not isinstance(result, Mapping):
        return result
    out = result.get("out")
    if isinstance(out, Sequence) and not isinstance(out, str | bytes | bytearray):
        return out[0] if out else None
    return result


def _app_action_data(value: Any) -> Any:
    """Extract the ``d`` field from an app-action output entry.

    Raises RuntimeError if the entry carries a non-zero error code.
    """
    if not isinstance(value, Mapping):
        return None
    r = value.get("r")
    if r is not None and r != 0:
        raise RuntimeError(f"App action returned error {r}: {value}")
    return value.get("d")


def _normalize_map_list_entries(raw: Any) -> list[dict[str, Any]]:
    """Parse the flat array-of-arrays returned by the MAPL action."""
    entries = _app_action_data(raw)
    if not isinstance(entries, Sequence) or isinstance(
        entries, str | bytes | bytearray
    ):
        return []
    result: list[dict[str, Any]] = []
    for item in entries:
        if not isinstance(item, Sequence) or isinstance(item, str | bytes | bytearray):
            continue
        values = list(item)
        if len(values) < 4:
            continue
        result.append(
            {
                "idx": values[0],
                "current": bool(values[1]),
                "created": bool(values[2]),
                "has_backup": bool(values[3]),
            }
        )
    return result


# ---------------------------------------------------------------------------
# Internal: map fetch
# ---------------------------------------------------------------------------


def _fetch_app_map_payload(
    action_fn: Callable[[dict[str, Any]], Any],
    chunk_size: int = _APP_MAP_CHUNK_SIZE,
) -> tuple[dict[str, Any], Any]:
    """Fetch the current map payload.  Returns ``(payload_dict, map_idx)``."""

    map_list_raw = _call_app_action(action_fn, {"m": "g", "t": "MAPL"})
    entries = _normalize_map_list_entries(map_list_raw)

    # Prefer the entry flagged as both current *and* created; fall back to any created.
    target_idx = None
    for entry in entries:
        if entry.get("current") and entry.get("created"):
            target_idx = entry["idx"]
            break
    if target_idx is None:
        for entry in entries:
            if entry.get("created"):
                target_idx = entry["idx"]
                break
    if target_idx is None:
        raise RuntimeError(f"No usable map in app map list ({len(entries)} entries)")

    # Get map size.
    info_raw = _call_app_action(
        action_fn, {"m": "g", "t": "MAPI", "d": {"idx": target_idx}}
    )
    info = _app_action_data(info_raw)
    if not isinstance(info, Mapping):
        raise RuntimeError(f"MAPI returned unexpected type: {type(info).__name__}")
    size = info.get("size")
    if not isinstance(size, int) or size <= 0:
        raise RuntimeError(f"MAPI returned invalid size: {size!r}")

    # Fetch payload in chunks.
    chunks = bytearray()
    offset = 0
    while offset < size:
        requested = min(size - offset, chunk_size)
        chunk_raw = _call_app_action(
            action_fn,
            {"m": "g", "t": "MAPD", "d": {"start": offset, "size": requested}},
        )
        data = _app_action_data(chunk_raw)
        if not isinstance(data, Mapping):
            raise RuntimeError(f"MAPD returned unexpected type at offset {offset}")
        text = data.get("data")
        returned_size = data.get("size")
        if not isinstance(text, str) or not text:
            raise RuntimeError(f"MAPD returned empty data at offset {offset}")
        chunk_bytes = text.encode("utf-8")
        chunks.extend(chunk_bytes)
        actual = len(chunk_bytes)
        offset += (
            returned_size
            if isinstance(returned_size, int) and returned_size > 0
            else actual
        )

    payload = json.loads(chunks.decode("utf-8"))
    if not isinstance(payload, Mapping):
        raise RuntimeError("App map payload is not a JSON object")
    return dict(payload), target_idx


# ---------------------------------------------------------------------------
# Internal: coordinate extraction
# ---------------------------------------------------------------------------


def _app_map_coordinate_sets(value: Any) -> list[list[tuple[float, float]]]:
    """Extract lists of (x, y) polygons from a map/spot/trajectory field."""
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    result: list[list[tuple[float, float]]] = []
    for item in value:
        raw = item.get("data") if isinstance(item, Mapping) else item
        points = _app_map_points(raw)
        if points:
            result.append(points)
    return result


def _app_map_points(value: Any) -> list[tuple[float, float]]:
    """Extract a flat list of (x, y) tuples from a coordinate array."""
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    points: list[tuple[float, float]] = []
    for item in value:
        if (
            not isinstance(item, Sequence)
            or isinstance(item, str | bytes | bytearray)
            or len(item) < 2
        ):
            continue
        x, y = item[0], item[1]
        if isinstance(x, int | float) and isinstance(y, int | float):
            points.append((float(x), float(y)))
    return points


# ---------------------------------------------------------------------------
# Internal: rendering
# ---------------------------------------------------------------------------


def _render_app_map_payload_png(
    payload: Mapping[str, Any],
) -> tuple[bytes, int, int]:
    """Render map, spot, and trajectory coordinates to a PNG.

    Returns ``(png_bytes, width, height)``.  Raises ``ValueError`` if the
    payload contains no drawable coordinates.
    """
    from PIL import Image, ImageDraw  # noqa: PLC0415

    map_polygons = _app_map_coordinate_sets(payload.get("map"))
    spot_polygons = _app_map_coordinate_sets(payload.get("spot"))
    trajectories = _app_map_coordinate_sets(payload.get("trajectory"))
    points = _app_map_points(payload.get("point"))

    all_points: list[tuple[float, float]] = [
        p for group in [*map_polygons, *spot_polygons, *trajectories] for p in group
    ] + points
    if not all_points:
        raise ValueError("App map payload has no drawable coordinates")

    min_x = min(p[0] for p in all_points)
    max_x = max(p[0] for p in all_points)
    min_y = min(p[1] for p in all_points)
    max_y = max(p[1] for p in all_points)
    span_x = max(max_x - min_x, 1.0)
    span_y = max(max_y - min_y, 1.0)
    padding = 48
    canvas = 900
    scale = min((canvas - padding * 2) / span_x, (canvas - padding * 2) / span_y)
    width = max(int(span_x * scale) + padding * 2, 320)
    height = max(int(span_y * scale) + padding * 2, 320)

    def project(p: tuple[float, float]) -> tuple[int, int]:
        return (
            int(round((p[0] - min_x) * scale + padding)),
            int(round((max_y - p[1]) * scale + padding)),
        )

    image = Image.new("RGBA", (width, height), (248, 250, 252, 255))
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    for polygon in sorted(map_polygons, key=len, reverse=True):
        projected = [project(p) for p in polygon]
        if len(projected) >= 3:
            draw.polygon(
                projected, fill=(187, 230, 197, 150), outline=(44, 125, 83, 255)
            )
            draw.line(projected + [projected[0]], fill=(44, 125, 83, 255), width=4)

    for polygon in spot_polygons:
        projected = [project(p) for p in polygon]
        if len(projected) >= 3:
            draw.polygon(projected, fill=(250, 204, 21, 95), outline=(161, 98, 7, 255))
            draw.line(projected + [projected[0]], fill=(161, 98, 7, 255), width=3)

    for trajectory in trajectories:
        projected = [project(p) for p in trajectory]
        if len(projected) >= 2:
            draw.line(projected, fill=(37, 99, 235, 255), width=4, joint="curve")

    for p in points:
        x, y = project(p)
        draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=(15, 23, 42, 255))

    image = Image.alpha_composite(image, overlay).convert("RGB")
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue(), width, height
