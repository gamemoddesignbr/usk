PicoFly (RP2040 Picofly) — open-source hardware glitching modchip for Nintendo Switch Mariko (Tegra X1+): V2, Lite, OLED, later models. Uses low-cost Raspberry Pi RP2040 (Pico/Zero board) for two functions: direct read/write to internal eMMC NAND, and nanosecond-scale CPU voltage/power glitching. Together bypass Nintendo's hardware-enforced secure boot chain and load unsigned code (Hekate + Atmosphere CFW) on every boot.

Fully open-source. Firmware (C, heavy RP2040 PIO use for high-speed eMMC emulation and timing) at https://github.com/rehius/usk, docs/mirrors at https://github.com/Ansem-SoD/Picofly.

What is BCT?
BCT (Boot Configuration Table) — critical signed struct in Tegra devices including Nintendo Switch. Resides in boot partitions (BOOT0/BOOT1) of internal eMMC NAND. Stores early boot config:
- SDRAM/memory controller init timings and params
- Clock, power rail, pinmux settings
- Location, size, load addresses for next boot stages (e.g., bootloader)
- Crypto metadata: RSA signatures + public key hashes matching SoC-fused values

Normal boot: BootROM reads BCT from eMMC, validates structure/RSA sig against fused key hashes, applies config, loads next signed stage. Multiple redundant BCT copies exist (normal, backup, safe-mode, etc.). BCT is prime fault-injection target — glitching during sig verification or BCT parsing can skip checks and execute unsigned code.

Tegra BootROM – Root of Trust
Immutable first-stage boot code, mask ROM in Tegra SoC silicon. On Switch Tegra X1 (Erista) or X1+ (Mariko), runs on BPMP (Boot and Power Management Processor) — small ARM core handling early boot while main processors stay in reset. BootROM responsibilities:
* Minimal HW init (clocks, power rails, eMMC controller)
* Read and validate BCT from storage
* Apply BCT settings, load next bootloader stage (nvtboot/Package1 on Switch)
* Enforce chain of trust via RSA sig checks and fuse keys (including SBK)

BootROM minimal to limit attack surface. Erista had Fusée Gelée (USB RCM buffer overflow) — unauthenticated code exec before sig checks, unpatchable (mask ROM). Mariko fixed that flaw in silicon, shifting bypass to hardware glitching. Sig verification failure normally → RCM or brick-like state depending on fuse settings.

How PicoFly Works – Full Technical Flow
PicoFly connects to Switch motherboard via two interfaces:
* eMMC interface (4–5 wires: RST, CMD, CLK, DAT0, power/ground). RP2040 acts as eMMC host, reads/writes NAND directly.
* CPU glitch interface (MOSFET or APU flex cable at glitch points SP1/SP2 or equivalent). Precise signal/power rail shorting to induce controlled fault in CPU/BPMP.

Optional I2C CPU downvolting widens glitch timing window for better reliability. Exploit is purely hardware-based, occurs before signed Nintendo code enforces security.

First Boot (Initial Payload Injection / Training Phase)
1) Power on Switch with PicoFly.
2) RP2040 boots firmware, immediately connects to eMMC.
3) eMMC write / BCT copy phase (long white LED flashes):
* Init eMMC.
* Write small unsigned payload (stage-1 exploit stub, BCT patches or pointer to real bootloader) into BCT area or adjacent reserved space in boot partition.
* Read back and verify written data.
* Source of errors: "BCT copy failed – write failure / comparison failure / read failure" or general eMMC write failures.
4) Glitch phase (blue LED):
* PIO monitors boot signals and timing.
* At exact window when BootROM reads/parses/verifies BCT sig, triggers glitch (MOSFET short).
* Fault disrupts control flow or sig checking in BootROM/BPMP.
* BootROM loads and executes unsigned payload instead of rejecting it.
* Success → "No SD card" screen or direct Hekate boot from SD.

Write happens only once (or when payload missing/corrupt). Long white LED is clear indicator.

Second Boot and Onward (Persistent Operation)
Once payload resides in eMMC:
* Each subsequent boot: RP2040 does quick eMMC check (fast read for magic bytes or BCT markers), skips full write phase.
* Goes directly to glitch phase.
* Same glitch forces BootROM to load pre-injected payload.
* Hekate (or other bootloader) loads from SD, boots Atmosphere CFW.

Firmware 2.7x+ includes adaptive glitch timing: collects boot stats, refines window automatically for higher reliability. Works independently of installed official firmware version, if glitch params appropriate.

LED Behavior and Error Codes (post-2.70 firmware)
* White (long pulses): eMMC flashing / BCT payload write in progress (first boot or recovery)
* Blue: Glitching in progress
* Yellow + pulse patterns: Errors (eMMC connection/init/write/read failures, BCT copy issues, glitch timeout, CPU never/always reaching BCT check, etc.)
* Success: LED turns off

Why This Design Succeeds
Payload in persistent NAND = reliable across reboots. Glitch still needed every boot because hardware-enforced sig checks (BootROM + BCT) remain active; glitch is bypass forcing injected code execution. Direct eMMC access + PIO nanosecond timing = complete low-level control.

For deepest technical view, see firmware: `main.c`, `boot_detect.c`, `emmc.pio`, BCT handling routines, glitch timing constants.

Practical Notes:
* Permanent hardware mod. Poor soldering (DAT0/CMD/CLK on OLED) = common cause of bricks/instability.
* Do full NAND backup via Hekate after first successful boot.
* RP2040 firmware updates mainly for improved glitch reliability on newer official firmware or bug fixes.
