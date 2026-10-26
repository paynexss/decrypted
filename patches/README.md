# ipatool 多账号补丁

上游 `ipatool` 一台机器只能保存一个 Apple ID:钥匙串条目的 `service` 和
`account` 两个字段都是源码里的固定常量,第二次 `auth login` 会命中同一条目
并覆盖前一个账号。这个目录里是对上游的一处极小改动,让它支持多账号并存。

## 改了什么

三处,一共不到 30 行:

| 文件 | 改动 |
|------|------|
| `cmd/constants.go` | `KeychainServiceName` 从常量改为 `var`,可被 `IPATOOL_KEYCHAIN_SERVICE` 覆盖;新增 `CookieJarPath`(`IPATOOL_COOKIE_JAR`) |
| `cmd/common.go` | `newCookieJar` 优先使用 `CookieJarPath` |
| `cmd/auth.go` | `auth info` 多输出一个 `storeFront` 字段(区域探测必需) |

两个环境变量合起来 = 一个独立登录态:

```
IPATOOL_KEYCHAIN_SERVICE=ipa-manager.us     # 凭据存哪个钥匙串命名空间
IPATOOL_COOKIE_JAR=/path/to/us-cookies      # 会话 cookie 存哪
```

不设这两个变量时,行为与上游**完全一致**(已实测:默认命名空间仍能读到
原有账号,`auth info` 输出多一个字段)。所以打了补丁也不会影响你原来的用法。

## 为什么不用别的办法

试过并排除的替代路径:

- **换 `HOME` / `XDG_STATE_HOME` 跑多个 ipatool 实例** —— 没用。macOS 的
  钥匙串搜索列表来自 `~/Library/...`,换 `HOME` 后只剩 `System.keychain`,
  条目根本不在搜索范围内,报的是 `item could not be found`(而不是「找到但
  解锁失败」)。cookie 倒是能隔离,但凭据不行,等于白搭。
- **改用 `FileBackend` 存凭据** —— ipatool 的 `AllowedBackends` 里确实列了它,
  但它的密码回调 `FilePasswordFunc` 在非交互模式需要 passphrase,而
  `--keychain-passphrase` 是 `cmd/common.go` 里的局部 flag,不在
  `RootCmd` 的 PersistentFlags 里 —— 子命令引用不到,报
  `flag accessed but not defined`。这是上游的一个 bug,不是配置问题。

## 怎么编译

```bash
git clone --depth 1 https://github.com/majd/ipatool.git /tmp/ipatool
cd /tmp/ipatool

# 应用本目录下的三个补丁文件(直接覆盖即可)
cp <本仓库>/patches/cmd_constants.go   cmd/constants.go
cp <本仓库>/patches/cmd_common.go      cmd/common.go
cp <本仓库>/patches/cmd_auth.go        cmd/auth.go

go build -o <本仓库>/backend/vendor/ipatool-patched/ipatool .

# 验证
<本仓库>/backend/vendor/ipatool-patched/ipatool auth info --format json
# 应当多出 "storeFront":"143467-2,34" 这样的字段
```

> 本目录只保存改动后的**完整文件**,而不是 `.patch` 文件。原因:上游的
> `cmd/common.go` 有 110 多行,`.patch` 在上下文行变化时容易冲突;而这三个
> 文件本身不大,整份替换更省事,也更容易在升级上游后重新对照改动点。
>
> 升级上游 ipatool 时:把这三个文件与新版逐一 diff,确认改动点仍然只有上面
> 列出的三处(上游若已自行支持多账号,可以整个删掉本目录)。

## 对应关系

`backend/app/accounts.py` 依赖这三个改动:

- 读 `IPATOOL_KEYCHAIN_SERVICE` / `IPATOOL_COOKIE_JAR` 来切换账号;
- 读 `auth info` 的 `storeFront` 来自动判断每个账号属于哪个区域。

如果哪天换回**未打补丁**的 ipatool,`backend/accounts.json` 里的
`keychain_service` 就必须全部写成 `ipatool-auth.service`、`cookie_jar` 全部
指向同一个文件 —— 那样实际只有一个账号能用。系统会正常工作但会提示
「没有可用于 XX 地区的已登录账号」。
