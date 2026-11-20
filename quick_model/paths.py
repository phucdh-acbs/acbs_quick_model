"""Thư mục cấu hình: `quick_model/config` khi đóng gói phát hành, ngược lại `config/` ở gốc repo."""

from __future__ import annotations

from pathlib import Path

_PKG = Path(__file__).resolve().parent
CONFIG_DIR = _PKG / "config" if (_PKG / "config").is_dir() else _PKG.parent / "config"
