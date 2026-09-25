import type { ReactNode } from "react";
import { thsStockUrl, isAStockCode } from "@/lib/stock-links";
import { cn } from "@/lib/utils";

interface StockLinkProps {
  code?: string | null;
  children: ReactNode;
  className?: string;
  title?: string;
}

export function StockLink({ code, children, className, title }: StockLinkProps) {
  if (!isAStockCode(code)) return <span className={className}>{children}</span>;

  return (
    <a
      href={thsStockUrl(code)}
      target="_blank"
      rel="noopener noreferrer"
      title={title || `打开同花顺 ${code}`}
      className={cn("underline-offset-2 hover:text-primary hover:underline", className)}
    >
      {children}
    </a>
  );
}
