# thermal-web 开发与发布流程

> 一句话：**在本地改代码 → 推送到 GitHub → 让服务器自己拉取更新 → 打开
> <https://thermal-web.example.com:8080> 验收。**

不再 ssh 到服务器直接改代码。

## 0. 环境（一次性，已经配好）

| 项目 | 值 |
|---|---|
| 本地工作副本 | `E:\code\thermal-web` |
| 远端仓库 | <https://github.com/huahuo022/thermal-web> |
| 验收地址 | <https://thermal-web.example.com:8080> |
| 登录 | 用户名 `admin`，密码在服务器 `/etc/thermal-web.env` |
| 服务器拉取副本 | `/root/thermal-web`（**只读，不要在上面改代码**） |
| 服务器运行目录 | `/opt/thermal-web`（由 `install.sh` 覆盖，只同步 `*.py` 和 `web/`） |

推送凭据已存在 Windows 凭据管理器（`git:https://github.com`，GitHub 账号登录），
正常情况下不会再提示输入。若哪天又提示登录，用浏览器完成一次授权即可，之后长期有效。

## 1. 改代码

只在 `E:\code\thermal-web` 里改。**不要** ssh 到服务器改 `/root/thermal-web`：
那会让服务器工作区变脏，`merge --ff-only` 会失败，更新就卡住了。

## 2. 提交并推送

```powershell
cd E:\code\thermal-web
git status                     # 确认改了什么
git add -A
git commit -m "feat: 说明这次改了什么"
git push
```

## 3. 让服务器自己拉取并更新

### 方式 A：一条命令（推荐）

```powershell
$env:THERMAL_WEB_PASSWORD = "<服务器密码>"   # 当前 PowerShell 窗口设置一次即可
cd E:\code\thermal-web
.\tools\deploy.ps1 -Message "feat: 说明这次改了什么"
```

`deploy.ps1` 会依次做四件事，并且**最后会核对服务器上的提交与刚推送的一致**才算成功：

1. 有改动就提交（`-Message` 是提交信息）
2. `git push`
3. `POST /api/update/apply`，让服务器去 `fetch + merge --ff-only + install.sh`
4. 轮询 `/api/update?fetch=0`，直到服务器的提交哈希与本地一致（或超时报错）

如果第 2 步已经手动做过，直接跑 `.\tools\deploy.ps1`（不带 `-Message`）也可以，
它只会触发第 3、4 步。

### 方式 B：手动两步

```powershell
# 3.1 触发更新
$auth = "Basic " + [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("admin:<服务器密码>"))
Invoke-RestMethod -Method Post -Uri "https://thermal-web.example.com:8080/api/update/apply" `
  -Headers @{Authorization = $auth} -ContentType "application/json" -Body "{}"

# 3.2 看服务器现在跑的是哪个提交（fetch=1 会联网检查，约 4~5 秒）
Invoke-RestMethod -Uri "https://thermal-web.example.com:8080/api/update?fetch=1" -Headers @{Authorization = $auth}
```

也可以直接在浏览器里点：**设置页最下方 → 检查更新 / 一键更新并重启**。

### 方式 C：应急——服务器连不上 GitHub

如果服务器的网络出问题（到 GitHub 的 443 / 22 都不通），正常路径走不了，改用本地直推：

```powershell
cd E:\code\thermal-web
python tools\emergency-deploy.py
```

它会把当前工作副本经 SSH 传到服务器，走仓库自己的 `install.sh` 安装并重启，并在最后
逐个文件核对 sha256。细节见 README 的「应急部署」一节。

⚠️ 这条路**不经过 GitHub**。网络恢复后要把同样的改动正常 `commit + push` 一次，
否则下一次正常更新会把服务器上的代码覆盖回远端旧版本。

### 相关接口

| 方法与路径 | 作用 |
|---|---|
| `GET /api/update?fetch=1` | **只检查**：本地/远端提交、落后几个提交、工作区是否干净 |
| `GET /api/update?fetch=0` | 只看本地状态、不联网（`deploy.ps1` 用它轮询） |
| `POST /api/update/apply` | **真正更新**：后台 `fetch + merge --ff-only + install.sh`，服务会重启 |
| `GET /api/status` | 版本号、`started_at`（重启后会变）、打印机是否就绪 |

注意 `GET /api/update` 不会更新，它只是"看看有没有新东西"，要更新必须用 `POST`。

## 4. 打开 https://thermal-web.example.com:8080 验收

1. **登录**：未登录会跳到 `/login`。
2. **刷新页面**：静态资源带 `Cache-Control: no-store`，普通刷新即可；若界面没变再
   `Ctrl+F5` 强刷一次。
3. **确认版本/提交**：标题栏右侧显示 `vX.Y.Z`；设置页「版本更新」里的
   `本地/远端提交` 哈希应与刚推送的一致。
4. **按改动类型验收**：
   - **界面改动**：六个页签（文本 / 图片 / 二维码·条码 / 小票 / 历史 / 设置）逐个点一遍，
     看布局、控件状态与交互
   - **打印相关**：先点「试运行（不打印）」核对字节数与是否报错，确认无误再真打；
     需要整机自检时点设置页的「打印自检页」
   - **接口改动**：直接打接口，例如
     `curl -u admin:<密码> https://thermal-web.example.com:8080/api/status`
5. **确认服务真重启完成**：`/api/status` 正常返回且 `started_at` 是新值。

## 5. 出问题了怎么办

| 现象 | 排查 |
|---|---|
| `/api/update` 里 `dirty: true` | 服务器副本被改过：`ssh` 进去 `git -C /root/thermal-web status`，用 `git stash` 或 `git checkout -- <文件>` 清理后再更新 |
| 报 `not a git working copy` | 设置页「git 工作副本路径」填错了，应为 `/root/thermal-web` |
| 一直等不到重启 | 看服务器上 `/opt/thermal-web/data/update.log` 最后几行，pull / install 的全部输出都在里面 |
| 更新成功但页面没变化 | 先强刷；再确认设置页里的提交哈希是否已经变成新值 |
| 想回滚 | 本地 `git revert <commit>` 后 push，再走一次同一套更新流程 |
| 推送时又要登录 | 完成一次 GCM 浏览器授权即可；或改用 `git-credential-manager github login` |

## 6. 几条硬规则

- 服务器上的 `/root/thermal-web` 是**只读拉取副本**，永远不在上面改代码。
- **不要把密码写进仓库里的任何文件**（本文档用 `<服务器密码>` 占位）。
- 部署目录 `/opt/thermal-web` 只同步 `*.py` 和 `web/`，`tools/` 与文档不会同步过去；
  所以 `tools/deploy.ps1` 只是本地工具。
- 服务器访问 GitHub 的 **HTTPS git 端点不通**（443 超时），它自己的 remote 必须保持 SSH
  （已配 deploy key）；本地推送仍走 HTTPS 账号凭据，两者互不影响。
- `/etc/thermal-web.env` 在部署过程中不会被覆盖，认证配置是安全的。
