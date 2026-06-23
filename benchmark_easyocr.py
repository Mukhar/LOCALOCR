"""
benchmark_easyocr.py
~~~~~~~~~~~~~~~~~~~~
Benchmarks EasyOCR (CPU + GPU) vs PaddleOCR (CPU) using real frames from
output/matched/ (recursively across all matched-keyword subfolders).

Usage:
    cd /Users/m0j0f4p/personalWs/LOCALOCR
    python benchmark_easyocr.py              # starts at 10 frames, auto-escalates
    python benchmark_easyocr.py --frames 50  # fixed frame count
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

FRAMES_DIR = Path(__file__).parent / "output" / "matched"
LANGUAGES = ["hi", "en"]
DIFF_THRESHOLD = 0.15  # escalate if fastest vs slowest engine differ by less than 15%
ESCALATE_SIZES = [10, 100, 200]


def check_easyocr_gpu_support() -> tuple:
    """Return (ok, message) indicating whether EasyOCR has a GPU backend."""
    try:
        import torch
    except Exception as e:
        return False, f"PyTorch import failed: {e}"

    has_cuda = torch.cuda.is_available()
    has_mps = hasattr(torch.backends, "mps") and torch.backends.mps.is_available()
    if has_cuda or has_mps:
        backend = "CUDA" if has_cuda else "MPS"
        return True, f"EasyOCR GPU backend available: {backend}"
    return False, "EasyOCR GPU backend unavailable (neither CUDA nor MPS found)"


def check_paddle_cpu_support() -> tuple:
    """Return (ok, message) indicating whether Paddle is importable for CPU inference."""
    try:
        import paddle  # noqa: F401
    except Exception as e:
        return False, f"Paddle import failed: {e}"
    return True, "Paddle CPU backend available"


def sample_frames(n: int) -> list:
    """Pick n evenly-spaced frames from output/matched/ (recursive)."""
    all_frames = sorted(FRAMES_DIR.rglob("*.png"))
    if not all_frames:
        print(f"ERROR: No frames found in {FRAMES_DIR}")
        sys.exit(1)
    if n >= len(all_frames):
        return all_frames
    step = len(all_frames) / n
    return [all_frames[int(i * step)] for i in range(n)]


def run_easyocr_benchmark(frames: list, use_gpu: bool) -> dict:
    """
    Run EasyOCR on the requested device and return timing dict.
    Creates a fresh Reader each call.
    """
    import easyocr

    label = f"EasyOCR {'GPU' if use_gpu else 'CPU'}"
    print(f"\n  [{label}] Initializing EasyOCR reader (languages={LANGUAGES})...", flush=True)

    t_init_start = time.perf_counter()
    try:
        reader = easyocr.Reader(LANGUAGES, gpu=use_gpu, verbose=False)
    except Exception as e:
        print(f"  [{label}] Reader init FAILED: {e}")
        return {
            "label": label,
            "init_s": None,
            "total_s": None,
            "per_frame_s": None,
            "errors": 0,
            "frame_count": len(frames),
            "error": str(e),
        }
    t_init_end = time.perf_counter()
    init_s = t_init_end - t_init_start
    print(f"  [{label}] Reader ready in {init_s:.1f}s. Running {len(frames)} frames...", flush=True)

    frame_times = []
    errors = 0
    t_total_start = time.perf_counter()

    for i, frame_path in enumerate(frames):
        t0 = time.perf_counter()
        try:
            reader.readtext(str(frame_path), detail=1, paragraph=False)
        except Exception:
            errors += 1
        t1 = time.perf_counter()
        frame_times.append(t1 - t0)

        if (i + 1) % 10 == 0 or (i + 1) == len(frames):
            elapsed = sum(frame_times)
            avg = elapsed / len(frame_times)
            eta = avg * (len(frames) - (i + 1))
            print(
                f"  [{label}] {i+1}/{len(frames)} frames | "
                f"avg {avg:.2f}s/frame | ETA {eta:.0f}s",
                flush=True,
            )

    t_total_end = time.perf_counter()
    total_s = t_total_end - t_total_start
    per_frame_s = total_s / len(frames)

    return {
        "label": label,
        "init_s": init_s,
        "total_s": total_s,
        "per_frame_s": per_frame_s,
        "errors": errors,
        "frame_count": len(frames),
        "error": None,
    }


def run_paddleocr_benchmark(frames: list) -> dict:
    """
    Run PaddleOCR on CPU and return timing dict.
    """
    from paddleocr import PaddleOCR

    label = "PaddleOCR (device=cpu)"
    print(f"\n  [{label}] Initializing PaddleOCR reader (lang=hi)...", flush=True)

    t_init_start = time.perf_counter()
    try:
        reader = PaddleOCR(lang="hi", device="cpu", use_textline_orientation=True)
    except Exception as e:
        print(f"  [{label}] Reader init FAILED: {e}")
        return {
            "label": label,
            "init_s": None,
            "total_s": None,
            "per_frame_s": None,
            "errors": 0,
            "frame_count": len(frames),
            "error": str(e),
        }
    t_init_end = time.perf_counter()
    init_s = t_init_end - t_init_start
    print(f"  [{label}] Reader ready in {init_s:.1f}s. Running {len(frames)} frames...", flush=True)

    frame_times = []
    errors = 0
    t_total_start = time.perf_counter()

    for i, frame_path in enumerate(frames):
        t0 = time.perf_counter()
        try:
            reader.predict(str(frame_path))
        except Exception:
            errors += 1
        t1 = time.perf_counter()
        frame_times.append(t1 - t0)

        if (i + 1) % 10 == 0 or (i + 1) == len(frames):
            elapsed = sum(frame_times)
            avg = elapsed / len(frame_times)
            eta = avg * (len(frames) - (i + 1))
            print(
                f"  [{label}] {i+1}/{len(frames)} frames | "
                f"avg {avg:.2f}s/frame | ETA {eta:.0f}s",
                flush=True,
            )

    t_total_end = time.perf_counter()
    total_s = t_total_end - t_total_start
    per_frame_s = total_s / len(frames)

    if errors == len(frames):
        return {
            "label": label,
            "init_s": init_s,
            "total_s": total_s,
            "per_frame_s": per_frame_s,
            "errors": errors,
            "frame_count": len(frames),
            "error": "All PaddleOCR frame inferences failed",
        }

    return {
        "label": label,
        "init_s": init_s,
        "total_s": total_s,
        "per_frame_s": per_frame_s,
        "errors": errors,
        "frame_count": len(frames),
        "error": None,
    }


def print_results(results: list, n: int) -> dict:
    """Print a formatted comparison table and return comparison metrics."""
    print("\n" + "=" * 60)
    print(f"  RESULTS - {n} frames | EasyOCR CPU/GPU {LANGUAGES} vs PaddleOCR CPU (hi)")
    print("=" * 60)

    def fmt(r):
        if r["error"]:
            return f"  FAILED: {r['error']}"
        return (
            f"  Init time     : {r['init_s']:.1f}s\n"
            f"  Total OCR time: {r['total_s']:.1f}s\n"
            f"  Per frame     : {r['per_frame_s']:.3f}s\n"
            f"  Errors        : {r['errors']}"
        )

    for r in results:
        print(f"\n[{r['label']}]")
        print(fmt(r))

    successful = [r for r in results if not r["error"]]
    if len(successful) < 2:
        print("\n  Cannot compare — fewer than two engines succeeded.")
        return None

    fastest = min(successful, key=lambda r: r["per_frame_s"])
    slowest = max(successful, key=lambda r: r["per_frame_s"])

    print("\n  Pairwise per-frame ratios (slower / fastest):")
    for r in successful:
        ratio = r["per_frame_s"] / fastest["per_frame_s"]
        print(f"    {r['label']:<28}: {r['per_frame_s']:.3f}s/frame ({ratio:.2f}x)")

    diff_pct = (slowest["per_frame_s"] - fastest["per_frame_s"]) / slowest["per_frame_s"]
    print(f"\n  Fastest engine       : {fastest['label']} ({fastest['per_frame_s']:.3f}s/frame)")
    print(f"  Slowest engine       : {slowest['label']} ({slowest['per_frame_s']:.3f}s/frame)")
    print(f"  Spread (slow vs fast): {diff_pct * 100:.1f}%")
    print("=" * 60)

    return {
        "fastest": fastest,
        "slowest": slowest,
        "diff_pct": diff_pct,
    }


def print_verdict(comparison: dict):
    """Print final architecture recommendation based on benchmark results."""
    print("\n  VERDICT:")
    print("  " + "-" * 56)
    if not comparison:
        print("  No comparison available — check errors above.")
        print()
        return

    fastest = comparison["fastest"]
    slowest = comparison["slowest"]
    speedup = slowest["per_frame_s"] / fastest["per_frame_s"]

    print(
        f"  Fastest: {fastest['label']} at {fastest['per_frame_s']:.3f}s/frame "
        f"({speedup:.2f}x faster than {slowest['label']})."
    )
    print("  Recommendation: prefer the fastest engine for this Hindi+English workload,")
    print("  unless OCR accuracy or operational simplicity dictates otherwise.")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="EasyOCR CPU+GPU vs PaddleOCR CPU benchmark for Hindi OCR"
    )
    parser.add_argument(
        "--frames",
        type=int,
        default=None,
        help="Fixed number of frames (skips auto-escalation)",
    )
    args = parser.parse_args()

    print("\nBackend preflight checks:")
    easy_gpu_ok, easy_gpu_msg = check_easyocr_gpu_support()
    paddle_cpu_ok, paddle_cpu_msg = check_paddle_cpu_support()
    print(f"  EasyOCR GPU : {easy_gpu_msg}")
    print(f"  EasyOCR CPU : always available (PyTorch CPU)")
    print(f"  Paddle CPU  : {paddle_cpu_msg}")

    if not paddle_cpu_ok:
        print("\nERROR: Paddle is not importable — cannot run PaddleOCR CPU benchmark.")
        sys.exit(2)
    if not easy_gpu_ok:
        print("\nWARNING: EasyOCR GPU backend not available — GPU run will be skipped.")

    sizes = [args.frames] if args.frames else ESCALATE_SIZES
    last_comparison = None

    for n in sizes:
        print(f"\n{'='*60}")
        print(f"  BENCHMARK — {n} frames")
        print(f"{'='*60}")

        frames = sample_frames(n)
        print(f"  Sampled {len(frames)} frames from {FRAMES_DIR} (recursive)")

        results = []

        # 1) EasyOCR CPU
        results.append(run_easyocr_benchmark(frames, use_gpu=False))

        # 2) EasyOCR GPU (only if backend present)
        if easy_gpu_ok:
            results.append(run_easyocr_benchmark(frames, use_gpu=True))
        else:
            print("\n  [EasyOCR GPU] Skipped — no GPU backend available.")

        # 3) PaddleOCR CPU
        results.append(run_paddleocr_benchmark(frames))

        comparison = print_results(results, n)
        last_comparison = comparison

        if args.frames:
            break  # fixed size, no auto-escalation

        if comparison is None:
            print("\n  Stopping — cannot compare due to error.")
            break

        if comparison["diff_pct"] > DIFF_THRESHOLD:
            print(
                f"\n  Spread ({comparison['diff_pct']*100:.1f}%) "
                f"> {DIFF_THRESHOLD*100:.0f}% threshold — clear result at n={n}. Stopping."
            )
            break
        else:
            idx = sizes.index(n)
            if idx + 1 < len(sizes):
                next_n = sizes[idx + 1]
                print(
                    f"\n  Spread ({comparison['diff_pct']*100:.1f}%) "
                    f"<= {DIFF_THRESHOLD*100:.0f}% threshold — escalating to {next_n} frames..."
                )
            else:
                print(f"\n  Reached max sample size ({n}). Reporting final result.")

    print_verdict(last_comparison)


if __name__ == "__main__":
    main()
