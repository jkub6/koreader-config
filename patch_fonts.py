#!/usr/bin/env python3
"""
Patch fonts for KOReader so that Greek and Hebrew characters (including
polytonic accents, breathing marks, vowel points / cantillation, and
math/technical Greek symbols) are stripped from primary reading fonts,
forcing KOReader/HarfBuzz to fall back cleanly to SBL BibLit (SBL_BLit.ttf).
"""

import argparse
import glob
from pathlib import Path
import sys
from fontTools.subset import Options, Subsetter
from fontTools.ttLib import TTFont

# Core Greek & Hebrew Unicode blocks + isolated lookalikes in Latin/Letterlike blocks
GREEK_HEBREW_RANGES = [
    (0x00B5, 0x00B5, "Micro Sign (µ)"),
    (0x0370, 0x03FF, "Greek and Coptic"),
    (0x0590, 0x05FF, "Hebrew (letters, niqqud, cantillation)"),
    (0x1D26, 0x1D2A, "Phonetic Greek small capitals"),
    (0x1D5D, 0x1D6A, "Phonetic Greek modifier letters & subscripts"),
    (0x1DBF, 0x1DBF, "Modifier letter small theta"),
    (0x1F00, 0x1FFF, "Greek Extended (Polytonic)"),
    (0x2126, 0x2126, "Ohm Sign (Ω)"),
    (0x2C80, 0x2CFF, "Coptic"),
    (0xAB65, 0xAB65, "Greek letter small capital omega"),
    (0xFB1D, 0xFB4F, "Hebrew Presentation Forms"),
    (0x10140, 0x1018F, "Ancient Greek Numbers"),
    (0x101A0, 0x101A0, "Greek Symbol Tau Rho"),
    (0x1D200, 0x1D24F, "Ancient Greek Musical Notation"),
    (0x1D6A8, 0x1D7CB, "Mathematical Alphanumeric Greek Symbols"),
]

# Greek-specific combining marks inside the general Combining Diacritical Marks block (U+0300..U+036F)
GREEK_SPECIFIC_COMBINING_RANGES = [
    (0x0340, 0x0345, "Greek tonos, perispomeni, koronis, dialytika tonos, ypogegrammeni"),
    (0x0360, 0x0361, "Combining double tilde / inverted breve"),
]

# Full Combining Diacritical Marks blocks (optional, if text uses un-normalized NFD Greek)
ALL_COMBINING_RANGES = [
    (0x0300, 0x036F, "Combining Diacritical Marks"),
    (0x1AB0, 0x1AFF, "Combining Diacritical Marks Extended"),
    (0x1DC0, 0x1DFF, "Combining Diacritical Marks Supplement"),
]


def is_in_ranges(cp: int, ranges: list[tuple[int, int, str]]) -> bool:
    return any(start <= cp <= end for start, end, _ in ranges)


def resolve_inputs(patterns: list[str]) -> list[Path]:
    paths: list[Path] = []
    seen: set[Path] = set()
    for pat in patterns:
        p = Path(pat)
        if p.is_dir():
            matched = sorted(
                f for f in p.iterdir()
                if f.is_file() and f.suffix.lower() in {".ttf", ".otf"}
            )
        elif any(ch in pat for ch in "*?[]"):
            matched = [Path(m) for m in sorted(glob.glob(pat, recursive=True)) if Path(m).is_file()]
        elif p.exists():
            matched = [p]
        else:
            print(f"Warning: '{pat}' did not match any files.", file=sys.stderr)
            continue

        for m in matched:
            resolved = m.resolve()
            if resolved not in seen:
                seen.add(resolved)
                paths.append(m)
    return paths


def patch_font(
    font_path: Path,
    out_path: Path,
    target_ranges: list[tuple[int, int, str]],
    dry_run: bool = False,
) -> tuple[int, int]:
    font = TTFont(font_path)
    cmap = font.getBestCmap() or {}

    removed_cps = sorted(cp for cp in cmap if is_in_ranges(cp, target_ranges))
    keep_cps = [cp for cp in cmap if not is_in_ranges(cp, target_ranges)]

    if dry_run:
        return len(cmap), len(removed_cps)

    if removed_cps:
        options = Options()
        # Preserve all OpenType layout features, naming, kerning, and hinting for remaining glyphs
        options.layout_features = ["*"]
        options.name_IDs = ["*"]
        options.name_legacy = True
        options.name_languages = ["*"]
        options.notdef_glyph = True
        options.notdef_outline = True
        options.recommended_glyphs = True
        options.hinting = True
        options.legacy_kern = True
        options.symbol_cmap = True
        options.recalc_bounds = True
        options.recalc_timestamp = False

        # Drop FontForge timestamp table if present (fontTools subsetter doesn't subset it and warns)
        if "FFTM" in font:
            del font["FFTM"]

        subsetter = Subsetter(options=options)
        subsetter.populate(unicodes=keep_cps)
        subsetter.subset(font)

        if "OS/2" in font and hasattr(font["OS/2"], "recalcUnicodeRanges"):
            font["OS/2"].recalcUnicodeRanges(font)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    font.save(out_path)
    font.close()
    return len(cmap), len(removed_cps)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Remove Greek and Hebrew characters from fonts so KOReader falls back "
            "cleanly to SBL BibLit (or another fallback font)."
        )
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        help="Font file(s), directory, or glob pattern(s) (e.g., 'fonts/*.ttf').",
    )
    parser.add_argument(
        "-i",
        "--in-place",
        action="store_true",
        help="Overwrite the input font files in-place (default if neither -o nor --suffix is given).",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        help="Write patched fonts to this directory instead of modifying in-place.",
    )
    parser.add_argument(
        "-s",
        "--suffix",
        type=str,
        default="",
        help="Filename suffix before extension (e.g. '-patched' -> 'Font-patched.ttf').",
    )
    parser.add_argument(
        "--strip-combining",
        action="store_true",
        help=(
            "Also strip the entire Combining Diacritical Marks blocks (U+0300..U+036F, etc.). "
            "Use this if your texts contain decomposed (NFD) Greek accents and you don't need "
            "decomposed Latin diacritics from the base font."
        ),
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Inspect and report how many characters would be stripped without modifying files.",
    )

    args = parser.parse_args()

    files = resolve_inputs(args.inputs)
    if not files:
        print("Error: No font files found to process.", file=sys.stderr)
        return 1

    target_ranges = list(GREEK_HEBREW_RANGES) + list(GREEK_SPECIFIC_COMBINING_RANGES)
    if args.strip_combining:
        target_ranges.extend(ALL_COMBINING_RANGES)

    for fpath in files:
        if "sbl" in fpath.name.lower() or "blit" in fpath.name.lower():
            print(f"Skipping fallback font '{fpath}' to avoid stripping SBL BibLit itself.")
            continue

        out_dir = args.output_dir if args.output_dir else fpath.parent
        out_name = f"{fpath.stem}{args.suffix}{fpath.suffix}"
        out_path = out_dir / out_name

        try:
            total, removed = patch_font(
                fpath,
                out_path,
                target_ranges=target_ranges,
                dry_run=args.dry_run,
            )
            action = "Would strip" if args.dry_run else "Stripped"
            dest_info = "" if (args.dry_run or out_path == fpath) else f" -> {out_path}"
            print(f"[{fpath.name}] {action} {removed}/{total} codepoints{dest_info}")
        except Exception as e:
            print(f"[{fpath.name}] Error patching font: {e}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
