#!/usr/bin/env bash
#
# 编译 UnfairPlay 到 backend/vendor/unfairplay/unfairplay。
#
# 为什么要在项目里放一份:它是一条**不需要越狱设备**的离线解密路径
# (材料来自一台登录了 Mac App Store 的 Mac),而 backend/app/unfairplay.py
# 默认就去 vendor/ 下找。放 vendor 里比塞进 PATH 更容易看出「这个依赖是项目自带的」。
#
# 用法:
#   bash tools/build_unfairplay.sh                    # 克隆上游 + 编译
#   bash tools/build_unfairplay.sh /path/to/unfairplay-src   # 用本地源码编译
#   UNFAIRPLAY_REF=<sha> bash tools/build_unfairplay.sh      # 固定到某个提交
#
set -euo pipefail

REPO="${UNFAIRPLAY_REPO:-https://github.com/DrWhax/unfairplay}"
REF="${UNFAIRPLAY_REF:-main}"

BACKEND="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$BACKEND/vendor/unfairplay"
CACHE="${UNFAIRPLAY_CACHE:-$BACKEND/vendor/.unfairplay-src}"

command -v go >/dev/null || {
    echo "需要 Go 工具链: https://go.dev/dl/  (brew install go)" >&2
    exit 1
}
echo "go: $(go version)"

if [ "$#" -ge 1 ]; then
    SRC="$1"
    echo "用本地源码: $SRC"
else
    SRC="$CACHE"
    if [ ! -d "$SRC/.git" ]; then
        echo "克隆 $REPO → $SRC"
        mkdir -p "$(dirname "$SRC")"
        git clone --quiet "$REPO" "$SRC"
    fi
    echo "取 $REF"
    git -C "$SRC" fetch --quiet origin
    git -C "$SRC" checkout --quiet "$REF"
    # 拉一下,避免工作区停在旧提交上
    git -C "$SRC" pull --quiet --ff-only 2>/dev/null || true
fi

[ -f "$SRC/cmd/unfairplay/main.go" ] || {
    echo "$SRC 不像 UnfairPlay 源码(缺 cmd/unfairplay/main.go)" >&2
    exit 1
}

echo "编译中(这个仓库有 30 万行 Go,首次可能要一两分钟)…"
mkdir -p "$DEST"
( cd "$SRC" && go build -o "$DEST/unfairplay" ./cmd/unfairplay )

chmod +x "$DEST/unfairplay"
echo
echo "完成: $DEST/unfairplay"
ls -lh "$DEST/unfairplay"
echo
echo "自检(应输出你 Mac 的 fairplayd profile):"
"$DEST/unfairplay" profile \
    --binary /System/Library/PrivateFrameworks/CoreFP.framework/Versions/A/fairplayd \
    --output /tmp/unfairplay-profile.json >/dev/null 2>&1 \
    && python3 -c "
import json
d = json.load(open('/tmp/unfairplay-profile.json'))
print(f\"  sha256={d['sha256'][:16]}…  family={d.get('structural_family')}  \"
      f\"anchors={d.get('exact_anchor_count')}/{d.get('unique_anchor_count')}  \"
      f\"portable={d.get('portable_candidate')}\")
" || echo "  (profile 自检跳过 —— 不在 macOS 上,或路径不对)"

echo
echo "下一步: python3 manage_unfairplay.py list"
