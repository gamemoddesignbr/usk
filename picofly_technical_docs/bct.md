BCT (Boot Configuration Table) — signed binary struct central to NVIDIA Tegra secure boot, incl. Nintendo Switch. Stored in BOOT0/BOOT1 eMMC partitions. Provides BootROM params to init hardware (esp. memory) + locate/load next boot stage. Multiple redundant copies: normal, backup, safe-mode + backups.

BCT = first signed object BootROM processes after reset. RSA sig (2048-bit RSA-PSS) verified against public key modulus in BCT itself — must match hash fused in SoC. Fail → boot aborts or enters recovery. Prime target for glitching attacks (PicoFly/HWFLY): precise fault during sig verification/parsing → BootROM accepts unsigned/modified BCT/payload.

## General BCT Layout (Tegra Family)

* Header: version, size, crypto metadata, validation data
* Bad Block Table (BBT): tracks defective storage blocks
* SDRAM/Memory Config: up to 4 param sets — timings, clock settings, memory controller init (critical before full DRAM)
* Boot Device/Storage Config: eMMC params, partition info
* Bootloader Info: location, size, load addr, entry point, attributes for next stage (e.g. Package1 on Switch)
* Customer Data Area: reserved space (used for custom payloads in mods)
* Signatures + Keys: RSA public key modulus + signature over BCT content
* Reserved/Padding: alignment + future use

Supports redundancy + multiple boot configs. On Mariko, may involve extra encryption (Boot Encryption Key), but core sig = RSA. BCT size: 0x4000 bytes (16 KB) per copy on Switch. Older Tegra: 8 KB. NVIDIA tools (`tegrabct_v2`, `bct_dump`) parse/gen BCTs from `.dts`/`.dtb` or config files. Format evolved: older Tegra flat binary; T23x+ uses Device Tree.

## Switch BCT Structure (Erista / T210)

Tegra X1-specific variant. Layout from community RE (Erista primary; Mariko similar, diff key handling + potential encryption):

Offset | Size | Field | Description
-------|------|-------|------------
0x000 | 0x210 | BadBlockTable | Bad block info. • 0x000: EntriesUsed (uint32) • 0x004: VirtualBlockSizeLog2 (uint8) • 0x005: BlockSizeLog2 (uint8) • 0x006: BadBlocks array • 0x206: Reserved
0x210 | 0x100 | Key (RSA Public Key Modulus) | 2048-bit (256-byte) RSA modulus to validate BCT sig. Must hash-match fused value. K1–K6 (Erista), M1+ (Mariko) — identifiable by first byte.
0x310 | 0x110 | Signature | • 0x310: CryptoHash (often empty/SHA-related) • 0x320: RsaPssSig (RSA-PSS, 256 bytes)
0x420 | 0x4 | SecProvisioningKeyNumInsecure | Factory secure provisioning (usually 0 on retail)
0x424 | 0x20 | SecProvisioningKey | Provisioning key (usually zeroed on retail)

Remaining bytes (~0x444+):
* Multiple SDRAM param sets (up to 4): EMC timings, clock freqs, init sequences
* Boot device config: eMMC specifics, partition locations
* Bootloader descriptors: version, load addr, size, entry point, attributes
* Keyblob area/refs (used by Package1ldr to derive keys for decrypting Package1)
* Customer data / reserved (exploitable for payload injection in glitching)
* Version fields, ODM data, JTAG control, etc.

Exact offsets vary by BCT version/SoC rev. BOOT0 partition layout:
* 0x000000: Normal Firmware BCT
* 0x004000: SafeMode Firmware BCT
* 0x008000 + 0x00C000: Backups (each BCT = 0x4000 bytes)

Active BCT bootloader version field → influences keyblob selection.

## Mariko (Tegra X1+) Differences

Same high-level structure +
* Hardened key handling (Fusée Gelée fixed in silicon)
* Potential encryption of bootloader sections with console-specific/shared BEK
* Different public key versions (M1+, first byte 0x19 for prototypes)
* Stricter sig enforcement

PicoFly on Mariko: injected payload uses pre-crafted static BCT (or patches existing one). Mod writes into BCT area during first boot → glitches to bypass verification → BootROM accepts unsigned/custom content.

## Boot Process + Glitching

Normal flow:
1. BootROM reads BCT copy from eMMC boot partition
2. Validates structure, checks RSA sig via embedded pubkey (matched to fused hash)
3. Applies SDRAM params → inits memory
4. Loads bootloader at given addr, verifies, jumps

PicoFly flow:
* First boot: RP2040 writes custom payload (stage-1 stub + BCT mods/replacement) into/near BCT region
* Every boot: glitches precisely during BCT sig check/parsing → BootROM skips rejection, executes injected code
* Mechanism: glitch disrupts control flow or RSA computation at critical window

## Practical Notes

* BCT pubkey region + keyblobs in BOOT0 often write-protected by modern CFW (e.g. Atmosphere) — prevents corruption during updates
* Manual BCT mod = risky — sig mismatch bricks boot; glitching bypasses by faulting check
* Tools: `bct_dump` (Tegra communities), Hekate — inspect/dump BCTs; full BootROM disassembly + BCT parsing in open Switch repos
* Patched Mariko: no software exploits → hardware fault injection + eMMC payload persistence = standard method

BCT intentionally compact yet comprehensive — minimizes BootROM complexity while enforcing chain of trust. Memory config + bootloader descriptors enable reliable early boot; crypto protection = glitching target.

For exact byte-level offsets (SDRAM fields, bootloader descriptors, Mariko variants) or help interpreting dumped BCT: provide hex dump snippet or firmware version. Best public refs: switchbrew.org BCT wiki page, NVIDIA historical BCT overview docs.
