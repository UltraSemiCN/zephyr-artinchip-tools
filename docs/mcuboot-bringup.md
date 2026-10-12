# MCUboot boot and validation notes

Use the build and packaging commands in [README](../README.md).
Image names use `<board>_v<version>.img`. The installed boot chain is
ROM → PBP → MCUboot → signed Zephyr (RAM-load).

## Memory layouts

| Profile | MCUboot entry | Signed application load | Framebuffer |
| --- | --- | --- | --- |
| UltraSemi 720x1280 | `0x40000100` | `0x40200000` | Below application RAM |
| D125 demo88 Hello World | `0x40000100` | `0x40080000` | Disabled |
| D125 demo88 1024x600 | `0x40000100` | `0x40130000` | `0x40000000..0x4012bfff` |
| D133 demo88 1024x600 LVDS | `0x40000100` | `0x40130000` | `0x40000000..0x4012bfff` |
| D133 demo88 Hello World | `0x40000100` | `0x30080000` | Disabled |

The signed image includes a 512-byte header before application code. Use the
ELF and both domains' devicetree metadata for exact load, entry and region bounds.
The D125 landscape application overrides the baseline RAM layout in both domains.
It reuses the former MCUboot RAM as framebuffer only after handoff; application
RAM remains separate. Do not reuse the Hello World layout for this display.

PBP initializes PSRAM and parent PLLs. MCUboot initializes UART/QSPI, verifies
the signed application and loads it into RAM. The vendor USB updater runs only
during flashing; its `bootloader.bin` resource remains required by AirBurn.
Factory packaging clears the secondary slot and is not an OTA workflow.

## Validation scope

On 2026-10-12, the user confirmed D125 demo88 Hello World + shell and UltraSemi
dashboard images passed on hardware. The user also confirmed the current
`dashboard_720x1280_d125_mipi` and D125 `dashboard_1024x600_d125_mipi` builds passed hardware tests.
D125 images passed compilation and offline checks. D133 reached a responsive
shell using a temporary UART diagnostic; the normal LVDS image subsequently
passed the display test on 2026-10-12.
See [exact hashes and results](../../zephyr-artinchip-demo-apps/VALIDATION.md).

UART0 uses PA0/PA1, 115200 8N1, without hardware flow control. External NOR uses
PB0-PB5. Demo application console policy selects `aic:~$ `; board boot support
does not force shell or logging settings.

Record the image SHA-256, board/SoC, PSRAM size, panel connection and serial log
for each hardware test. Check cold boot, MCUboot handoff, shell input, continuing
animation and display clipping. Offline signature rejection tests do not verify
on-device update behavior. NOR write/erase, runtime rejection, updates and
power-loss recovery require separate tests. Development signing keys are not
production keys.

## PBP private resource format

D12P uses `private2` resources with a PDAT header. D13x uses legacy `private`
resources containing raw configuration sections. The packer selects the format
from the generated image configuration, matching the vendor build procedure.
Wrapping D13x data with PDAT can prevent PSRAM initialization and USB stage
reconnection. The verifier checks the resource format and the embedded USB
PSRAM-init component in addition to checksums and signatures.

The D133 dashboard initially failed after its 29712-byte PSRAM-init component.
After correcting the format, the component is 29456 bytes and matches vendor
output. The later diagnostic image successfully reached the Zephyr shell.

## D133 PBP handoff investigation

The initial SRAM MCUboot image produced PBP output through `Psram_init done.`
without a MCUboot banner. Its loader range started at `0x30040000` and overlapped
the `0x30043000` workspace referenced by the vendor PBP binary. The current
D133 profile places MCUboot at `0x40000100` in PBP-initialized PSRAM, following
the vendor practice of loading the next stage outside this SRAM workspace.
The UART-restoring diagnostic subsequently reached MCUboot and the Zephyr shell.
The normal clock driver now restores D13x UART gates and reads PLL_INT1; the
diagnostic wrapper is removed. The dashboard reuses lower PSRAM for its
framebuffer after MCUboot hands off. The LVDS image passed 19 offline checks
and passed the hardware display test. Repeated cold boot and an interactive
`help` check remain to be recorded for this image.
