from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.-]*$")


@dataclass(frozen=True)
class WatchlistResult:
    symbols: list[str]
    errors: list[str]


def normalize_symbol(raw_symbol: str) -> str:
    symbol = raw_symbol.strip().upper()
    if not symbol:
        raise ValueError("证券代码为空")
    if not SYMBOL_PATTERN.fullmatch(symbol):
        raise ValueError(f"证券代码包含非法字符：{raw_symbol!r}")
    if "." not in symbol:
        symbol = f"{symbol}.US"
    return symbol


def load_watchlist(path: Path) -> WatchlistResult:
    if not path.is_file():
        raise FileNotFoundError(f"股票池文件不存在：{path}")

    symbols: list[str] = []
    errors: list[str] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line.strip():
            continue
        try:
            symbol = normalize_symbol(raw_line)
        except ValueError as exc:
            errors.append(f"第 {line_number} 行：{exc}")
            continue
        if symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    return WatchlistResult(symbols=symbols, errors=errors)


def save_watchlist(path: Path, raw_symbols: list[str]) -> WatchlistResult:
    symbols: list[str] = []
    errors: list[str] = []
    seen: set[str] = set()
    for line_number, raw_symbol in enumerate(raw_symbols, 1):
        if not raw_symbol.strip():
            continue
        try:
            symbol = normalize_symbol(raw_symbol)
        except ValueError as exc:
            errors.append(f"第 {line_number} 行：{exc}")
            continue
        if symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    if errors:
        return WatchlistResult(symbols=symbols, errors=errors)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep US symbols pleasant to edit; normalization restores .US at runtime.
    display_symbols = [symbol[:-3] if symbol.endswith(".US") else symbol for symbol in symbols]
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("\n".join(display_symbols) + ("\n" if display_symbols else ""), encoding="utf-8")
    temporary.replace(path)
    return WatchlistResult(symbols=symbols, errors=[])
