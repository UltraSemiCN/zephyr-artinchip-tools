#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Inspect, verify or stage vendor resource bundles (does not build firmware)."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

PACK = Path(__file__).resolve().parent / "pack"


def verify(profile):
    for name, asset in profile["files"].items():
        source = PACK / asset["path"]
        if not source.is_file():
            raise ValueError(f"missing catalog asset: {source}")
        if hashlib.sha256(source.read_bytes()).hexdigest() != asset["sha256"]:
            raise ValueError(f"checksum mismatch: {source}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", help="resource profile, e.g. d21x/demo88-mmc")
    parser.add_argument("--output-dir", type=Path, help="stage resources into an empty directory")
    args = parser.parse_args()
    profiles = json.loads((PACK / "catalog.json").read_text())["profiles"]
    if args.output_dir and not args.profile:
        parser.error("--output-dir requires --profile")
    if args.profile and args.profile not in profiles:
        parser.error(f"unknown profile: {args.profile}")
    selected = {args.profile: profiles[args.profile]} if args.profile else profiles
    try:
        for name, profile in selected.items():
            verify(profile)
            missing = ", ".join(profile["missing_files"])
            print(f"{name}: {profile['media']}; " + (f"missing {missing}" if missing else "resources verified"))
            if args.output_dir:
                if missing:
                    raise ValueError(f"cannot stage {name}: missing {missing}")
                if args.output_dir.exists() and (not args.output_dir.is_dir() or any(args.output_dir.iterdir())):
                    raise ValueError("output directory must be empty")
                args.output_dir.mkdir(parents=True, exist_ok=True)
                for filename, asset in profile["files"].items():
                    shutil.copy2(PACK / asset["path"], args.output_dir / filename)
                print(f"Staged to {args.output_dir}; no firmware image has been generated.")
                if "image_cfg.json" in profile["files"]:
                    print("image_cfg.json is an unresolved vendor template.")
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print("Resource staging does not compile firmware or generate a flashable image.")


if __name__ == "__main__":
    main()
