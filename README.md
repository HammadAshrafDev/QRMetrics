# QRMetrics

**Find the barcode preprocessing recipe that reads the most codes and see what it truly costs.**

QRMetrics is a local visual workbench for testing barcode regions against many image-processing configurations. Draw a box or four-point polygon around the area you care about, and QRMetrics compares the resulting reads by completeness, speed, and approximate memory impact.

The first release uses [ZXing-C++](https://github.com/zxing-cpp/zxing-cpp). The longer-term goal is a shared benchmark for popular barcode libraries, including pyzbar/ZBar, OpenCV `BarcodeDetector`, and pylibdmtx.

> QRMetrics is an experiment workbench, not a claim that one preprocessing recipe is universally best. Its value is showing what works on *your* images, regions, and hardware.

## Why QRMetrics?

A barcode reader can be fast on a clean image and unreliable on the frame that reaches production. Blur, glare, scale, perspective, contrast, and an incomplete region of interest can each change the result.

QRMetrics makes those trade-offs visible. It runs the same selected region through a reproducible matrix of preprocessing and orientation tests, then puts successful and failed attempts side by side. Results that recover more barcodes rank above faster but incomplete results; time breaks ties.

## Quick start

QRMetrics is currently tested on Ubuntu/Linux and requires Python 3 with `venv` support.

```bash
git clone https://github.com/HammadAshrafDev/QRMetrics.git
cd QRMetrics
chmod +x start.sh
./start.sh
```

On first launch, the script creates `.venv`, installs the dependencies, starts the local server, and opens [http://127.0.0.1:5000](http://127.0.0.1:5000).

To start it manually:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

## From image to recommendation

1. Upload an image, drop a local image, or provide a public image URL.
2. Draw a bounding box or a perspective-aware four-point polygon around the barcode area.
3. Choose a barcode format—or let ZXing search all supported formats—and set the reader controls.
4. QRMetrics tests preprocessing variants and orientations, measuring each attempt.
5. Compare the reads. By default, configurations that find the most barcodes appear first, with faster configurations winning ties.

For the complete pipeline, preprocessing matrix, metric definitions, and result-ranking logic, read [How QRMetrics works](HOW_IT_WORKS.md).

## What it can do

- Compare raw and preprocessed versions of an accurately selected image region.
- Test each variant at 0°, 90°, 180°, and 270° with configurable ZXing search behavior.
- Report decoded values and formats alongside preprocessing time, decode time, total time, and approximate RSS change.
- Rank and filter results by barcode count, speed, status, detected format, or applied steps.
- Preserve several ROI runs for comparison and download successful or complete cutout sets.

The current matrix covers common operations such as scaling, contrast correction, sharpening, denoising, thresholding, morphology, perspective correction, polarity inversion, deblurring, quiet-zone padding, parameter sweeps, and a combined pipeline.

## Using the interface

Upload an image, then use **Box** to drag a rectangular region or **Polygon** to click its four corners. Use the mouse wheel to zoom and **Fit image** to return to the full view.

| Key | Action |
|---|---|
| `B` | Box selection mode |
| `P` | Four-point polygon mode |
| `R` | Clear all ROI runs |
| `S` | Download successful cutouts from the latest run |
| `D` | Download every generated cutout |

Data Matrix is the default format. Auto detect searches all formats and, after the first successful run, can switch future runs to the detected format. **Pure barcode** defaults to off; **Try harder** defaults to on.

## Understanding the results

Each result card shows how many barcodes were found, their formats and values, the exact image recipe and reader flags, the generated cutout, and the measured cost of that configuration.

The default **Most complete** order uses:

1. Higher barcode count.
2. Lower total configuration time when counts are equal.

Use **Fastest** when latency is the only concern, or set a minimum barcode count to hide incomplete reads. See [How QRMetrics works](HOW_IT_WORKS.md#how-results-are-ranked) before treating a result as a production recommendation.

## Decoder support

| Backend | Status |
|---|---|
| ZXing-C++ | Available in v1 |
| pyzbar / ZBar | Planned |
| OpenCV `BarcodeDetector` | Planned |
| pylibdmtx / libdmtx | Planned |

Future backends should receive the same ROI and image variants, return a normalized result shape, and retain their own library-specific controls. Similarly named options will not be presented as equivalent unless they actually are.

## Benchmark responsibly

Measurements are most useful when runs use the same source image, ROI, machine, and software versions. RSS deltas are approximate because Python and OpenCV may reuse allocated memory. QRMetrics does not yet calculate ground-truth precision/recall, peak RSS, CPU utilization, or repeated-trial percentiles.

Uploaded images and generated artifacts stay under `.web_data` on the local machine. The server binds to `127.0.0.1`; public-URL downloads are capped at 15 MB and private-network targets are rejected.

## Contributing

Contributions are welcome, particularly new decoder adapters, repeatable measurements, cross-platform support, and focused preprocessing experiments. Start with [CONTRIBUTING.md](CONTRIBUTING.md) and follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## License

QRMetrics is available under the [MIT License](LICENSE). ZXing-C++ and other current or future dependencies retain their own licenses.
