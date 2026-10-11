#!/usr/bin/env python3
# Copyright (c) 2026 UltraSemi. SPDX-License-Identifier: Apache-2.0
"""Package a signed Zephyr sysbuild for ArtInChip AirBurn."""
import argparse
from pathlib import Path
import sys
import subprocess


def build_image(build_dir: Path, board: str | None = None,
                zephyr_base: Path | None = None) -> Path:
    if not (build_dir / "domains.yaml").is_file():
        raise ValueError("Packaging requires a sysbuild root with domains.yaml; "
                         "build the board with --sysbuild")
    from mk_image_mcuboot import build_sysbuild_image
    return build_sysbuild_image(build_dir, board, zephyr_base)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Package signed Zephyr firmware from a sysbuild root"
    )
    parser.add_argument(
        "--build-dir",
        required=True,
        type=Path,
        help="Sysbuild directory containing domains.yaml",
    )
    parser.add_argument(
        "--board",
        help="Optional board assertion; must match the build (default: CMakeCache.txt)",
    )
    parser.add_argument("--zephyr-base", type=Path, help="Zephyr source tree (default: build cache)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        build_image(args.build_dir.resolve(), args.board, args.zephyr_base)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
