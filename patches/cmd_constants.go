package cmd

import "os"

const (
	ConfigDirectoryName = ".ipatool"
	CookieJarFileName   = "cookies"

	defaultKeychainName = "ipatool-auth.service"
)

// KeychainServiceName 决定凭据在系统钥匙串里的命名空间。
//
// 上游把它写死成常量,导致一台机器上只能保存一个 Apple ID ——
// 第二次 auth login 会命中同一条目并覆盖。
//
// 这里改成可被 IPATOOL_KEYCHAIN_SERVICE 覆盖:每个账号用各自的服务名,
// 凭据就在钥匙串里各占一条,互不覆盖。默认值保持与上游一致,
// 不设该变量时行为完全不变。
var KeychainServiceName = envOrDefault("IPATOOL_KEYCHAIN_SERVICE", defaultKeychainName)

// CookieJarPath,非空时用作 cookie 文件的完整路径,覆盖 stateDirectory/cookies。
//
// 换 HOME / XDG_STATE_HOME 也能隔离 cookie,但那会一并移动状态目录,
// 属于杀鸡用牛刀;而且调用方可能并不希望进程的 HOME 被改动。
// 单独把 cookie 文件路径参数化,账号隔离的代价最小。
var CookieJarPath = os.Getenv("IPATOOL_COOKIE_JAR")

func envOrDefault(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}

	return fallback
}
