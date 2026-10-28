# -*- coding: utf-8 -*-
"""额度控制与持久化管理服务。

功能：
1. 区分不同模型的独立额度计数与上限对比；
2. 支持 URL 参数灵活配置额度（单数值、多模型键值对如 muse-video:10,zimage:5、JSON等）；
3. 支持 .env 配置各模型独立额度及超额提示词；
4. 基于 SQLite 本地持久化（history.db），保证服务重启不丢失当天累加额度；
5. 支持动态调整 quota（如当天已用6次，quota由10调整为5时，当天累加6次保持不变，新对比立即判定超额并拦截）；
6. 提供跨进程线程安全的原子扣减与重置接口。
"""

import json
import os
import re
import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Tuple

import dotenv

_db_lock = threading.Lock()
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "uploaded_files" / "history_records" / "history.db"

# 兼容历史导出
_quota_usage: dict = {}


def init_quota_db():
    """初始化额度持久化 SQLite 数据表与索引"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _db_lock:
        conn = sqlite3.connect(DB_PATH, timeout=20.0)
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS quota_usage (
                    open_id TEXT NOT NULL,
                    date_str TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    used_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (open_id, date_str, scope)
                )"""
            )
            conn.execute(
                """CREATE INDEX IF NOT EXISTS idx_quota_usage_lookup ON quota_usage(open_id, date_str, scope)"""
            )
            conn.execute(
                """CREATE INDEX IF NOT EXISTS idx_quota_usage_date ON quota_usage(date_str)"""
            )
            conn.commit()
        finally:
            conn.close()


init_quota_db()


def parse_quota(value: str, default: int = -1) -> int:
    """兼容旧接口：解析单一 quota 参数为整数，解析失败返回 default"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_model_quotas(quota_str: str) -> dict[str, int]:
    """解析 quota 参数为多模型配额映射字典。
    
    支持格式：
    1. 纯数字："5" -> {"*": 5}（对所有模型各生效5次）
    2. 键值对字符串："muse-video:10,zimage:5,*:2" 或 "muse-video=10;zimage=5"
    3. JSON字符串：'{"muse-video": 10, "zimage": 5, "default": 2}'
    """
    if not quota_str or not str(quota_str).strip():
        return {}

    text = str(quota_str).strip()

    # 1. 尝试纯数字
    try:
        val = int(text)
        return {"*": val}
    except ValueError:
        pass

    # 2. 尝试 JSON
    if text.startswith("{") and text.endswith("}"):
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                res = {}
                for k, v in data.items():
                    try:
                        norm_k = "*" if str(k).strip().lower() in ("*", "default", "all") else str(k).strip().lower()
                        res[norm_k] = int(v)
                    except (ValueError, TypeError):
                        pass
                return res
        except Exception:
            pass

    # 3. 键值对拆分
    res = {}
    items = re.split(r"[,;]", text)
    for item in items:
        item = item.strip()
        if not item:
            continue
        parts = re.split(r"[:=]", item, maxsplit=1)
        if len(parts) == 2:
            k, v = parts[0].strip().lower(), parts[1].strip()
            norm_k = "*" if k in ("*", "default", "all") else k
            try:
                res[norm_k] = int(v)
            except ValueError:
                pass
        else:
            try:
                res["*"] = int(item)
            except ValueError:
                pass
    return res


def resolve_model_quota(
    model: str,
    quota_raw: str = "",
    extra_params: dict = None,
    scope_category: str = "",
) -> int:
    """为指定模型解析其实际生效的每日配额上限。
    
    优先级：
    1. URL 独立参数（如 quota_muse_video=5 或 quota[muse-video]=5）
    2. URL quota 参数中该模型的明确指定（如 muse-video:5）
    3. .env 环境变量模型专属配置（如 MODEL_QUOTA_MUSE_VIDEO=5 或 QUOTA_MUSE_VIDEO=5）
    4. URL quota 参数中的全局通用限制（如纯数字 "5" 或 "*:5"）
    5. .env 环境变量业务类别默认配置（如 TEXT2VIDEO_QUOTA / TEXT2IMAGE_QUOTA / DEFAULT_QUOTA）
    6. 未配置时返回 -1（代表不设限）
    """
    clean_model = (model or "").strip().lower()

    # 1. 检查 extra_params 单独参数
    if extra_params and isinstance(extra_params, dict):
        variants = [
            f"quota_{clean_model}",
            f"quota[{clean_model}]",
            f"quota_{clean_model.replace('-', '_')}",
            f"quota_{clean_model.replace('_', '-')}",
        ]
        for key in variants:
            for ep_k, ep_v in extra_params.items():
                if ep_k.strip().lower() == key:
                    try:
                        return int(ep_v)
                    except (TypeError, ValueError):
                        pass

    # 2. 检查 quota_raw 映射
    parsed_map = parse_model_quotas(quota_raw)
    if clean_model in parsed_map:
        return parsed_map[clean_model]
    if clean_model.replace("-", "_") in parsed_map:
        return parsed_map[clean_model.replace("-", "_")]
    if clean_model.replace("_", "-") in parsed_map:
        return parsed_map[clean_model.replace("_", "-")]

    candidate_fallback = parsed_map.get("*")

    # 3. 检查 .env 模型专属配置
    try:
        dotenv.load_dotenv(override=True)
    except Exception:
        pass

    env_key = re.sub(r"[^A-Za-z0-9]", "_", clean_model).upper()
    for env_var in (f"MODEL_QUOTA_{env_key}", f"QUOTA_{env_key}"):
        env_val = os.getenv(env_var)
        if env_val is not None:
            try:
                return int(env_val)
            except (ValueError, TypeError):
                pass

    # 4. 如果 URL 中配置了全局通用配额，如 "5"
    if candidate_fallback is not None:
        return candidate_fallback

    # 5. 检查业务类别默认配置
    if scope_category == "video":
        for env_var in ("TEXT2VIDEO_QUOTA", "TEXT2VIDEO_DEFAULT_QUOTA"):
            env_val = os.getenv(env_var)
            if env_val is not None:
                try:
                    return int(env_val)
                except (ValueError, TypeError):
                    pass
    elif scope_category == "image":
        for env_var in ("TEXT2IMAGE_QUOTA", "TEXT2IMAGE_DEFAULT_QUOTA"):
            env_val = os.getenv(env_var)
            if env_val is not None:
                try:
                    return int(env_val)
                except (ValueError, TypeError):
                    pass

    global_env = os.getenv("DEFAULT_QUOTA")
    if global_env is not None:
        try:
            return int(global_env)
        except (ValueError, TypeError):
            pass

    return -1


def get_quota_exceeded_msg(
    scope: str = "",
    model: str = "",
    limit: int = -1,
    used: int = 0,
) -> str:
    """获取额度超限时的提示文案，支持多层级覆盖与模板占位符"""
    try:
        dotenv.load_dotenv(override=True)
    except Exception:
        pass

    clean_model = (model or scope or "").strip().lower()
    env_model_key = re.sub(r"[^A-Za-z0-9]", "_", clean_model).upper()

    msg = None
    if env_model_key:
        msg = os.getenv(f"QUOTA_EXCEEDED_MSG_{env_model_key}")

    if not msg and scope:
        if "video" in scope:
            msg = os.getenv("TEXT2VIDEO_QUOTA_EXCEEDED_MSG")
        elif "image" in scope:
            msg = os.getenv("TEXT2IMAGE_QUOTA_EXCEEDED_MSG")

    if not msg:
        msg = os.getenv("QUOTA_EXCEEDED_MSG")

    default_msg = "当前应用的额度已用完，如需继续使用，请前往该地址重置额度：https://cas.luler.top/memberQuota"
    final_msg = msg.strip() if msg and msg.strip() else default_msg

    try:
        final_msg = final_msg.format(
            model=model or scope or "当前模型",
            scope=scope,
            limit=limit,
            used=used,
        )
    except Exception:
        pass

    return final_msg


async def reserve_generation_quota(
    open_id: str,
    requested_count: int,
    daily_limit: int,
    scope: str = "default",
) -> Tuple[bool, int]:
    """尝试为指定 open_id 和模型 scope 预约/扣减额度（持久化写入 SQLite，重启不丢失）
    
    规则：
    1. 若 daily_limit < 0，代表不限额度，直接放行；
    2. 当天该模型累加的已用次数保持在数据库中，无论外部 quota 如何动态调整；
    3. 若 当天已用 + 本次申请 > daily_limit，立即判定超额并返回 (False, current_used)；
    4. 未超额时，原子增加 used_count 并返回 (True, current_used + requested_count)。
    """
    if daily_limit < 0:
        return True, 0

    clean_open_id = (open_id or "default").strip()
    clean_scope = (scope or "default").strip().lower()

    today_str = date.today().isoformat()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with _db_lock:
        conn = sqlite3.connect(DB_PATH, timeout=20.0)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT used_count FROM quota_usage WHERE open_id = ? AND date_str = ? AND scope = ?",
                (clean_open_id, today_str, clean_scope),
            )
            row = cur.fetchone()
            current_used = row[0] if row else 0

            if current_used + requested_count > daily_limit:
                return False, current_used

            cur.execute(
                """INSERT INTO quota_usage (open_id, date_str, scope, used_count, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(open_id, date_str, scope) DO UPDATE SET
                       used_count = used_count + excluded.used_count,
                       updated_at = excluded.updated_at""",
                (clean_open_id, today_str, clean_scope, requested_count, now_str),
            )
            conn.commit()
            return True, current_used + requested_count
        finally:
            conn.close()


async def reset_quota(open_id: str, scope: str = "all") -> int:
    """重置指定 open_id 的额度使用记录（从 SQLite 删除）
    
    参数 scope：
    - "all" 或 "" 或 "*"：重置该用户的所有模型额度
    - "video"：重置该用户的所有视频模型额度
    - "image"：重置该用户的所有图片模型额度
    - 具体模型名称（如 "muse-video" 或 "zimage"）：精确重置该模型
    """
    clean_open_id = (open_id or "default").strip()
    clean_scope = (scope or "all").strip().lower()

    with _db_lock:
        conn = sqlite3.connect(DB_PATH, timeout=20.0)
        try:
            cur = conn.cursor()
            if clean_scope in ("all", "*", ""):
                cur.execute("DELETE FROM quota_usage WHERE open_id = ?", (clean_open_id,))
            elif clean_scope == "video":
                video_models = [
                    m.strip().lower()
                    for m in os.getenv("TEXT2VIDEO_MODELS", "muse-video").split(",")
                    if m.strip()
                ]
                video_models.extend(["video", "default"])
                video_models = list(set(video_models))
                placeholders = ",".join("?" for _ in video_models)
                cur.execute(
                    f"DELETE FROM quota_usage WHERE open_id = ? AND scope IN ({placeholders})",
                    [clean_open_id] + video_models,
                )
            elif clean_scope == "image":
                image_models = [
                    m.strip().lower()
                    for m in os.getenv("TEXT2IMAGE_MODELS", "zimage,qwen-image-aiproxy,gpt-image-2,muse-image").split(",")
                    if m.strip()
                ]
                image_models.extend(["image", "default"])
                image_models = list(set(image_models))
                placeholders = ",".join("?" for _ in image_models)
                cur.execute(
                    f"DELETE FROM quota_usage WHERE open_id = ? AND scope IN ({placeholders})",
                    [clean_open_id] + image_models,
                )
            else:
                cur.execute(
                    "DELETE FROM quota_usage WHERE open_id = ? AND lower(scope) = lower(?)",
                    (clean_open_id, clean_scope),
                )

            deleted_count = cur.rowcount
            conn.commit()
            return deleted_count
        finally:
            conn.close()


async def get_quota_status(open_id: str) -> dict:
    """获取指定 open_id 当前的使用状态（分模型展示，从 SQLite 读取）"""
    clean_open_id = (open_id or "default").strip()
    today_str = date.today().isoformat()

    with _db_lock:
        conn = sqlite3.connect(DB_PATH, timeout=20.0)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT date_str, scope, used_count, updated_at FROM quota_usage WHERE open_id = ? ORDER BY date_str DESC, scope ASC",
                (clean_open_id,),
            )
            rows = cur.fetchall()

            usage_map = {}
            today_usage = {}
            records = []
            for r in rows:
                date_str, scope, used_count, updated_at = r[0], r[1], r[2], r[3]
                usage_map[f"{date_str}:{scope}"] = used_count
                if date_str == today_str:
                    today_usage[scope] = used_count
                records.append({
                    "date": date_str,
                    "scope": scope,
                    "model": scope,
                    "used_count": used_count,
                    "updated_at": updated_at,
                })

            return {
                "open_id": clean_open_id,
                "today": today_str,
                "today_usage": today_usage,
                "usage": usage_map,
                "records": records,
            }
        finally:
            conn.close()
