PicoFly (RP2040 Picofly) is an open-source hardware glitching modchip for Nintendo Switch consoles equipped with the Mariko Tegra X1+ SoC (including V2, Lite, OLED, and later models). It uses a low-cost Raspberry Pi RP2040 microcontroller — typically mounted on a Pico or Zero board — to achieve two critical functions: direct read/write access to the console’s internal eMMC NAND storage and precise nanosecond-scale CPU voltage/power glitching. Together, these capabilities allow the modchip to bypass Nintendo’s hardware-enforced secure boot chain and reliably load unsigned code (such as Hekate + Atmosphere custom firmware) on every boot.
The project is fully open-source. The main firmware (written in C, with heavy use of the RP2040’s Programmable I/O or PIO state machines for high-speed eMMC protocol emulation and timing) lives primarily in the repository at https://github.com/rehius/usk, with documentation and mirrors at https://github.com/Ansem-SoD/Picofly.

What is the BCT?
The BCT (Boot Configuration Table) is a critical signed data structure used in NVIDIA Tegra devices, including the Nintendo Switch. It resides in the boot partitions (BOOT0/BOOT1) of the internal eMMC NAND and contains essential configuration parameters that the early boot code needs to initialize the system.Key elements stored in the BCT include:
SDRAM/memory controller initialization timings and parameters.
Clock, power rail, and pinmux settings.
Location, size, and load addresses for the next boot stages (e.g., the bootloader).
Cryptographic metadata: RSA signatures and references to public key hashes that must match hardware fuses burned into the SoC.

During a normal boot, the immutable BootROM reads a BCT copy from eMMC, validates its structure and RSA signature against fused public key hashes, applies the configuration data, and only then proceeds to load the next signed stage. Multiple redundant BCT copies exist for fault tolerance (normal, backup, safe-mode, etc.). Because the BCT is one of the first signed objects processed after reset, it is a prime target for fault-injection attacks: glitching during signature verification or BCT parsing can cause the BootROM to skip checks and execute unsigned or modified code.

Tegra BootROM – The Root of Trust
The Tegra BootROM is the immutable first-stage boot code hard-coded (mask ROM) into the silicon of NVIDIA Tegra SoCs. On the Switch’s Tegra X1 (Erista) or X1+ (Mariko), it executes on the dedicated BPMP (Boot and Power Management Processor), a small ARM core that handles early boot and power management while the main application processors remain in reset.Core responsibilities of the BootROM:
* Minimal hardware initialization (clocks, power rails, boot media controller for eMMC).
* Reading and validating a BCT from storage.
* Applying BCT settings and loading the next bootloader stage (e.g., nvtboot/Package1 on Switch).
* Enforcing the chain of trust via RSA signature checks and hardware fuse keys (including the Secure Boot Key – SBK).

The BootROM is designed to be minimal to limit attack surface. On Erista units, it contained the famous Fusée Gelée vulnerability (a buffer overflow in USB Recovery Mode / RCM), which allowed unauthenticated code execution before any signature checks. This was unpatchable in software because the code is mask ROM. On Mariko units, that specific flaw was fixed in silicon, shifting the primary bypass method to hardware glitching.In normal operation, any failure in signature verification typically leads to Recovery Mode (RCM) or a brick-like state, depending on fuse settings.
How PicoFly Works – Full Technical Flow
PicoFly connects to the Switch motherboard via two main interfaces:
* eMMC interface (typically 4–5 wires: RST, CMD, CLK, DAT0, plus power/ground). This lets the RP2040 act as an eMMC host controller and directly read from or write to the NAND.
* CPU glitch interface (MOSFET or APU flex cable soldered to specific glitch points, often SP1/SP2 or equivalent). This enables precise shorting of signals or power rails to induce a controlled fault in the CPU/BPMP.

Optional I2C-based CPU downvolting can widen the glitch timing window for better reliability.The exploit is purely hardware-based and occurs before signed Nintendo code can fully enforce security.
First Boot (Initial Payload Injection / Training Phase)
1) Power on the Switch with PicoFly installed.
2) The RP2040 boots its firmware and immediately connects to the eMMC.
3) eMMC write / BCT copy phase (visible as long white LED flashes):
* Initialize the eMMC.
* Write a small custom unsigned payload (stage-1 exploit stub, often with BCT patches or a pointer to the real bootloader) into the BCT area or adjacent reserved space in the boot partition.
* Read back and verify the written data (comparison check).
* This is the source of error codes like “BCT copy failed – write failure / comparison failure / read failure” or general eMMC write failures.
4) Glitch phase (blue LED):
* Using precise timers and PIO, the RP2040 monitors boot signals and timing.
* At the exact window when the BootROM is reading, parsing, or verifying the BCT signature, it triggers the glitch (MOSFET short).
* The fault disrupts control flow or signature checking in the BootROM/BPMP.
* As a result, the BootROM loads and executes the unsigned payload injected into/near the BCT instead of rejecting it.
* Success typically leads to the “No SD card” screen or direct boot into Hekate from the SD card.

This write happens only once (or when the payload is missing/corrupt). The long white LED is the clear indicator.

Second Boot and Onward (Persistent Operation)
Once the payload resides safely in the eMMC:
* On every subsequent boot, the RP2040 performs only a quick eMMC check (fast read for magic bytes or BCT markers) and skips the full write phase.
* It proceeds directly to the glitch phase.
* The same precise glitch forces the BootROM to load the pre-injected payload.
* Hekate (or another bootloader) then loads from the SD card and boots Atmosphere CFW.

Newer firmware versions (2.7x+) include adaptive glitch timing that collects boot statistics and refines the window automatically for higher reliability. The mod works independently of the installed official firmware version, as long as glitch parameters are appropriate.
LED Behavior and Error Codes (post-2.70 firmware)
* White (long pulses): eMMC flashing / BCT payload writing in progress (first boot or recovery).
* Blue: Glitching in progress.
* Yellow + pulse patterns: Errors (e.g., eMMC connection/init/write/read failures on specific lines, BCT copy issues, glitch timeout, CPU never/always reaching BCT check, etc.).
* Success usually ends with the LED turning off.

Why This Design Succeeds
Writing the payload to persistent NAND storage makes the exploit reliable across reboots. Glitching remains necessary on every boot because the hardware-enforced signature checks (starting in BootROM with the BCT) are still active; the glitch is the bypass that forces execution of the injected code. The combination of direct eMMC access and PIO-based nanosecond timing gives the RP2040 complete low-level control.
For the deepest technical view, examine the open-source firmware: files such as main.c, boot_detect.c, the emmc.pio state machine, BCT handling routines, and glitch timing constants.
Practical Notes:
* This is a permanent hardware modification. Careful soldering is essential — poor connections (especially DAT0/CMD/CLK on OLED models) are a common cause of bricks or instability.
* Immediately perform a full NAND backup via Hekate after the first successful boot.
* RP2040 firmware updates are mainly for improved glitch reliability on newer official firmware or bug fixes.

This cohesive explanation integrates the mechanics of PicoFly, the role of the BCT, and the inner workings of the Tegra BootROM into a single technical narrative.