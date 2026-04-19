The Boot Configuration Table (BCT) is a signed binary data structure central to the NVIDIA Tegra secure boot process, including on the Nintendo Switch. Stored in the boot partitions (BOOT0/BOOT1) of the internal eMMC NAND, the BCT provides the BootROM with all necessary parameters to initialize hardware (especially memory) and locate/load the next boot stage (bootloader). Multiple redundant copies exist for reliability: typically normal, backup, safe-mode, and their backups.
The BCT is one of the first signed objects the immutable BootROM processes after reset. Its RSA signature (usually 2048-bit RSA-PSS) is verified against a public key modulus stored in the BCT itself, which must match a hash fused into the SoC hardware. If verification fails, boot aborts or enters recovery. This makes the BCT a prime target for glitching attacks (as used by PicoFly/HWFLY): a precise fault during signature verification or parsing can cause the BootROM to accept an unsigned or modified BCT/payload.

General BCT Layout and Purpose (Tegra Family)
A typical Tegra BCT contains:
* Header with version, size, cryptographic metadata, and validation data.
* Bad Block Table (BBT) — tracks defective blocks in storage.
* SDRAM / Memory Configuration — up to four "parameter sets" with timings, clock settings, and initialization data for the memory controller (critical for early boot before full DRAM use).
* Boot Device / Storage Configuration — details on the boot medium (eMMC parameters, partition info).
* Bootloader Information — location, size, load address, entry point, and attributes for the next stage (e.g., Package1 on Switch).
* Customer Data Area — reserved space for arbitrary data (sometimes used for custom payloads in mods).
* Signatures and Keys — RSA public key modulus + signature over the BCT content.
* Reserved / Padding — for alignment and future use.

The structure supports redundancy and multiple boot configurations. On modern Tegra (including Mariko), parts may involve additional encryption (e.g., with a Boot Encryption Key in some contexts), though the core signature remains RSA-based. BCT size is commonly 0x4000 bytes (16 KB) per copy on the Switch, though older Tegra variants used smaller sizes like 8 KB.
NVIDIA tools (e.g., tegrabct_v2, bct_dump) parse/generate BCTs from device tree sources (.dts/.dtb) or config files. The format evolved: older Tegra used a flat binary layout; newer ones (T23x+) leverage Device Tree for flexibility.

Detailed BCT Structure on Nintendo Switch (Erista / T210 Focus)
The Switch uses a Tegra X1-specific variant. Here is the documented layout (primarily from community reverse-engineering on Erista; Mariko is similar but with differences in key handling and potential encryption):
Offset		Size		Field							Description
0x000		0x210		BadBlockTable					Information on bad blocks in storage.
														• 0x000: EntriesUsed (uint32)
														• 0x004: VirtualBlockSizeLog2 (uint8)
														• 0x005: BlockSizeLog2 (uint8)
														• 0x006: BadBlocks array
														• 0x206: Reserved
0x210		0x100		Key (RSA Public Key Modulus)	2048-bit (256-byte) RSA public key modulus used to validate the BCT signature. The modulus in the BCT must hash-match the value fused in hardware. Different "K" versions (K1–K6 for Erista, M1+ for Mariko) use distinct public keys identifiable by the first byte.
0x310		0x110		Signature						Cryptographic signature of the BCT.
														• 0x310: CryptoHash (often empty or SHA-related)
														• 0x320: RsaPssSig (RSA-PSS signature, typically 256 bytes)
0x420		0x4			SecProvisioningKeyNumInsecure	Factory secure provisioning (usually 0 on retail units)
0x424		0x20		SecProvisioningKey				Related provisioning key (usually zeroed on retail)

The remaining bytes (after ~0x444) contain the core configuration sections:
* Multiple SDRAM parameter sets (up to 4) with EMC (External Memory Controller) timings, clock frequencies, and initialization sequences.
* Boot device configuration (eMMC specifics, partition locations).
* Bootloader descriptors: version, load address, size, entry point, and attributes.
* Keyblob area or references (used later by Package1ldr to derive keys for decrypting Package1).
* Customer data / reserved regions (exploitable for payload injection in glitching mods).
* Additional version fields, ODM data, JTAG control, etc.

The exact offsets for SDRAM/bootloader sections vary slightly by BCT version and SoC revision, but they follow the general Tegra pattern documented in NVIDIA's BCT overview materials.
On the Switch eMMC (BOOT0 partition), BCT copies are laid out at fixed offsets, e.g.:
* 0x000000: Normal Firmware BCT
* 0x004000: SafeMode Firmware BCT
* Backups at 0x008000 and 0x00C000 (each BCT is 0x4000 bytes).

The active BCT's bootloader version field can influence keyblob selection.

Mariko (Tegra X1+) Differences
Mariko BCTs share the same high-level structure but include:
* Hardened key handling (Fusée Gelée fixed in silicon).
* Potential encryption of certain bootloader sections with a console-specific or shared Boot Encryption Key (BEK).
* Different public key versions (starting with M1, first byte 0x19 for prototypes, etc.).
* Stricter signature enforcement.

In glitching modchips like PicoFly, the injected payload often uses a pre-crafted static BCT (or patches an existing one) tailored for Mariko. The mod writes this into the BCT area during first boot, then glitches to bypass verification so the BootROM accepts the unsigned/custom content.

Role in Boot Process and Glitching
1) BootROM reads a BCT copy from eMMC boot partition.
2) Validates structure, checks RSA signature using the embedded public key (matched to fused hash).
3) Applies SDRAM params → initializes memory.
4) Loads the specified bootloader at the given address, verifies it, and jumps.

In PicoFly:
* During first boot, the RP2040 writes a custom payload (stage-1 stub + BCT modifications or replacement) into/near the BCT region.
* On every boot, it glitches precisely during BCT signature check/parsing → BootROM skips rejection and executes the injected code.
* This works because the glitch disrupts control flow or RSA computation at the critical window.

Practical Notes for Switch Modding
* BCT public key region and keyblobs in BOOT0 are often write-protected in modern CFW (e.g., Atmosphere) to prevent accidental corruption during updates.
* Modifying BCT manually is risky — signature mismatches brick boot. Glitching bypasses this by faulting the check.
* Tools like bct_dump (from Tegra communities) or Hekate can inspect/dump BCTs. Full BootROM disassembly and BCT parsing code exist in open Switch hacking repos.
* On patched Mariko units, software exploits are unavailable, so hardware fault injection + eMMC payload persistence is the standard method.

The BCT is intentionally compact yet comprehensive to minimize BootROM complexity while enforcing the chain of trust. Its detailed fields (especially memory config and bootloader descriptors) are what allow reliable early boot, and its cryptographic protection is what glitching attacks target.
If you need the exact byte-level offsets for a specific SDRAM field, bootloader descriptor, or Mariko-specific variations, or want help interpreting a dumped BCT file from your console, provide more details (e.g., a hex dump snippet or firmware version) and I can refine this further. For the absolute lowest level, the switchbrew.org wiki page on BCT and NVIDIA's historical BCT overview documents are the best public references.