# UltraSemi D125 firmware

Build and package using the standard sysbuild commands in [README](../README.md).
Public image names use `<board>_v<version>.img`; bootloader implementation
is not part of the board or project name.

## Boot layout

- ROM loads the AIC-wrapped MCUboot image from the `spl` partition.
- The 256-byte AIC header precedes MCUboot, linked at `0x40000100`.
- PBP initializes PSRAM and parent PLLs.
- MCUboot initializes UART/QSPI, verifies ECDSA-P256 signatures and loads
  the application at `0x40200000`.
- The application entry follows its 512-byte signed header at `0x40200200`.
- Both domains use matching fixed partitions and disjoint RAM regions.

The vendor USB updater runs in RAM during flashing. Its `bootloader.bin`
resource remains required by AirBurn; it is not the persistent bootloader.
Factory packaging clears the secondary slot and is not an OTA workflow.

## Hardware validation

The user confirmed shell startup on D125 after flashing the image with SHA-256
`57db20535a02477a9ccabfce61ecfbc796aaec11618315913baf7bdbdd622573`.
It includes native UART/QSPI, the E907 initial-thread interrupt-level fix and
removal of temporary startup diagnostics. The board/version filename was
confirmed with this hardware test.
All 17 offline artifact checks passed. Later builds require their own validation.

UART0 uses PA0/PA1, 115200 8N1; external NOR uses PB0–PB5.
The shell prompt is `aic:~$ `. No display was connected during the latest test.
Panel operation, flash updates and power-loss recovery remain unverified.
Development signing keys and flash-geometry fallbacks require production review.
