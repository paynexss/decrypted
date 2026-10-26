# IPA 下载与砸壳管理系统

自建的 App Store 搜索 / IPA 下载 / 本地文件库管理工具。

砸壳有两条路,`auto` 模式下自动挑:

1. **UnfairPlay 离线解密**(优先,不需要越狱机也不需要手机)—— 基于
   [**DrWhax/unfairplay**](https://github.com/DrWhax/unfairplay):
   从一台登录了 Mac App Store 的 Mac 上回收 FairPlay enrollment 模型,
   之后拿模型离线解。详见「[九、离线解密](#九离线解密不需要设备)」。
2. **设备砸壳**(兜底)—— 连越狱机,frida 读内存。详见「[八、砸壳](#八砸壳设备直连)」。

上游项目与许可说明见「[十、上游与致谢](#十上游与致谢)」;
最初的设计方案(已被取代)见 [docs/PLAN.md](docs/PLAN.md)。

## 当前进度

| 阶段 | 内容 | 状态 |
|------|------|------|
| 第一阶段 | 搜索 + 本地文件库 | ✅ 已完成 |
| 阶段 2 | 砸壳(设备直连,frida 读内存) | ✅ 已完成并验证 |
| 阶段 2·补 | 离线解密(UnfairPlay) | ✅ 已实测跑通 |
| 第三阶段 | UI 打磨、断点续传、多设备支持 | ⬜ 未开始 |

砸壳实现方式与原方案不同:原方案要在手机上跑一个 HTTP 服务收包,
实际做下来没必要 —— 手机上有 root SSH,电脑直接连过去就行,手机端零部署。
详见「[八、砸壳](#八砸壳设备直连)」;方案演进记录在 [docs/PLAN.md](docs/PLAN.md) 的头部。

第一阶段已实现:

- 按应用名称 / Bundle ID / **App Store 链接**搜索,支持选择国家与平台(iOS / iPadOS)
- 砸壳解密:设备直连,frida 读内存,产出可直接分析的 IPA
- **离线解密**:基于 UnfairPlay,不需要越狱机也不需要手机
- **多账号**:多个 Apple ID 同时保持登录,**按应用所属区域自动选账号下载**
- SQLite 记录 + 本地文件库(列表 / 统计 / 下载文件 / 删除)
- ipatool 登录状态、手机端在线状态的检测与提示

> **下载前提**:目标应用必须已经用对应区域的 Apple ID 在 App Store「获取」过。
> 多账号配置见「[多账号](#七多账号多区域下载)」;
> 单账号时下载用的商店由登录账号决定 —— 这是 Apple 侧的限制,
> 完整说明见「[已知限制](#1-下载使用哪个国家的商店不由本工具决定)」。

---

## 目录结构

```
decrypted/
├── backend/
│   ├── app/
│   │   ├── main.py           # FastAPI 入口:初始化、路由挂载、托管前端
│   │   ├── config.py         # 配置(路径、超时、环境变量)
│   │   ├── constants.py      # 国家 / 平台字典
│   │   ├── models.py         # HTTP 出入参模型
│   │   ├── db.py             # SQLite 连接与建表
│   │   ├── repository.py     # SQL 读写
│   │   ├── ipatool.py        # ipatool 命令行封装
│   │   ├── itunes.py         # iTunes Search API 客户端
│   │   ├── decrypt.py        # 解密流程(离线解密 / 设备砸壳 二选一 → 归档)
│   │   ├── unfairplay.py     # 离线解密:Mac 回收的模型(调 upstream CLI)
│   │   ├── tasks.py          # 后台任务队列与进度
│   │   ├── storage.py        # 文件命名、归档、删除
│   │   ├── library.py        # 文件库业务逻辑
│   │   ├── accounts.py       # 多账号表(区域 → Apple ID 映射)
│   │   ├── device.py         # 设备客户端(SSH + frida 砸壳那条路)
│   │   └── routers/          # search / download / library 三组路由
│   ├── storage/              # 下载产物(运行时创建)
│   │   ├── encrypted/
│   │   ├── decrypted/
│   │   └── staging/          # 下载中间态,启动时自动清理
│   ├── data/library.db       # SQLite 数据库
│   ├── data/models/          # 离线解密模型 <账号 id>.bin(0600,不进版本库)
│   ├── accounts.json         # 多账号配置(含邮箱,不进版本库)
│   ├── manage_accounts.py    # 账号管理 CLI(list / login / logout)
│   ├── manage_unfairplay.py  # 模型管理 CLI(collect / recover / list / verify)
│   ├── tools/
│   │   └── build_unfairplay.sh # 把上游源码编到 vendor/unfairplay/
│   ├── tests/
│   │   └── selftest_offline.py # 离线解密自检(合成数据,不需要真凭证/真设备)
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.vue           # 页面外壳,协调三个异步流
│   │   ├── api.js            # 后端接口客户端
│   │   ├── format.js         # 字节 / 时间格式化
│   │   ├── styles.css        # 设计变量与基础样式
│   │   ├── composables/      # useMeta / useTaskPolling
│   │   └── components/       # StatusBar / SearchBar / AppCard / TaskList / Library
│   ├── vite.config.js
│   └── package.json
├── decrypt/
│   └── ios_dump.py           # 设备砸壳脚本(SSH 取文件 + frida 读内存)
├── patches/                  # ipatool 多账号补丁(源码 + 说明)
├── run.sh                    # 启动脚本(注入 .env 后拉起 uvicorn)
└── docs/PLAN.md              # 最初的设计方案(已被取代,见文件头说明)
```

---

## 一、环境准备

### 1. 安装 ipatool

```bash
brew install ipatool        # macOS
ipatool --version           # 应输出 2.6.0 或更高
```

非 Homebrew 安装时,用环境变量指定可执行文件路径:

```bash
export IPATOOL_BIN=/path/to/ipatool
```

### 2. 登录 Apple ID

**必须手动在终端执行**,本服务不代登录(密码不应出现在进程参数里,
同机其他用户通过 `ps` 就能看到)。

```bash
ipatool auth login -e 'your@email.com' -p 'your-password'
```

首次登录可能需要 2FA 验证码。非交互式场景下这样传:

```bash
ipatool auth login -e 'your@email.com' -p 'your-password' --auth-code 123456
```

登录成功后确认:

```bash
ipatool auth info
# 输出:  {"name":"...","email":"...","success":true}
```

> 建议使用**小号 Apple ID**,并开启双重认证。见文末「风险与注意事项」。

### 3. 关于凭据口令(关键)

ipatool 把登录凭据加密后保存在系统钥匙串里。在**非交互模式**下它需要口令来解锁,
如果没提供,任何命令都会失败并提示:

```
keychain passphrase is required when not running in interactive mode; use the "--keychain-passphrase" flag
```

本服务以 `--non-interactive` 方式调用 ipatool,因此**必须**让它能拿到口令。
推荐做法(凭据只留在你的 shell 里):

```bash
# 生成一个口令并记牢,首次设置时 ipatool 会用它加密保存凭据
export IPATOOL_KEYCHAIN_PASSPHRASE='your-local-passphrase'
ipatool auth login -e 'your@email.com' -p 'your-password' --keychain-passphrase "$IPATOOL_KEYCHAIN_PASSPHRASE"
```

之后启动本服务时,让同一个变量也存在于服务进程的环境中即可(见下面的启动步骤)。

**症状对照**:如果在网页上点下载后报「ipatool 无法解锁登录凭据」,
说明服务进程没有拿到这个变量 —— 不是登录过期,别去重新登录(重置登录会重新加密凭据)。

### 4. Python 依赖

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 5. 前端依赖

```bash
cd frontend
npm install
```

---

## 二、运行

### 开发模式(改前端代码时)

两个终端:

```bash
# 终端 1 —— 后端
cd backend
source .venv/bin/activate
export IPATOOL_KEYCHAIN_PASSPHRASE='your-local-passphrase'
uvicorn app.main:app --reload --port 8000

# 终端 2 —— 前端(自带代理,把 /api 转发到 8000)
cd frontend
npm run dev
```

打开 http://localhost:5173

### 生产模式(日常使用,单端口)

```bash
cd frontend && npm run build          # 产物输出到 frontend/dist

cd ../backend
source .venv/bin/activate
export IPATOOL_KEYCHAIN_PASSPHRASE='your-local-passphrase'
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

打开 http://localhost:8000 —— 前端由 FastAPI 直接托管,前后端同源。

> `--host 0.0.0.0` 只在需要让手机或局域网其他设备访问时才加。
> 本服务没有任何鉴权,不要暴露到公网。

---

## 三、使用流程

1. **搜索** —— 三种方式,点「搜索」前先选平台与国家:

   | 模式 | 输入 | 说明 |
   |------|------|------|
   | 应用名称 | `微信` / `Procreate` | 模糊搜索,结果可能有多个 |
   | Bundle ID | `com.tencent.xin` | 精确定位唯一一个 |
   | App Store 链接 | `https://apps.apple.com/cn/app/id1142110895` | 见下 |

   **链接模式解决什么问题**:想找抖音,但在美区搜不到 —— 因为它只在中国大陆
   商店上架。这时直接从 App Store 复制它的分享链接粘进来即可,链接里的
   `/cn/` 会决定用哪个地区的商店去查,**不需要先切国家再搜**。
   链接没带地区时(共享链接形如 `apps.apple.com/app/id1142110895`),
   会依次尝试界面选的地区、US、CN,自动找到它上架的那个。

2. **下载解密** —— 在搜索结果卡片上点「下载解密」。这是唯一的操作:
   系统会自动判断该怎么做。

   - **该区域已备 FairPlay 凭证** → 离线解密:下载加密包,本机算,不碰设备
     (见「[九、离线解密](#九离线解密不需要设备)」);
   - **没有凭证、但设备上已经装着这个应用**(比如你从 App Store 正常装的抖音)
     → 直接砸壳,几秒钟开始;
   - **没有凭证、设备上也没有** → 自动下载安装包、装到设备上,再砸壳(几分钟)。

   任务区会显示当前阶段。设备不在线、该区域也没有凭证时按钮禁用并说明原因。

3. **看进度** —— 「下载任务」区显示阶段(`下载中` / `装到设备` / `砸壳中` /
   `离线解密` / `归档中`)。任务串行执行,同时点多个会排队。

4. **下载产物** —— 完成后文件库里出现条目,点「下载解密」把 IPA 存到本地。

5. **管理文件库** —— 每一条可以下载解密包、删除(连文件一起删)。

---

## 四、API 一览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| GET | `/api/meta/options` | 国家 / 平台下拉选项 |
| GET | `/api/search` | 搜索:`q` `mode`(`name`/`bundle_id`/`url`)`country` `platform` `limit` |
| POST | `/api/download/decrypted` | 发起砸壳(唯一的下载类接口),返回 `task_id` |
| GET | `/api/tasks` | 最近任务列表 |
| GET | `/api/tasks/{task_id}` | 单任务进度(前端轮询这个) |
| GET | `/api/account` | 账号总览:单/多账号模式、每个账号的登录状态与区域、哪些区域已备离线凭证 |
| GET | `/api/device/status` | 砸壳设备在线状态(会真的连一次 SSH) |
| GET | `/api/library` | 文件库列表与统计 |
| GET | `/api/library/{id}/file?type=encrypted\|decrypted` | 下载文件 |
| DELETE | `/api/library/{id}` | 删除记录与文件 |

交互式 API 文档:http://localhost:8000/docs

---

## 五、配置项(环境变量)

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `IPATOOL_BIN` | `ipatool` | ipatool 可执行文件路径。配了 `backend/.env` 时指向补丁版 |
| `IPATOOL_KEYCHAIN_PASSPHRASE` | — | **通过 shell 环境传给 ipatool**,解锁登录凭据;不设则下载会失败 |
| `IPATOOL_TIMEOUT_SECONDS` | `1800` | 单个下载任务超时(秒) |
| `IPATOOL_AUTO_PURCHASE` | `false` | 是否传 `--purchase`(自动获取应用许可)。默认关闭,**理由见「已知限制 5」** |
| `IPA_STORAGE_DIR` | `backend/storage` | 下载产物根目录 |
| `IPA_DATABASE_PATH` | `backend/data/library.db` | SQLite 文件路径 |
| `IPA_ACCOUNTS_FILE` | `backend/accounts.json` | 多账号表路径;文件不存在时按单账号模式运行 |
| `FAIRPLAY_MODE` | `auto` | 解密路径:`auto` / `offline` / `unfairplay` / `device` |
| `IPA_MODELS_DIR` | `backend/data/models` | UnfairPlay 模型目录,默认文件名 `<账号 id>.bin` |
| `UNFAIRPLAY_BIN` | 自动查找 | unfairplay 可执行文件;留空则找 `vendor/unfairplay/`,再找 `PATH` |
| `DEVICE_SSH_HOST` | `127.0.0.1` | 设备 SSH 地址。用 iproxy 时保持默认 |
| `DEVICE_SSH_PORT` | `2222` | 设备 SSH 端口(iproxy 转发的本地端口) |
| `FRIDA_PYTHON` | 自动查找 | 装了 frida 的 Python 解释器路径 |
| `DEVICE_TIMEOUT_SECONDS` | `10` | 设备 SSH 健康检查超时 |
| `ITUNES_TIMEOUT_SECONDS` | `20` | iTunes API 超时 |

---

## 六、已知限制(请先读这一节)

### 1. 下载使用哪个国家的商店,不由本工具决定

这是第一阶段最重要的一条限制。

- **搜索**走的是公开的 iTunes Search API,支持任意 `country=XX`,所以界面上的
  国家选择对搜索结果是有效的。
- **下载**走的是 `ipatool`,而 ipatool 2.6.0 **没有** `--country` / `--storefront`
  参数 —— 它用的是当前登录账号所属商店(`account.StoreFront`)。
  下载链路上的国家码全部来自 `countryCodeFromStoreFront(account.StoreFront)`。

因此:

- 想下载美区应用 → 登录一个美区 Apple ID;
- 想下载国区应用 → 登录一个国区 Apple ID;
- 在网页上把国家选成 `CN` 却用美区账号登录,下载会报 `app not found`;
- 用「App Store 链接」模式搜索时,地区由链接决定(可覆盖界面选择),
  但**下载用的商店仍然是登录账号所属的那个** —— 搜索能查到不等于能下载。

**怎么确认自己的账号属于哪个商店**:

```bash
# ipatool 不打印 storeFront,但下载时的报错会暴露它 —— 用一个明确只在某国
# 上架的应用去探:能查到就说明账号在这个商店。
ipatool download --app-id 1142110895 -b com.ss.iphone.ugc.Aweme --platform iphone \
  --output /tmp/probe.ipa --non-interactive
# "app not found"  -> 账号不在中国大陆商店
# 开始下载          -> 账号在中国大陆商店(记得删掉 /tmp/probe.ipa)
```

另一种办法:某应用在 A 国能查到、B 国查不到,用 `ipatool list-versions -b <bundleId>`
挨个试 —— 能列出版本的就是你账号所在的商店。

要换商店,只能重新执行 `ipatool auth login` 换账号。

> 本工具不会自动「猜」你的商店,也不会为了让某个下载成功而偷偷改 province / 区码。
> 商店由 Apple 侧根据登录账号决定,客户端无法绕过。

#### 上游 ipatool 一次只能保存一个账号

本系统通过一个极小的 ipatool 补丁解决了这个问题,详见
「[多账号](#七多账号多区域下载)」。不改补丁也能用,只是退回单账号模式。

上游的限制是实测确认的:

1. **凭据条目 key 固定。** 凭据存在 macOS 钥匙串里,`service` 和 `account`
   两个字段都是源码里的常量:

   ```go
   // cmd/constants.go
   KeychainServiceName = "ipatool-auth.service"
   // cmd/common.go:93                     Label: KeychainServiceName
   // pkg/appstore/appstore_login.go:223   t.keychain.Set("account", data)
   ```

   本机钥匙串里该 service 下**只有一条** `acct=account` 条目 ——
   第二次 `auth login` 会命中同一条目并覆盖。

2. **没有任何账号选择参数。** 逐条列过 `cmd/*.go` 的 `Flags().*Var*`,
   没有 `--account` / `--profile` 之类的东西。

3. **换 `HOME` 隔离不掉凭据。** macOS 的钥匙串搜索列表来自 `~/Library/...`,
   自定义 `HOME` 下只剩 `System.keychain`,条目根本不在搜索范围内 ——
   报的是 `item could not be found` 而不是「找到但解锁失败」:

   ```
   $ HOME=/tmp/iso XDG_STATE_HOME=/tmp/iso/state ipatool auth info
   {"error":"failed to get account: failed to get item: The specified item could not be found in the keyring"}
   ```

   cookie 倒是能这样隔离(状态目录跟着 `HOME` 走),但凭据不行,等于白搭。
   `AllowedBackends` 里的 `FileBackend` 也指望不上:它的密码回调在非交互模式
   需要 passphrase,而 `--keychain-passphrase` 是 `cmd/common.go` 里的局部 flag,
   不在 `RootCmd` 的 PersistentFlags 里,子命令引用不到。

**一个 Apple ID 只有一个商店** —— 想下载多个地区的应用,必须有多个对应地区的账号。

---

### 2. 砸壳走哪条路取决于有没有离线材料

`POST /api/download/decrypted` 按 `auto` 模式的优先级挑:

1. 该区域账号**有 UnfairPlay 模型** → 离线解密,**不需要设备**;
2. 有越狱机凭证 → 同样离线;
3. 都没有 → 要求越狱设备在线(SSH 可达 + frida-server 在跑),
   设备不在时返回 400 并说明原因,不会创建一个必然失败的后台任务。

第 3 条那条路还有个前提:**应用必须能在设备上运行**。要么设备上已经装了它,
要么本地有加密包可供安装(需要 AppSync Unified)。被下架、装不上的应用砸不了 ——
这类应用改用离线解密(见「[九、离线解密](#九离线解密不需要设备)」)。

各条路的完整前置条件见「[八、砸壳](#八砸壳设备直连)」与「[九、离线解密](#九离线解密不需要设备)」。

### 3. 任务是串行的

两个原因:

- ipatool 把 cookie 持久化在 `~/.ipatool/cookies`,每个请求都会读写它。
  多个 ipatool 进程并行会争抢这个文件,可能把会话写坏(表现为随机的「未登录」);
- frida 砸壳本身也要独占设备 —— 同一时刻只能有一个进程被附加。

所以任务队列的并发数固定为 1:排队等待,但不互相破坏。

### 4. 进度是估算值

ipatool 不输出机器可读的进度。后端通过轮询下载文件的大小来估算,
分母用的是 iTunes 报告的 `fileSizeBytes`。两者可能有出入,
因此进度条在完成前封顶 99%,完成时才跳到 100%。
**进度条只用于判断「还在动」,不是精确值。**

### 5. 应用能否下载取决于你的 Apple ID

**只有该 Apple ID 已经在 App Store「获取」过(免费)或购买过(付费)的应用才能下载。**

- 免费应用:也要先在 iPhone / App Store 里点一次「获取」,之后才能用 ipatool 下载;
- 付费应用:必须此前已购买。本工具不会、也不能替你完成购买;
- 被下架的应用:用 Bundle ID 搜不到,必须用数字 ID 下载 —— 代码里已经把
  `--app-id` 和 `--bundle-identifier` 一起传给 ipatool,后者定位失败会自动回退。

**关于 `--purchase`(ipatool 的自动获取许可)**:本工具**默认关闭**它
(`IPATOOL_AUTO_PURCHASE` 默认 `false`),这是实测结论:

```
# 不带 --purchase,账号已拥有的应用 -> 正常下载
$ ipatool download -i 1436799971 -b com.czzhao.binance --platform iphone -o x.ipa --non-interactive
{"level":"info","output":"x.ipa","purchased":false,"success":true}   # 691.9 MB

# 带 --purchase,同一个应用 -> 失败
$ ... --purchase
{"level":"error","error":"sign-in request failed: authentication request failed
 after 3 attempts (HTTP 204, 204, 204): apple returned no usable authentication response"}
```

原因:`--purchase` 会让 ipatool 走一次**基于密码的重新登录**,而开启双重认证的
账号在这种情况下会被 Apple 拒绝。结果不是「顺便把许可拿到」,而是把本来能下载的
应用也一起弄失败。

所以流程是:先用同一账号在 App Store 里获取一次 → 回到这里下载。
如果你确认自己的账号可以承受自动获取(例如未开 2FA 的专用小号),可以打开:

```bash
export IPATOOL_AUTO_PURCHASE=1
```

打开后未拥有的应用会尝试自动获取,失败时报错会明确提示这一点。

### 6. 服务没有任何鉴权

任何人都能通过这个服务用你的 Apple ID 下载应用。默认只监听 `127.0.0.1`;
除非必要不要用 `--host 0.0.0.0`,更不要暴露到公网。

---

## 七、多账号(多区域下载)

一个中国区账号 + 一个美国区账号时,系统会按应用所属区域自动挑账号下载,
不需要手动切换。

### 它怎么工作

```
搜索时选国家 → 后端按国家查 accounts.json → 选中账号
                                              │
                   IPATOOL_KEYCHAIN_SERVICE=<该账号的钥匙串命名空间>
                   IPATOOL_COOKIE_JAR=<该账号的 cookie 文件>
                                              │
                                        ipatool download
```

这两个环境变量由[补丁](patches/README.md)引入。它们合起来构成一个独立登录态,
所以多个 Apple ID 可以同时保持登录、互不覆盖。

账号属于哪个区域**不用你填**:登录后系统读 `auth info` 的 `storeFront`
(补丁让它输出这个字段),按 ipatool 自带的映射表反查国家码,写回 `accounts.json`。

### 一次性配置

**1. 编译带补丁的 ipatool**(约 30 秒,需要 Go 工具链):

```bash
git clone --depth 1 https://github.com/majd/ipatool.git /tmp/ipatool
cd /tmp/ipatool
cp <本项目>/patches/cmd_constants.go cmd/constants.go
cp <本项目>/patches/cmd_common.go    cmd/common.go
cp <本项目>/patches/cmd_auth.go      cmd/auth.go
go build -o <本项目>/backend/vendor/ipatool-patched/ipatool .
```

补丁内容与「为什么不能不改源码」见 [patches/README.md](patches/README.md)。

**2. 写账号表**:

```bash
cp backend/accounts.example.json backend/accounts.json
```

编辑 `backend/accounts.json`:

```json
{
  "accounts": [
    {
      "id": "cn",
      "label": "中国区",
      "email": "你的国区 Apple ID",
      "keychain_service": "ipa-manager.cn",
      "cookie_jar": "data/accounts/cn-cookies",
      "region": "CN"
    },
    {
      "id": "us",
      "label": "美国区",
      "email": "你的美区 Apple ID",
      "keychain_service": "ipa-manager.us",
      "cookie_jar": "data/accounts/us-cookies",
      "region": "US"
    }
  ]
}
```

- `id` 不能重复,只用于日志和界面;
- `keychain_service` **每个账号必须不同** —— 这是凭据互不覆盖的关键;
- `cookie_jar` 的相对路径按 `accounts.json` 所在目录解析;
- `region` 留空也行,首次启动会自动探测并回填。

**3. 逐个登录**(每个账号单独执行一次):

```bash
cd backend
python3 manage_accounts.py login cn        # 中国区
python3 manage_accounts.py login us        # 美国区
```

它会从 `accounts.json` 读出该账号的 `keychain_service` / `cookie_jar`,
自动建好 cookie 目录,再用补丁版 ipatool 登录。首次登录可能提示输入 2FA 验证码。

查看全部账号状态:

```bash
python3 manage_accounts.py list
```

> **不要手敲环境变量登录。** `keychain_service` 写错不会报错 —— 凭据会安静地落进
> 另一个命名空间,之后服务找不到它,你只会看到「未登录」。
> `manage_accounts.py` 就是为了避免这个。

<details>
<summary>等价的手工命令(不想用脚本时)</summary>

```bash
# 中国区
IPATOOL_KEYCHAIN_SERVICE=ipa-manager.cn \
IPATOOL_COOKIE_JAR="$PWD/data/accounts/cn-cookies" \
  vendor/ipatool-patched/ipatool auth login -e '国区 Apple ID' -p '密码'
```

注意 cookie 的父目录必须先存在,否则 ipatool 会报一个**指向错误方向**的错误
(`file locked for too long`)。详见「常见问题」。
</details>

**4. 启动**:

```bash
./run.sh
```

启动日志会打印每个账号的状态与探测到的区域:

```
INFO app.accounts: 已加载 2 个账号: cn, us
INFO app.main: 账号 cn(中国区): 已登录, 区域 CN
INFO app.main: 账号 us(美国区): 已登录, 区域 US
```

### 使用

界面上:

- 顶部状态条显示「可下载区域:中国大陆 / 美国」,点开能看到每个账号的登录状态;
- 搜索时把国家选成对应区域,下载按钮即可用;
- 选了**没有账号**的国家时,搜索框下方直接给出警告 —— 不用等到点下载才失败。

后端的行为:

- 按国家挑不到已登录账号时**直接拒绝并列出可用区域**,绝不「猜」一个账号去试 ——
  那样只会得到误导性的 `app not found`;
- 选中的账号会写进下载日志:`ipatool 下载开始: ...(账号 ipa-manager.us)`。

### 新增第三个区域

在 `accounts.json` 里加一项(用没被占用的 `keychain_service` 和 `cookie_jar`),
按上面第 3 步登录,重启服务。

### 不用补丁会怎样

把 `backend/.env` 里的 `IPATOOL_BIN` 注释掉、改用系统 ipatool 时,系统自动退回
**单账号模式**:忽略 `accounts.json`,直接用 ipatool 的默认命名空间。
此时界面只显示一行登录状态,与所选国家无关 —— 也就是本功能上线前的行为。

### 只有一个账号

不需要 `accounts.json`,也不需要补丁。删掉 `backend/accounts.json` 即可,
系统行为与本文档其余部分一致。

---

## 八、砸壳(设备直连)

砸壳 = 把 App Store 加密 IPA 解密。完成后文件库里的条目会多出一个解密包。

### 它是怎么工作的

```
电脑                                      iPhone(越狱)
 │                                              │
 ├─ ① SSH:scp -r 拉取 .app 整个目录 ──────────► │
 │                                              │
 ├─ ② frida:spawn 应用 → resume → attach ─────► │
 │    读内存里解密后的加密区段                    │
 │                                              │
 ├─ ③ 把解密的字节写回磁盘上的二进制              │
 │    并把 LC_ENCRYPTION_INFO_64 的 cryptid 置 0 │
 │                                              │
 └─ ④ 打成 IPA,存进 storage/decrypted/
```

原理:App Store 下发的包里,主可执行文件的加密区段是密文;应用运行时内核
会把它解密到内存。所以要覆盖的字节范围直接从 Mach-O 头的
`LC_ENCRYPTION_INFO_64` 读出来,再从内存取对应内容。

**为什么不用原方案(手机上跑 HTTP 服务)**:手机上已经有 root SSH,
`scp -r` 一条命令就能把整个 bundle 连目录结构、权限、符号链接一起拉下来,
比在手机上写个 HTTP 服务收包可靠得多。而且 frida 本来就是电脑直连 USB 的,
不需要手机上有什么在跑。现在手机端**零部署**。

### 前置条件

| 项 | 说明 |
|---|---|
| 越狱设备 | 已验证:iPhone 11 Pro Max / iOS 26.0.1 / Dopamine rootless |
| OpenSSH | 设备上装好,root 可以免密登录(用公钥) |
| frida-server | 设备上装 `re.frida.server`,**版本必须与电脑端 frida 一致** |
| AppSync Unified | 只有「本地有加密包但设备没装」时才需要(用于安装) |
| 电脑端 frida | 见下面的一键准备 |
| iproxy | `brew install libimobiledevice`;服务会自动拉起,不用手动跑 |

准备电脑端的 frida 环境(只需一次):

```bash
python3 -m venv ~/.local/frida-venv
~/.local/frida-venv/bin/pip install "frida==17.23.3"
```

> 版本要跟设备的 frida-server 对齐。查设备上的版本:
> ```bash
> ssh -p 2222 root@127.0.0.1 '/var/jb/usr/sbin/frida-server --version'
> ```
> 不对齐时附加会失败,报 `unexpected early end-of-stream` 之类的错误。

### 怎么用

界面上的操作只有一个:点「下载解密」。系统自己判断走哪条路。

**情况 A —— 设备上已经装着这个应用**(最常见)

直接砸壳。不需要任何下载 —— 砸壳就是读设备上运行中的应用的内存。

**情况 B —— 设备上没有这个应用**

系统会自动:下载安装包 → 推到设备并用 appinst 安装 → 再砸壳。
需要设备上装了 AppSync Unified。这一步可能要几分钟(取决于包大小),
任务区会显示「装到设备」阶段。

**前提**:情况 B 走不通的两条死路,系统会在点按钮时立刻告诉你 ——
应用没有对应地区的账号、或 ipatool 未登录。

### 命令行用法

砸壳脚本可以独立运行,不依赖本服务:

```bash
# 先起 SSH 转发(服务会自动做,手动用时需要自己起)
iproxy 2222 22 &

~/.local/frida-venv/bin/python decrypt/ios_dump.py com.tuyangfeng.Via \
    --output /tmp/via.ipa

# 设备与电脑同网段时可以直接连 IP,不用 iproxy
~/.local/frida-venv/bin/python decrypt/ios_dump.py com.example.app \
    --ssh-host 192.168.1.20 --ssh-port 22
```

### 怎么验证砸出来的包是真的解密了

```bash
# 看 cryptid 是否为 0(0 = 已解密,1 = 仍加密)
python3 - <<'EOF'
import struct, pathlib, sys
data = pathlib.Path(sys.argv[1]).read_bytes()
ncmds = struct.unpack_from('<I', data, 16)[0]
off = 32
for _ in range(ncmds):
    cmd, sz = struct.unpack_from('<II', data, off)
    if sz == 0: break
    if cmd == 0x2C:
        print("cryptid =", struct.unpack_from('<III', data, off+8)[2])
        break
    off += sz
EOF
```

更彻底的验证是熵值:加密数据接近 8.0 bits/byte,真实机器码通常在 5.5-6.5。
本项目实测抖音从 **7.956 → 5.923**。

### 重复砸同一个应用会怎样

**同一个版本不会重复砸。** 点「下载解密」时系统先读设备上**已安装包**的
版本号(`CFBundleShortVersionString`),与文件库里的记录比对:

| 情况 | 行为 |
|------|------|
| 设备版本 = 库里版本,且文件在 | 拒绝,提示「已经砸过了」 |
| 设备版本不同(应用升级了) | 正常重砸,并**自动清掉旧版本的包** |
| 记录在但文件被删了 | 正常重砸(不算「已砸过」) |

需要强制重砸时:先删除文件库里的条目,再点解密。

> **版本号取自设备,不是商店。** 商店里可能已经是 40.7.0,而你设备上装的
> 是 40.6.0 —— 砸出来的内容对应后者,所以文件名与「已砸过」判断都用设备版本。
> 用商店版本会让文件名与内容不符,以后追溯版本时对不上。
```bash
# 直接查设备上某个应用的实际版本
ssh -p 2222 root@127.0.0.1 \
  'grep -A1 CFBundleShortVersionString /var/containers/Bundle/Application/*/Aweme.app/Info.plist | tail -1'
```

### 已知限制

- **砸壳需要应用能在设备上运行。** 被下架、已购但装不上的应用砸不了 ——
  这类应用改用「[九、离线解密](#九离线解密不需要设备)」。
- **一次一个任务。** 任务队列是串行的,同时点多个会排队。
- **耗时取决于包大小。** 抖音(主二进制 435 MB)约 100 秒,其中大头是
  SSH 拉取整个 bundle(971 MB 的 AwemeCore.framework 也在里面)。
- **framework 通常不需要解密。** 实测抖音的所有 framework 在设备上就是
  cryptid=0 —— 只有主二进制是加密的。脚本也只处理主二进制。

## 九、离线解密(不需要设备)

和「[八、砸壳](#八砸壳设备直连)」二选一 —— **有模型就走它,更快也更省事;
没有才退回设备流程**。

这条路基于 [**DrWhax/unfairplay**](https://github.com/DrWhax/unfairplay):
从一台登录了 Mac App Store 的 Mac 上回收一次 FairPlay enrollment 模型,
之后拿模型在本地离线解,**不需要越狱机,也不需要手机**。

### 它是怎么工作的

macOS 在 Apple Silicon 上安装 iOS 应用时**会解密** —— 应用要能跑。实测:

```
商店 IPA            主二进制 1880 页密文,__TEXT 熵 8.000(纯随机)
    ↓ 装到 Mac
Mac 上的那份        只剩 1 页密文,__TEXT 熵 6.874(代码),21263 条可读字符串
```

也就是说 Mac 安装目录**本身就接近一份明文**。但每个二进制会剩一页(4 KB)
被 macOS 用**机器专属密钥**重新加密 —— 实测那一页和商店的密文并不相同,
所以不是「没解到」,而是「解完又用本机密钥封了一页」。

UnfairPlay 干的事:从 Mac 上抓一次状态(硬件身份 + ADI + SC Info + fairplayd 本体),
再结合**同一个 App 的加密 IPA**,离线重演 fairplayd 的密钥派生,产出模型。
之后拿这个模型就能解开该账号下的任意 IPA,连那一页也补齐。

### 前置条件

- 一台 Apple Silicon Mac,Mac App Store **登录目标账号**
- 从 Mac App Store 装**任意一个 iOS 应用**(免费即可)并**打开过一次**
- 用同一个账号通过 ipatool 下载**同一个 App** 的加密 IPA

> 那个 App 只是「引子」—— 模型**不绑 App**,只绑账号 + 这台 Mac。
> 装一个能装的就行,不必是你真正想砸的那个。
>
> Mac 上装的 iOS 应用是**包在 Wrapper 里**的,`SC_Info` 在内层:
> `/Applications/<App>.app/Wrapper/<App>.app`

### 步骤

```bash
cd backend

# 0. 编译 UnfairPlay(需要 Go;首次约一两分钟,仓库有 30 万行 Go)
bash tools/build_unfairplay.sh

# 1. 一条命令:collect → recover → 安装模型 → verify
#    collect 要 sudo(读 /private/var/db/fpsd,只读不写系统)
python3 manage_unfairplay.py collect us \
    --app "/Applications/Surge-iOS.app/Wrapper/Surge-iOS.app" \
    --ipa ~/Downloads/surge.ipa

# 2. 看状态
python3 manage_unfairplay.py list

# 3. 之后正常用:在界面上点「下载解密」,或强制离线
FAIRPLAY_MODE=offline ./run.sh
```

`collect` 会自动跑完四步并打印每步结果。模型落到
`backend/data/models/<账号 id>.bin`(权限 0600),旁边有一个 `.json` 记着
selector / profile / 安装时间,界面靠它显示归属。

### 实测数据

```
collect       秒级          抓 17 MB fairplayd + 硬件身份 + SC 状态
recover       1.5 秒        离线重演,产出 4.1 KB 模型
decrypt       0.45 秒       4720 页 / 9 个二进制
```

验证方式(两条独立路径互证):UnfairPlay 从 IPA 解出的明文,和 macOS 安装时
解出的明文**逐字节比对**,差异只有「头部 / 代码签名 / 那一页机器专属数据」,
**其它 0 字节**。

`manage_unfairplay.py verify <账号> <IPA>` 可以随时用真实包确认模型还能用。

### 给多个账号开通

材料是**账号级**的:哪个 Apple ID 下载的包,就得用哪个账号的模型解 ——
拿美国区模型解中国区下载的包只会报「解不开」。每个账号各跑一次 `collect`:

```bash
python3 manage_unfairplay.py collect cn \
    --app "/Applications/<Mac上装的iOS应用>.app/Wrapper/<同名>.app" \
    --ipa ~/Downloads/cn-app.ipa
```

注意 Mac App Store 要**换成那个账号**再装引子应用 —— 而装的那一步同时也
给该账号拿到了应用许可(否则 ipatool 下包会报 `license is required`)。

> 找「CN 区有哪些 iOS 应用能在 Mac 上装」:
> `python3 ~/Reverse/tools/ios-toolkit/mac_available.py cn 阅读 记账 词典`
> (App Store 页面是本地化的,英文站的 `Requires macOS` 在中文站写作
> `需要 macOS`,查的时候两种都要认。)

### 支持范围

| 项 | 支持 |
|---|---|
| SUPF 版本 | v3、v5(magic `507` / `510` / `612`) |
| SINF `priv` 记录 | 448 字节,或 440 字节且末 8 字节为 0 |
| Mach-O | arm64(加密区必须是 4096 的整数倍) |
| fairplayd profile | 未知的 arm64e 版本也会尝试,但只有 recovery + 完整 IPA 校验都过才认 |

已经明文的二进制一律跳过并记录原因。

### 注意事项

- **模型 = 那个账号在这台 Mac 上的内容解密能力。** `data/models/` 与
  `data/unfairplay/`(collect 的中间产物,含 fairplayd 副本与本机硬件身份)
  都在 `.gitignore` 里,权限 0600,别外传。
- **绑的是「账号 + 这台 Mac」**:换 Mac 或换 Apple ID 都要重新 `collect`。
- **产物没有重签名**。分析用足够;要装回真机自己 `ldid`/自签。
- 走离线解密时**不需要设备在线**,后端也不会去连它 ——
  界面上「离线解密:美国」那行就是「这些区域不需要设备」。

## 十、上游与致谢

这个项目站在几个现成工具上,感谢它们的作者:

| 项目 | 许可 | 用在哪 | 怎么引入 |
|---|---|---|---|
| [**DrWhax/unfairplay**](https://github.com/DrWhax/unfairplay) | MIT | **离线解密** —— 从 Mac 回收 FairPlay enrollment 模型,不需要越狱机也不需要手机 | `tools/build_unfairplay.sh` 编译到 `vendor/unfairplay/`(**不 vendored 源码**,仓库里只有编译脚本) |
| [majd/ipatool](https://github.com/majd/ipatool) | MIT | 下载加密 IPA、多账号登录 | `patches/` 里带一个多账号补丁(改钥匙串命名空间 + cookie 文件路径);补丁源码随仓库 |
| [facebook/fishhook](https://github.com/facebook/fishhook) | BSD | `QreadVipUnlock` 那个 tweak 用(不在本仓库) | — |

## 十一、常见问题

**Q: 搜索能用,但一点下载就提示未登录?**

在终端跑 `ipatool auth info` 确认。若报 `item could not be found in the keyring`,
就是没登录,执行 `ipatool auth login`。

**Q: 提示「ipatool 无法解锁登录凭据」?**

服务进程没有拿到 `IPATOOL_KEYCHAIN_PASSPHRASE`。用 `export` 设置后重启服务,
不要重新登录(重新登录会重新加密凭据,反而更麻烦)。

**Q: 下载报「找不到 App ID xxx,请确认该应用在此国家/地区上架」?**

iTunes API 在该国家查不到这个 app_id。通常是把国家选成了与账号/应用不匹配的地区。

**Q: 下载任务失败,错误是「App Store 在**当前登录账号所属的商店**里找不到这个应用」?**

账号所在商店里没有这个应用。例如抖音(`com.ss.iphone.ugc.Aweme`)只在中国大陆商店,
用非国区账号必然失败。界面上的国家选项不会改变下载商店 —— 详见「已知限制 1」,
里面有确认自己账号属于哪个商店的探针命令。

**Q: 下载任务失败,错误是「该 Apple ID 没有这个应用的许可」?**

这个应用没被该账号获取过。免费应用也用同一账号在 App Store 里点一次「获取」,
然后回来重试。

**Q: 错误里有 `no usable authentication response` / HTTP 204 / 301?**

Apple 拒绝了 ipatool 用已保存密码直接换会话的尝试。最常见于开启双重认证的账号,
且通常发生在「获取许可」这一步。确认 `IPATOOL_AUTO_PURCHASE` 是关闭状态(默认),
需要许可的应用改从 App Store 手动获取。

**Q: 登录报 `file locked for too long ... failed to create lock file ... no such file or directory`?**

**这不是文件锁的问题,是 cookie 的父目录不存在。** ipatool 写 cookie 时不创建目录,
只在建 `.lock` 文件时才失败,错误被包装成了「文件锁超时」。

```bash
mkdir -p backend/data/accounts      # 或你配的 cookie_jar 的父目录
```

用 `python3 manage_accounts.py login <id>` 登录会自动建目录,不会踩这个坑。

**Q: 点「下载解密」报「设备不可用」?**

先确认三件事:USB 线连着、手机上装了 OpenSSH、电脑上装了 iproxy
(`brew install libimobiledevice`)。服务会自己把端口转发起起来,不用手动跑。

**Q: 砸壳报 `unexpected early end-of-stream` / `ProcessNotRespondingError`?**

电脑端 frida 与设备 frida-server **版本不一致**。查设备版本:

```bash
ssh -p 2222 root@127.0.0.1 '/var/jb/usr/sbin/frida-server --version'
```

然后装同版本到电脑:

```bash
~/.local/frida-venv/bin/pip install "frida==<同一个版本>"
```

**Q: 砸壳报「设备上没有 XXX,需要下载安装包,但没有可用于 XX 地区的账号」?**

应用不在设备上,系统需要下载安装包才能把它装上去,而这个地区没有已登录的账号。
两条路:用对应地区的 Apple ID 登录(见「[七、多账号](#七多账号多区域下载)」),
或者直接在手机上用 App Store 装上这个应用 —— 那样就不需要下载安装包了。

**Q: 砸壳出来的 IPA 能确认真的解密了吗?**

看 Mach-O 的 `cryptid` 是否为 0(详见「[八、砸壳](#八砸壳设备直连)」里的验证片段)。
也可以看熵值:密文接近 8.0 bits/byte,真实代码在 5.5-6.5。实测抖音 7.956 → 5.923。

**Q: 多账号配了三个,界面显示三个都「已登录」但邮箱是同一个?**

说明跑的是**没打补丁的** ipatool(系统 brew 装的那个)。它不认
`IPATOOL_KEYCHAIN_SERVICE` / `IPATOOL_COOKIE_JAR`,所有账号都去读同一条钥匙串记录。
确认 `backend/.env` 里的 `IPATOOL_BIN` 指向 `vendor/ipatool-patched/ipatool`,
并且该文件存在:

```bash
backend/vendor/ipatool-patched/ipatool auth info --format json
# 补丁版会多输出 "storeFront" 字段;没有这个字段说明是未打补丁的版本
```

**Q: 任务一直停在「下载中」,字节数不动?**

看 `backend` 终端的日志。常见原因是 Apple ID 的会话过期 —— ipatool 在下载时
若遇到 `tokenExpired` 会用保存的密码自动重新登录,若失败会直接报错,任务会转为失败状态。

**Q: 想换存储位置(比如外置硬盘)?**

```bash
export IPA_STORAGE_DIR=/Volumes/YourDisk/ipa-storage
```

数据库位置用 `IPA_DATABASE_PATH`。注意更换存储目录后,数据库里记录的文件名
在新目录里找不到,**已下载的记录需要重新下载**。

---

## 十二、风险与注意事项

- **仅限个人研究使用。** 不要把这套服务公开出去提供解密服务。
- **Apple ID 安全**:建议使用小号并开启双重认证。登录凭据由 ipatool 加密保存在
  系统钥匙串,本服务质量不接触、不存储你的密码。
- **设备砸壳那条路依赖越狱机**。iOS 26 + 实验性越狱上 frida 的可用性会变,
  优先用 UnfairPlay 离线解密(见「[九、离线解密](#九离线解密不需要设备)」)。
- **网络传输**:IPA 体积可达数 GB,局域网传输时注意不要在不可信网络中操作。
- **版权**:下载的应用仅供你个人研究,不要分发。

---

## 十三、开发说明

### 后端为什么这么分层

- `routers/` 只做 HTTP 转换(参数校验、状态码、错误翻译),不含业务逻辑;
- 业务流程在 `decrypt.py` / `library.py`,可被直接调用和测试,不依赖 HTTP 层;
- 外部依赖各占一个模块:`ipatool.py`(命令行)、`itunes.py`(HTTP API)、
  `device.py`(设备通信)、`accounts.py`(多账号),替换其中任一个不影响其他部分;
- `tasks.py` 负责把「可能跑几十分钟的下载」与 HTTP 请求生命周期解耦 ——
  否则一个大包就会把请求挂死,浏览器超时后用户完全不知道发生了什么。

### 数据库

一张 `apps` 表,按 `(bundle_id, country, platform)` 唯一:一个应用一条记录,
重复下载只更新加密包相关字段,**不会覆盖已经砸好的解密包**。

时间字段存 ISO-8601 UTC 字符串,由前端转本地时区显示。

### 检查点

```bash
# 后端路由是否正常挂载
cd backend && python3 -c "import sys; sys.path.insert(0,'.'); from app.main import app; print(len(app.routes))"

# 离线解密全链路自检(合成 SINF/SUPF/Mach-O,不需要真凭证、真设备)
cd backend && .venv/bin/python tests/selftest_offline.py

# UnfairPlay 离线材料状态
cd backend && python3 manage_unfairplay.py list

# 前端构建
cd frontend && npm run build
```

自检覆盖什么、不覆盖什么要说清楚:**它用合成数据验证管线**(SINF/SUPF 解析、
页密钥派生、逐页 AES、IPA 重写、cryptid 清零、材料错配拒绝、双后端选路优先级、
API 字段),但「Apple 的算法在真实包上算得对不对」只能拿真实材料 + 真实包确认:

```bash
python3 manage_unfairplay.py verify <账号> <加密 IPA>
```
