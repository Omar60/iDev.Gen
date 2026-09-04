"""The contact sheet's two layouts.

A probe writes one label per arm and several seeds per label, and the sheet lays
that out one arm per row. A CELL session writes ten frames that all carry the
same label (`composed-1` .. `composed-10`), so every frame is its own arm and the
sheet came out ten rows of one column — unreadable, and the reason the grid was
hand-rolled twice before `--cols` existed.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("contact_sheet", ROOT / "scripts" / "contact_sheet.py")
contact_sheet = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contact_sheet)


def _folder(tmp_path: Path, names: list[str]) -> Path:
    folder = tmp_path / "frames"
    folder.mkdir()
    for name in names:
        Image.new("RGB", (40, 60), "red").save(folder / name)
    return folder


def _run(monkeypatch, tmp_path: Path, argv: list[str]) -> Image.Image:
    monkeypatch.setattr(contact_sheet, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["contact_sheet.py", *argv])
    assert contact_sheet.main() == 0
    return Image.open(tmp_path / "frames" / "sheet-composed.png")


def test_a_cell_session_flows_into_a_grid(monkeypatch, tmp_path):
    """Ten same-label frames at --cols 5 are two rows of five, not ten of one."""
    _folder(tmp_path, [f"0948{i}_composed-{i + 1}.png" for i in range(10)])
    sheet = _run(monkeypatch, tmp_path, ["composed", "--dir", "frames", "--cell", "20", "--cols", "5"])
    step_w, step_h = 20 + contact_sheet.GUTTER, 30 + contact_sheet.GUTTER
    assert sheet.size == (5 * step_w, 2 * step_h)


def test_without_cols_every_frame_is_still_its_own_arm(monkeypatch, tmp_path):
    """The default is unchanged: one row per label, which is what a probe wants."""
    _folder(tmp_path, [f"0948{i}_composed-{i + 1}.png" for i in range(10)])
    sheet = _run(monkeypatch, tmp_path, ["composed", "--dir", "frames", "--cell", "20"])
    step_w, step_h = 20 + contact_sheet.GUTTER, 30 + contact_sheet.GUTTER
    assert sheet.size == (1 * step_w, 10 * step_h)


def test_row_eleven_sorts_after_row_two(monkeypatch, tmp_path):
    """The row key is zero-padded. Unpadded, '10' sorted before '2' and the last
    two frames landed in the THIRD row with no error to show for it.

    Each frame gets its own grey, so the sheet says where the frame went: the
    bottom row must hold the last two frames, not frames 4 and 5.
    """
    folder = tmp_path / "frames"
    folder.mkdir()
    for i in range(22):
        Image.new("RGB", (40, 60), (10 * i, 10 * i, 10 * i)).save(folder / f"{i:05d}_composed-{i + 1}.png")

    sheet = _run(monkeypatch, tmp_path, ["composed", "--dir", "frames", "--cell", "20", "--cols", "2"])
    step_w, step_h = 20 + contact_sheet.GUTTER, 30 + contact_sheet.GUTTER
    assert sheet.size == (2 * step_w, 11 * step_h)
    # Bottom row, left tile, below the yellow label: frame 21 (index 20).
    assert sheet.getpixel((4, 10 * step_h + 25)) == (200, 200, 200)


def test_a_sheet_is_not_a_frame(monkeypatch, tmp_path):
    """A second run must not lay the first run's sheet out as one more
    photograph: `sheet-composed.png` matches the prefix `composed`."""
    _folder(tmp_path, [f"0948{i}_composed-{i + 1}.png" for i in range(4)] + ["sheet-composed.png"])
    sheet = _run(monkeypatch, tmp_path, ["composed", "--dir", "frames", "--cell", "20", "--cols", "2"])
    step_w, step_h = 20 + contact_sheet.GUTTER, 30 + contact_sheet.GUTTER
    assert sheet.size == (2 * step_w, 2 * step_h)
