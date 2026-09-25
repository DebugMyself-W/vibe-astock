import { Info } from "lucide-react";

// 中立免责条 —— 产品定调：只客观呈现公开数据/榜单，不推荐、不预测、无倾向；方向由用户自己的 AI 给出。
export function Disclaimer({ compact = false }: { compact?: boolean }) {
  if (compact) {
    return (
      <p className="text-[11px] leading-relaxed text-muted-foreground/70">
        Vibe-Astock 提供公开数据与确定性策略分级；不自动下单、不承诺成交或收益、不构成投资建议。
      </p>
    );
  }
  return (
    <div className="mt-8 flex items-start gap-2 rounded-lg border border-border/60 bg-muted/20 p-3 text-xs leading-relaxed text-muted-foreground">
      <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span>
        Vibe-Astock 是信息整理、AI 接入与规则筛选工具。榜单（连板股 / 成交额等）均为<b className="text-foreground">客观公开数据</b>；部分页面会以预先公开的确定性规则对个股排序、分级和后续校准，但<b className="text-foreground">不自动下单、不发送交易指令、不承诺成交或收益，亦不构成投资建议</b>。生成式 AI 深度分析不参与策略排名。请自行核实并独立决策，风险自担。
      </span>
    </div>
  );
}
