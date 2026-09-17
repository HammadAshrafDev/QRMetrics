"""Draw barcode ROIs and test them with ZXing-C++.

Edit the small SETTINGS section below, then run:
    python barcode_bbox_tester.py

Mouse: drag a rectangle in B mode, or click four corners in P mode
Keys:  B = box mode, P = polygon mode, R = clear, S = save found, D = save all
"""

try:
    from pathlib import Path
    import sys
    from time import perf_counter

    import cv2
    import numpy as np
    import psutil
    import zxingcpp
except ImportError:
    raise SystemExit("Missing dependency. Install with: pip install -r requirements.txt")


# ------------------------------- SETTINGS ----------------------------------
IMAGE_PATH = "image (2).png"
EXPECTED_VALUE = None       # Example: "ABC123"; leave as None to skip matching
CUTOUT_DIR = "cutouts"      # Created beside this script when S or D is pressed
QUIET_ZONE_PIXELS = 30      # White border added in the final preprocessing stage
CLAHE_CLIP_LIMIT = 2.0
CLAHE_TILE_SIZE = (8, 8)
ADAPTIVE_BLOCK_SIZE = 31    # Must be odd and greater than 1
ADAPTIVE_C = 5
TIGHT_CROP_PERCENT = 5
GAMMA_VALUE = 1.5
THRESHOLD_VALUES = (80, 120, 160, 200)
MORPH_KERNEL_SIZES = (2, 3)
LOCAL_THRESHOLD_WINDOW = 31
# ---------------------------------------------------------------------------

WINDOW_NAME = "QRMetrics | B box | P polygon | R reset | S found | D all"
ROTATIONS = {
    "0 deg": None,
    "90 deg": cv2.ROTATE_90_CLOCKWISE,
    "180 deg": cv2.ROTATE_180,
    "270 deg": cv2.ROTATE_90_COUNTERCLOCKWISE,
}


def rotate(image, code):
    return image if code is None else cv2.rotate(image, code)


def tight_crop(image):
    height, width = image.shape[:2]
    dx = min(int(width * TIGHT_CROP_PERCENT / 100), max(0, width // 2 - 1))
    dy = min(int(height * TIGHT_CROP_PERCENT / 100), max(0, height // 2 - 1))
    return image[dy:height - dy, dx:width - dx].copy()


def contrast_stretch(gray):
    low, high = np.percentile(gray, (2, 98))
    if high <= low:
        return gray.copy()
    return np.clip((gray.astype(np.float32) - low) * 255 / (high - low), 0, 255).astype(np.uint8)


def gamma_correct(gray):
    table = np.array([((value / 255.0) ** GAMMA_VALUE) * 255 for value in range(256)], dtype=np.uint8)
    return cv2.LUT(gray, table)


def unsharp(gray, amount=1.5):
    blurred = cv2.GaussianBlur(gray, (0, 0), 1.2)
    return cv2.addWeighted(gray, 1.0 + amount, blurred, -amount, 0)


def local_threshold(gray, method):
    image = gray.astype(np.float32)
    mean = cv2.boxFilter(image, -1, (LOCAL_THRESHOLD_WINDOW, LOCAL_THRESHOLD_WINDOW))
    square_mean = cv2.boxFilter(image * image, -1, (LOCAL_THRESHOLD_WINDOW, LOCAL_THRESHOLD_WINDOW))
    std = np.sqrt(np.maximum(square_mean - mean * mean, 0))
    if method == "sauvola":
        threshold = mean * (1 + 0.2 * (std / 128.0 - 1))
    else:
        threshold = mean - 0.2 * std
    return np.where(image > threshold, 255, 0).astype(np.uint8)


def order_four_points(points):
    points = np.asarray(points, dtype=np.float32)
    sums, diffs = points.sum(axis=1), np.diff(points, axis=1).ravel()
    return np.array([
        points[np.argmin(sums)], points[np.argmin(diffs)],
        points[np.argmax(sums)], points[np.argmax(diffs)],
    ], dtype=np.float32)


def warp_four_points(image, points):
    tl, tr, br, bl = order_four_points(points)
    width = max(int(np.linalg.norm(br - bl)), int(np.linalg.norm(tr - tl)))
    height = max(int(np.linalg.norm(tr - br)), int(np.linalg.norm(tl - bl)))
    if width < 2 or height < 2:
        return None
    target = np.array([
        [0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]
    ], dtype=np.float32)
    transform = cv2.getPerspectiveTransform(np.array([tl, tr, br, bl]), target)
    return cv2.warpPerspective(image, transform, (width, height))


def rectify_perspective(gray):
    """Rectify the largest 4-corner region; return the original if none is found."""
    edges = cv2.Canny(gray, 50, 150)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:10]:
        perimeter = cv2.arcLength(contour, True)
        corners = cv2.approxPolyDP(contour, 0.03 * perimeter, True)
        if len(corners) != 4 or cv2.contourArea(corners) < gray.size * 0.05:
            continue
        warped = warp_four_points(gray, corners.reshape(4, 2))
        if warped is not None:
            return warped
    return gray.copy()


def wiener_deblur(gray, kernel_size=5, sigma=1.2, noise=0.01):
    """Small Gaussian Wiener deconvolution with no extra dependency."""
    kernel_size = min(kernel_size, min(gray.shape[:2]))
    if kernel_size % 2 == 0:
        kernel_size -= 1
    if kernel_size < 2:
        return gray.copy()
    gaussian = cv2.getGaussianKernel(kernel_size, sigma)
    kernel = gaussian @ gaussian.T
    psf = np.zeros(gray.shape, dtype=np.float32)
    psf[:kernel_size, :kernel_size] = kernel
    psf = np.fft.ifftshift(psf)
    transfer = np.fft.fft2(psf)
    observed = np.fft.fft2(gray.astype(np.float32) / 255.0)
    restored = np.fft.ifft2(np.conj(transfer) * observed / (np.abs(transfer) ** 2 + noise)).real
    return np.clip(restored * 255, 0, 255).astype(np.uint8)


def make_variants_with_metrics(roi):
    """Build each standalone variant and measure its time and process RSS."""
    process = psutil.Process()
    variants, metrics = {}, {}

    def gray():
        return cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if roi.ndim == 3 else roi.copy()

    def otsu_image():
        return cv2.threshold(gray(), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]

    def add(label, operation):
        rss_before = process.memory_info().rss / (1024 * 1024)
        started = perf_counter()
        image = operation()
        elapsed_ms = (perf_counter() - started) * 1000
        rss_after = process.memory_info().rss / (1024 * 1024)
        variants[label] = image
        metrics[label] = {
            "preprocess_ms": round(elapsed_ms, 3),
            "rss_before_mb": round(rss_before, 3),
            "rss_after_mb": round(rss_after, 3),
            "rss_delta_mb": round(rss_after - rss_before, 3),
            "output_mb": round(image.nbytes / (1024 * 1024), 3),
        }

    def blackhat_image():
        result = cv2.morphologyEx(gray(), cv2.MORPH_BLACKHAT, np.ones((9, 9), np.uint8))
        return cv2.normalize(result, None, 0, 255, cv2.NORM_MINMAX)

    def adaptive_image():
        return cv2.adaptiveThreshold(
            gray(), 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY,
            ADAPTIVE_BLOCK_SIZE, ADAPTIVE_C,
        )

    def all_steps_image():
        image = tight_crop(gray())
        image = rectify_perspective(image)
        image = cv2.resize(image, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        image = cv2.fastNlMeansDenoising(image, None, 10, 7, 21)
        image = contrast_stretch(image)
        image = gamma_correct(image)
        image = cv2.createCLAHE(CLAHE_CLIP_LIMIT, CLAHE_TILE_SIZE).apply(image)
        image = wiener_deblur(image)
        image = unsharp(image)
        image = cv2.adaptiveThreshold(
            image, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY,
            ADAPTIVE_BLOCK_SIZE, ADAPTIVE_C,
        )
        image = cv2.morphologyEx(image, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        return cv2.copyMakeBorder(
            image, QUIET_ZONE_PIXELS, QUIET_ZONE_PIXELS, QUIET_ZONE_PIXELS,
            QUIET_ZONE_PIXELS, cv2.BORDER_CONSTANT, value=255,
        )

    # Raw configurations intentionally have zero preprocessing cost.
    for label in ("Raw (all types)", "Raw (DataMatrix)", "Raw + auto rotate (DataMatrix)"):
        variants[label] = roi
        rss = process.memory_info().rss / (1024 * 1024)
        metrics[label] = {
            "preprocess_ms": 0.0, "rss_before_mb": round(rss, 3),
            "rss_after_mb": round(rss, 3), "rss_delta_mb": 0.0,
            "output_mb": round(roi.nbytes / (1024 * 1024), 3),
        }

    add("Tight ROI crop (DataMatrix)", lambda: tight_crop(roi))
    add("Upscale 2x (DataMatrix)", lambda: cv2.resize(gray(), None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC))
    add("Upscale 3x (DataMatrix)", lambda: cv2.resize(gray(), None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC))
    add("Contrast stretch (DataMatrix)", lambda: contrast_stretch(gray()))
    add(f"Gamma {GAMMA_VALUE:g} (DataMatrix)", lambda: gamma_correct(gray()))
    add("Unsharp mask (DataMatrix)", lambda: unsharp(gray()))
    add("Black-hat (DataMatrix)", blackhat_image)
    add("CLAHE (DataMatrix)", lambda: cv2.createCLAHE(CLAHE_CLIP_LIMIT, CLAHE_TILE_SIZE).apply(gray()))
    add("Adaptive (DataMatrix)", adaptive_image)
    add("Otsu (DataMatrix)", otsu_image)
    add("Sauvola (DataMatrix)", lambda: local_threshold(gray(), "sauvola"))
    add("Niblack (DataMatrix)", lambda: local_threshold(gray(), "niblack"))
    add("Quiet zone (DataMatrix)", lambda: cv2.copyMakeBorder(
        gray(), QUIET_ZONE_PIXELS, QUIET_ZONE_PIXELS, QUIET_ZONE_PIXELS,
        QUIET_ZONE_PIXELS, cv2.BORDER_CONSTANT, value=255,
    ))
    add("Morph closing (DataMatrix)", lambda: cv2.morphologyEx(
        otsu_image(), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8),
    ))
    add("Perspective rectify (DataMatrix)", lambda: rectify_perspective(gray()))
    add("Polarity inverted (DataMatrix)", lambda: cv2.bitwise_not(gray()))
    add("Denoise + sharpen (DataMatrix)", lambda: unsharp(
        cv2.fastNlMeansDenoising(gray(), None, 10, 7, 21),
    ))
    add("Wiener deblur (DataMatrix)", lambda: wiener_deblur(gray()))
    add("All preprocessing (DataMatrix)", all_steps_image)
    for value in THRESHOLD_VALUES:
        add(f"Threshold {value} (DataMatrix)", lambda value=value: cv2.threshold(
            gray(), value, 255, cv2.THRESH_BINARY,
        )[1])
    for size in MORPH_KERNEL_SIZES:
        kernel = np.ones((size, size), np.uint8)
        add(f"Erode {size}x{size} (DataMatrix)", lambda kernel=kernel: cv2.erode(otsu_image(), kernel))
        add(f"Dilate {size}x{size} (DataMatrix)", lambda kernel=kernel: cv2.dilate(otsu_image(), kernel))
    return variants, metrics


def make_variants(roi):
    """Return every image variant used by the test matrix."""
    return make_variants_with_metrics(roi)[0]


def read_barcodes(
    image, all_types=False, auto_rotate=False, barcode_format=None,
    is_pure=False, try_harder=True,
):
    """Read barcodes with the search controls exposed by the Python binding."""
    options = dict(
        try_rotate=auto_rotate,
        try_downscale=try_harder,
        try_invert=try_harder,
        is_pure=is_pure,
    )
    if all_types:
        return list(zxingcpp.read_barcodes(image, **options))
    selected = barcode_format or zxingcpp.BarcodeFormat.DataMatrix
    if isinstance(selected, str):
        selected = getattr(zxingcpp.BarcodeFormat, selected)
    return list(zxingcpp.read_barcodes(image, formats=selected, **options))


def result_mark(results):
    if not results:
        return "--"
    if EXPECTED_VALUE is None:
        return "✓ FOUND"
    return "✓ MATCH" if any(item.text == EXPECTED_VALUE for item in results) else "✗ MISMATCH"


def print_table(box_number, region_description, matrix):
    headers = ["Preprocessing", *ROTATIONS]
    rows = [[stage, *(result_mark(matrix[stage][angle]) for angle in ROTATIONS)] for stage in matrix]
    widths = [max(len(str(row[i])) for row in [headers, *rows]) for i in range(len(headers))]

    def line(char="-"):
        return "+" + "+".join(char * (width + 2) for width in widths) + "+"

    def row(values):
        return "|" + "|".join(f" {str(value):<{width}} " for value, width in zip(values, widths)) + "|"

    print(f"\nROI #{box_number}  region={region_description}")
    print("Locked ZXing settings: Pure_Barcode=False, Try_Harder=True")
    if EXPECTED_VALUE is not None:
        print(f"Expected value: {EXPECTED_VALUE!r}  (✓ = exact match)")
    print(line(), row(headers), line("="), sep="\n")
    for values in rows:
        print(row(values))
    print(line())

    print("Raw ZXing reads:")
    found_any = False
    for stage, angles in matrix.items():
        for angle, results in angles.items():
            for item in results:
                found_any = True
                matched = EXPECTED_VALUE is not None and item.text == EXPECTED_VALUE
                suffix = "  ✓ MATCH" if matched else ""
                print(
                    f"  [{stage} | {angle}] format={item.format} "
                    f"text={item.text!r} bytes={item.bytes!r}{suffix}"
                )
    if not found_any:
        print("  No barcode detected in any test.")


def test_roi(roi, box_number, region_description):
    variants = make_variants(roi)
    matrix = {}
    for stage, variant in variants.items():
        matrix[stage] = {}
        for angle, rotation_code in ROTATIONS.items():
            matrix[stage][angle] = read_barcodes(
                rotate(variant, rotation_code),
                all_types=(stage == "Raw (all types)"),
                auto_rotate=(stage == "Raw + auto rotate (DataMatrix)"),
            )
    print_table(box_number, region_description, matrix)
    return matrix


def safe_name(text):
    cleaned = "".join(character.lower() if character.isalnum() else " " for character in text)
    return "_".join(cleaned.split())


def save_all_cutouts(selections):
    if not selections:
        print("\nNo ROIs to save. Draw at least one box first.")
        return

    folder = Path(__file__).resolve().parent / CUTOUT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    saved = 0
    for box_number, selection in enumerate(selections, 1):
        roi = selection["roi"]
        for stage, variant in make_variants(roi).items():
            for angle, rotation_code in ROTATIONS.items():
                filename = f"roi_{box_number:02d}__{safe_name(stage)}__{safe_name(angle)}.png"
                if cv2.imwrite(str(folder / filename), rotate(variant, rotation_code)):
                    saved += 1
    print(f"\nSaved {saved} test cutouts to: {folder}")


def save_found_cutouts(latest_test):
    if latest_test is None:
        print("\nNo latest ROI test to save. Draw a box first.")
        return

    box_number, roi, matrix = latest_test
    variants = make_variants(roi)
    folder = Path(__file__).resolve().parent / CUTOUT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    saved = 0
    for stage, angles in matrix.items():
        for angle, results in angles.items():
            if not results:
                continue
            rotation_code = ROTATIONS[angle]
            filename = f"roi_{box_number:02d}__{safe_name(stage)}__{safe_name(angle)}__found.png"
            if cv2.imwrite(str(folder / filename), rotate(variants[stage], rotation_code)):
                saved += 1

    if saved:
        print(f"\nSaved {saved} successful cutouts from ROI #{box_number} to: {folder}")
    else:
        print(f"\nROI #{box_number} had no successful barcode reads; nothing was saved.")


def main():
    if ADAPTIVE_BLOCK_SIZE <= 1 or ADAPTIVE_BLOCK_SIZE % 2 == 0:
        sys.exit("ADAPTIVE_BLOCK_SIZE must be an odd integer greater than 1.")

    path = Path(IMAGE_PATH).expanduser()
    image = cv2.imread(str(path))
    if image is None:
        sys.exit(f"Could not open image: {path.resolve()}")

    selections = []
    latest_test = None
    drawing_mode = "box"
    drag_start = None
    drag_end = None
    polygon_points = []

    def redraw():
        canvas = image.copy()
        for index, selection in enumerate(selections, 1):
            points = selection["points"]
            if selection["kind"] == "box":
                x1, y1, x2, y2 = selection["bounds"]
                cv2.rectangle(canvas, (x1, y1), (x2, y2), (0, 255, 0), 2)
                label_point = (x1 + 4, y1 + 20)
            else:
                cv2.polylines(canvas, [np.array(points, np.int32)], True, (255, 200, 0), 2)
                label_point = (points[0][0] + 4, points[0][1] + 20)
            cv2.putText(canvas, str(index), label_point, cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 0), 2)
        if drag_start is not None and drag_end is not None:
            cv2.rectangle(canvas, drag_start, drag_end, (0, 255, 255), 2)
        if polygon_points:
            cv2.polylines(canvas, [np.array(polygon_points, np.int32)], False, (0, 255, 255), 2)
            for point in polygon_points:
                cv2.circle(canvas, point, 5, (0, 255, 255), -1)
        cv2.putText(canvas, f"Mode: {drawing_mode.upper()}", (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 255, 255), 2)
        cv2.imshow(WINDOW_NAME, canvas)

    def mouse(event, x, y, _flags, _data):
        nonlocal drag_start, drag_end, latest_test, polygon_points
        if drawing_mode == "polygon" and event == cv2.EVENT_LBUTTONDOWN:
            polygon_points.append((x, y))
            redraw()
            if len(polygon_points) == 4:
                points = polygon_points.copy()
                polygon_points.clear()
                roi = warp_four_points(image, points)
                if roi is None:
                    print("\nPolygon is too small or invalid; please try again.")
                else:
                    selections.append({"kind": "polygon", "points": points, "roi": roi})
                    number = len(selections)
                    redraw()
                    matrix = test_roi(roi, number, f"polygon={points}")
                    latest_test = (number, roi.copy(), matrix)
        elif drawing_mode == "box" and event == cv2.EVENT_LBUTTONDOWN:
            drag_start = (x, y)
            drag_end = (x, y)
        elif drawing_mode == "box" and event == cv2.EVENT_MOUSEMOVE and drag_start is not None:
            drag_end = (x, y)
            redraw()
        elif drawing_mode == "box" and event == cv2.EVENT_LBUTTONUP and drag_start is not None:
            x1, x2 = sorted((drag_start[0], x))
            y1, y2 = sorted((drag_start[1], y))
            drag_start = drag_end = None
            if x2 - x1 >= 2 and y2 - y1 >= 2:
                bounds = (x1, y1, x2, y2)
                roi = image[y1:y2, x1:x2]
                selections.append({
                    "kind": "box", "points": [(x1, y1), (x2, y2)],
                    "bounds": bounds, "roi": roi.copy(),
                })
                redraw()
                matrix = test_roi(roi, len(selections), bounds)
                latest_test = (len(selections), roi.copy(), matrix)
            else:
                redraw()

    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(WINDOW_NAME, mouse)
    redraw()
    print("B: box mode | P: four-point polygon | R: reset | S: latest found | D: all")

    while True:
        key = cv2.waitKey(20) & 0xFF
        if key in (ord("q"), 27):
            break
        if key in (ord("b"), ord("B")):
            drawing_mode = "box"
            polygon_points.clear()
            print("\nDrawing mode: bounding box")
            redraw()
        if key in (ord("p"), ord("P")):
            drawing_mode = "polygon"
            drag_start = drag_end = None
            polygon_points.clear()
            print("\nDrawing mode: polygon; click four corners.")
            redraw()
        if key in (ord("r"), ord("R")):
            selections.clear()
            latest_test = None
            polygon_points.clear()
            print("\nAll ROIs cleared.")
            redraw()
        if key in (ord("s"), ord("S")):
            save_found_cutouts(latest_test)
        if key in (ord("d"), ord("D")):
            save_all_cutouts(selections)
        if cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
            break
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
