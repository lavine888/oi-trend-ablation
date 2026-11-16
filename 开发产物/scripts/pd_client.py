# SPDX-License-Identifier: GPL-3.0-or-later
"""PandaAI data (panda_data SDK) 统一入口封装。

用途
----
Alpha-61 的所有数据输入必须来自平台真实入口 `panda_data`（量枢院《Alpha 因子开发与
生产规则 V2》第 1、6 节：不得使用个人临时整理的数据文件作为正式输入）。本模块把
登录、重试、调用留痕收敛到一处，供 factor.py / backtest.py / validate.py 复用。

留痕口径
--------
每次远程调用都会写一行 JSONL 到 `开发产物/logs/platform_calls.jsonl`：
    时间戳 / 接口名 / 入参（脱敏）/ 返回行数 / 返回列 / 数据首末日期 / 耗时
凭据本身不写盘、不打印，只记录 `username_hash`（sha256 前 12 位）。

兼容说明
--------
panda_data 在 init_token 成功后会把凭据重新加密写回 `user.json`。若该路径不可写，
本次会话直接复用磁盘上已存在的加密凭据完成登录（等价入口，不改任何因子口径）。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

LOG_DIR = Path(__file__).resolve().parents[1] / "logs"
CALL_LOG = LOG_DIR / "platform_calls.jsonl"
CST = timezone(timedelta(hours=8))

# 凭据目录：panda_data 默认把加密凭据写在 Python 安装目录（Lib\user.json），
# 该位置在本机受写入限制，写入会在会话结束后消失。这里统一改到工作区内，
# 既保证可读，又把「凭据文件在哪」写进交付记录。文件仍是机器绑定加密的。
CRED_DIR = Path(os.getenv("ALPHA61_CRED_DIR") or (Path(__file__).resolve().parents[2] / ".secrets"))

_INITIALIZED = False


def prepare_cred_dir() -> Path:
    """把 panda_data 的凭据路径重定向到工作区，并确保目录存在。"""
    from panda_data import auth_manager

    CRED_DIR.mkdir(parents=True, exist_ok=True)
    auth_manager._user_json_dir = str(CRED_DIR)
    return CRED_DIR / "user.json"


def _now() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


def username_hash(value: Optional[str]) -> str:
    if not value:
        return "none"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def log_call(record: Dict[str, Any]) -> None:
    """把一次平台调用的输入/输出摘要追加到留痕文件。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    record = {"ts": _now(), **record}
    with CALL_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def bootstrap(force: bool = False) -> Dict[str, Any]:
    """登录 panda_data，返回不含敏感信息的会话信息。"""
    global _INITIALIZED
    if _INITIALIZED and not force:
        return {"reused": True}

    import panda_data
    from panda_data import auth_manager

    prepare_cred_dir()

    info: Dict[str, Any] = {"sdk": getattr(panda_data, "__version__", "unknown")}
    try:
        import importlib.metadata as md

        info["sdk_version"] = md.version("panda_data")
    except Exception:
        pass

    creds = auth_manager.get_decrypted_credentials() or {}
    username = os.getenv("DEFAULT_USERNAME") or creds.get("username")
    password = os.getenv("DEFAULT_PASSWORD") or creds.get("password")
    base_url = os.getenv("JAVA_SERVICE_BASE_URL") or creds.get("base_url") or "http://pandadata.pandaaiquant.com"

    if not username or not password:
        raise RuntimeError(
            "panda_data 未配置凭据：请先执行 panda_data.init_token(username, password, base_url)，"
            "或设置 DEFAULT_USERNAME / DEFAULT_PASSWORD 环境变量。"
        )

    # 凭据已存在时不让 SDK 回写 user.json（只读会话，避免污染既有凭据文件）
    original_persist = auth_manager._persist_credentials
    auth_manager._persist_credentials = lambda *a, **k: None  # type: ignore[assignment]
    try:
        panda_data.init_token(username=username, password=password, base_url=base_url)
    finally:
        auth_manager._persist_credentials = original_persist  # type: ignore[assignment]

    _INITIALIZED = True
    info.update(
        {
            "base_url": base_url,
            "username_hash": username_hash(username),
            "login_at": _now(),
            "reused": False,
        }
    )
    log_call({"event": "bootstrap", **info})
    return info


def call(name: str, fn, **kwargs) -> pd.DataFrame:
    """带留痕的接口调用：记录入参、行数、列、日期范围与耗时。"""
    bootstrap()
    t0 = time.time()
    df = fn(**kwargs)
    elapsed = round(time.time() - t0, 3)

    rec: Dict[str, Any] = {
        "event": "call",
        "api": name,
        "params": {k: (v if not isinstance(v, (list, tuple)) else list(v)) for k, v in kwargs.items()},
        "elapsed_s": elapsed,
        "rows": int(len(df)) if isinstance(df, pd.DataFrame) else None,
    }
    if isinstance(df, pd.DataFrame):
        rec["columns"] = list(df.columns)
        for col in ("date", "trade_date", "settleDate"):
            if col in df.columns and len(df):
                rec["date_min"] = str(df[col].min())
                rec["date_max"] = str(df[col].max())
                break
        rec["sha1"] = hashlib.sha1(
            pd.util.hash_pandas_object(df, index=True).values.tobytes()
        ).hexdigest()[:16]
    log_call(rec)
    return df


def future_daily(symbols, start_date: str, end_date: str, fields=None) -> pd.DataFrame:
    import panda_data

    return call(
        "get_future_daily",
        panda_data.get_future_daily,
        symbol=list(symbols),
        start_date=start_date,
        end_date=end_date,
        fields=fields or [],
    )


def future_detail() -> pd.DataFrame:
    import panda_data

    return call("get_future_detail", panda_data.get_future_detail)


def future_dominant(start_date: str, end_date: str, fields=None) -> pd.DataFrame:
    import panda_data

    return call(
        "get_future_dominant",
        panda_data.get_future_dominant,
        start_date=start_date,
        end_date=end_date,
        fields=fields or [],
    )


def future_symbol_posi(start_date: str, end_date: str, symbol=None) -> pd.DataFrame:
    import panda_data

    kwargs: Dict[str, Any] = {"start_date": start_date, "end_date": end_date}
    if symbol:
        kwargs["symbol"] = symbol
    return call("get_future_symbol_posi", panda_data.get_future_symbol_posi, **kwargs)
