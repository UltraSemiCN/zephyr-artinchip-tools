# zephyr-artinchip-tools

UltraSemi-maintained tools for packaging signed Zephyr firmware into the
ArtInChip AirBurn format. The supported boot chain is:

```text
ROM → PBP → MCUboot → signed Zephyr (RAM-load)
```

D125 and D133 profiles use external SPI NOR on PB0-PB5; D213 uses SPI NAND:

| Board / application | MCUboot entry | Application RAM | Hardware status |
| --- | --- | --- | --- |
| UltraSemi 5.5-inch dashboard | `0x40000100` | `0x40200000` | Passed, 2026-10-12 |
| D125 demo88 Hello World + shell | `0x40000100` | `0x40080000`, 1536 KiB | Passed, 2026-10-12 |
| D125 demo88 1024x600 dashboard | `0x40000100` | `0x40130000`, 832 KiB | Passed, 2026-10-12 |
| D133 demo88 1024x600 LVDS dashboard | `0x40000100` | `0x40130000`, 832 KiB | Passed, 2026-10-12 |
| D213 demo128 1024x600 LVDS dashboard | `0x41000100` | `0x40200000`, 8 MiB | Passed, 2026-10-12 |
| D133 demo88 Hello World | `0x40000100` | `0x30080000`, 512 KiB | Build and offline checks passed; hardware pending |

The four dashboard applications and their board build targets are documented in
[demo applications](../zephyr-artinchip-demo-apps/README.md). Exact tested hashes
and validation scope are recorded in
[validation results](../zephyr-artinchip-demo-apps/VALIDATION.md).
D125 and D133 demo88 profiles have two 1 MiB signed-image slots. D133 uses the
vendor PBP-prefix/loader format; its ROM wrapper differs from D125.
Resource availability and successful compilation do not establish hardware support.

## Build and package

Use the Zephyr Python environment with MCUboot imgtool dependencies installed.
Run from the west workspace containing `zephyr-artinchip`,
`zephyr-artinchip-demo-apps` and this repository:

```bash
west build --sysbuild -p always \
  -b ultrasemi_d12p_usbdisp_demo88_nor_5_5/d125ccv \
  zephyr-artinchip-demo-apps/applications/dashboard_720x1280_d125_mipi \
  -d build/dashboard_720x1280_d125_mipi -- \
  -DZEPHYR_EXTRA_MODULES="$PWD/zephyr-artinchip-tools"
```

The application selects MCUboot, signing and RAM-load through standard sysbuild;
the board supplies the required memory layout and handoff support. This module adds the
`artinchip-airburn` packaging target. Omitting `ZEPHYR_EXTRA_MODULES` still
builds MCUboot and the signed application. Package those results separately:

```bash
python zephyr-artinchip-tools/scripts/mk_image_zephyr.py \
  --build-dir build/dashboard_720x1280_d125_mipi
```

Always pass the **sysbuild root**, not an individual application or MCUboot
domain.

Output:

```text
build/dashboard_720x1280_d125_mipi/airburn/<board>_v<version>.img
build/dashboard_720x1280_d125_mipi/airburn/image-path.txt
build/dashboard_720x1280_d125_mipi/airburn/build-info.json
```

Board and version follow the build metadata. Application `VERSION` takes
precedence over the Zephyr version in the filename. The signed MCUboot version
is determined by standard application `VERSION` / Kconfig settings; a filename
version fallback does not override the signed header. The current dashboard
applications have no `VERSION` file, so their signed version is `0.0.0+0`.
The optional `--board` argument asserts
identity and rejects mismatches. `--zephyr-base` overrides the cached source
path used to load devicetree metadata. Vendor platform/product IDs remain
unchanged for compatibility with ArtInChip flashing tools.

Use the normal AirBurn GUI or the bundled `scripts/upgcmd` to flash the image.
Building and packaging do not write to hardware. For a non-development key,
set `SB_CONFIG_BOOT_SIGNATURE_KEY_FILE` to your signing key and rebuild.

## What remains in the image

| Component | Role |
| --- | --- |
| PBP and private resources | PSRAM initialization and boot parameters |
| `mcuboot.aic` | Persistent ROM boot target in the `spl` partition |
| `zephyr.signed.bin` | Signed application in MCUboot slot0 |
| Empty secondary slot | Clears stale higher-version slot1 images during factory flashing |
| Vendor `bootloader.bin` / `bootloader.aic` | RAM-resident USB updater used by AirBurn only |
| Environment and optional filesystems | Retained for the existing partition/flashing format |

**Do not delete `bootloader.bin`:** it no longer boots the installed application,
but AirBurn still downloads it into RAM to program the flash. The vendor
protocol names `image.updater.spl` and `image.target.spl` remain; the latter
contains MCUboot. The persistent image has no FIT/ITB `os` target.

The verified flash offsets and partition names are retained. The old `os`
region is reserved and is not programmed with a boot payload. Layout comes
from both domains' `edt.pickle`; the packer rejects inconsistent layouts,
overlaps, out-of-range components, mismatched ELF/BIN, incorrect load addresses,
wrong signing keys and stale signatures. Optional `data.lfs` and `rodata.fatfs`
require matching DTS partitions. PBP remains required for PSRAM training.

## Repository layout

- `scripts/mk_image_zephyr.py`: public CLI, accepts signed MCUboot sysbuilds.
- `scripts/mk_image_mcuboot.py`: validates builds/signatures and packages images.
- `scripts/airburn.py`: shared AirBurn container and staging helpers.
- `scripts/zephyr_image_metadata.py`: build identity, ELF and partition metadata.
- `scripts/mk_image.py`, resource generators and vendor utilities: low-level
  ArtInChip container implementation. Generic vendor format handlers are kept
  intact; their presence does not enable legacy Zephyr boot chains.
- `scripts/pack/`: retained vendor resources for D12P, D12x, D13x and D21x.
- `sysbuild/`: optional automatic packaging integration.
- `tests/`: unit tests and independent generated-image verification.

## Other chip resources

D12x, D21x, D126 and D13x assets remain available for future adaptation. They
are **resource bundles, not additional supported boards**.
`scripts/pack/catalog.json` records imported resource provenance and hashes. The D125
resources are retained from the validated Demo88 bundle; the 5.5-inch board
uses those assets. D13x resources remain under `scripts/pack/d13x/`, including demo88 and Kunlun Pi resources.

```bash
python scripts/pack_resources.py
python scripts/pack_resources.py --profile d21x/demo88-mmc \
  --output-dir build/d21x-demo88-mmc-pack
```

Run the resource commands above from this tools repository.
Resource staging does not create a flashable image. Original vendor templates
may contain legacy boot components and require adaptation before use. D12x MMC
and D126 bundles still lack their vendor `bootloader.bin`; this is not new
support or a claim of hardware validation.

## Verification

Run from this tools repository:

```bash
python -m unittest discover -s tests -p 'test_*.py'
python tests/verify_mcuboot_build.py ../build/dashboard_720x1280_d125_mipi
```

The artifact verifier checks signatures, corrupted/wrong-key rejection, ROM
header/load/entry, component CRCs, exact payloads, and separation of the USB
updater from the persistent boot target. It writes `airburn/validation.json`.
Those are offline checks; record hardware feedback separately for the exact
image hash. See [boot details](docs/mcuboot-bringup.md).

## License

Module-maintained code uses Apache-2.0. Preserve the original notices in
bundled vendor and third-party files; resource provenance does not replace
review of their redistribution terms before public publication.

D213EC-V4 / demo128 NAND (XT26G01CW) passed NAND boot, Shell and LVDS color-bar
tests. Its cached MCUboot Dashboard also passed board testing on 2026-10-12;
see the demo-apps validation record. See [D213 NAND bring-up](docs/d213-nand-bringup.rst).
