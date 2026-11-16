# SPDX-License-Identifier: GPL-3.0-or-later
"""单进程全流程：登录 -> 拉数建面板 -> 回测 -> 验证 -> 落生产产物。

为什么要有这个脚本
------------------
panda_data 的凭据存储（加密 `user.json`）在本机会话结束后消失，导致「登录成功但下一
个进程读不到」。本脚本把登录与全部计算放进**同一个进程**：token 只留在内存里，
不依赖任何凭据文件落盘，因此一条命令就能跑完。

运行
----
    cd <repo>/开发产物
    python scripts\\run_pipeline.py

可选
----
    --reuse-panel   复用已有面板，不重新拉数
    --skip-fetch    同上（别名）
    --no-prompt     若磁盘已有可解密凭据则静默复用，绝不弹输入
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pd_client  # noqa: E402

CST = timezone(timedelta(hours=8))


class Tee:
    """把标准输出同时写到日志文件，便于事后核对（即使终端被关闭）。"""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = path.open("a", encoding="utf-8")
        self.stdout = sys.stdout

    def write(self, s: str) -> int:
        self.stdout.write(s)
        try:
            self.fh.write(s)
            self.fh.flush()
        except Exception:
            pass
        return len(s)

    def flush(self) -> None:
        self.stdout.flush()
        try:
            self.fh.flush()
        except Exception:
            pass


def log(msg: str) -> None:
    stamp = datetime.now(CST).strftime("%H:%M:%S")
    print(f"[{stamp}] {msg}", flush=True)


def ensure_auth(no_prompt: bool = False) -> None:
    """优先复用磁盘凭据；否则在**本进程内**交互登录，token 留在内存。"""
    import panda_data
    from panda_data import auth_manager

    pd_client.prepare_cred_dir()
    creds = auth_manager.get_decrypted_credentials()

    if creds and creds.get("username") and creds.get("password"):
        log("复用磁盘加密凭据登录")
        pd_client.bootstrap()
        return

    # 环境变量凭据（alpha-44 同款做法：PANDA_DATA_USERNAME / PANDA_DATA_PASSWORD）
    if os.getenv("DEFAULT_USERNAME") and os.getenv("DEFAULT_PASSWORD"):
        log("使用环境变量凭据登录")
        pd_client.bootstrap(force=True)
        return

    if no_prompt:
        raise SystemExit("没有可用凭据，且指定了 --no-prompt")

    base_url = os.getenv("JAVA_SERVICE_BASE_URL") or "http://pandadata.pandaaiquant.com"
    default_user = os.getenv("DEFAULT_USERNAME") or os.getenv("PANDA_DATA_USERNAME") or ""
    print("=" * 60)
    print("PandaAI data 登录（仅本进程使用，token 不落盘）")
    print(f"  base_url: {base_url}")
    u = input(f"  用户名/手机号 [{default_user}]: ").strip() or default_user
    p = getpass.getpass("  密码（输入不回显）: ")
    if not u or not p:
        raise SystemExit("用户名或密码为空")

    # 登录成功后把 token 留在内存：通知 pd_client 不要再走磁盘凭据路径
    panda_data.init_token(username=u, password=p, base_url=base_url)
    pd_client._INITIALIZED = True  # type: ignore[attr-defined]
    pd_client.log_call({"event": "bootstrap", "mode": "in_process_prompt",
                        "base_url": base_url, "login_at": datetime.now(CST).isoformat(timespec="seconds")})
    log(f"登录成功；磁盘凭据可解密: {'是' if auth_manager.get_decrypted_credentials() else '否（不影响本次运行）'}")


def run_step(name: str, fn) -> None:
    log(f"=== {name} 开始")
    t0 = time.time()
    fn()
    log(f"=== {name} 完成，用时 {time.time()-t0:.1f}s")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse-panel", action="store_true")
    ap.add_argument("--skip-fetch", action="store_true")
    ap.add_argument("--no-prompt", action="store_true")
    args = ap.parse_args()

    out = Path(os.getenv("ALPHA61_OUT") or (HERE.parent / "output"))
    logdir = HERE.parent / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    sys.stdout = Tee(logdir / f"run_pipeline_{datetime.now(CST):%Y%m%d_%H%M%S}.log")
    log(f"输出目录: {out}")
    log(f"日志文件: {getattr(sys.stdout, 'fh', None) and sys.stdout.fh.name}")

    ensure_auth(no_prompt=args.no_prompt)

    import factor
    import backtest
    import validate
    import build_production

    reuse = args.reuse_panel or args.skip_fetch
    argv_backup = sys.argv[:]
    try:
        sys.argv = ["factor.py"] + (["--reuse-panel"] if reuse else [])
        run_step("factor（拉数/建面板/出信号）", factor.main)

        sys.argv = ["backtest.py"]
        run_step("backtest（B0/B1/P1 扣费回测 + IC/分层）", backtest.main)

        sys.argv = ["validate.py"]
        run_step("validate（未来函数/置换/极值/风险）", validate.main)

        sys.argv = ["build_production.py"]
        run_step("build_production（数据库.parquet）", build_production.main)

        import fill_report
        sys.argv = ["fill_report.py"]
        run_step("fill_report（把结果写回研究报告 §4–§7）", lambda: fill_report.main())

        import verify_deliverables
        sys.argv = ["verify_deliverables.py"]
        run_step("verify_deliverables（交付门禁）", lambda: verify_deliverables.main())
    finally:
        sys.argv = argv_backup

    log("全部完成。结果见 开发产物/output/ 与 生产产物/")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        sys.exit(1)
