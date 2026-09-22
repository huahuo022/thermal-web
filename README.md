# thermal-web

给热敏小票机用的自托管 Web 界面。**零第三方依赖**（只用 Python 3 标准库），
文本走打印机原生码页，图片在浏览器里抖动，所见即所打。

专为 58mm / 80mm ESC/POS 小票机设计，和 CUPS 那套"上传 PDF → Ghostscript
栅格化 → 送位图"的路线不同：**不经过 CUPS、不经过 Ghostscript**，直接把
ESC/POS 指令写到打印机，所以文字锐利、速度快、能用切纸和钱箱。

## 特性

- **文本打印** — 中文按打印机码页（GBK/GB18030/BIG5）输出，不需要服务器装字体；
  对齐、字号、加粗、下划线、反白、字体 A/B 可调
- **图片打印** — 浏览器端完成缩放、阈值/Floyd–Steinberg/Atkinson 抖动与裁剪，
  预览就是打印机烧出来的点阵，服务器不需要 Pillow
- **二维码 / 条码** — 原生 `GS ( k` 二维码与 `GS k` 条码（CODE128/EAN13/CODE39/UPC-A/ITF）
- **小票模板** — 区块式编辑器（文本 / 键值 / 表格行 / 分隔线 / 二维码 / 条码 /
  图片 / 进纸 / 切纸 / 钱箱），支持 JSON 导入导出，实时预览
- **打印历史** — 每次任务都存字节流，可一键重打
- **多种目标** — USB 设备节点 `device:/dev/usb/lp0`、网络打印机
  `socket:192.168.1.50:9100`、CUPS 队列 `cups:queue-name`，随时切换不用重启
- **试运行** — 不浪费纸：显示生成的字节数与 ESC/POS 十六进制

## 安装

```bash
git clone https://github.com/huahuo022/thermal-web.git
cd thermal-web
sudo ./install.sh          # 默认装到 /opt/thermal-web，监听 8080
```

手动启动（不装服务）：

```bash
python3 server.py --port 8080 --data ./data
```

打开 `http://<主机>:8080`。默认监听 `0.0.0.0`，在设置页把"打印目标"改成你的设备。

### 登录与访问密码

启用后，未登录的浏览器会被重定向到 `/login` 登录页（与主界面同一套视觉），
登录成功签发一个 HMAC 签名的会话 Cookie（`HttpOnly` + `SameSite=Lax`，默认 7 天）。

密码放在**仓库之外**的 `/etc/thermal-web.env`（权限 600），这样 `install.sh` 每次
重新生成 unit 文件时都不会把它冲掉：

```bash
sudo tee /etc/thermal-web.env >/dev/null <<'EOF'
THERMAL_WEB_USER=admin
THERMAL_WEB_PASSWORD=换成一个只有你知道的密码
EOF
sudo chmod 600 /etc/thermal-web.env
sudo systemctl restart thermal-web
```

`install.sh` 首次运行会自动创建这个文件（带注释的模板）。把密码行注释掉再重启即可
关闭认证；unit 里的 `EnvironmentFile=-/etc/thermal-web.env` 前面那个 `-` 表示文件
不存在也不报错。

不想让明文密码留在磁盘上，就改用哈希（哈希存在时优先于明文）：

```bash
printf 'THERMAL_WEB_USER=admin\nTHERMAL_WEB_PASSWORD_SHA256=%s\n' \
  "$(printf '%s' '你的密码' | sha256sum | cut -d' ' -f1)" | sudo tee /etc/thermal-web.env
sudo chmod 600 /etc/thermal-web.env
sudo systemctl restart thermal-web
```

其他认证相关变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `THERMAL_WEB_SESSION_HOURS` | `168` | 会话有效期（小时） |
| `THERMAL_WEB_COOKIE_SECURE` | `0` | 置 `1` 时**强制**给 Cookie 加 `Secure` |

**反代 + HTTPS 的说明**：服务会读取 `X-Forwarded-Proto`（以及 Cloudflare 的
`CF-Visitor`、标准的 `Forwarded`）来自动判断客户端用的是不是 HTTPS，是的话就给会话
Cookie 加 `Secure`，否则不加。所以同一个实例可以同时被 `http://内网IP:8080` 和
`https://域名/login` 访问，两条路都能正常登录。如果你的反代没转发这些头，再手动把
`THERMAL_WEB_COOKIE_SECURE` 设为 `1`（代价是纯 HTTP 访问会登录不上）。

连续输错 5 次密码会临时限流（5 分钟窗口，按来源 IP 计）。所有 API 仍然接受
HTTP Basic，方便脚本调用（`curl -u admin:密码 ...`）；服务器不会再返回
`WWW-Authenticate`，所以浏览器不会弹出它自己的那套原生登录框。

### 把服务器本身当工作副本

如果打印服务器就是你要改代码的地方（用仓库的 deploy key 推送）：

```bash
cd /root/thermal-web
vim escpos.py                       # 改代码
git commit -am "fix: ..." && git push
sudo ./deploy.sh                    # 拉取 + 重新部署 + 重启服务
```

`deploy.sh` 只做两件事：`git pull --ff-only`，然后调用 `install.sh`。

### 推荐的开发流程：本地改 → 推送 → 让服务器自己更新

> 完整的「开发 → 发布 → 通过网页验收」步骤写在 [WORKFLOW.md](WORKFLOW.md)。

**不要再 ssh 到服务器改代码**。服务器上的 `/root/thermal-web` 现在只是一个
**只读的拉取副本**——在里面改东西会让 `merge --ff-only` 失败，更新就会卡住。

```bash
# 1) 本地（例如 E:\code\thermal-web）改代码并推送
git add -A && git commit -m "feat: xxx"
git push

# 2) 让服务器自己拉取 + 重新部署 + 重启
curl -u admin:你的密码 -X POST https://thermal-web.example.com:8080/api/update/apply \
     -H 'Content-Type: application/json' -d '{}'

# 3) 查状态（GET 只检查，不会更新）
curl -u admin:你的密码 'https://thermal-web.example.com:8080/api/update?fetch=1'
```

Windows 上用 `tools/deploy.ps1` 可以把这三步合成一条命令（推送完会自动等服务器重启
并核对提交是否一致）：

```powershell
$env:THERMAL_WEB_PASSWORD = "你的密码"
.\tools\deploy.ps1 -Message "fix: 修正小票预览对齐"
```

### 本地调试（Windows 为例）

`E:\code\thermal-web` 下已经建好虚拟环境 `.venv`（Python 3.11）：

```powershell
cd E:\code\thermal-web

# 可选依赖：运行本身是纯标准库，不装也能跑
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

# 起一份本地实例（避开 8080，免得和别的程序撞）
.\.venv\Scripts\python.exe server.py --port 8099 --data .\data
# 然后打开 http://127.0.0.1:8099
```

几个本地调试要点：

- 本地**不要**设 `THERMAL_WEB_PASSWORD`，这样不用登录，改界面刷新即可。
- **没有打印机也能调**：默认目标 `device:/dev/usb/lp0` 在 Windows 上不存在，状态栏会显示
  "不可用"，但页面照常渲染；要确认生成的指令对不对，点「试运行（不打印）」看字节。
- **想让打印真的落到文件**（推荐，方便核对 ESC/POS 原始字节）：
  ```powershell
  New-Item -ItemType File .\out.bin -Force
  # 设置页把「打印目标」改成 device:out.bin，然后正常点「打印」
  ```
  打完 `out.bin` 里就是完整的指令流，可以用十六进制工具或
  `Format-Hex .\out.bin` 看。（`device:` 目标要求文件先存在，这是故意的。）
- 要连真实打印机：目标改成 `socket:<打印机IP>:9100`，或者直接用服务器上的正式实例。
- 打印相关改动请**先在本地用"试运行 / 文件目标"确认字节，再往真机上打**。

### 应急部署（服务器连不上 GitHub 时）

正常流程是服务器自己去 GitHub 拉代码。如果服务器的网络出问题（例如到 GitHub 的
443 / 22 都不通），就用这个脚本从本机经 SSH 直接把代码推上去：

```powershell
cd E:\code\thermal-web
python tools\emergency-deploy.py             # 默认 ssh 别名 rk3318
python tools\emergency-deploy.py --dry-run   # 只打印计划
python tools\emergency-deploy.py --ssh user@host --port 42222 -i ~\.ssh\rk3318
```

它的动作是：备份服务器上现有的 `/opt/thermal-web`（排除 `data/`）→ 把工作副本打成
tar 流式传到服务器 `/tmp` → 用仓库自己的 `install.sh` 安装并重启 → 逐个文件比对
sha256 并打印 `/api/status`；最后给出回滚命令。

两个设计取舍值得知道：

- **不修改服务器上的 git 工作副本**（`/root/thermal-web`）。往里写文件会让工作区变脏，
  之后 `merge --ff-only` 必然失败，正常更新路径就废了。所以文件解到 `/tmp` 的临时目录，
  再用那个目录里的 `install.sh` 安装。
- **复用 `install.sh`**，这样应急路径部署的文件集合（`*.py` + `web/`）与正常路径完全一致。

⚠️ 应急部署**不会**把代码送进 GitHub。网络恢复后请照常 `commit + push`，否则下一次
正常更新会把服务器覆盖回 GitHub 上的旧版本。

推送凭据建议用 **deploy key**（仓库级、可写），不要用长期 token：把公钥加到
仓库的 Settings → Deploy keys，勾上 Allow write access，然后让本地仓库用 SSH remote
并只对这个仓库指定密钥：

```bash
git remote set-url origin git@github.com:huahuo022/thermal-web.git
git config core.sshCommand "ssh -i ~/.ssh/thermal-web -o IdentitiesOnly=yes"
```

### 版本更新（设置页里一键拉取并重启）

设置页底部有「版本更新」区块，不用登录服务器就能更新：

1. **git 工作副本路径**（默认 `/root/thermal-web`）必须是**带 `.git` 的目录**，
   运行目录 `/opt/thermal-web` 只是它的一份副本。
2. 点**检查更新**：服务会在该目录执行 `git fetch`，对比本地 `HEAD` 与
   `origin/<分支>`，显示两边的最新提交、落后/领先几个提交、工作区是否干净。
3. 点**一键更新并重启**：后台执行 `git pull --ff-only && ./install.sh <运行目录>`，
   服务重启后页面会自己刷新。

几个实现细节，都是踩过的坑：

- **更新进程跑在独立 cgroup 里**（`systemd-run` 起一个瞬时单元，失败时退回 `setsid`）。
  因为 `install.sh` 会重启本服务，而 systemd 会清理该服务 cgroup 内的所有进程——
  只换会话（`setsid`）不够，更新进程会被连带杀掉，日志写不完、部署可能被腰斩。
- 拉取固定用 `--ff-only`：工作副本有未提交改动时不会硬合并，界面会提前把 `dirty`
  标出来并禁用按钮。
- 更新进程的全部输出写到 `<数据目录>/update.log`，界面里可直接展开查看。
- `/api/update` 与 `/api/update/apply` 都需要登录；`apply` 会以服务身份执行
  git 与 `install.sh`（所以服务本身以 root 运行）。
- 该目录必须能**免交互**访问远端：本项目在服务器上用的是 deploy key + SSH remote
  （注意 GitHub 的 HTTPS git 端点在部分网络下不可用，改用 SSH 即可）。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `THERMAL_WEB_PORT` | `8080` | 监听端口 |
| `THERMAL_WEB_HOST` | `0.0.0.0` | 监听地址 |
| `THERMAL_WEB_DATA` | `./data` | 数据库目录（设置与历史） |
| `THERMAL_WEB_PASSWORD` | 空 | 非空则启用 Basic 认证 |
| `THERMAL_WEB_USER` | `admin` | Basic 认证用户名 |

## HTTP API

所有接口都是 JSON，`POST /api/print` 是唯一的打印入口，加 `"dry_run": true`
只生成字节不打印。

```bash
# 打印文本
curl -s localhost:8080/api/print -H 'Content-Type: application/json' -d '{
  "kind": "text", "text": "你好\n金额：12.00", "align": "center", "size": "double"
}'

# 打印二维码
curl -s localhost:8080/api/print -H 'Content-Type: application/json' \
  -d '{"kind":"qr","data":"https://example.com","module":6,"ecc":"M"}'

# 打印一单小票（模板由区块组成）
curl -s localhost:8080/api/print -H 'Content-Type: application/json' -d '{
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

常用接口：`GET /api/status`、`GET|POST /api/settings`、`GET /api/history`、
`POST /api/reprint/{id}`、`POST /api/test`、`POST /api/preview`。

## 支持的元素

| type | 关键字段 |
|---|---|
| `text` | `text, align, size, bold, underline, invert, font` |
| `kv` | `key, value, bold` |
| `row` | `cells[], widths[], aligns[], bold` |
| `divider` | `char` |
| `qr` | `data, module, ecc, align` |
| `barcode` | `data, symbology, height, width, hri, font` |
| `image` | `bitmap`(base64 的 1bit 位图), `width`, `height` |
| `feed` / `feed_dots` | `lines` / `dots` |
| `cut` | `mode`: `partial` / `full` / `none` |
| `drawer` | `pin`: 2 或 5 |
| `beep` | `times`, `duration` |

## 排障

| 现象 | 处理 |
|---|---|
| 打印出来右边被截断 | 设置里把纸宽改成 **384 点**（58mm 机） |
| 中文乱码 | 试 `GB18030`；部分机型需要关掉"中文模式指令"或改 `BIG5` |
| 完全没反应 | `GET /api/status` 看 `printer.available`；检查 `/dev/usb/lp0` 权限（服务以 root 运行） |
| 不出纸但历史显示 sent | 打印机自检；部分机型不支持 `GS V` 切纸，改"不切"试试 |
| 图片一片黑/一片白 | 调阈值滑杆，文字用"阈值"、照片用 Floyd–Steinberg |
| 队列模式 | 目标填 `cups:队列名`，会通过 `lp -o raw` 交给 CUPS（可复用已有的队列/历史） |

## 设计说明

- **为什么不用 CUPS/Ghostscript**：票据是"指令流"不是"页面"。走 CUPS 要把
  PDF 栅格化成位图，慢、费纸（一张 A4 能出 80cm 纸）、文字不如打印机内置点阵
  字体锐利，而且拿不到切纸/钱箱/码页这些原生命令。
- **为什么浏览器端抖动**：服务器上可能没有 Pillow、更没有图像库，浏览器天生
  有 canvas；把 1bit 点阵直接发给后端，**预览与打印结果逐位一致**。
- **为什么文本不做成图片**：原生码页走的是打印机自带点阵字体，比服务器渲染的
  位图清晰得多，也省去了字体依赖（服务器连中文字体都不用装）。

## 协议

MIT
