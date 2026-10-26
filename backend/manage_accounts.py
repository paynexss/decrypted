#!/usr/bin/env python3
"""
账号管理命令行工具。

为什么需要它:多账号模式下,每个账号的 `ipatool auth login` 都必须带上
正确的 IPATOOL_KEYCHAIN_SERVICE 与 IPATOOL_COOKIE_JAR。手敲这两个变量
有两个坑:

1. 写错了不会报错 —— 凭据会安静地落进另一个命名空间,之后服务找不到它,
   你只会看到「尚未登录」。
2. cookie 文件的父目录 ipatool 不会自己建,缺目录时的报错是
   「file locked for too long ... failed to create lock file」,
   完全看不出真正原因。

本工具把这两个坑都堵上:从 accounts.json 读配置,自动建目录,再调用 ipatool。

用法:

    cd backend
    python3 manage_accounts.py list                    # 列出账号与状态
    python3 manage_accounts.py login cn                # 登录中国区账号
    python3 manage_accounts.py login cn --auth-code 123456
    python3 manage_accounts.py logout cn               # 注销
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import accounts  # noqa: E402
from app.config import IPATOOL_BIN  # noqa: E402


def _resolve_ipatool() -> str:
    """
    找到 ipatool 可执行文件。

    与 run.sh 同样的规则:.env 里的相对路径按 backend/ 解析。
    直接运行本脚本时不会经过 run.sh,所以这里自己读一次 .env。
    """
    binary = IPATOOL_BIN
    env_file = Path(__file__).resolve().parent / ".env"

    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("IPATOOL_BIN="):
                binary = line.split("=", 1)[1].strip().strip('"').strip("'")
                break

    path = Path(binary)
    if not path.is_absolute():
        path = (Path(__file__).resolve().parent / path).resolve()

    if not path.exists():
        # 回退到 PATH 里的 ipatool,并明确告知用户可能没有多账号支持。
        print(f"警告: 未找到 {path},改用 PATH 中的 ipatool(可能不含多账号补丁)", file=sys.stderr)
        return "ipatool"

    return str(path)


def _select(account_id: str) -> accounts.Account:
    account = accounts.get(account_id)
    if account is None:
        available = ", ".join(item.id for item in accounts.load()) or "(账号表为空)"
        raise SystemExit(f"账号表中没有 id 为 {account_id!r} 的账号。可用: {available}")

    return account


def cmd_list(_: argparse.Namespace) -> int:
    configured = accounts.load()
    if not configured:
        raise SystemExit(
            f"未找到账号表 {accounts.ACCOUNTS_FILE}。\n"
            "单账号模式不需要它;要启用多账号请先:\n"
            "    cp accounts.example.json accounts.json"
        )

    print(f"账号表: {accounts.ACCOUNTS_FILE}\n")

    for account in configured:
        accounts.probe(account)
        state = "已登录" if account.logged_in else "未登录"
        print(f"  {account.id:6} [{account.region or '未探测':>6}] {account.label}")
        print(f"         {state}   {account.email or '(未填邮箱)'}")
        if account.error:
            print(f"         {account.error[:100]}")
        print(f"         keychain={account.keychain_service}")
        print(f"         cookie={account.cookie_jar}")
        print()

    return 0


def cmd_login(args: argparse.Namespace) -> int:
    account = _select(args.account_id)
    binary = _resolve_ipatool()

    # 关键:cookie 文件的父目录。ipatool 不会自己建。
    account.ensure_cookie_dir()

    command = [binary, "auth", "login", "-e", args.email or account.email]
    if args.password:
        command += ["-p", args.password]
    if args.auth_code:
        command += ["--auth-code", args.auth_code]

    if not args.email and not account.email:
        raise SystemExit("未提供邮箱:用 --email 指定,或先在 accounts.json 里填好 email 字段")

    print(f"登录账号 {account.id}({account.label})")
    print(f"  keychain: {account.keychain_service}")
    print(f"  cookie:   {account.cookie_jar}")
    print()

    env = {**os.environ, **account.environment()}

    # 密码走 stdin 交互:不通过命令行参数传,避免出现在 ps 输出里。
    try:
        result = subprocess.run(command, env=env, check=False)
    except KeyboardInterrupt:
        print("\n已取消", file=sys.stderr)
        return 130

    if result.returncode == 0:
        print("\n登录成功。重启服务后即可在界面上看到这个账号。")
    else:
        print(f"\n登录失败(退出码 {result.returncode})", file=sys.stderr)

    return result.returncode


def cmd_logout(args: argparse.Namespace) -> int:
    account = _select(args.account_id)
    binary = _resolve_ipatool()

    print(f"注销账号 {account.id}({account.label})")
    env = {**os.environ, **account.environment()}
    result = subprocess.run([binary, "auth", "revoke", "--non-interactive"], env=env, check=False)

    # 注销只清凭据,不清 cookie 文件 —— 那是会话缓存,留着无害且下次登录会覆盖。
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description="多账号管理(配合 accounts.json)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="列出所有账号及登录状态").set_defaults(func=cmd_list)

    login = subparsers.add_parser("login", help="登录某个账号")
    login.add_argument("account_id", help="accounts.json 里的 id,例如 cn")
    login.add_argument("--email", help="覆盖账号表里的邮箱")
    login.add_argument("--password", help="不推荐:会出现在进程列表里,建议交给 ipatool 交互式提示")
    login.add_argument("--auth-code", help="2FA 验证码")
    login.set_defaults(func=cmd_login)

    logout = subparsers.add_parser("logout", help="注销某个账号")
    logout.add_argument("account_id")
    logout.set_defaults(func=cmd_logout)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
