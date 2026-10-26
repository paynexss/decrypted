#!/usr/bin/env bash
#
# 启动脚本:把 backend/.env 里的变量注入环境后再拉起服务。
#
# 为什么需要它:uvicorn 自己不读 .env,而 ipatool 的路径、账号补丁需要的
# 环境变量都得在**服务进程**的环境里。写在 shell 里每次手敲容易漏。
#
# 用法:
#   ./run.sh              # 前台启动,监听 127.0.0.1:8000
#   PORT=9000 ./run.sh    # 换端口
#   HOST=0.0.0.0 ./run.sh # 允许局域网访问(注意服务无鉴权)

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT/backend"

if [[ -f .env ]]; then
  # set -a 让 .env 里定义的变量自动导出给子进程。
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

# .env 里写相对路径时,按 backend/ 目录解析成绝对路径。
# 不做这一步的话,IPATOOL_BIN 会相对于运行时的工作目录解析,
# 换一种启动方式(比如 IDE 里跑)就会找不到文件。
if [[ -n "${IPATOOL_BIN:-}" && "$IPATOOL_BIN" != /* ]]; then
  IPATOOL_BIN="$PROJECT_ROOT/backend/$IPATOOL_BIN"
  export IPATOOL_BIN
fi

# 同理,账号表里的 cookie_jar 相对路径按账号表所在目录解析 ——
# 这部分由 app/accounts.py 负责,不在这里处理。

exec python3 -m uvicorn app.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}"
