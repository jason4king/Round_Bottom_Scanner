from pathlib import Path

from app.watchlist import load_watchlist, normalize_symbol, save_watchlist


def test_normalize_symbol_adds_us_suffix() -> None:
    assert normalize_symbol(" aapl ") == "AAPL.US"
    assert normalize_symbol("700.hk") == "700.HK"


def test_watchlist_ignores_blanks_and_preserves_unique_order(tmp_path: Path) -> None:
    path = tmp_path / "watchlist.txt"
    path.write_text("AAPL\n\nMSFT.US\nAAPL\n", encoding="utf-8")
    result = load_watchlist(path)
    assert result.symbols == ["AAPL.US", "MSFT.US"]
    assert result.errors == []


def test_save_watchlist_omits_us_suffix_but_load_restores_it(tmp_path: Path) -> None:
    path = tmp_path / "watchlist.txt"
    result = save_watchlist(path, ["aapl", "MSFT.US", "700.HK", "AAPL"])
    assert result.symbols == ["AAPL.US", "MSFT.US", "700.HK"]
    assert path.read_text(encoding="utf-8") == "AAPL\nMSFT\n700.HK\n"
    assert load_watchlist(path).symbols == result.symbols
