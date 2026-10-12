D213EC-V4 / demo128-nand boot resources

Hardware: QFN128, 128 MiB DDR3, XTX XT26G01CW SPI NAND, LVDS.
XT26G01C vendor table: manufacturer 0x0b, device 0x11 (not yet read by Zephyr).
Geometry: 128 MiB, 2048-byte pages, 128-byte OOB, 64 pages/block, 1024 blocks.
Erase block: 128 KiB. ECC status: bits 7:4; 0xf means uncorrectable.
Reference: bsp/peripheral/spinand/xtx.c at the source commit recorded below.
Do not reuse the generic 64-byte OOB NAND template for this device.
MCUboot uses a read-only map that skips bad blocks independently in each partition.
Software checks pass; NAND boot on D213 hardware remains to be verified.
Exact source commit and file hashes are recorded in provenance.json.
bootloader.bin is reserved for the RAM-resident USB updater, not the target boot chain.
The target chain is PBP -> MCUboot -> signed Zephyr.
The common D21x PBP is in ../common/d21x.pbp.
The DDR3 entry in ddr_init.json specifies 128 MiB at 600 MHz.
AirBurn profile: artinchip_d21x_demo128_nand (XT26G01C geometry only).
See docs/d213-nand-bringup.rst for the test build and its limitations.
USB receive buffer: 0x41000000.
Vendor updater load address: 0x47b80000; entry: 0x47b80100.
These are distinct from the Zephyr-built MCUboot entry at 0x41000100.
The updater formula, source paths and binary pointer evidence are in provenance.json.
bootloader.bin is rebuilt with CONFIG_SPI_NAND_XTX=y using Xuantie V2.6.1.
updater_defconfig records the reproducible configuration; the original prebuilt
updater lacked XTX support and rejected hardware ID 0b110011.
