# Contributing to Barcode Benchmark

Thank you for helping make barcode benchmarks more useful and reproducible.

## Before you start

- Keep changes focused and easy to review.
- Do not commit private barcode images, customer data, generated cutouts, `.web_data`,
  virtual environments, or secrets.
- Use only test images that you created or are licensed to redistribute, and document
  their source.
- Preserve the existing keyboard controls and simple local setup unless a change has a
  clear migration path.

For a substantial feature or new decoder backend, discuss the design before investing
in a large implementation.

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000` and test the affected workflow with both a successful and
failed barcode image.

## Adding a decoder backend

ZXing-C++ is the only v1 backend. New backends should:

1. Accept the same preprocessed image and ROI used by other engines.
2. Return normalized text/raw bytes, format, position, and success information.
3. Keep backend-specific flags explicit rather than mapping unlike options silently.
4. Separate preprocessing, data-transfer, detection, and decode timing where possible.
5. Report unsupported formats and unavailable native dependencies clearly.
6. Include tests and update the README support table.

## Benchmark integrity

- Do not rank partial detections above more complete detections solely because they are
  faster.
- Keep raw and preprocessed results available for visual inspection.
- Record relevant versions and configuration flags.
- Avoid claiming general performance improvements from one image or machine.
- Explain measurement limitations, especially around RSS and warm/cold execution.

## Submitting a change

1. Create a branch from `main`.
2. Make the smallest coherent change.
3. Run `python -m py_compile app.py barcode_bbox_tester.py`.
4. Exercise the changed browser or OpenCV workflow.
5. Update documentation when behavior or setup changes.
6. Open a pull request describing the problem, approach, verification, and any benchmark
   limitations.

By contributing, you agree that your contribution will be licensed under the MIT
License.
