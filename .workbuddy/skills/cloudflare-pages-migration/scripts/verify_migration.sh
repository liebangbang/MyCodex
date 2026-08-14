#!/usr/bin/env bash
# verify_migration.sh <domain> [download-path]
# 检查 Cloudflare Pages 迁移是否完成：
#   1) NS 是否切到 Cloudflare
#   2) HTTPS 首页是否可访问
#   3) 下载链接是否返回 200
# 用法示例：
#   bash verify_migration.sh mycodex.me
#   bash verify_migration.sh mycodex.me download/MyCodex-macOS.zip
set -u

DOMAIN="${1:?用法: bash verify_migration.sh <domain> [download-path]}"
DL="${2:-download/MyCodex-macOS.zip}"

echo "========== 迁移验证: $DOMAIN =========="
echo ""

echo "=== 1. NS 是否切到 Cloudflare ==="
NS=$(dig NS "$DOMAIN" +short 2>/dev/null)
echo "当前 NS 记录:"
if [ -z "$NS" ]; then
  echo "(无 NS 记录返回)"
else
  echo "$NS"
fi
if echo "$NS" | grep -qi 'cloudflare'; then
  echo "[OK] NS 已切到 Cloudflare (dig 缓存已刷新)"
else
  echo "[待办] NS 尚未切到 Cloudflare，可能仍指向原注册商。"
  echo "       WHOIS 层可能已变（dig NS 缓存几分钟到十几分钟才刷新），可用 whois 复核。"
fi

echo ""
echo "=== 2. HTTPS 首页 ==="
curl -sI "https://$DOMAIN/" | head -3

echo ""
echo "=== 3. 下载链接 ($DL) ==="
curl -sI "https://$DOMAIN/$DL" | head -3
echo ""
echo "========================================"
