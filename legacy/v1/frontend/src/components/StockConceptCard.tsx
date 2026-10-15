import { Boxes } from "lucide-react";
import { GlassCard } from "@/components/ui/GlassCard";
import type { Blocks, HotConcept } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Props {
  blocks: Blocks | null;
  hotConcepts: HotConcept[];
  className?: string;
  selectedConcept?: string | null;
  onConceptClick?: (concept: string) => void;
}

/** 个股研究页与复盘行情弹窗共用；只展示后端既有板块和热门概念结果。 */
export function StockConceptCard({
  blocks,
  hotConcepts,
  className,
  selectedConcept = null,
  onConceptClick,
}: Props) {
  const tags = blocks?.concept_tags ?? [];
  return (
    <GlassCard className={cn("mb-4", className)}>
      <h3 className="mb-3 flex items-center gap-1.5 text-sm font-semibold">
        <Boxes className="h-4 w-4 text-primary" /> 板块归属 · 概念
      </h3>
      {tags.length > 0 ? (
        <div className="mb-3 flex flex-wrap gap-1.5">
          {tags.slice(0, 24).map((tag, index) => onConceptClick ? (
            <button
              type="button"
              key={`${tag}-${index}`}
              aria-pressed={selectedConcept === tag}
              onClick={() => onConceptClick(tag)}
              className={cn(
                "rounded-full border border-border/70 px-2 py-0.5 text-xs text-muted-foreground transition-colors hover:border-primary hover:text-primary",
                selectedConcept === tag && "border-primary bg-primary/10 text-primary",
              )}
            >
              {tag}
            </button>
          ) : (
            <span key={`${tag}-${index}`} className="rounded-full border border-border/70 px-2 py-0.5 text-xs text-muted-foreground">{tag}</span>
          ))}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">概念归属暂不可用</p>
      )}
      {hotConcepts.length > 0 && (
        <div>
          <p className="mb-1.5 text-xs text-muted-foreground">当下热门概念命中</p>
          <div className="flex flex-wrap gap-1.5">
            {hotConcepts.slice(0, 12).map((item, index) => (
              <span key={`${item.bk}-${item.concept}-${index}`} className="rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary">{item.concept}</span>
            ))}
          </div>
        </div>
      )}
    </GlassCard>
  );
}
