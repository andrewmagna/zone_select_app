from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

import cv2
import numpy as np


def polygon_centroid(points: List[List[int]]) -> tuple[float, float]:
    if not points:
        return (0.0, 0.0)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def sort_polygons_row_major(polygons: List[Dict[str, Any]], image_height: int) -> List[Dict[str, Any]]:
    """
    Sort polygons top-to-bottom, then left-to-right.
    Uses row bucketing so slightly curved / uneven rows still sort sensibly.
    """
    if not polygons:
        return polygons

    row_tolerance = max(20, int(image_height * 0.04))

    polys_with_centroids = []
    for poly in polygons:
        cx, cy = polygon_centroid(poly["points"])
        polys_with_centroids.append(
            {
                "poly": poly,
                "cx": cx,
                "cy": cy,
            }
        )

    polys_with_centroids.sort(key=lambda p: p["cy"])

    rows: List[List[Dict[str, Any]]] = []
    for item in polys_with_centroids:
        placed = False
        for row in rows:
            row_avg_y = sum(r["cy"] for r in row) / len(row)
            if abs(item["cy"] - row_avg_y) <= row_tolerance:
                row.append(item)
                placed = True
                break
        if not placed:
            rows.append([item])

    sorted_polys: List[Dict[str, Any]] = []
    for row in rows:
        row.sort(key=lambda p: p["cx"])
        sorted_polys.extend([r["poly"] for r in row])

    return sorted_polys


def build_red_mask(img_bgr: np.ndarray) -> np.ndarray:
    """
    Detect bright red overlay lines.
    Uses HSV and direct BGR thresholding together for robustness.
    """
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)

    lower_red_1 = np.array([0, 80, 60], dtype=np.uint8)
    upper_red_1 = np.array([12, 255, 255], dtype=np.uint8)

    lower_red_2 = np.array([168, 80, 60], dtype=np.uint8)
    upper_red_2 = np.array([180, 255, 255], dtype=np.uint8)

    hsv_mask_1 = cv2.inRange(hsv, lower_red_1, upper_red_1)
    hsv_mask_2 = cv2.inRange(hsv, lower_red_2, upper_red_2)
    hsv_mask = cv2.bitwise_or(hsv_mask_1, hsv_mask_2)

    b = img_bgr[:, :, 0]
    g = img_bgr[:, :, 1]
    r = img_bgr[:, :, 2]
    bgr_mask = np.where((r > 170) & (g < 130) & (b < 130), 255, 0).astype(np.uint8)

    red_mask = cv2.bitwise_or(hsv_mask, bgr_mask)

    close_kernel = np.ones((3, 3), np.uint8)
    red_mask = cv2.morphologyEx(red_mask, cv2.MORPH_CLOSE, close_kernel, iterations=1)

    dilate_kernel = np.ones((3, 3), np.uint8)
    red_mask = cv2.dilate(red_mask, dilate_kernel, iterations=1)

    return red_mask


def extract_enclosed_regions(red_mask: np.ndarray) -> np.ndarray:
    """
    Keep only enclosed regions, excluding the outside background.
    """
    h, w = red_mask.shape[:2]

    free_space = cv2.bitwise_not(red_mask)

    flood = free_space.copy()
    flood_mask = np.zeros((h + 2, w + 2), dtype=np.uint8)

    for seed in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
        if flood[seed[1], seed[0]] != 0:
            cv2.floodFill(flood, flood_mask, seed, 0)

    enclosed = flood

    open_kernel = np.ones((3, 3), np.uint8)
    enclosed = cv2.morphologyEx(enclosed, cv2.MORPH_OPEN, open_kernel, iterations=1)

    return enclosed


def contour_to_polygon(cnt: np.ndarray) -> List[List[int]]:
    perimeter = cv2.arcLength(cnt, True)
    epsilon = max(2.0, 0.0025 * perimeter)
    approx = cv2.approxPolyDP(cnt, epsilon, True)
    return [[int(pt[0][0]), int(pt[0][1])] for pt in approx]


def build_foreground_mask(img_bgr: np.ndarray) -> np.ndarray:
    """
    Build a mask for the actual door area by removing the light gray background.
    Assumes the background is bright and low-saturation, which matches your images.
    """
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)

    background_mask = cv2.inRange(
        hsv,
        np.array([0, 0, 180], dtype=np.uint8),
        np.array([180, 70, 255], dtype=np.uint8),
    )

    fg_mask = cv2.bitwise_not(background_mask)

    kernel = np.ones((5, 5), np.uint8)
    fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, kernel, iterations=1)
    fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, kernel, iterations=2)

    return fg_mask


def largest_mask_bbox(mask: np.ndarray) -> Tuple[int, int, int, int]:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        raise ValueError("Could not detect foreground bounds")

    cnt = max(contours, key=cv2.contourArea)
    x, y, w, h = cv2.boundingRect(cnt)
    return int(x), int(y), int(w), int(h)


def transform_points_between_bboxes(
    points: List[List[int]],
    src_bbox: Tuple[int, int, int, int],
    dst_bbox: Tuple[int, int, int, int],
) -> List[List[int]]:
    src_x, src_y, src_w, src_h = src_bbox
    dst_x, dst_y, dst_w, dst_h = dst_bbox

    if src_w <= 0 or src_h <= 0:
        raise ValueError("Invalid source bounding box")

    scale_x = dst_w / src_w
    scale_y = dst_h / src_h

    out: List[List[int]] = []
    for px, py in points:
        nx = dst_x + (px - src_x) * scale_x
        ny = dst_y + (py - src_y) * scale_y
        out.append([int(round(nx)), int(round(ny))])

    return out


def import_polygons_from_overlay(
    overlay_path: Path,
    clean_path: Path | None = None,
) -> Dict[str, Any]:
    if not overlay_path.exists():
        raise FileNotFoundError(f"Overlay image not found: {overlay_path}")

    overlay_img = cv2.imread(str(overlay_path), cv2.IMREAD_COLOR)
    if overlay_img is None:
        raise ValueError("Failed to load overlay image")

    overlay_height, overlay_width = overlay_img.shape[:2]

    red_mask = build_red_mask(overlay_img)
    enclosed = extract_enclosed_regions(red_mask)

    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(enclosed, connectivity=8)

    polygons: List[Dict[str, Any]] = []

    min_area = max(1200, int(overlay_width * overlay_height * 0.00035))
    max_area = int(overlay_width * overlay_height * 0.20)

    for label in range(1, num_labels):
        area = int(stats[label, cv2.CC_STAT_AREA])

        if area < min_area:
            continue
        if area > max_area:
            continue

        component_mask = np.where(labels == label, 255, 0).astype(np.uint8)

        contours, _ = cv2.findContours(component_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue

        cnt = max(contours, key=cv2.contourArea)
        cnt_area = cv2.contourArea(cnt)

        if cnt_area < min_area:
            continue

        x, y, w, h = cv2.boundingRect(cnt)

        if x <= 2 or y <= 2 or (x + w) >= overlay_width - 2 or (y + h) >= overlay_height - 2:
            continue

        points = contour_to_polygon(cnt)
        if len(points) < 3:
            continue

        polygons.append(
            {
                "zone_id": 0,
                "points": points,
            }
        )

    polygons = sort_polygons_row_major(polygons, image_height=overlay_height)

    for idx, poly in enumerate(polygons, start=1):
        poly["zone_id"] = idx

    result: Dict[str, Any] = {
        "image_size": {"width": overlay_width, "height": overlay_height},
        "zones": polygons,
    }

    if clean_path is not None:
        if not clean_path.exists():
            raise FileNotFoundError(f"Clean image not found: {clean_path}")

        clean_img = cv2.imread(str(clean_path), cv2.IMREAD_COLOR)
        if clean_img is None:
            raise ValueError("Failed to load clean image")

        clean_height, clean_width = clean_img.shape[:2]

        overlay_fg_mask = build_foreground_mask(overlay_img)
        clean_fg_mask = build_foreground_mask(clean_img)

        overlay_bbox = largest_mask_bbox(overlay_fg_mask)
        clean_bbox = largest_mask_bbox(clean_fg_mask)

        aligned_zones: List[Dict[str, Any]] = []
        for zone in polygons:
            aligned_points = transform_points_between_bboxes(
                zone["points"],
                src_bbox=overlay_bbox,
                dst_bbox=clean_bbox,
            )
            aligned_zones.append(
                {
                    "zone_id": zone["zone_id"],
                    "points": aligned_points,
                }
            )

        result = {
            "image_size": {"width": clean_width, "height": clean_height},
            "zones": aligned_zones,
            "debug": {
                "overlay_size": {"width": overlay_width, "height": overlay_height},
                "clean_size": {"width": clean_width, "height": clean_height},
                "overlay_bbox": {
                    "x": overlay_bbox[0],
                    "y": overlay_bbox[1],
                    "w": overlay_bbox[2],
                    "h": overlay_bbox[3],
                },
                "clean_bbox": {
                    "x": clean_bbox[0],
                    "y": clean_bbox[1],
                    "w": clean_bbox[2],
                    "h": clean_bbox[3],
                },
            },
        }

    return result