export function displaySymbol(symbol: string | null | undefined): string {
  return symbol?.replace(/\.US$/i, "") ?? "";
}
