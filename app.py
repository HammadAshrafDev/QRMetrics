"""Local web UI for benchmarking barcode preprocessing and decoding."""

from io import BytesIO
import ipaddress
from pathlib import Path
import os
import shutil
import socket
import threading
from time import perf_counter
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import uuid
import webbrowser
import zipfile

import cv2
import numpy as np
import psutil
from flask import Flask, jsonify, render_template, request, send_file, send_from_directory

import barcode_bbox_tester as engine


BASE_DIR = Path(__file__).resolve().parent
WORK_DIR = BASE_DIR / ".web_data"
UPLOAD_DIR = WORK_DIR / "uploads"
RUN_DIR = WORK_DIR / "runs"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
RUN_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
uploads = {}
runs = {}
MAX_REMOTE_IMAGE_BYTES = 15 * 1024 * 1024

BARCODE_FORMATS = {
    "auto": "Auto detect — all supported formats",
    "DataMatrix": "Data Matrix",
    "QRCode": "QR Code",
    "MicroQRCode": "Micro QR Code",
    "RMQRCode": "rMQR Code",
    "Aztec": "Aztec",
    "MaxiCode": "MaxiCode",
    "PDF417": "PDF417",
    "CompactPDF417": "Compact PDF417",
    "MicroPDF417": "MicroPDF417",
    "Code128": "Code 128",
    "Code39": "Code 39",
    "Code93": "Code 93",
    "Codabar": "Codabar",
    "ITF": "ITF",
    "ITF14": "ITF-14",
    "EAN13": "EAN-13",
    "EAN8": "EAN-8",
    "UPCA": "UPC-A",
    "UPCE": "UPC-E",
    "DataBar": "DataBar",
}


def clean_stage_name(value):
    return engine.safe_name(value)


def result_data(items):
    return [
        {
            "text": item.text,
            "raw_hex": item.bytes.hex(" "),
            "raw_repr": repr(item.bytes),
            "format": str(item.format),
            "format_key": next(
                (key for key, label in BARCODE_FORMATS.items() if label == str(item.format)),
                None,
            ),
            "orientation": item.orientation,
            "matches_expected": engine.EXPECTED_VALUE is not None and item.text == engine.EXPECTED_VALUE,
        }
        for item in items
    ]


def displayed_stage(stage, selected_format):
    if stage == "Raw (all types)":
        return "Original ROI — all-format baseline"
    name = stage.replace(" (DataMatrix)", "")
    if name == "Raw":
        name = "Original ROI"
    if name == "Raw + auto rotate":
        name = "Original ROI + ZXing auto-rotation"
    return f"{name} — {BARCODE_FORMATS[selected_format]}"


def preprocessing_recipe(stage):
    recipes = {
        "Raw (all types)": "None. Original ROI pixels are passed directly to ZXing.",
        "Raw (DataMatrix)": "None. Original ROI pixels are passed directly to ZXing.",
        "Raw + auto rotate (DataMatrix)": "None. Original ROI; ZXing rotation search is enabled.",
        "Tight ROI crop (DataMatrix)": f"Crop {engine.TIGHT_CROP_PERCENT}% from every ROI edge.",
        "Upscale 2x (DataMatrix)": "Resize to 2× using bicubic interpolation.",
        "Upscale 3x (DataMatrix)": "Resize to 3× using bicubic interpolation.",
        "Contrast stretch (DataMatrix)": "Grayscale; stretch the 2nd–98th intensity percentiles to 0–255.",
        f"Gamma {engine.GAMMA_VALUE:g} (DataMatrix)": f"Grayscale; gamma correction γ={engine.GAMMA_VALUE:g}.",
        "Unsharp mask (DataMatrix)": "Grayscale; Gaussian blur σ=1.2; unsharp amount=1.5.",
        "Black-hat (DataMatrix)": "Grayscale; 9×9 morphological black-hat; normalize to 0–255.",
        "CLAHE (DataMatrix)": f"Grayscale; CLAHE clip={engine.CLAHE_CLIP_LIMIT}, tiles={engine.CLAHE_TILE_SIZE}.",
        "Adaptive (DataMatrix)": f"Grayscale; Gaussian adaptive threshold, block={engine.ADAPTIVE_BLOCK_SIZE}, C={engine.ADAPTIVE_C}.",
        "Otsu (DataMatrix)": "Grayscale; automatic global Otsu binary threshold.",
        "Sauvola (DataMatrix)": f"Grayscale; Sauvola local threshold, window={engine.LOCAL_THRESHOLD_WINDOW}, k=0.2, R=128.",
        "Niblack (DataMatrix)": f"Grayscale; Niblack local threshold, window={engine.LOCAL_THRESHOLD_WINDOW}, k=-0.2.",
        "Quiet zone (DataMatrix)": f"Grayscale; add a {engine.QUIET_ZONE_PIXELS}px white border on every side.",
        "Morph closing (DataMatrix)": "Grayscale; Otsu threshold; morphological closing with 3×3 kernel.",
        "Perspective rectify (DataMatrix)": "Grayscale; detect largest four-corner contour; perspective warp if found.",
        "Polarity inverted (DataMatrix)": "Grayscale; invert every pixel (255 − value).",
        "Denoise + sharpen (DataMatrix)": "Grayscale; fast NLM denoise h=10/template=7/search=21; unsharp amount=1.5.",
        "Wiener deblur (DataMatrix)": "Grayscale; Wiener deconvolution, Gaussian PSF=5, σ=1.2, noise=0.01.",
        "All preprocessing (DataMatrix)": "Crop 5% → rectify → bicubic 2× → NLM denoise → 2–98% contrast stretch → gamma 1.5 → CLAHE → Wiener deblur → unsharp → adaptive threshold → 3×3 close → 30px quiet zone.",
    }
    if stage.startswith("Threshold "):
        return f"Grayscale; fixed binary threshold at {stage.split()[1]}."
    if stage.startswith("Erode "):
        return f"Grayscale; Otsu threshold; erode with {stage.split()[1]} kernel."
    if stage.startswith("Dilate "):
        return f"Grayscale; Otsu threshold; dilate with {stage.split()[1]} kernel."
    return recipes.get(stage, stage)


def flags_for(stage, angle, selected_format, is_pure, try_harder):
    baseline = stage == "Raw (all types)"
    flags = {
        "Image treatment": preprocessing_recipe(stage),
        "Barcode search scope": "All supported formats (baseline)" if baseline else BARCODE_FORMATS[selected_format],
        "Input rotation": angle,
        "ZXing rotation search": stage == "Raw + auto rotate (DataMatrix)",
        "ZXing PureBarcode": is_pure,
        "Extended search (UI Try harder)": try_harder,
        "ZXing try invert": try_harder,
        "ZXing try downscale": try_harder,
        "Native TryHarder": "Always enabled internally by zxing-cpp Python binding",
        "ZXing binarizer": "LocalAverage (library default)",
    }
    return flags


@app.get("/")
def index():
    return render_template(
        "index.html", expected_value=engine.EXPECTED_VALUE,
        barcode_formats=BARCODE_FORMATS,
    )


@app.post("/api/upload")
def upload_image():
    uploaded = request.files.get("image")
    if not uploaded or not uploaded.filename:
        return jsonify(error="Choose an image first."), 400
    data = uploaded.read()
    decoded = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if decoded is None:
        return jsonify(error="That file could not be read as an image."), 400
    return save_uploaded_image(decoded, uploaded.filename)


def save_uploaded_image(decoded, filename):
    image_id = uuid.uuid4().hex
    path = UPLOAD_DIR / f"{image_id}.png"
    cv2.imwrite(str(path), decoded)
    uploads[image_id] = path
    return jsonify(
        image_id=image_id,
        image_url=f"/api/image/{image_id}",
        width=decoded.shape[1],
        height=decoded.shape[0],
        filename=filename,
    )


def validate_remote_host(url):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Only public HTTP or HTTPS image URLs are supported.")
    for address in socket.getaddrinfo(parsed.hostname, parsed.port or 443):
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ValueError("Local and private network URLs are not allowed.")


@app.post("/api/upload-url")
def upload_image_url():
    url = (request.get_json(silent=True) or {}).get("url", "").strip()
    try:
        validate_remote_host(url)
        remote = urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0 QRMetrics/1.0"}), timeout=12)
        validate_remote_host(remote.geturl())
        data = remote.read(MAX_REMOTE_IMAGE_BYTES + 1)
        if len(data) > MAX_REMOTE_IMAGE_BYTES:
            return jsonify(error="Remote image is larger than 15 MB."), 400
        decoded = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if decoded is None:
            return jsonify(error="The dropped URL did not return a readable image."), 400
        filename = Path(urlparse(remote.geturl()).path).name or "remote-image"
        return save_uploaded_image(decoded, filename)
    except Exception as error:
        return jsonify(error=f"Could not fetch that image: {error}"), 400


@app.get("/api/image/<image_id>")
def uploaded_image(image_id):
    path = uploads.get(image_id)
    if path is None or not path.exists():
        return jsonify(error="Image not found."), 404
    return send_file(path)


@app.post("/api/analyze")
def analyze():
    run_started = perf_counter()
    process = psutil.Process()
    run_rss_before = process.memory_info().rss / (1024 * 1024)
    payload = request.get_json(silent=True) or {}
    image_id = payload.get("image_id")
    image_path = uploads.get(image_id)
    if image_path is None:
        return jsonify(error="Upload an image first."), 400
    image = cv2.imread(str(image_path))
    points = payload.get("points", [])
    kind = payload.get("kind")
    selected_format = payload.get("barcode_format", "DataMatrix")
    is_pure = bool(payload.get("is_pure", False))
    try_harder = bool(payload.get("try_harder", True))
    if selected_format not in BARCODE_FORMATS:
        return jsonify(error="Unsupported barcode format."), 400

    if kind == "box" and len(points) == 2:
        x1, x2 = sorted((int(points[0][0]), int(points[1][0])))
        y1, y2 = sorted((int(points[0][1]), int(points[1][1])))
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(image.shape[1], x2), min(image.shape[0], y2)
        roi = image[y1:y2, x1:x2]
        description = [x1, y1, x2, y2]
    elif kind == "polygon" and len(points) == 4:
        points = [[int(x), int(y)] for x, y in points]
        roi = engine.warp_four_points(image, points)
        description = points
    else:
        return jsonify(error="Draw a valid box or four-point polygon."), 400

    if roi is None or roi.size == 0 or min(roi.shape[:2]) < 2:
        return jsonify(error="The selected region is too small."), 400

    run_id = uuid.uuid4().hex
    output_dir = RUN_DIR / run_id
    output_dir.mkdir(parents=True)
    variants, preprocessing_metrics = engine.make_variants_with_metrics(roi)
    attempts = []
    table = []
    for stage, variant in variants.items():
        display_name = displayed_stage(stage, selected_format)
        preprocess = preprocessing_metrics[stage]
        row = {"stage": display_name, "preprocessing": preprocess, "cells": []}
        for angle, rotation_code in engine.ROTATIONS.items():
            config_rss_before = process.memory_info().rss / (1024 * 1024)
            rotation_started = perf_counter()
            test_image = engine.rotate(variant, rotation_code)
            rotation_ms = (perf_counter() - rotation_started) * 1000
            decode_started = perf_counter()
            results = engine.read_barcodes(
                test_image,
                all_types=stage == "Raw (all types)" or selected_format == "auto",
                auto_rotate=stage == "Raw + auto rotate (DataMatrix)",
                barcode_format=None if selected_format == "auto" else selected_format,
                is_pure=is_pure,
                try_harder=try_harder,
            )
            decode_ms = (perf_counter() - decode_started) * 1000
            config_rss_after = process.memory_info().rss / (1024 * 1024)
            performance = {
                "preprocess_ms": preprocess["preprocess_ms"],
                "rotation_ms": round(rotation_ms, 3),
                "decode_ms": round(decode_ms, 3),
                "configuration_ms": round(rotation_ms + decode_ms, 3),
                "total_ms": round(preprocess["preprocess_ms"] + rotation_ms + decode_ms, 3),
                "preprocess_rss_delta_mb": preprocess["rss_delta_mb"],
                "configuration_rss_delta_mb": round(config_rss_after - config_rss_before, 3),
                "rss_after_mb": round(config_rss_after, 3),
                "output_mb": preprocess["output_mb"],
            }
            filename = f"{clean_stage_name(stage)}__{clean_stage_name(angle)}.png"
            cv2.imwrite(str(output_dir / filename), test_image)
            details = result_data(results)
            success = bool(results)
            row["cells"].append({
                "angle": angle, "success": success, "barcode_count": len(details), "results": details,
                "performance": performance,
            })
            attempts.append(
                {
                    "stage": display_name,
                    "angle": angle,
                    "success": success,
                    "barcode_count": len(details),
                    "results": details,
                    "flags": flags_for(stage, angle, selected_format, is_pure, try_harder),
                    "performance": performance,
                    "image_url": f"/api/artifact/{run_id}/{filename}",
                    "filename": filename,
                }
            )
        table.append(row)

    record = {
        "id": run_id,
        "image_id": image_id,
        "kind": kind,
        "description": description,
        "barcode_format": selected_format,
        "is_pure": is_pure,
        "try_harder": try_harder,
        "attempts": attempts,
        "table": table,
        "output_dir": output_dir,
    }
    runs[run_id] = record
    successes = sum(attempt["success"] for attempt in attempts)
    max_barcodes_found = max((attempt["barcode_count"] for attempt in attempts), default=0)
    detected_formats = sorted({
        result["format"] for attempt in attempts for result in attempt["results"]
    })
    run_rss_after = process.memory_info().rss / (1024 * 1024)
    run_performance = {
        "elapsed_ms": round((perf_counter() - run_started) * 1000, 3),
        "rss_before_mb": round(run_rss_before, 3),
        "rss_after_mb": round(run_rss_after, 3),
        "rss_delta_mb": round(run_rss_after - run_rss_before, 3),
    }
    return jsonify(
        run_id=run_id,
        kind=kind,
        description=description,
        table=table,
        attempts=attempts,
        success_count=successes,
        failure_count=len(attempts) - successes,
        max_barcodes_found=max_barcodes_found,
        expected_value=engine.EXPECTED_VALUE,
        barcode_format=selected_format,
        barcode_format_label=BARCODE_FORMATS[selected_format],
        run_performance=run_performance,
        detected_formats=detected_formats,
        is_pure=is_pure,
        try_harder=try_harder,
    )


@app.get("/api/artifact/<run_id>/<filename>")
def artifact(run_id, filename):
    record = runs.get(run_id)
    if record is None:
        return jsonify(error="Run not found."), 404
    return send_from_directory(record["output_dir"], filename)


@app.post("/api/download")
def download():
    payload = request.get_json(silent=True) or {}
    requested_ids = payload.get("run_ids", [])
    successful_only = bool(payload.get("successful_only"))
    memory = BytesIO()
    count = 0
    with zipfile.ZipFile(memory, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, run_id in enumerate(requested_ids, 1):
            record = runs.get(run_id)
            if record is None:
                continue
            for attempt in record["attempts"]:
                if successful_only and not attempt["success"]:
                    continue
                source = record["output_dir"] / attempt["filename"]
                if source.exists():
                    archive.write(source, f"run_{index:02d}/{attempt['filename']}")
                    count += 1
    if count == 0:
        return jsonify(error="There are no matching cutouts to save."), 400
    memory.seek(0)
    return send_file(memory, mimetype="application/zip", as_attachment=True, download_name="barcode_cutouts.zip")


@app.post("/api/reset")
def reset():
    payload = request.get_json(silent=True) or {}
    for run_id in payload.get("run_ids", []):
        record = runs.pop(run_id, None)
        if record:
            shutil.rmtree(record["output_dir"], ignore_errors=True)
    return jsonify(ok=True)


if __name__ == "__main__":
    url = "http://127.0.0.1:5000"
    if os.environ.get("OPEN_BROWSER") == "1":
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=5000, debug=False)
