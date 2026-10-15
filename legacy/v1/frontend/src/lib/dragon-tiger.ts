import type { DragonTiger, DtSeat } from "@/lib/api";

function formatSeat(seat: DtSeat, index: number): string {
  const name = seat.name?.trim() || "未知席位";
  const net = `${seat.net > 0 ? "+" : ""}${seat.net.toFixed(1)}`;
  return `${index + 1}. ${name}｜买入 ${seat.buy_amt.toFixed(1)} 万元｜卖出 ${seat.sell_amt.toFixed(1)} 万元｜净额 ${net} 万元`;
}

export function formatDragonTigerContext(data: DragonTiger, analysisDate: string): string {
  const records = data.records.length
    ? data.records.map((record) =>
      `- ${record.date}｜${record.reason || "上榜原因未知"}｜净买入 ${record.net_buy > 0 ? "+" : ""}${record.net_buy.toFixed(1)} 万元｜换手率 ${record.turnover.toFixed(2)}%`).join("\n")
    : "- 截止查询日，回看窗口内无上榜记录";
  const buy = data.seats.buy.length
    ? data.seats.buy.map(formatSeat).join("\n")
    : "- 买方席位数据为空（不得推测）";
  const sell = data.seats.sell.length
    ? data.seats.sell.map(formatSeat).join("\n")
    : "- 卖方席位数据为空（不得推测）";
  const institution = data.institution;
  return [
    `龙虎榜查询截止日：${analysisDate}`,
    "### 上榜记录", records,
    "### 买方席位 TOP5", buy,
    "### 卖方席位 TOP5", sell,
    "### 机构专用席位汇总",
    `买入 ${institution.buy_amt.toFixed(1)} 万元｜卖出 ${institution.sell_amt.toFixed(1)} 万元｜净额 ${institution.net_amt > 0 ? "+" : ""}${institution.net_amt.toFixed(1)} 万元`,
    "口径：席位明细对应最近一次上榜日；买方榜与卖方榜可能出现同一席位，分析时不得去重或只引用一侧。",
  ].join("\n");
}
