# Barcode Benchmark

Barcode Benchmark is a local visual workbench for comparing barcode preprocessing
recipes by detection completeness, decoding time, total processing time, and
approximate memory impact.

The first release uses **ZXing-C++** through its Python binding. The project is designed
to support additional decoding engines later—including **pyzbar/ZBar**, OpenCV's
`BarcodeDetector`, and **pylibdmtx**—so the same image regions and preprocessing recipes
can be compared fairly across popular libraries.

## Why this exists

The fastest attempt is not useful if it misses most of the symbols. If an image has 20
barcodes, a configuration that finds all 20 should rank above one that finds five,
even when the partial result returns sooner.

Barcode Benchmark therefore ranks successful attempts by:

1. Highest number of barcodes detected.
2. Lowest total configuration time as the tie-breaker.

You can also switch to time-only sorting or filter by a minimum barcode count.

## Current capabilities

- Upload an image, drag in a public web image, or use the local OpenCV interface.
- Draw a rectangular ROI or a perspective-aware four-point polygon.
- Zoom to place regions accurately.
- Select a barcode format or let ZXing-C++ search supported formats.
- Compare raw input and preprocessing variations at 0°, 90°, 180°, and 270°.
- See every decoded value, raw bytes, detected format, and orientation.
- Compare preprocessing, rotation, decode, and total wall time.
- Inspect output-buffer size and approximate process RSS changes.
- Rank by barcode count before speed and filter by count, status, format, or steps.
- Expand result cards for exact recipes and reader flags.
- Save successful cutouts from the latest run or download all generated cutouts.
- Keep multiple ROI runs in the session until reset.

Preprocessing experiments include tight cropping, 2×/3× upscaling, contrast stretch,
gamma correction, CLAHE, adaptive/Otsu/Sauvola/Niblack thresholding, unsharp masking,
black-hat morphology, closing, erode/dilate sweeps, perspective rectification,
polarity inversion, denoise-before-sharpen, Wiener deblurring, quiet-zone padding, and
a combined pipeline.

## Requirements

- Ubuntu/Linux is the currently tested environment.
- Python 3 with `venv` support.
- A desktop browser for the web interface.

Windows and macOS support are planned but not yet verified.

## Quick start

Make the launcher executable if necessary, then run it:

```bash
chmod +x start.sh
./start.sh
```

On first use, the script creates `.venv`, installs `requirements.txt`, starts the local
server, and opens `http://127.0.0.1:5000`.

You can also start it manually:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

## Using the browser interface

1. Upload or drop an image.
2. Choose the expected barcode format. Data Matrix is the default.
3. Use **Box** and drag around a region, or use **Polygon** and click four corners.
4. Wait for the preprocessing and rotation matrix to finish.
5. Compare the successful cards. The default order favors completeness before speed.
6. Use the minimum-count, status, format, and step filters to narrow the results.

Keyboard shortcuts:

| Key | Action |
|---|---|
| `B` | Box selection mode |
| `P` | Four-point polygon mode |
| `R` | Clear all ROI runs |
| `S` | Save successful cutouts from the latest run |
| `D` | Download every generated cutout |

Mouse-wheel zoom ranges from the fitted view to 800%. **Fit image** restores the
full-image view.

## Reader controls

- **Pure barcode** defaults to OFF. Enable it only for a clean, tightly cropped,
  perfectly aligned single symbol.
- **Try harder** defaults to ON. In the installed Python binding, this UI control maps
  to additional downscale and inverted-polarity searches. Native C++ `TryHarder` is not
  exposed by this binding and remains enabled internally.
- Restricting the expected barcode format can reduce unnecessary search work.

## Reading benchmark results

The table separates preprocessing cost from ZXing decoding cost. Each card shows the
number and formats of barcodes detected, the complete preprocessing recipe, rotation,
reader flags, timing breakdown, approximate RSS change, and generated cutout.

RSS deltas are exploratory measurements: Python and OpenCV reuse allocated memory, so
small before/after differences are not equivalent to true peak memory. Results should
be compared on the same machine, image, ROI, and software versions.

This release does not yet provide ground-truth precision/recall, repeated-trial
percentiles, peak-memory sampling, or process CPU utilization. Treat it as an
experiment workbench rather than a scientific benchmark suite.

## Planned decoder backends

| Backend | Status | Intended comparison |
|---|---|---|
| ZXing-C++ | Available in v1 | Multi-format baseline and current reader controls |
| pyzbar / ZBar | Planned | Common Python wrapper and 1D/QR workloads |
| OpenCV `BarcodeDetector` | Planned | OpenCV-native detection and decoding |
| pylibdmtx / libdmtx | Planned | Data Matrix-focused comparison |

Future backends should consume the same ROI and preprocessed image, return a normalized
result schema, and expose backend-specific flags without pretending that differently
named settings are equivalent.

## Project structure

```text
app.py                    Flask application and benchmark orchestration
barcode_bbox_tester.py    Preprocessing recipes and OpenCV desktop interface
templates/index.html      Browser UI
static/app.js             Drawing, zoom, run state, sorting, and filters
static/style.css          Application styling
requirements.txt          Python dependencies
start.sh                  Local setup and launcher
```

Generated uploads, benchmark artifacts, cutouts, virtual environments, and local test
images are excluded from Git.

## Privacy and security

The server binds to `127.0.0.1`. Uploaded images and generated variations are stored
under `.web_data` for the running local application. Remote image downloads are limited
to 15 MB, and private/local network targets are rejected.

Do not upload confidential production images to a deployment you do not control.

## Contributing

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before proposing a
change, and follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## License

Barcode Benchmark is available under the [MIT License](LICENSE). ZXing-C++ and other
current or future dependencies retain their own licenses.
