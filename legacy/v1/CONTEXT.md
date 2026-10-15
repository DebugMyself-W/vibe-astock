# Vibe-AStock Context

This context names the product concepts that keep A-share data collection reliable without coupling it to the user's AI connection.

## Language

**Market Data Network Policy**:
The persisted choice that routes every HTTP market-data request through automatic fallback, direct access, or a user-configured Clash proxy.
_Avoid_: Global proxy, AI proxy

**Market Data Route**:
The direct or proxy path actually used for one market-data HTTP request.
_Avoid_: System route

**AI Connection**:
The separately configured Codex, Claude, WorkBuddy, or model-API connection used for analysis and conversation.
_Avoid_: Market proxy

**Source Fallback**:
An independent data provider used when a primary provider cannot supply trustworthy data.
_Avoid_: Proxy fallback

## Relationships

- One **Market Data Network Policy** selects one or two candidate **Market Data Routes** for each HTTP request.
- A **Source Fallback** may use the same **Market Data Network Policy**, but remains independent of route fallback.
- An **AI Connection** is never modified by the **Market Data Network Policy**.

## Example dialogue

> **Dev:** "东财直连失败后，是换数据源还是切 Clash？"
> **Domain expert:** "先按 **Market Data Network Policy** 切换 **Market Data Route**；只有端点仍不可用或数据不完整时，才启用 **Source Fallback**。不要改 **AI Connection**。"

## Flagged ambiguities

- “代理设置”曾同时指市场数据出口和 AI 出口；现明确为两个独立概念，页面中的市场数据设置只管理 **Market Data Network Policy**。
