# thermal-web

给热敏小票机用的自托管 Web 界面。**零第三方依赖**（只用 Python 3 标准库），
文本走打印机原生码页，图片在浏览器里抖动，**预览即所打**。

专为 58mm / 80mm ESC/POS 小票机设计。和 CUPS 那套「上传文件 → Ghostscript
栅格化 → 送位图」不同：**不经过 CUPS、不经过 Ghostscript**，直接把 ESC/POS
指令写到打印机，所以文字锐利、速度快，而且能用切纸、钱箱、码页这些原生命令。

- 服务器端只有 6 个 Python 文件，没有第三方依赖、没有构建产物
- 文本走打印机自带点阵字体，服务器**不需要装中文字体**
- 图片在浏览器里抖动，1 bit 点阵直传，服务器**不需要 Pillow**
- 自带登录页、打印历史、试运行（不浪费纸）、一键从 GitHub 更新

## 特性

| 功能 | 说明 |
|---|---|
| 文本打印 | 中文按打印机码页输出（GBK / GB18030 / BIG5 / CP437），对齐、字号、加粗、下划线、反白、字体 A·B 可调 |
| 图片打印 | 浏览器端缩放 + 阈值 / Floyd–Steinberg / Atkinson 抖动 + 裁白边，预览逐位等于打印结果 |
| 二维码 / 条码 | 原生 `GS ( k` 二维码；`GS k` 条码（CODE128 / EAN13 / CODE39 / UPC-A / ITF 等） |
| 小票模板 | 区块式编辑器（文本、键值、表格行、分隔线、二维码、条码、图片、进纸、切纸、钱箱），JSON 导入导出 + 实时预览 |
| 打印历史 | 每次任务连字节流一起存，可一键重打 |
| 试运行 | 只生成字节不打印，界面直接显示字节数与 ESC/POS 十六进制 |
| 多种目标 | `device:/dev/usb/lp0`（USB）、`socket:IP:9100`（网口）、`cups:队列名`（交给 CUPS），随时切换不用重启 |
| 登录保护 | `/login` 登录页 + HMAC 签名会话 Cookie；API 同时接受 HTTP Basic 供脚本调用 |
| 一键更新 | 设置页里检查 GitHub 有没有新提交，并一键拉取 + 重启 |
| 应急部署 | 服务器连不上 GitHub 时，用 `tools/emergency-deploy.py` 从本机经 SSH 直推 |

## 快速开始

### 在服务器上安装

```bash
git clone https://github.com/huahuo022/thermal-web.git
cd thermal-web
sudo ./install.sh            # 装到 /opt/thermal-web，监听 8080，开机自启
```

只想跑一下、不装服务：

```bash
python3 server.py --port 8080 --data ./data
```

打开 `http://<主机>:8080`，在**设置**页把「打印目标」改成你的设备。

`install.sh` 做三件事：把 `*.py` 与 `web/` 复制到目标目录 → 用模板重写
`/etc/systemd/system/thermal-web.service` → `systemctl enable --now`。
它**不会碰** `/etc/thermal-web.env`，所以认证配置在重新部署时不会丢。

### 登录与访问密码

启用后，未登录的浏览器会被重定向到 `/login`（与主界面同一套视觉），登录成功签发
HMAC 签名的会话 Cookie（`HttpOnly` + `SameSite=Lax`，默认 7 天）。

密码放**仓库之外**的 `/etc/thermal-web.env`（权限 600）：

```bash
sudo tee /etc/thermal-web.env >/dev/null <<'EOF'
THERMAL_WEB_USER=admin
THERMAL_WEB_PASSWORD=换成只有你知道的密码
EOF
sudo chmod 600 /etc/thermal-web.env
sudo systemctl restart thermal-web
```

不想让明文留在磁盘上，就用哈希（哈希存在时优先于明文）：

```bash
printf 'THERMAL_WEB_USER=admin\nTHERMAL_WEB_PASSWORD_SHA256=%s\n' \
  "$(printf '%s' '你的密码' | sha256sum | cut -d' ' -f1)" | sudo tee /etc/thermal-web.env
sudo chmod 600 /etc/thermal-web.env && sudo systemctl restart thermal-web
```

把密码行注释掉再重启即可关闭认证（unit 里 `EnvironmentFile=-/etc/thermal-web.env`
前面那个 `-` 表示文件不存在也不报错）。

行为细节：

- 连续输错 5 次会限流（5 分钟窗口，按来源 IP 计）
- API 仍接受 HTTP Basic，方便脚本：`curl -u admin:密码 ...`；服务器**不返回**
  `WWW-Authenticate`，所以浏览器永远不会弹出它自己那套原生登录框
- 反代 + HTTPS：服务会读 `X-Forwarded-Proto`（以及 Cloudflare 的 `CF-Visitor`、
  标准的 `Forwarded`）判断客户端是否走 HTTPS，是就给 Cookie 加 `Secure`。
  所以同一个实例可以同时被 `http://内网IP:8080` 和 `https://域名/login` 访问。
  反代没转发这些头时，手动设 `THERMAL_WEB_COOKIE_SECURE=1`（代价是纯 HTTP 访问会登录不上）

### 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `THERMAL_WEB_PORT` | `8080` | 监听端口 |
| `THERMAL_WEB_HOST` | `0.0.0.0` | 监听地址 |
| `THERMAL_WEB_DATA` | `./data` | 数据目录（设置、历史、会话密钥、更新日志） |
| `THERMAL_WEB_USER` | `admin` | 登录用户名 |
| `THERMAL_WEB_PASSWORD` | 空 | 登录密码；留空则不启用认证 |
| `THERMAL_WEB_PASSWORD_SHA256` | 空 | 用 sha256 十六进制代替明文（优先级更高） |
| `THERMAL_WEB_SESSION_HOURS` | `168` | 会话有效期（小时） |
| `THERMAL_WEB_COOKIE_SECURE` | `0` | 置 `1` 时强制给 Cookie 加 `Secure` |

## 使用

### 界面

| 页签 | 用途 |
|---|---|
| 文本 | 多行文本，配合对齐 / 字号 / 字体 / 下划线 / 加粗 / 反白 |
| 图片 | 拖入图片 → 选抖动算法与阈值 → 预览 → 打印 |
| 二维码 / 条码 | 二维码（模块大小、纠错级别）与各类条码（条高、条宽、HRI 位置） |
| 小票 | 区块式模板编辑，可新增 / 删除 / 上下移动区块，JSON 导入导出 |
| 历史 | 打印记录，一键重打、清空 |
| 设置 | 打印目标、纸宽、编码、切纸、中文模式、版本更新、打印自检页 |

右侧常驻**预览**：文本与小票按打印机列宽排版，图片直接按真实点阵渲染，
底部虚线表示切纸位置。**试运行**不打印，只显示字节数与 ESC/POS 十六进制。

### HTTP API

所有接口都是 JSON。`POST /api/print` 是唯一的打印入口，加 `"dry_run": true`
只生成字节不打印。**除 `/api/login` 外都需要登录**（会话 Cookie 或 Basic）。

```bash
# 打印文本
curl -s -u admin:密码 localhost:8080/api/print -H 'Content-Type: application/json' -d '{
  "kind": "text", "text": "你好\n金额：12.00", "align": "center", "size": "double"
}'

# 打印二维码
curl -s -u admin:密码 localhost:8080/api/print -H 'Content-Type: application/json' \
  -d '{"kind":"qr","data":"https://example.com","module":6,"ecc":"M"}'

# 打印一单小票（区块模板）
curl -s -u admin:密码 localhost:8080/api/print -H 'Content-Type: application/json' -d '{
  "kind": "receipt",
  "blocks": [
    {"type":"text","text":"某某餐厅","align":"center","size":"double","bold":true},
    {"type":"divider"},
    {"type":"kv","key":"桌号","value":"A12"},
    {"type":"row","cells":["宫保鸡丁","1","38.00"],"widths":[28,6,12],
     "aligns":["left","center","right"]},
    {"type":"kv","key":"合计","value":"¥ 38.00","bold":true},
    {"type":"qr","data":"https://example.com/order/1234"},
    {"type":"cut"}
  ]
}'
```

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/status` | GET | 版本、启动时间、打印目标是否可用 |
| `/api/settings` | GET / POST | 读写设置（打印目标、纸宽、编码、切纸、git 副本路径…） |
| `/api/print` | POST | 打印，`kind` 可取 `text` / `qr` / `barcode` / `bitmap` / `image-file` / `receipt` |
| `/api/preview` | POST | 等同 print + `dry_run`，返回字节数与十六进制 |
| `/api/test` | POST | 打印自检页 |
| `/api/history` | GET | 打印历史；清空用 `POST /api/history/clear` |
| `/api/reprint/{id}` | POST | 重打某条历史（存下来的字节流原样重发） |
| `/api/login`、`/api/logout` | POST | 登录 / 退出 |
| `/api/update` | GET | **只检查**更新（`?fetch=0` 不联网，`?fetch=1` 会 fetch） |
| `/api/update/apply` | POST | 拉取 + 重新部署 + 重启服务 |

### 支持的元素

| type | 关键字段 |
|---|---|
| `text` | `text, align, size, bold, underline, invert, font` |
| `kv` | `key, value, bold` |
| `row` | `cells[], widths[], aligns[], bold` |
| `divider` | `char` |
| `qr` | `data, module, ecc, align` |
| `barcode` | `data, symbology, height, width, hri, font` |
| `image` | `bitmap`（base64 的 1 bit 位图）, `width`, `height` |
| `feed` / `feed_dots` | `lines` / `dots` |
| `cut` | `mode`：`partial` / `full` / `none` |
| `drawer` | `pin`：2 或 5 |
| `beep` | `times`, `duration` |

## 本地开发与发布

> 完整的「开发 → 发布 → 通过网页验收」步骤写在 [WORKFLOW.md](WORKFLOW.md)。

### 本地调试（Windows 为例）

仓库下的 `.venv` 就是为本机调试准备的（Python 3.11）：

```powershell
cd E:\code\thermal-web

# 可选依赖：运行本身是纯标准库，不装也能跑
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

# 起一份本地实例（避开 8080，免得和别的程序撞）
.\.venv\Scripts\python.exe server.py --port 8099 --data .\data
# 然后打开 http://127.0.0.1:8099
```

几个要点：

- 本地**不要**设 `THERMAL_WEB_PASSWORD`，这样不用登录，改界面刷新即可
- **没有打印机也能调**：默认目标 `device:/dev/usb/lp0` 在 Windows 上不存在，状态栏会
  显示"不可用"，但页面照常渲染；要确认生成的指令对不对，点「试运行（不打印）」看字节
- **想让打印落到文件**（推荐，方便核对 ESC/POS 原始字节）：

  ```powershell
  New-Item -ItemType File .\out.bin -Force
  # 设置页把「打印目标」改成 device:out.bin，然后正常点「打印」
  Format-Hex .\out.bin
  ```

  （`device:` / `file:` 目标要求文件先存在，这是故意的，避免误建文件）
- 要连真实打印机：目标改成 `socket:<打印机IP>:9100`，或直接用服务器上的正式实例
- 打印相关改动请**先在本地用「试运行 / 文件目标」确认字节，再往真机上打**

### 发布流程：本地改 → 推送 → 让服务器自己更新

**服务器上的 git 副本是只读的**：它只负责从 GitHub 拉取。在里面直接改文件会让工作区
变脏，`merge --ff-only` 就会失败、更新卡住（设置页会标出 `dirty` 并禁用按钮）。

```bash
# 1) 本地改完并推送
git add -A && git commit -m "feat: xxx" && git push

# 2) 让服务器自己拉取 + 重新部署 + 重启
curl -u admin:密码 -X POST https://<你的地址>/api/update/apply \
     -H 'Content-Type: application/json' -d '{}'

# 3) 查状态（GET 只检查，不会更新）
curl -u admin:密码 'https://<你的地址>/api/update?fetch=1'
```

Windows 上 `tools/deploy.ps1` 能把这几步合成一条命令（推送后会等服务器重启并核对提交
是否一致才算成功）：

```powershell
$env:THERMAL_WEB_PASSWORD = "你的密码"
.\tools\deploy.ps1 -Message "fix: 修正小票预览对齐"
```

推送凭据：本机用 GitHub 账号凭据（HTTPS，交给 Git Credential Manager 记住）即可；
想走 SSH 就在仓库里指定密钥
`git config core.sshCommand "ssh -i ~/.ssh/xxx -o IdentitiesOnly=yes"`。
注意**服务器那台必须用 SSH**——它的 HTTPS git 端点在部分网络下不通。

### 设置页里的「版本更新」

设置页底部有「版本更新」区块，不用登录服务器就能更新：

1. **git 工作副本路径**（默认 `/root/thermal-web`）必须是**带 `.git` 的目录**；
   运行目录 `/opt/thermal-web` 只是它的一份副本
2. 点**检查更新**：在该目录执行 `git fetch`，对比本地 `HEAD` 与 `origin/<分支>`，
   显示两边最新提交、落后/领先几个提交、工作区是否干净
3. 点**一键更新并重启**：后台执行 `fetch + merge --ff-only + ./install.sh <运行目录>`，
   服务重启后页面会自己刷新

几个踩过的坑：

- **更新进程跑在独立 cgroup 里**（`systemd-run` 起瞬时单元，失败时退回 `setsid`）。
  `install.sh` 会重启本服务，而 systemd 会清理该服务 cgroup 内的所有进程——只换会话
  不够，更新进程会被连带杀掉，日志写不完、部署可能被腰斩
- 拉取固定 `--ff-only`，工作副本有未提交改动时不会硬合并，界面提前标 `dirty`
- 更新进程的全部输出写到 `<数据目录>/update.log`，界面里可展开查看
- 该目录必须能**免交互**访问远端（服务器上用 deploy key + SSH remote）

### 应急部署（服务器连不上 GitHub 时）

正常流程依赖服务器自己去 GitHub 拉代码。如果服务器网络出问题（例如到 GitHub 的
443 / 22 都不通），用这个脚本从本机经 SSH 直接把代码推上去：

```powershell
cd E:\code\thermal-web
python tools\emergency-deploy.py             # 默认 ssh 别名 rk3318
python tools\emergency-deploy.py --dry-run   # 只打印计划
python tools\emergency-deploy.py --ssh user@host --port 42222 -i ~\.ssh\rk3318
```

它依次做：备份服务器上的 `/opt/thermal-web`（排除 `data/`）→ 把工作副本打成 tar
流式传到服务器 `/tmp` → 用仓库自己的 `install.sh` 安装并重启 → 逐个文件比对 sha256、
核对服务报告的版本 → 打印回滚命令。

两个设计取舍：

- **不修改服务器上的 git 工作副本**（`/root/thermal-web`）。往里写文件会让工作区变脏，
  之后 `merge --ff-only` 必然失败，正常更新路径就废了。所以文件解到 `/tmp` 的临时目录，
  再用那里的 `install.sh` 安装
- **复用 `install.sh`**，保证应急路径部署的文件集合（`*.py` + `web/`）与正常路径完全一致

⚠️ 应急部署**不会**把代码送进 GitHub。网络恢复后请照常 `commit + push`，
否则下一次正常更新会把服务器覆盖回远端旧版本。

### 目录结构

```
thermal-web/
├── server.py                  HTTP 服务、路由、会话校验、API
├── auth.py                    登录、HMAC 签名会话 Cookie、失败限流
├── update.py                  检查 / 执行从 git 更新（独立 cgroup）
├── escpos.py                  ESC/POS 指令生成（文本 / 位图 / 二维码 / 条码）
├── transport.py               把字节送到 device / socket / cups
├── store.py                   SQLite：设置 + 打印历史
├── web/                       前端（原生 HTML/CSS/JS，无构建步骤）
├── systemd/thermal-web.service  unit 模板
├── tools/deploy.ps1           本地：推送 + 触发更新 + 等重启
├── tools/emergency-deploy.py  本地：服务器连不上 GitHub 时直推
├── install.sh                 安装 / 部署到目标目录
├── deploy.sh                  仓库内更新并重新部署
└── WORKFLOW.md                开发 → 发布 → 验收的完整流程
```

## 排障

| 现象 | 处理 |
|---|---|
| 打印出来右边被截断 | 设置里把纸宽改成 **384 点**（58mm 机） |
| 中文乱码 | 试 `GB18030`；部分机型需要关掉「中文模式指令」或改 `BIG5` |
| 完全没反应 | `GET /api/status` 看 `printer.available`；检查 `/dev/usb/lp0` 权限（服务以 root 运行） |
| 不出纸但历史显示 sent | 打印机自检；部分机型不支持 `GS V` 切纸，改「不切」试试 |
| 图片一片黑 / 一片白 | 调阈值滑杆；文字用「阈值」，照片用 Floyd–Steinberg |
| 想复用已有 CUPS 队列 | 目标填 `cups:队列名`，会通过 `lp -o raw` 交给 CUPS |
| 更新时提示 `dirty` | 服务器副本被改过：`ssh` 进去 `git -C /root/thermal-web status`，用 `git stash` 或 `git checkout -- <文件>` 清理后再更新 |
| 更新等不到重启 | 看服务器上 `/opt/thermal-web/data/update.log` 最后几行 |
| 更新后页面没变化 | 强刷（Ctrl+F5）；再确认设置页里的提交哈希是否已变 |

## 设计说明

- **为什么不用 CUPS / Ghostscript**：票据是"指令流"不是"页面"。走 CUPS 要把 PDF
  栅格化成位图，慢、费纸（一张 A4 能出 80cm 纸）、文字不如打印机内置点阵字体锐利，
  而且拿不到切纸、钱箱、码页这些原生命令
- **为什么在浏览器端抖动**：服务器上可能没有 Pillow、更没有图像库，浏览器天生有
  canvas；把 1 bit 点阵直接发给后端，**预览与打印结果逐位一致**
- **为什么文本不做成图片**：原生码页走的是打印机自带点阵字体，比服务器渲染的位图
  清晰得多，也省掉了字体依赖（服务器连中文字体都不用装）

## 协议

MIT
