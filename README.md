# 长赢计划数据追踪

自动从且慢抓取长赢指数投资计划数据，每日更新，生成可嵌入网站的展示组件。

## 功能

- 每日自动抓取长赢计划交易信号（买入/卖出）
- 自动拉取持仓基金的历史净值数据（Tushare）
- 点击持仓份数可查看净值走势图 + 买卖点标注
- 纯静态页面，通过 GitHub Pages 托管

## 首次配置

### 1. 设置 GitHub Secrets

进入仓库 → Settings → Secrets and variables → Actions → 添加：

| Name | Value |
|------|-------|
| `QIEMAN_TOKEN` | 且慢 Authorization token（浏览器 F12 Network 复制） |
| `TUSHARE_TOKEN` | Tushare API token |

### 2. 开启 GitHub Pages

Settings → Pages → Source: `Deploy from a branch` → Branch: `main`, 目录: `/docs`

### 3. 手动触发第一次数据拉取

Actions → Update LongWin Data → Run workflow

### 4. 嵌入网站

```html
<iframe
  src="https://<用户名>.github.io/longwin-tracker/"
  width="100%"
  height="800px"
  style="border:none;">
</iframe>
```

## Token 更新

- **QIEMAN_TOKEN**：有效期约 30 天，到期后从浏览器重新获取并更新 Secret
- **TUSHARE_TOKEN**：长期有效
