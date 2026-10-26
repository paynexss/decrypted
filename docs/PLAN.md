# ⚠️ 这是最初的设计方案,已被取代

**保留它只为记录当时的思路,不要照着做。** 实际实现与它有三处关键差异:

| 原方案 | 实际做法 | 为什么 |
|---|---|---|
| 手机上跑一个 HTTP 服务接收 IPA | 电脑直接 SSH 连过去(`decrypt/ios_dump.py`) | 手机有 root SSH,零部署更简单;原方案要在手机上装服务、传几百 MB 的包 |
| 解密只有「设备砸壳」一条路 | 现在是三选一:UnfairPlay 离线 → 越狱机凭证离线 → 设备砸壳 | 离线解密不需要设备,更快也更省事 |
| 手机端负责「安装 → 砸壳 → 打包 → 回传」 | 电脑端编排,手机只提供 SSH 与内存读取 | 手机上不需要任何脚本 |

当前实现的说明见仓库根目录的 `README.md`(尤其「八、砸壳」与「九、离线解密」)。

---

下面是一份**完整、可直接交给本地 AI 实现的技术方案**。目标是做出类似你截图的自建砸壳网站，配合你的 iPhone 11（iOS 26.0.1 越狱）使用。

---

# 自建 IPA 下载 + 砸壳系统 完整方案

## 1. 系统目标

- 网页端搜索 App Store 应用（支持国家、iOS/iPadOS）
- 支持「下载加密 IPA」和「下载解密 IPA」
- 本地文件库管理（已下载的加密/解密包）
- 解密流程依赖你自己的越狱 iPhone 11，不依赖任何第三方在线砸壳服务
- 电脑端作为主服务，手机端只负责砸壳

## 2. 整体架构

```
[浏览器前端]
      │
      ▼
[电脑后端 (FastAPI / Node)]
      │  1. 用 ipatool 下载加密 IPA
      │  2. 需要解密时 → 把 IPA 传到手机
      ▼
[iPhone 11 越狱机 (HTTP 服务)]
      │  接收 IPA → 安装 → frida dump → 打包解密 IPA → 返回
      ▼
[电脑后端] → 返回给前端下载
```

## 3. 技术选型

### 电脑端
- **后端**：Python 3.11+ + FastAPI（推荐）或 Node.js + Express
- **下载工具**：`ipatool`（必须）
- **前端**：Vue 3 + Vite 或纯 HTML + Tailwind（简单优先）
- **数据库**：SQLite（记录已下载文件）
- **文件存储**：本地 `./storage/encrypted/` 和 `./storage/decrypted/`

### 手机端（越狱）
- **服务**：Python（建议用 `http.server` + 简单脚本，或 Flask）
- **砸壳工具**：`frida-ios-dump` 或兼容 iOS 26 的 dump 脚本
- **依赖**：frida-server、AppSync Unified、OpenSSH、python3（越狱源可装）

## 4. 目录结构建议

```
ipa-manager/
├── backend/
│   ├── main.py                 # FastAPI 入口
│   ├── ipatool_wrapper.py      # 封装 ipatool 调用
│   ├── device_client.py        # 与手机通信
│   ├── models.py               # 数据库模型
│   ├── storage/                # 文件存储
│   │   ├── encrypted/
│   │   └── decrypted/
│   └── requirements.txt
├── frontend/
│   ├── index.html
│   ├── src/
│   │   ├── App.vue
│   │   ├── components/
│   │   │   ├── SearchBar.vue
│   │   │   ├── AppCard.vue
│   │   │   └── Library.vue
│   │   └── api.js
│   └── package.json
├── device/                     # 手机端代码
│   ├── server.py               # 手机 HTTP 服务
│   ├── dump.sh                 # 砸壳脚本
│   └── requirements.txt
└── README.md
```

## 5. 功能需求详细说明

### 5.1 前端界面（严格参考你的截图）

**顶部搜索区**
- 标题：「查找应用 / 从 App Store 开始」
- 切换：应用名称 / Bundle ID
- 选择：平台（iOS / iPadOS）、国家（美国、中国等）
- 搜索框 + 绿色「搜索」按钮

**文件库**
- 显示「已保存的 IPA」数量和总大小
- 每个条目显示：
  - 图标
  - 应用名
  - 平台 + 国家 + 版本
  - 状态：解密 / 加密 + 大小
  - Bundle ID
  - 下载时间
  - 三个按钮：下载解密、下载加密、删除

**交互**
- 搜索结果点击后可直接「下载加密」或「下载解密」
- 下载中显示进度
- 文件库支持刷新

### 5.2 后端 API 设计

```
GET  /api/search?q=xxx&country=US&entity=software
POST /api/download/encrypted
POST /api/download/decrypted
GET  /api/library
DELETE /api/library/{id}
GET  /api/download/file/{id}?type=encrypted|decrypted
GET  /api/device/status          # 检查手机是否在线
```

**关键接口说明**

- `/api/download/encrypted`  
  接收 bundleId 或 appId → 调用 ipatool 下载 → 存到 storage/encrypted → 写入数据库 → 返回文件信息

- `/api/download/decrypted`  
  1. 先确保有加密包（没有就先下载）
  2. 把加密 IPA 通过 HTTP/SCP 传到手机
  3. 调用手机端砸壳接口
  4. 等待手机返回解密 IPA
  5. 存到 storage/decrypted → 返回

### 5.3 手机端服务（核心）

手机上跑一个简单 HTTP 服务，监听例如 `0.0.0.0:8080`。

**接口：**
```
POST /dump
Content-Type: multipart/form-data
file: encrypted.ipa

返回：解密后的 IPA 文件流
```

**砸壳流程（dump.sh 或 Python 实现）：**
1. 接收加密 IPA，保存到 `/var/mobile/Documents/incoming/`
2. 使用 `appinst` 或 `ideviceinstaller` 安装（需要 AppSync）
3. 启动 App（用 `uiopen` 或 frida）
4. 调用 frida-ios-dump 或兼容脚本进行 dump
5. 把 dump 出来的 .app 重新打包成 IPA
6. 返回给电脑，并清理临时文件

**注意 iOS 26 适配：**
- 确认 frida 版本支持 iOS 26
- rootless 越狱路径不同（`/var/jb/`）
- 可能需要使用最新的 dump 工具或自己改脚本

## 6. 电脑与手机通信方式

推荐两种（按优先级）：

1. **HTTP（简单）**  
   手机开 HTTP 服务，电脑直接 POST 文件过去。  
   手机需和电脑在同一局域网，固定 IP（你截图里已有 192.168.6.211）。

2. **SSH + SCP（更稳定）**  
   电脑用 paramiko 或 subprocess 调用 `scp` + `ssh` 执行手机脚本。

建议先实现 HTTP 方式，简单快速。

## 7. 数据库设计（SQLite）

```sql
CREATE TABLE apps (
    id INTEGER PRIMARY KEY,
    name TEXT,
    bundle_id TEXT,
    version TEXT,
    country TEXT,
    platform TEXT,
    icon_url TEXT,
    encrypted_path TEXT,
    decrypted_path TEXT,
    encrypted_size INTEGER,
    decrypted_size INTEGER,
    created_at DATETIME,
    updated_at DATETIME
);
```

## 8. 实现优先级（给本地 AI 的顺序）

**第一阶段（先跑通加密下载）**
1. 安装并配置 ipatool（支持登录 Apple ID）
2. 写后端搜索 + 下载加密 IPA 接口
3. 写最简单的前端搜索 + 下载加密
4. 实现本地文件库列表

**第二阶段（手机砸壳）**
1. 在越狱手机上部署简单 HTTP 接收服务
2. 写砸壳脚本（先用已有 frida-ios-dump 测试单个 App）
3. 电脑端实现「下载解密」完整流程
4. 错误处理和超时（砸壳可能需要几分钟）

**第三阶段（完善）**
1. 前端做成和截图接近的 UI
2. 进度显示、断点续传、删除功能
3. 设备在线检测
4. 日志和异常处理

## 9. 关键依赖安装命令（参考）

**电脑：**
```bash
pip install fastapi uvicorn python-multipart aiofiles httpx
# 安装 ipatool（根据官方方式）
```

**手机（越狱源）：**
```bash
# 安装 frida、python3、AppSync Unified、OpenSSH 等
```

## 10. 风险与注意事项（必须写进 README）

- 仅限个人研究使用，不要公开提供解密服务
- Apple ID 安全：建议用小号，开启双重认证
- iOS 26 上砸壳工具可能需要额外适配，第一阶段先验证单个 App 能否成功 dump
- 手机存储有限，定期清理临时文件
- 网络传输 IPA 时注意局域网安全性

---
