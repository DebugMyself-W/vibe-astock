const A_STOCK_CODE = /^\d{6}$/;

export function isAStockCode(code?: string | null): code is string {
  return typeof code === "string" && A_STOCK_CODE.test(code.trim());
}

export function thsStockUrl(code: string): string {
  return `https://stockpage.10jqka.com.cn/${code.trim()}`;
}
