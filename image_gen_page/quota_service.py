# -*- coding: utf-8 -*-
"""额度控制与持久化管理服务。

功能：
1. 区分不同模型的独立额度计数与上限对比；
2. 支持 URL 传入纯数字参数配置当前模型配额；
3. 支持 .env 配置各模型独立额度及超额提示词；
4. 基于 SQLite 本地持久化（history.db），保证服务重启不丢失当天累加额度；
5. 支持动态调整 quota（如当天已用6次，quota由10调整为5时，当天累加6次保持不变，新对比立即判定超额并拦截）；
6. 提供跨进程线程安全的原子扣减与重置接口。
"""

import math
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


def resolve_model_quota(
    model: str = "",
    quota_raw: str = "",
    scope_category: str = "",
    extra_params: dict = None,
) -> int:
    """为指定模型解析其实际生效的每日配额上限。
    
    1. 优先使用 URL 中传入的 quota 纯数字（若传入且为有效非负整数，直接生效）；
    2. 若未在 URL 传入 quota，检查 .env 中针对该模型的配置（如 QUOTA_MUSE_VIDEO）；
    3. 检查业务类别默认配置（如 TEXT2IMAGE_QUOTA / TEXT2VIDEO_QUOTA / DEFAULT_QUOTA）；
    4. 均未配置时返回 -1（代表不设限）。
    """
    # 1. 优先使用 URL 传入的数字 quota
    if quota_raw is not None and str(quota_raw).strip() != "":
        try:
            return int(str(quota_raw).strip())
        except (ValueError, TypeError):
            pass

    clean_model = (model or "").strip().lower()

    # 2. 检查 .env 环境变量模型专属配置（如 MODEL_QUOTA_XXX 或 QUOTA_XXX）
    try:
        dotenv.load_dotenv(override=True)
    except Exception:
        pass

    if clean_model:
        env_key = re.sub(r"[^A-Za-z0-9]", "_", clean_model).upper()
        for env_var in (f"MODEL_QUOTA_{env_key}", f"QUOTA_{env_key}"):
            env_val = os.getenv(env_var)
            if env_val is not None:
                try:
                    return int(env_val)
                except (ValueError, TypeError):
                    pass

    # 3. 检查业务类别默认配置
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
    category: str = "",
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

    if not msg:
        if category == "video" or "video" in scope:
            msg = os.getenv("TEXT2VIDEO_QUOTA_EXCEEDED_MSG")
        elif category == "image" or "image" in scope:
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
    union_id: str,
    requested_count: int,
    daily_limit: int,
    scope: str = "default",
) -> Tuple[bool, int]:
    """尝试为指定 union_id 和模型 scope 预约/扣减额度（持久化写入 SQLite，重启不丢失）
    
    规则：
    1. 若 daily_limit < 0，代表不限额度，直接放行；
    2. 当天该模型累加的已用次数保持在数据库中，无论外部 quota 如何动态调整；
    3. 若 当天已用 + 本次申请 > daily_limit，立即判定超额并返回 (False, current_used)；
    4. 未超额时，原子增加 used_count 并返回 (True, current_used + requested_count)。
    """
    if daily_limit < 0:
        return True, 0

    clean_id = (union_id or "default").strip()
    clean_scope = (scope or "default").strip().lower()

    today_str = date.today().isoformat()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with _db_lock:
        conn = sqlite3.connect(DB_PATH, timeout=20.0)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT used_count FROM quota_usage WHERE open_id = ? AND date_str = ? AND scope = ?",
                (clean_id, today_str, clean_scope),
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
                (clean_id, today_str, clean_scope, requested_count, now_str),
            )
            conn.commit()
            return True, current_used + requested_count
        finally:
            conn.close()


async def reset_quota(union_id: str, scope: str = "all") -> int:
    """重置指定 union_id 的额度使用记录（从 SQLite 删除）
    
    参数 scope：
    - "all" 或 "" 或 "*"：重置该用户的所有模型额度
    - "video"：重置该用户的所有视频模型额度
    - "image"：重置该用户的所有图片模型额度
    - 具体模型名称（如 "muse-video" 或 "zimage"）：精确重置该模型
    """
    clean_id = (union_id or "default").strip()
    clean_scope = (scope or "all").strip().lower()

    with _db_lock:
        conn = sqlite3.connect(DB_PATH, timeout=20.0)
        try:
            cur = conn.cursor()
            if clean_scope in ("all", "*", ""):
                cur.execute("DELETE FROM quota_usage WHERE open_id = ?", (clean_id,))
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
                    [clean_id] + video_models,
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
                    [clean_id] + image_models,
                )
            else:
                cur.execute(
                    "DELETE FROM quota_usage WHERE open_id = ? AND lower(scope) = lower(?)",
                    (clean_id, clean_scope),
                )

            deleted_count = cur.rowcount
            conn.commit()
            return deleted_count
        finally:
            conn.close()


async def get_quota_status(
    union_id: str = "",
    page: int = 1,
    page_size: int = 100,
) -> dict:
    """分页获取 union_id 的额度消耗情况，传入 union_id 则进行筛选过滤"""
    clean_uid = (union_id or "").strip()
    page = max(1, page)
    page_size = max(1, page_size)
    offset = (page - 1) * page_size
    today_str = date.today().isoformat()

    with _db_lock:
        conn = sqlite3.connect(DB_PATH, timeout=20.0)
        try:
            cur = conn.cursor()

            where_sql = ""
            params = []
            if clean_uid:
                where_sql = "WHERE open_id = ?"
                params.append(clean_uid)

            count_sql = f"SELECT COUNT(DISTINCT open_id) FROM quota_usage {where_sql}"
            cur.execute(count_sql, params)
            total = cur.fetchone()[0]

            page_sql = f"""
                SELECT open_id, MAX(updated_at) as last_active, SUM(used_count) as total_used
                FROM quota_usage
                {where_sql}
                GROUP BY open_id
                ORDER BY last_active DESC
                LIMIT ? OFFSET ?
            """
            cur.execute(page_sql, params + [page_size, offset])
            u_rows = cur.fetchall()

            total_pages = math.ceil(total / page_size) if total > 0 else 1
            if not u_rows:
                return {
                    "page": page,
                    "page_size": page_size,
                    "total": total,
                    "total_pages": total_pages,
                    "items": [],
                }

            target_ids = [r[0] for r in u_rows]
            summary_map = {r[0]: {"last_active": r[1], "total_used": r[2]} for r in u_rows}

            placeholders = ",".join("?" for _ in target_ids)
            detail_sql = f"""
                SELECT open_id, date_str, scope, used_count, updated_at
                FROM quota_usage
                WHERE open_id IN ({placeholders})
                ORDER BY date_str DESC, scope ASC
            """
            cur.execute(detail_sql, target_ids)
            detail_rows = cur.fetchall()

            user_records = {uid: [] for uid in target_ids}
            user_today = {uid: {} for uid in target_ids}
            user_usage = {uid: {} for uid in target_ids}

            for row in detail_rows:
                uid, d_str, sc, cnt, u_at = row[0], row[1], row[2], row[3], row[4]
                user_usage[uid][f"{d_str}:{sc}"] = cnt
                if d_str == today_str:
                    user_today[uid][sc] = cnt
                user_records[uid].append({
                    "date": d_str,
                    "model": sc,
                    "scope": sc,
                    "used_count": cnt,
                    "updated_at": u_at,
                })

            items = []
            for uid in target_ids:
                sm = summary_map.get(uid, {})
                items.append({
                    "union_id": uid,
                    "total_used": sm.get("total_used", 0),
                    "last_active": sm.get("last_active", ""),
                    "today_usage": user_today.get(uid, {}),
                    "usage": user_usage.get(uid, {}),
                    "records": user_records.get(uid, []),
                })

            return {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
                "items": items,
            }
        finally:
            conn.close()
