"""Generate the NafaPilot synthetic dataset.

Run from the backend folder with the venv active:

    python ../scripts/generate_dataset.py                 # seed 42 -> data/synthetic/
    python ../scripts/generate_dataset.py --seed 7 --out ../data/other

The same seed always produces byte-identical CSV files.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.synthetic import generate, write_dataset  # noqa: E402
from app.synthetic.specs import DEFAULT_SEED  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the synthetic dataset.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "synthetic")
    args = parser.parse_args()

    manifest = write_dataset(generate(args.seed), args.out)
    print(f"Synthetic dataset written to {args.out.resolve()} (seed {args.seed})")
    for name, info in manifest["files"].items():
        print(f"  {name:<24} {info['rows']:>7} rows")
    print("All records are synthetic. Not real merchants, customers or Paytm data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
