#!/usr/bin/env python3
"""应急部署：服务器连不上 GitHub 时，直接通过 SSH 把本地代码推上去并重启。

正常流程是「本地 push → 服务器自己 git pull」，但如果服务器的网络出问题
（比如到 GitHub 的 443/22 都不通），那条路就走不通了。这个脚本把当前工作副本
打包后用 SSH 传过去，在服务器上用仓库自己的 ``install.sh`` 安装并重启。

设计上的两个取舍：

* **不修改服务器上的 git 工作副本**（``/root/thermal-web``）。如果往里写文件，
  工作区会变脏，之后 ``merge --ff-only`` 就会失败，正常更新路径会被彻底堵死。
  所以文件是解到 ``/tmp`` 下的临时目录，再用那个目录里的 ``install.sh`` 安装。
* **复用 install.sh**，这样部署的文件集合（``*.py`` + ``web/``）与正常更新
  完全一致，不会出现"应急路径部署的东西和正常路径不一样"这种坑。

用法：

    python tools/emergency-deploy.py                     # 默认 ssh 别名 rk3318
    python tools/emergency-deploy.py --dry-run            # 只打印计划
    python tools/emergency-deploy.py --ssh user@host --port 42222 -i ~/.ssh/rk3318
    python tools/emergency-deploy.py --no-backup --keep-temp

注意：应急部署的代码不会自动进 GitHub。网络恢复后请照常 commit + push，否则
下一次正常更新会把 ``/opt/thermal-web`` 覆盖回 GitHub 上的旧版本。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shlex
import subprocess
import sys
import tarfile
import time

# 终端编码照旧（Windows 控制台是 GBK），但绝不允许因为编码问题崩掉
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001 - 老解释器/被重定向时忽略
        pass

# 与 install.sh 需要的文件保持一致（另外带上文档，方便服务器上排查）
INCLUDE_FILES = ["install.sh", "deploy.sh", "README.md", "WORKFLOW.md", "LICENSE"]
INCLUDE_DIRS = ["web", "systemd"]
INCLUDE_GLOBS = ["*.py"]
EXCLUDE_DIRS = {".git", ".venv", "venv", "data", "__pycache__", ".idea", ".vscode"}

# install.sh 实际会安装到运行目录的东西；其余只用于部署过程本身
DEPLOYED = lambda name: name.endswith(".py") or name.startswith("web/")


def log(message: str = ""):
    print(message, flush=True)


def step(message: str):
    log("==> " + message)


def fail(message: str) -> int:
    log("!! " + message)
    return 1


def collect_files(root: str) -> list:
    """要打包进去的文件，相对路径，顺序稳定。"""
    picked = []
    for name in INCLUDE_FILES:
        path = os.path.join(root, name)
        if os.path.isfile(path):
            picked.append(name)
    for name in INCLUDE_GLOBS:
        for entry in sorted(os.listdir(root)):
            if entry.endswith(name.lstrip("*")) and os.path.isfile(os.path.join(root, entry)):
                picked.append(entry)
    for name in INCLUDE_DIRS:
        base = os.path.join(root, name)
        if not os.path.isdir(base):
            continue
        for current, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for item in sorted(files):
                picked.append(os.path.relpath(os.path.join(current, item), root))
    return sorted({name.replace(os.sep, "/") for name in picked})


def local_path(root: str, name: str) -> str:
    return os.path.join(root, *name.split("/"))


def ssh_base(args) -> list:
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    if args.port:
        command += ["-p", str(args.port)]
    if args.identity:
        command += ["-i", os.path.expanduser(args.identity)]
    return command + [args.ssh]


def run(command: list, **kwargs) -> subprocess.CompletedProcess:
    # 不用 text=True：那会按本机编码（Windows 上是 gbk）解码，而服务器返回的是
    # UTF-8（API 的 JSON 里有中文），会直接把读取线程搞崩。
    return subprocess.run(command, capture_output=True, **kwargs)


def text(data) -> str:
    """把子进程输出按 UTF-8 解码，坏字节用替换字符兜住。"""
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", "replace")
    return str(data)


def remote(args, shell_command: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return run(ssh_base(args) + [shell_command], timeout=timeout)


def local_hashes(root: str, names: list) -> dict:
    digests = {}
    for name in names:
        path = local_path(root, name)
        if not os.path.isfile(path):
            continue
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        digests[name] = digest.hexdigest()
    return digests


def local_version(root: str) -> str:
    try:
        with open(local_path(root, "server.py"), "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.startswith("VERSION = "):
                    return line.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    return ""


def status_version(payload: str) -> str:
    match = re.search(r'"version"\s*:\s*"([^"]+)"', payload or "")
    return match.group(1) if match else ""


def git_info(root: str) -> dict:
    def git(*args):
        try:
            return text(run(["git", "-C", root, *args], timeout=30).stdout).strip()
        except Exception:
            return ""
    return {
        "head": git("rev-parse", "--short", "HEAD"),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(git("status", "--porcelain")),
        "unpushed": git("log", "--oneline", "origin/HEAD..HEAD"),
    }


def stream_tarball(root: str, files: list, args) -> int:
    """把文件用 tar 流式写到服务器的临时目录里。"""
    remote_dir = args.tmp
    command = "mkdir -p {d} && tar -xzf - -C {d}".format(d=remote_dir)
    process = subprocess.Popen(
        ssh_base(args) + [command],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        with tarfile.open(fileobj=process.stdin, mode="w|gz") as archive:
            for name in files:
                archive.add(local_path(root, name), arcname=name)
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        try:
            process.stdin.close()
        except Exception:
            pass
    _, errors = process.communicate(timeout=600)
    if process.returncode != 0:
        log(text(errors).strip())
    return process.returncode


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="通过 SSH 把本地代码直接部署到服务器（应急用，绕过 GitHub）")
    parser.add_argument("--ssh", default="rk3318",
                        help="ssh 目标：别名或 user@host（默认 rk3318）")
    parser.add_argument("--port", type=int, help="ssh 端口（用别名时可省略）")
    parser.add_argument("-i", "--identity", help="ssh 私钥文件")
    parser.add_argument("--dest", default="/opt/thermal-web", help="服务器运行目录")
    parser.add_argument("--tmp", default=None, help="服务器上的临时解包目录")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不执行")
    parser.add_argument("--no-backup", action="store_true", help="跳过部署前备份")
    parser.add_argument("--keep-temp", action="store_true", help="保留服务器上的临时目录")
    args = parser.parse_args(argv)

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    stamp = time.strftime("%Y%m%dT%H%M%S")
    args.tmp = args.tmp or "/tmp/thermal-web-emergency-%s" % stamp
    backup = "/root/thermal-web-backup-%s.tar.gz" % stamp

    if not os.path.isfile(os.path.join(root, "install.sh")):
        return fail("在 %s 里找不到 install.sh，请在仓库目录下运行" % root)

    files = collect_files(root)
    names = [name for name in files if DEPLOYED(name)]
    hashes = local_hashes(root, names)
    version = local_version(root)
    info = git_info(root)
    if not any(name == "systemd/thermal-web.service" for name in files):
        return fail("打包内容里缺少 systemd/thermal-web.service，install.sh 会失败")

    log("仓库      : %s" % root)
    log("本地提交  : %s (%s)" % (info["head"] or "?", info["branch"] or "?"))
    log("ssh 目标  : %s" % args.ssh)
    log("服务器目录: %s" % args.dest)
    log("临时目录  : %s" % args.tmp)
    log("备份      : %s" % ("(跳过)" if args.no_backup else backup))
    log("将上传 %d 个文件（其中 %d 个是安装目标）" % (len(files), len(names)))
    log("本地版本  : %s" % (version or "?"))
    if info["dirty"] or info["unpushed"]:
        log()
        log("注意：本地有未提交/未推送的改动——这正是应急部署的用途。")
        log("      网络恢复后请照常 commit + push，否则下一次正常更新会覆盖回去。")
    if args.dry_run:
        log()
        log("（--dry-run：以下文件会被上传）")
        for name in files:
            log("   " + name)
        return 0

    step("检查 SSH 连通性")
    probe = remote(args, "echo ok && hostname && uname -m")
    if probe.returncode != 0:
        return fail("SSH 连不上 %s：%s" % (args.ssh, text(probe.stderr).strip()))
    log("    " + text(probe.stdout).strip().replace("\n", " / "))

    step("记录服务器当前状态")
    current = remote(args, "PW=$(grep '^THERMAL_WEB_PASSWORD=' /etc/thermal-web.env 2>/dev/null | cut -d= -f2-); "
                           "curl -s --max-time 8 -u \"admin:$PW\" http://127.0.0.1:8080/api/status "
                           "|| echo '(api 不可达)'")
    log("    " + text(current.stdout).strip()[:200])
    before_version = status_version(text(current.stdout))
    log("    部署前版本: %s" % (before_version or "?"))
    repo_head = remote(args, "git -C /root/thermal-web log --oneline -1 2>/dev/null || echo '(没有 git 工作副本)'")
    log("    服务器 git 副本: " + text(repo_head.stdout).strip())

    if not args.no_backup:
        step("备份服务器上的 %s" % args.dest)
        backup_command = (
            "tar czf {b} --exclude='{d}/data' -C {parent} {name}".format(
                b=backup, d=args.dest, parent=os.path.dirname(args.dest),
                name=os.path.basename(args.dest))
        )
        result = remote(args, backup_command, timeout=300)
        if result.returncode != 0:
            return fail("备份失败（可用 --no-backup 跳过）：%s"
                        % (text(result.stderr) or text(result.stdout)).strip())
        size = remote(args, "du -h %s | cut -f1" % backup)
        log("    已保存 %s（%s）" % (backup, text(size.stdout).strip()))

    step("上传代码")
    if stream_tarball(root, files, args) != 0:
        return fail("上传失败")
    listing = remote(args, "find %s -maxdepth 1 -type f -name '*.py' | wc -l" % args.tmp)
    log("    解包完成，%s 个 .py" % text(listing.stdout).strip())

    step("在服务器上执行 install.sh（会重启服务）")
    install = remote(args, "cd {t} && bash install.sh {d} 2>&1 | tail -4".format(
        t=args.tmp, d=args.dest), timeout=600)
    log("    " + text(install.stdout).strip().replace("\n", "\n    "))
    if install.returncode != 0:
        return fail("install.sh 失败，回滚可用："
                    "ssh %s 'tar xzf %s -C %s'" % (args.ssh, backup, os.path.dirname(args.dest)))

    step("校验部署结果")
    time.sleep(3)
    active = remote(args, "systemctl is-active thermal-web")
    log("    thermal-web: " + text(active.stdout).strip())
    deployed = {}
    quoted = " ".join(shlex.quote(name) for name in names)
    remote_hashes = remote(args, "cd %s && sha256sum %s 2>/dev/null" % (args.dest, quoted))
    for line in text(remote_hashes.stdout).strip().splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            deployed[parts[1].strip()] = parts[0]
    mismatched = [name for name, digest in hashes.items()
                  if deployed.get(name) not in (None, digest)]
    missing = [name for name in hashes if name not in deployed]
    for name in sorted(hashes):
        mark = "same" if deployed.get(name) == hashes[name] else "DIFF"
        log("    %-12s %s" % (mark, name))
    status = remote(args, "PW=$(grep '^THERMAL_WEB_PASSWORD=' /etc/thermal-web.env 2>/dev/null | cut -d= -f2-); "
                          "curl -s --max-time 8 -u \"admin:$PW\" http://127.0.0.1:8080/api/status "
                          "|| echo '(api 不可达)'")
    after_version = status_version(text(status.stdout))
    log("    " + text(status.stdout).strip()[:200])
    log("    部署后版本: %s（部署前 %s）" % (after_version or "?", before_version or "?"))
    if version and after_version and after_version != version:
        log("!! 文件已上传，但服务报告的版本仍是 %s（期望 %s）——多半是没重启成功，"
            "去看 /opt/thermal-web/data/update.log 或 journalctl -u thermal-web"
            % (after_version, version))

    if not args.keep_temp:
        remote(args, "rm -rf %s" % args.tmp)
        log("    已清理临时目录")
    else:
        log("    临时目录保留在 %s" % args.tmp)

    log()
    version_ok = (not version) or (after_version == version)
    if mismatched or missing or not version_ok:
        if mismatched or missing:
            log("!! 有文件与本地不一致：%s %s" % (mismatched, missing))
        if not version_ok:
            log("!! 服务报告的版本是 %s，本地是 %s" % (after_version or "?", version))
        log("   回滚：ssh %s 'tar xzf %s -C %s && systemctl restart thermal-web'"
            % (args.ssh, backup, os.path.dirname(args.dest)))
        return 1
    log("完成：代码已上传并部署。")
    log("提醒：这次改动还没进 GitHub，网络恢复后记得 commit + push，")
    log("      否则下一次正常更新会把服务器覆盖回旧版本。")
    if not args.no_backup:
        log("回滚命令：ssh %s 'tar xzf %s -C %s && systemctl restart thermal-web'"
            % (args.ssh, backup, os.path.dirname(args.dest)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
