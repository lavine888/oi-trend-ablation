# SPDX-License-Identifier: GPL-3.0-or-later
"""一次性登录脚本：把 panda_data 加密凭据写到**工作区内**的 `.secrets/user.json`。

为什么凭据要放在工作区
----------------------
panda_data 默认把加密凭据写到 Python 安装目录（`Lib\\user.json`）。本机上该位置
写入会被拦截或在会话结束后消失，导致「登录成功但下次读不到」。这里统一重定向到
`<项目根>/.secrets/user.json`，与 `pd_client.py` 完全一致。

密码通过 getpass 从键盘读入：不进命令行历史、不进聊天记录、不写任何明文文件；
落盘的只有机器绑定加密后的凭据。

运行
----
    cd <repo>/开发产物
    python scripts\\relogin.py
"""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pd_client  # noqa: E402


def main() -> int:
    import panda_data
    from panda_data import auth_manager

    # 关键：先重定向凭据目录，再登录
    target = pd_client.prepare_cred_dir()
    print(f"  凭据将写入: {target}")

    base_url = os.getenv("JAVA_SERVICE_BASE_URL") or "http://pandadata.pandaaiquant.com"
    default_user = os.getenv("DEFAULT_USERNAME") or os.getenv("PANDA_DATA_USERNAME") or ""

    print("PandaAI data 登录（panda_data SDK）")
    print(f"  base_url: {base_url}")
    u = input(f"  用户名/手机号 [{default_user}]: ").strip() or default_user
    if not u:
        print("用户名不能为空")
        return 2
    p = getpass.getpass("  密码（输入不回显）: ")
    if not p:
        print("密码不能为空")
        return 2

    panda_data.init_token(username=u, password=p, base_url=base_url)

    ok = bool(auth_manager.auth_info().get("authenticated"))
    written = target.exists()
    print(f"  登录结果: {'成功' if ok else '失败'}")
    print(f"  凭据文件: {'已写入 ' + str(target) if written else '未写入'}")

    if ok:
        df = panda_data.get_future_daily(symbol=["A_DOMINANT.DCE"], start_date="20250101", end_date="20250131")
        print(f"  连通性自检: get_future_daily 返回 {len(df)} 行，列 {len(df.columns)} 个")

    # 复查：重新从磁盘解密，确认下次进程能读到
    reread = auth_manager.get_decrypted_credentials()
    print(f"  磁盘凭据可解密: {'是' if reread else '否'}")
    return 0 if (ok and written and reread) else 1


if __name__ == "__main__":
    sys.exit(main())
