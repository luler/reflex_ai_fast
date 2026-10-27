# -*- coding: utf-8 -*-
"""现代化 AI 图片资产与生图历史在线管理后台 (SPA)。

参考 hello_pptx_mcp/web_admin.py 架构设计，纯内置原生 HTML5 + CSS3 + Vanilla JS，零外部 CDN 依赖。
支持：
- 严格按时间倒序展示生成记录列表；
- 记录每次请求生成的图片、参考图、提示词、模型名称、耗时、规格及来源；
- 支持全景高清大图预览灯箱（生成图与参考图对比切换、快捷复制完整提示词、下载大图、键盘/手势翻页）；
- 实时搜索过滤（提示词、模型、来源）、智能分页；
- 批量勾选与一键批量删除（级联本地图片文件清理与二次确认）；
- 本地文件持久化（图片存储于 uploaded_files/history_records/，自动持久化至 Docker 挂载卷）。
"""

import asyncio
import base64
import json
import math
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
import dotenv
import reflex as rx
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.staticfiles import StaticFiles

# 存储路径配置：保存在 uploaded_files/history_records 目录下，适配已有 Docker 卷挂载
BASE_DIR = Path("uploaded_files")
HISTORY_DIR = BASE_DIR / "history_records"
IMAGES_DIR = HISTORY_DIR / "images"
DB_PATH = HISTORY_DIR / "history.db"

_db_lock = threading.Lock()


def get_auth_key() -> str:
    """获取当前配置的管理授权密钥（支持热重载 .env）。"""
    dotenv.load_dotenv(override=True)
    return os.environ.get("AUTH_KEY", "").strip()


def is_auth_valid(req: Request) -> bool:
    """验证请求是否携带有效的 AUTH_KEY。若未配置 AUTH_KEY，则全量放行。"""
    auth_key_env = get_auth_key()
    if not auth_key_env:
        return True

    # 1. 检查 Authorization: Bearer <AUTH_KEY> 或 <AUTH_KEY>
    auth_header = req.headers.get("Authorization", "")
    if auth_header:
        parts = auth_header.strip().split()
        if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1] == auth_key_env:
            return True
        elif len(parts) == 1 and parts[0] == auth_key_env:
            return True

    # 2. 检查 Query 参数 ?auth_key=<AUTH_KEY>
    if req.query_params.get("auth_key") == auth_key_env:
        return True

    return False


def _normalize_stored_paths(paths_json: str | None) -> str:
    """幂等清洗图片路径，将历史任意重复嵌套的 /admin/ 前缀统一正规化为单个 /admin/history_files/。"""
    if not paths_json:
        return json.dumps([])
    try:
        items = json.loads(paths_json)
        if isinstance(items, list):
            cleaned = []
            for item in items:
                if isinstance(item, str) and "/history_files/" in item:
                    # 匹配任何可能包含多个连续 /admin 的历史路径，规整为单个 /admin/history_files/
                    item = re.sub(r'(?:/admin)*/history_files/', '/admin/history_files/', item)
                cleaned.append(item)
            return json.dumps(cleaned, ensure_ascii=False)
    except Exception:
        pass
    return paths_json


def init_db():
    """初始化 SQLite 数据库与存储目录。"""
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS records (
                    id TEXT PRIMARY KEY,
                    prompt TEXT,
                    model TEXT,
                    duration REAL,
                    source TEXT,
                    images TEXT,
                    ref_images TEXT,
                    extra TEXT,
                    created_at TEXT
                )"""
            )
            conn.execute(
                """CREATE INDEX IF NOT EXISTS idx_records_created_at ON records(created_at DESC)"""
            )
            # 自动迁移与幂等清洗历史记录中的图片路径至标准的 /admin/history_files/ 前缀
            cur = conn.cursor()
            cur.execute(
                "SELECT id, images, ref_images FROM records WHERE images LIKE '%/history_files/%' OR ref_images LIKE '%/history_files/%'"
            )
            rows = cur.fetchall()
            for rid, imgs, refs in rows:
                new_imgs = _normalize_stored_paths(imgs)
                new_refs = _normalize_stored_paths(refs)
                if new_imgs != imgs or new_refs != refs:
                    cur.execute(
                        "UPDATE records SET images = ?, ref_images = ? WHERE id = ?",
                        (new_imgs, new_refs, rid),
                    )
            conn.commit()
        finally:
            conn.close()


init_db()


def get_db_connection():
    """获取数据库连接。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


async def _save_single_image(img_data_or_url: str, dest_filename: str) -> str:
    """保存单张图片到本地 images 目录，返回相对访问路径。若下载失败则保留原始 URL。"""
    dest_path = IMAGES_DIR / dest_filename
    web_path = f"/admin/history_files/images/{dest_filename}"

    if not img_data_or_url:
        return ""

    # 1. Base64 数据处理
    if img_data_or_url.startswith("data:"):
        try:
            header, encoded = img_data_or_url.split(",", 1)
            raw_bytes = base64.b64decode(encoded)
            with open(dest_path, "wb") as f:
                f.write(raw_bytes)
            return web_path
        except Exception as e:
            print(f"[web_admin] Base64 解码失败: {e}")
            return img_data_or_url

    # 2. 本地文件复制
    if os.path.exists(img_data_or_url):
        try:
            shutil.copy2(img_data_or_url, dest_path)
            return web_path
        except Exception as e:
            print(f"[web_admin] 复制本地图片失败: {e}")

    # 检查是否是 uploaded_files 目录下的文件名
    local_in_upload = BASE_DIR / img_data_or_url
    if local_in_upload.exists():
        try:
            shutil.copy2(local_in_upload, dest_path)
            return web_path
        except Exception as e:
            print(f"[web_admin] 复制 uploaded_files 本地图片失败: {e}")

    # 3. HTTP / HTTPS 远程图片下载
    if img_data_or_url.startswith("http://") or img_data_or_url.startswith("https://"):
        try:
            timeout = aiohttp.ClientTimeout(total=25)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(img_data_or_url) as resp:
                    if resp.status == 200:
                        content = await resp.read()
                        with open(dest_path, "wb") as f:
                            f.write(content)
                        return web_path
                    else:
                        print(f"[web_admin] 下载远程图片失败，HTTP {resp.status}")
                        return img_data_or_url
        except Exception as e:
            print(f"[web_admin] 下载远程图片异常: {e}")
            return img_data_or_url

    return img_data_or_url


async def _process_and_save_record(
    record_id: str,
    prompt: str,
    model: str,
    duration: float,
    images: list[str],
    ref_images: list[str],
    source: str,
    extra: dict,
    created_at: str,
):
    """异步下载/整理图片并写入数据库。"""
    try:
        saved_images = []
        for i, img in enumerate(images):
            ext = "png"
            if isinstance(img, str):
                if img.startswith("data:image/webp") or (img.startswith("data:") and "UklGR" in img[:35]):
                    ext = "webp"
                elif img.startswith("data:image/jpeg"):
                    ext = "jpg"
                elif "." in img and not img.startswith("data:"):
                    potential_ext = img.split("?")[0].split(".")[-1].lower()
                    if potential_ext in ["jpg", "jpeg", "png", "webp", "gif", "mp4"]:
                        ext = potential_ext
            dest_name = f"{record_id}_{i}.{ext}"
            saved_path = await _save_single_image(img, dest_name)
            saved_images.append(saved_path)

        saved_ref_images = []
        for i, ref in enumerate(ref_images):
            ext = "jpg"
            if isinstance(ref, str) and "." in ref and not ref.startswith("data:"):
                potential_ext = ref.split("?")[0].split(".")[-1].lower()
                if potential_ext in ["jpg", "jpeg", "png", "webp"]:
                    ext = potential_ext
            dest_name = f"{record_id}_ref_{i}.{ext}"
            saved_path = await _save_single_image(ref, dest_name)
            saved_ref_images.append(saved_path)

        with _db_lock:
            conn = get_db_connection()
            try:
                conn.execute(
                    """INSERT INTO records (id, prompt, model, duration, source, images, ref_images, extra, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        record_id,
                        prompt,
                        model,
                        round(duration, 2),
                        source,
                        json.dumps(saved_images, ensure_ascii=False),
                        json.dumps(saved_ref_images, ensure_ascii=False),
                        json.dumps(extra or {}, ensure_ascii=False),
                        created_at,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
    except Exception as e:
        print(f"[web_admin] 保存历史记录失败: {e}")


def record_history(
    prompt: str,
    model: str,
    duration: float,
    images: list[str],
    ref_images: list[str] | None = None,
    source: str = "",
    extra: dict | None = None,
):
    """主入口：记录单次成功的图片生成信息。

    此函数完全非阻塞且容错，绝不抛出任何异常影响主业务流程。
    """
    try:
        record_id = uuid.uuid4().hex
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        images_list = list(images) if images else []
        ref_images_list = list(ref_images) if ref_images else []

        # 调度异步后台任务
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                _process_and_save_record(
                    record_id,
                    prompt,
                    model,
                    duration,
                    images_list,
                    ref_images_list,
                    source,
                    extra or {},
                    created_at,
                )
            )
        except RuntimeError:
            threading.Thread(
                target=lambda: asyncio.run(
                    _process_and_save_record(
                        record_id,
                        prompt,
                        model,
                        duration,
                        images_list,
                        ref_images_list,
                        source,
                        extra or {},
                        created_at,
                    )
                ),
                daemon=True,
            ).start()
    except Exception as e:
        print(f"[web_admin] 触发 record_history 失败: {e}")


def get_records(page: int = 1, page_size: int = 15, search: str = "") -> dict:
    """分页查询记录。"""
    page = max(1, page)
    page_size = max(1, min(100, page_size))
    offset = (page - 1) * page_size

    with _db_lock:
        conn = get_db_connection()
        try:
            where_clause = ""
            params = []
            if search.strip():
                where_clause = "WHERE prompt LIKE ? OR model LIKE ? OR source LIKE ?"
                pattern = f"%{search.strip()}%"
                params = [pattern, pattern, pattern]

            count_cur = conn.cursor()
            count_cur.execute(f"SELECT COUNT(*) FROM records {where_clause}", params)
            total = count_cur.fetchone()[0]

            query_sql = f"""SELECT * FROM records {where_clause}
                            ORDER BY created_at DESC LIMIT ? OFFSET ?"""
            query_params = list(params) + [page_size, offset]
            cur = conn.cursor()
            cur.execute(query_sql, query_params)
            rows = cur.fetchall()

            items = []
            for r in rows:
                items.append({
                    "id": r["id"],
                    "prompt": r["prompt"] or "",
                    "model": r["model"] or "",
                    "duration": r["duration"] or 0,
                    "source": r["source"] or "",
                    "images": json.loads(r["images"]) if r["images"] else [],
                    "ref_images": json.loads(r["ref_images"]) if r["ref_images"] else [],
                    "extra": json.loads(r["extra"]) if r["extra"] else {},
                    "created_at": r["created_at"] or "",
                })

            total_pages = math.ceil(total / page_size) if total > 0 else 1
            return {
                "items": items,
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
            }
        finally:
            conn.close()


def delete_record_by_id(record_id: str) -> bool:
    """删除单条记录并级联清理本地图片。"""
    with _db_lock:
        conn = get_db_connection()
        try:
            conn.execute("DELETE FROM records WHERE id = ?", (record_id,))
            conn.commit()
        finally:
            conn.close()

    # 清理相关文件
    try:
        for p in IMAGES_DIR.glob(f"{record_id}_*"):
            if p.is_file():
                p.unlink(missing_ok=True)
    except Exception as e:
        print(f"[web_admin] 删除图片文件失败: {e}")

    return True


def batch_delete_records(record_ids: list[str]) -> int:
    """批量删除多条记录。"""
    if not record_ids:
        return 0
    with _db_lock:
        conn = get_db_connection()
        try:
            placeholders = ",".join("?" for _ in record_ids)
            conn.execute(f"DELETE FROM records WHERE id IN ({placeholders})", record_ids)
            conn.commit()
        finally:
            conn.close()

    for rid in record_ids:
        try:
            for p in IMAGES_DIR.glob(f"{rid}_*"):
                if p.is_file():
                    p.unlink(missing_ok=True)
        except Exception:
            pass

    return len(record_ids)


def get_stats() -> dict:
    """获取大盘统计数据。"""
    with _db_lock:
        conn = get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*), AVG(duration) FROM records")
            row = cur.fetchone()
            total_count = row[0] or 0
            avg_duration = round(row[1] or 0, 2)
        finally:
            conn.close()

    total_disk_bytes = 0
    try:
        if IMAGES_DIR.exists():
            for p in IMAGES_DIR.iterdir():
                if p.is_file():
                    total_disk_bytes += p.stat().st_size
    except Exception:
        pass

    return {
        "total_count": total_count,
        "avg_duration": avg_duration,
        "total_disk_bytes": total_disk_bytes,
    }


def get_admin_html() -> str:
    """生成管理后台 SPA 页面 HTML。"""
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <title>AI Image Studio · 图片生成历史与资产管理</title>
  <style>
    :root {
      --bg: #0b0f19;
      --surface: #111827;
      --surface-subtle: #1f2937;
      --surface-hover: #283548;
      --border: #374151;
      --border-focus: #3b82f6;
      --primary: #2563eb;
      --primary-hover: #1d4ed8;
      --primary-light: #60a5fa;
      --accent: #0ea5e9;
      --danger: #ef4444;
      --danger-hover: #dc2626;
      --text: #f9fafb;
      --text-muted: #9ca3af;
      --text-dim: #6b7280;
      --badge-bg: #1e293b;
      --radius: 10px;
      --shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.4), 0 8px 10px -6px rgba(0, 0, 0, 0.3);
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
      background-color: var(--bg);
      color: var(--text);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }
    /* 顶部导航 */
    header {
      background: rgba(17, 24, 39, 0.85);
      backdrop-filter: blur(12px);
      border-bottom: 1px solid var(--border);
      position: sticky;
      top: 0;
      z-index: 40;
      padding: 0.85rem 1.75rem;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1rem;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      text-decoration: none;
      color: inherit;
    }
    .brand-icon {
      width: 2.2rem;
      height: 2.2rem;
      background: linear-gradient(135deg, #2563eb, #0ea5e9);
      border-radius: 8px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-weight: bold;
      font-size: 1.15rem;
      color: #fff;
      box-shadow: 0 0 12px rgba(37, 99, 235, 0.4);
    }
    .brand-title {
      font-size: 1.15rem;
      font-weight: 700;
      letter-spacing: -0.02em;
    }
    .brand-subtitle {
      font-size: 0.75rem;
      color: var(--text-muted);
    }
    .header-actions {
      display: flex;
      align-items: center;
      gap: 0.85rem;
    }
    .search-box {
      position: relative;
    }
    .search-input {
      background: var(--surface-subtle);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 0.45rem 0.85rem 0.45rem 2.2rem;
      color: var(--text);
      font-size: 0.85rem;
      width: 240px;
      transition: all 0.2s;
    }
    .search-input:focus {
      outline: none;
      border-color: var(--border-focus);
      width: 320px;
      background: var(--surface);
    }
    .search-icon {
      position: absolute;
      left: 0.75rem;
      top: 50%;
      transform: translateY(-50%);
      color: var(--text-dim);
      font-size: 0.85rem;
      pointer-events: none;
    }
    .btn {
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      padding: 0.45rem 0.95rem;
      font-size: 0.85rem;
      font-weight: 500;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: var(--surface-subtle);
      color: var(--text);
      cursor: pointer;
      transition: all 0.15s ease;
      text-decoration: none;
    }
    .btn:hover {
      background: var(--surface-hover);
      border-color: #4b5563;
    }
    .btn-primary {
      background: var(--primary);
      border-color: var(--primary);
      color: #fff;
    }
    .btn-primary:hover {
      background: var(--primary-hover);
      border-color: var(--primary-hover);
    }
    .btn-danger {
      background: rgba(239, 68, 68, 0.15);
      border-color: rgba(239, 68, 68, 0.35);
      color: #f87171;
    }
    .btn-danger:hover {
      background: var(--danger);
      color: #fff;
      border-color: var(--danger);
    }
    .btn-sm {
      padding: 0.3rem 0.65rem;
      font-size: 0.78rem;
    }

    /* 容器主体 */
    main {
      flex: 1;
      max-width: 1400px;
      width: 100%;
      margin: 0 auto;
      padding: 1.5rem 1.75rem;
    }

    /* 统计大盘与工具栏 */
    .dashboard-bar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 1rem;
      margin-bottom: 1.25rem;
    }
    .stats-group {
      display: flex;
      align-items: center;
      gap: 1.25rem;
      flex-wrap: wrap;
    }
    .stat-chip {
      background: var(--surface);
      border: 1px solid var(--border);
      padding: 0.35rem 0.85rem;
      border-radius: 20px;
      font-size: 0.82rem;
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }
    .stat-chip b {
      color: var(--primary-light);
      font-size: 0.95rem;
    }

    /* 批量操作悬浮条 */
    .batch-bar {
      background: linear-gradient(90deg, #1e3a8a, #0c4a6e);
      border: 1px solid #3b82f6;
      border-radius: var(--radius);
      padding: 0.65rem 1.25rem;
      margin-bottom: 1.25rem;
      display: none;
      align-items: center;
      justify-content: space-between;
      animation: slideDown 0.2s ease-out;
    }
    .batch-bar.active { display: flex; }
    @keyframes slideDown {
      from { opacity: 0; transform: translateY(-8px); }
      to { opacity: 1; transform: translateY(0); }
    }

    /* 表格容器 */
    .table-container {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      overflow: hidden;
      box-shadow: var(--shadow);
    }
    table {
      width: 100%;
      border-collapse: collapse;
      text-align: left;
      font-size: 0.88rem;
    }
    thead {
      background: var(--surface-subtle);
      border-bottom: 1px solid var(--border);
      color: var(--text-muted);
      font-size: 0.8rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    th, td {
      padding: 0.85rem 1rem;
      vertical-align: middle;
    }
    tbody tr {
      border-bottom: 1px solid rgba(55, 65, 81, 0.4);
      transition: background 0.15s ease;
    }
    tbody tr:hover {
      background: var(--surface-hover);
    }
    tbody tr.selected {
      background: rgba(37, 99, 235, 0.12);
    }

    /* 缩略图 */
    .thumb-box {
      width: 80px;
      height: 80px;
      border-radius: 8px;
      background: #020617;
      border: 1px solid var(--border);
      overflow: hidden;
      cursor: pointer;
      position: relative;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
    }
    .thumb-img {
      width: 100%;
      height: 100%;
      object-fit: cover;
      transition: transform 0.2s;
    }
    .thumb-box:hover .thumb-img {
      transform: scale(1.08);
    }
    .thumb-hover-overlay {
      position: absolute;
      inset: 0;
      background: rgba(0, 0, 0, 0.45);
      display: flex;
      align-items: center;
      justify-content: center;
      opacity: 0;
      transition: opacity 0.2s;
      color: #fff;
      font-size: 0.72rem;
      font-weight: 500;
    }
    .thumb-box:hover .thumb-hover-overlay { opacity: 1; }

    .no-ref-badge {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 80px;
      height: 80px;
      border-radius: 8px;
      background: var(--surface-subtle);
      border: 1px dashed var(--border);
      color: var(--text-dim);
      font-size: 0.72rem;
      text-align: center;
      padding: 0.25rem;
    }

    /* 提示词与元信息 */
    .prompt-cell {
      display: flex;
      flex-direction: column;
      gap: 0.4rem;
      max-width: 460px;
    }
    .prompt-text {
      font-weight: 500;
      color: var(--text);
      font-size: 0.9rem;
      line-height: 1.4;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
      cursor: pointer;
    }
    .prompt-text:hover {
      color: var(--primary-light);
    }
    .tags-row {
      display: flex;
      align-items: center;
      gap: 0.45rem;
      flex-wrap: wrap;
    }

    /* 徽章 */
    .badge {
      display: inline-flex;
      align-items: center;
      padding: 0.2rem 0.55rem;
      border-radius: 9999px;
      font-size: 0.75rem;
      font-weight: 500;
      background: var(--badge-bg);
      color: var(--text-muted);
      border: 1px solid var(--border);
    }
    .badge-primary {
      background: rgba(37, 99, 235, 0.2);
      color: #93c5fd;
      border-color: rgba(59, 130, 246, 0.35);
    }
    .badge-accent {
      background: rgba(14, 165, 233, 0.2);
      color: #7dd3fc;
      border-color: rgba(14, 165, 233, 0.35);
    }
    .badge-time {
      background: rgba(16, 185, 129, 0.15);
      color: #6ee7b7;
      border-color: rgba(16, 185, 129, 0.35);
    }

    /* 操作列 */
    .action-cell {
      white-space: nowrap;
      display: flex;
      gap: 0.4rem;
      align-items: center;
      justify-content: flex-end;
    }

    /* 空态提示 */
    .empty-state {
      padding: 4rem 2rem;
      text-align: center;
      color: var(--text-muted);
    }
    .empty-icon {
      font-size: 3rem;
      margin-bottom: 0.75rem;
      opacity: 0.6;
    }

    /* 灯箱大图预览 Modal */
    .modal-backdrop {
      position: fixed;
      inset: 0;
      background: rgba(3, 7, 18, 0.9);
      backdrop-filter: blur(8px);
      z-index: 100;
      display: none;
      align-items: center;
      justify-content: center;
      padding: 1.5rem;
    }
    .modal-backdrop.active { display: flex; }
    .lightbox-content {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      width: 95%;
      max-width: 1200px;
      max-height: 94vh;
      display: flex;
      flex-direction: column;
      overflow: hidden;
      box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.8);
    }
    .lightbox-header {
      padding: 0.85rem 1.25rem;
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1rem;
      background: var(--surface-subtle);
    }
    .lightbox-body {
      flex: 1;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      position: relative;
      background: #020617;
      min-height: 480px;
      padding: 1rem;
      overflow: hidden;
    }
    .lightbox-img-wrapper {
      flex: 1;
      width: 100%;
      display: flex;
      align-items: center;
      justify-content: center;
      position: relative;
    }
    .lightbox-img {
      max-width: 100%;
      max-height: 60vh;
      object-fit: contain;
      border-radius: 6px;
      box-shadow: 0 8px 30px rgba(0, 0, 0, 0.7);
      transition: opacity 0.2s;
    }
    .nav-arrow {
      position: absolute;
      top: 50%;
      transform: translateY(-50%);
      width: 44px;
      height: 44px;
      background: rgba(17, 24, 39, 0.75);
      border: 1px solid var(--border);
      color: #fff;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 1.25rem;
      cursor: pointer;
      transition: all 0.15s;
      z-index: 10;
    }
    .nav-arrow:hover {
      background: var(--primary);
      border-color: var(--primary);
    }
    .nav-prev { left: 1.25rem; }
    .nav-next { right: 1.25rem; }

    /* 灯箱底部控制与提示词展示 */
    .lightbox-bottom-panel {
      padding: 0.85rem 1.25rem;
      border-top: 1px solid var(--border);
      background: var(--surface-subtle);
      display: flex;
      flex-direction: column;
      gap: 0.65rem;
    }
    .lightbox-tabs {
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }
    .tab-btn {
      padding: 0.35rem 0.85rem;
      border-radius: 6px;
      font-size: 0.82rem;
      font-weight: 500;
      border: 1px solid var(--border);
      background: var(--surface);
      color: var(--text-muted);
      cursor: pointer;
      transition: all 0.15s ease;
    }
    .tab-btn:hover { background: var(--surface-hover); color: var(--text); }
    .tab-btn.active {
      background: var(--primary);
      border-color: var(--primary);
      color: #fff;
    }
    .lightbox-prompt-box {
      background: rgba(0, 0, 0, 0.3);
      border: 1px solid rgba(55, 65, 81, 0.6);
      border-radius: 6px;
      padding: 0.65rem 0.85rem;
      font-size: 0.85rem;
      color: var(--text);
      line-height: 1.45;
      max-height: 80px;
      overflow-y: auto;
      user-select: text;
    }

    /* Toast 通知 */
    .toast {
      position: fixed;
      bottom: 2rem;
      right: 2rem;
      background: var(--surface-subtle);
      border: 1px solid var(--border);
      color: #fff;
      padding: 0.75rem 1.25rem;
      border-radius: 8px;
      box-shadow: var(--shadow);
      z-index: 200;
      display: none;
      align-items: center;
      gap: 0.5rem;
      font-size: 0.88rem;
    }
    .toast.active { display: flex; animation: slideDown 0.2s ease-out; }

    /* 认证弹窗 */
    .auth-modal {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 14px;
      width: 100%;
      max-width: 440px;
      padding: 2rem;
      box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.8);
      text-align: center;
    }
    .auth-icon {
      width: 3.2rem;
      height: 3.2rem;
      margin: 0 auto 1.25rem auto;
      background: rgba(37, 99, 235, 0.15);
      border: 1px solid rgba(59, 130, 246, 0.35);
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 1.5rem;
      color: var(--primary-light);
    }
    .auth-input {
      width: 100%;
      padding: 0.75rem 1rem;
      background: var(--surface-subtle);
      border: 1px solid var(--border);
      border-radius: 8px;
      color: #fff;
      font-size: 0.95rem;
      margin: 1.25rem 0;
      outline: none;
    }
    .auth-input:focus {
      border-color: var(--border-focus);
    }
    .auth-error {
      color: #f87171;
      font-size: 0.82rem;
      margin-top: -0.5rem;
      margin-bottom: 1rem;
      display: none;
    }

    /* 分页导航栏 */
    .pagination-bar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 1rem;
      padding: 0.85rem 1.25rem;
      background: var(--surface-subtle);
      border-top: 1px solid var(--border);
      font-size: 0.85rem;
    }
    .pagination-info {
      color: var(--text-muted);
      font-size: 0.82rem;
    }
    .pagination-info b {
      color: var(--text);
    }
    .pagination-controls {
      display: flex;
      align-items: center;
      gap: 0.45rem;
      flex-wrap: wrap;
    }
    .page-size-select {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 6px;
      color: var(--text);
      padding: 0.25rem 0.5rem;
      font-size: 0.82rem;
      cursor: pointer;
      outline: none;
    }
    .page-size-select:focus {
      border-color: var(--border-focus);
    }
    .pagination-numbers {
      display: flex;
      align-items: center;
      gap: 0.25rem;
    }
    .page-num-btn {
      min-width: 30px;
      height: 28px;
      padding: 0 0.35rem;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border-radius: 6px;
      border: 1px solid var(--border);
      background: var(--surface);
      color: var(--text);
      cursor: pointer;
      font-size: 0.8rem;
      transition: all 0.15s ease;
    }
    .page-num-btn:hover {
      background: var(--surface-hover);
      border-color: #4b5563;
    }
    .page-num-btn.active {
      background: var(--primary);
      border-color: var(--primary);
      color: #fff;
      font-weight: bold;
    }
    .page-num-btn:disabled, .btn:disabled {
      opacity: 0.35;
      cursor: not-allowed;
      pointer-events: none;
    }

    /* 移动端标签行：默认在桌面端隐藏 */
    .mobile-tags-row {
      display: none;
    }

    /* ==================== 移动端适配 (Responsive Mobile CSS) ==================== */
    @media (max-width: 768px) {
      body {
        font-size: 14px;
        -webkit-tap-highlight-color: transparent;
      }
      header {
        padding: 0.65rem 0.85rem;
        flex-wrap: wrap;
        gap: 0.65rem;
      }
      .brand-subtitle { display: none; }
      .header-actions {
        width: 100%;
        display: flex;
        align-items: center;
        gap: 0.5rem;
        justify-content: space-between;
      }
      .search-box { flex: 1; min-width: 0; }
      .search-input { width: 100% !important; font-size: 0.85rem; }
      #refreshBtn { padding: 0.45rem 0.65rem; font-size: 0.8rem; flex-shrink: 0; }

      main { padding: 0.85rem 0.65rem 5rem 0.65rem; }

      .dashboard-bar { margin-bottom: 0.85rem; }
      .stats-group {
        width: 100%;
        display: grid;
        grid-template-columns: repeat(2, 1fr);
        gap: 0.5rem;
      }
      .stat-chip {
        padding: 0.4rem 0.65rem;
        font-size: 0.76rem;
        justify-content: space-between;
      }

      /* 表格平滑重构为现代卡片布局 */
      .table-container {
        background: transparent;
        border: none;
        box-shadow: none;
        overflow: visible;
      }
      table thead { display: none; }
      table, tbody { display: block; width: 100%; }
      tbody tr {
        display: grid;
        grid-template-columns: 80px 1fr;
        grid-template-rows: auto auto auto;
        gap: 0.65rem;
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: var(--radius);
        padding: 0.75rem;
        margin-bottom: 0.75rem;
        position: relative;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25);
      }
      tbody tr.selected {
        border-color: var(--primary);
        background: rgba(37, 99, 235, 0.14);
      }
      td { display: block; padding: 0 !important; border: none !important; }

      .col-checkbox {
        position: absolute;
        top: 0.4rem;
        right: 0.4rem;
        z-index: 5;
        width: 36px;
        height: 36px;
        display: flex;
        align-items: center;
        justify-content: center;
      }
      .col-checkbox input {
        width: 20px;
        height: 20px;
        accent-color: var(--primary);
      }

      .col-thumb-result { grid-column: 1; grid-row: 1; }
      .col-thumb-ref { display: none !important; }
      .col-prompt {
        grid-column: 2;
        grid-row: 1;
        min-width: 0;
        padding-right: 1.8rem !important;
      }
      .col-model, .col-time, .col-date { display: none !important; }

      .mobile-tags-row {
        display: flex;
        align-items: center;
        gap: 0.35rem;
        flex-wrap: wrap;
        margin-top: 0.35rem;
        font-size: 0.72rem;
      }

      .col-actions {
        grid-column: 1 / -1;
        grid-row: 2;
        width: 100%;
        margin-top: 0.35rem;
        padding-top: 0.55rem !important;
        border-top: 1px solid rgba(55, 65, 81, 0.4) !important;
      }
      .action-cell {
        width: 100%;
        display: flex;
        gap: 0.45rem;
        justify-content: stretch !important;
      }
      .action-cell .btn {
        flex: 1;
        justify-content: center;
        padding: 0.5rem 0.25rem;
        font-size: 0.8rem;
      }

      /* 批量浮动栏吸底 */
      .batch-bar {
        position: fixed;
        bottom: 0;
        left: 0;
        right: 0;
        margin: 0;
        border-radius: 14px 14px 0 0;
        z-index: 90;
        padding: 0.75rem 1rem calc(env(safe-area-inset-bottom, 0px) + 0.75rem);
        flex-direction: column;
        gap: 0.5rem;
      }
      .batch-bar > div:first-child { width: 100%; display: flex; justify-content: space-between; }
      .batch-bar > div:last-child { width: 100%; }
      #batchDeleteBtn { width: 100%; justify-content: center; padding: 0.6rem; }

      /* 灯箱全屏 */
      .modal-backdrop { padding: 0; }
      .lightbox-content {
        width: 100vw;
        height: 100vh;
        max-width: 100vw;
        max-height: 100vh;
        border-radius: 0;
        border: none;
      }
    }
  </style>
</head>
<body>

  <!-- 顶部导航栏 -->
  <header>
    <a href="/admin" class="brand">
      <div class="brand-icon">🎨</div>
      <div>
        <div class="brand-title">AI Image Studio</div>
        <div class="brand-subtitle">图片生成历史与资产看板 · History & Assets</div>
      </div>
    </a>

    <div class="header-actions">
      <div class="search-box">
        <span class="search-icon">🔍</span>
        <input type="text" id="searchInput" class="search-input" placeholder="按提示词、模型或页面过滤...">
      </div>

      <button id="refreshBtn" class="btn" title="刷新记录列表">🔄 刷新</button>
      <button id="authBtn" class="btn btn-sm" title="管理访问授权密钥" style="display:none;">🔑 凭证</button>
    </div>
  </header>

  <!-- 页面主体 -->
  <main>
    <!-- 大盘汇总 -->
    <div class="dashboard-bar">
      <div class="stats-group">
        <div class="stat-chip">
          <span>累计生图</span>
          <b id="totalCount">0</b>
        </div>
        <div class="stat-chip">
          <span>平均耗时</span>
          <b id="avgDuration">0 s</b>
        </div>
        <div class="stat-chip">
          <span>图片存储</span>
          <b id="totalSize">0 MB</b>
        </div>
        <div class="stat-chip">
          <span>排序方式</span>
          <span style="color:var(--text-muted);">🕒 生成时间倒序</span>
        </div>
      </div>
    </div>

    <!-- 批量操作悬浮条 -->
    <div id="batchBar" class="batch-bar">
      <div style="display: flex; align-items: center; gap: 1rem;">
        <span id="batchCountText" style="font-weight: 600; font-size: 0.9rem;">已选择 0 条记录</span>
        <button id="cancelSelectBtn" class="btn btn-sm" style="background: rgba(255,255,255,0.1); border-color: rgba(255,255,255,0.2);">取消全选</button>
      </div>
      <div>
        <button id="batchDeleteBtn" class="btn btn-danger">🗑️ 批量彻底删除选中记录</button>
      </div>
    </div>

    <!-- 记录表格 -->
    <div class="table-container">
      <table>
        <thead>
          <tr>
            <th style="width: 40px; text-align: center;">
              <input type="checkbox" id="selectAllCheckbox">
            </th>
            <th style="width: 90px;">生成结果</th>
            <th style="width: 90px;">参考原图</th>
            <th>提示词与规格信息</th>
            <th style="width: 140px;">使用模型</th>
            <th style="width: 90px;">耗时</th>
            <th style="width: 150px;">生成时间</th>
            <th style="text-align: right; width: 220px;">管理操作</th>
          </tr>
        </thead>
        <tbody id="recordsTableBody">
          <tr>
            <td colspan="8" class="empty-state">
              <div class="empty-icon">⏳</div>
              <div>正在加载生图记录...</div>
            </td>
          </tr>
        </tbody>
      </table>

      <!-- 分页控制栏 -->
      <div id="paginationBar" class="pagination-bar">
        <div class="pagination-info">
          共 <b id="pgTotalItems">0</b> 条记录，第 <b id="pgCurrentPage">1</b> / <b id="pgTotalPages">1</b> 页
        </div>
        <div class="pagination-controls">
          <label style="font-size: 0.82rem; color: var(--text-muted); display: flex; align-items: center; gap: 0.4rem;">
            每页:
            <select id="pageSizeSelect" class="page-size-select" onchange="changePageSize(this.value)">
              <option value="15" selected>15 条</option>
              <option value="30">30 条</option>
              <option value="50">50 条</option>
              <option value="100">100 条</option>
            </select>
          </label>
          <button id="pgFirstBtn" class="btn btn-sm" onclick="goToPage(1)">⏮️ 首页</button>
          <button id="pgPrevBtn" class="btn btn-sm" onclick="goToPage(currentPage - 1)">◀ 上一页</button>
          <div id="pgNumButtons" class="pagination-numbers"></div>
          <button id="pgNextBtn" class="btn btn-sm" onclick="goToPage(currentPage + 1)">下一页 ▶</button>
          <button id="pgLastBtn" class="btn btn-sm" onclick="goToPage(totalPages)">末页 ⏭️</button>
        </div>
      </div>
    </div>
  </main>

  <!-- 全景大图预览 Modal (Lightbox) -->
  <div id="lightboxModal" class="modal-backdrop">
    <div class="lightbox-content">
      <div class="lightbox-header">
        <div style="min-width: 0; flex: 1;">
          <div id="lbPromptTitle" style="font-weight: 600; font-size: 0.95rem; color: #fff; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;"></div>
          <div id="lbMetaBadges" style="margin-top: 0.25rem; display: flex; gap: 0.4rem; align-items: center; flex-wrap: wrap;"></div>
        </div>
        <div style="display: flex; align-items: center; gap: 0.65rem; flex-shrink: 0;">
          <button class="btn btn-sm" onclick="copyCurrentPrompt()">📋 复制提示词</button>
          <a id="lbDownloadBtn" href="#" class="btn btn-sm btn-primary" download="ai_image.png">📥 下载原图</a>
          <button id="lbCloseBtn" class="btn btn-sm" style="font-size: 1.1rem; line-height: 1;">✕</button>
        </div>
      </div>

      <div class="lightbox-body">
        <button id="lbPrevBtn" class="nav-arrow nav-prev">◀</button>
        <div class="lightbox-img-wrapper">
          <img id="lbMainImg" class="lightbox-img" src="" alt="预览图">
        </div>
        <button id="lbNextBtn" class="nav-arrow nav-next">▶</button>
      </div>

      <div class="lightbox-bottom-panel">
        <div style="display: flex; align-items: center; justify-content: space-between;">
          <div id="lbTabsContainer" class="lightbox-tabs"></div>
          <span id="lbImageIndexIndicator" class="badge">1 / 1</span>
        </div>
        <div id="lbFullPrompt" class="lightbox-prompt-box"></div>
      </div>
    </div>
  </div>

  <!-- 授权登录 Modal -->
  <div id="authModal" class="modal-backdrop">
    <div class="auth-modal" style="position: relative;">
      <button id="authCloseBtn" class="btn btn-sm" style="position: absolute; top: 1rem; right: 1rem; background: transparent; border: none; font-size: 1.25rem; color: var(--text-muted); cursor: pointer;" title="关闭">✕</button>
      <div class="auth-icon">🔒</div>
      <h2 style="font-size: 1.25rem; font-weight: 700; margin-bottom: 0.5rem;">访问授权验证</h2>
      <p style="font-size: 0.85rem; color: var(--text-muted); line-height: 1.4;">
        当前服务已启用 <code style="color:var(--primary-light);">AUTH_KEY</code> 安全防护机制。<br>请输入授权访问密钥以解锁生图管理权限。
      </p>

      <input type="password" id="authKeyInput" class="auth-input" placeholder="输入 AUTH_KEY 访问凭证..." autocomplete="current-password">
      <div id="authErrorMsg" class="auth-error">授权密钥错误，请重新输入</div>

      <div style="display: flex; gap: 0.75rem; justify-content: center;">
        <button id="authSubmitBtn" class="btn btn-primary" style="width: 100%; padding: 0.7rem;">🔓 解锁并进入管理台</button>
      </div>
    </div>
  </div>

  <!-- Toast 提示条 -->
  <div id="toast" class="toast">
    <span id="toastIcon">ℹ️</span>
    <span id="toastText">操作成功</span>
  </div>

  <script>
    let allRecords = [];
    let selectedIds = new Set();
    let currentLightbox = {
      record: null,
      imagesList: [], // [{ url: '...', label: '生成结果' }, ...]
      currentIndex: 0
    };
    let currentPage = 1;
    let pageSize = 15;
    let totalPages = 1;
    let totalCount = 0;
    let searchQuery = "";
    let searchDebounceTimer = null;

    function getAuthKey() {
      const urlParams = new URLSearchParams(window.location.search);
      const urlKey = urlParams.get("auth_key");
      if (urlKey) {
        localStorage.setItem("img_admin_auth_key", urlKey);
        return urlKey;
      }
      return localStorage.getItem("img_admin_auth_key") || "";
    }

    function setAuthKey(key) {
      if (key) {
        localStorage.setItem("img_admin_auth_key", key);
      } else {
        localStorage.removeItem("img_admin_auth_key");
      }
    }

    function getAuthHeaders(extraHeaders = {}) {
      const key = getAuthKey();
      const headers = { ...extraHeaders };
      if (key) {
        headers["Authorization"] = `Bearer ${key}`;
      }
      return headers;
    }

    function getApiUrl(path) {
      if (!path) return "";
      if (path.startsWith("http://") || path.startsWith("https://") || path.startsWith("data:")) {
        return path;
      }
      if (window.location.port === "3000") {
        return window.location.protocol + "//" + window.location.hostname + ":8000" + (path.startsWith("/") ? path : "/" + path);
      }
      return path;
    }

    function handleImgError(img) {
      img.onerror = null;
      img.src = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='80' height='80' viewBox='0 0 80 80'%3E%3Crect width='80' height='80' fill='%231f2937'/%3E%3Ctext x='40' y='45' font-size='12' fill='%239ca3af' text-anchor='middle'%3E加载失败%3C/text%3E%3C/svg%3E";
    }

    function showToast(text, isError = false) {
      const toast = document.getElementById("toast");
      document.getElementById("toastIcon").innerText = isError ? "❌" : "✅";
      document.getElementById("toastText").innerText = text;
      toast.classList.add("active");
      setTimeout(() => toast.classList.remove("active"), 2600);
    }

    function formatBytes(bytes) {
      if (!bytes || bytes <= 0) return "0 B";
      const k = 1024;
      const sizes = ["B", "KB", "MB", "GB"];
      const i = Math.floor(Math.log(bytes) / Math.log(k));
      return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
    }

    function escapeHtml(str) {
      if (!str) return "";
      return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    }

    // 加载数据
    async function loadRecords(page = currentPage) {
      currentPage = page;
      const tbody = document.getElementById("recordsTableBody");
      try {
        const params = new URLSearchParams({
          page: currentPage,
          page_size: pageSize,
          search: searchQuery
        });
        const resp = await fetch(getApiUrl(`/admin/api/records?${params.toString()}`), {
          headers: getAuthHeaders()
        });
        if (resp.status === 401) {
          document.getElementById("authBtn").style.display = "inline-flex";
          tbody.innerHTML = `
            <tr>
              <td colspan="8" class="empty-state">
                <div class="empty-icon">🔒</div>
                <div style="font-size:1.05rem; font-weight:600; color:#fff; margin-bottom:0.4rem;">需要管理员访问授权</div>
                <div style="font-size:0.85rem; color:var(--text-muted); margin-bottom:1.25rem; max-width:420px; margin-left:auto; margin-right:auto; line-height:1.5;">
                  当前服务已启用 AUTH_KEY 安全防护机制，请输入授权凭证以查看生图历史记录与统计数据。
                </div>
                <button class="btn btn-primary btn-sm" onclick="showAuthModal(false)">🔑 输入访问凭证</button>
              </td>
            </tr>
          `;
          showAuthModal(true);
          return;
        }
        if (!resp.ok) throw new Error("加载记录失败 (" + resp.status + ")");

        const data = await resp.json();
        if (data.auth_required) {
          document.getElementById("authBtn").style.display = "inline-flex";
        }
        allRecords = data.items || [];
        totalCount = data.total || 0;
        totalPages = data.total_pages || 1;
        currentPage = data.page || 1;

        renderTable(allRecords);
        renderPagination();
        loadStats();
      } catch (err) {
        tbody.innerHTML = `
          <tr>
            <td colspan="8" class="empty-state">
              <div class="empty-icon">⚠️</div>
              <div style="color:#f87171;">${escapeHtml(err.message)}</div>
              <button class="btn btn-sm" onclick="loadRecords()" style="margin-top:1rem;">重试</button>
            </td>
          </tr>
        `;
      }
    }

    async function loadStats() {
      try {
        const resp = await fetch(getApiUrl("/admin/api/stats"), {
          headers: getAuthHeaders()
        });
        if (resp.ok) {
          const stats = await resp.json();
          document.getElementById("totalCount").innerText = stats.total_count || 0;
          document.getElementById("avgDuration").innerText = (stats.avg_duration || 0) + " s";
          document.getElementById("totalSize").innerText = formatBytes(stats.total_disk_bytes || 0);
        }
      } catch (e) {}
    }

    function renderPagination() {
      const bar = document.getElementById("paginationBar");
      if (!bar) return;
      document.getElementById("pgTotalItems").innerText = totalCount;
      document.getElementById("pgCurrentPage").innerText = currentPage;
      document.getElementById("pgTotalPages").innerText = totalPages;

      document.getElementById("pgFirstBtn").disabled = currentPage <= 1;
      document.getElementById("pgPrevBtn").disabled = currentPage <= 1;
      document.getElementById("pgNextBtn").disabled = currentPage >= totalPages;
      document.getElementById("pgLastBtn").disabled = currentPage >= totalPages;

      const numContainer = document.getElementById("pgNumButtons");
      numContainer.innerHTML = "";

      let startP = Math.max(1, currentPage - 2);
      let endP = Math.min(totalPages, startP + 4);
      if (endP - startP < 4) {
        startP = Math.max(1, endP - 4);
      }

      for (let p = startP; p <= endP; p++) {
        const btn = document.createElement("button");
        btn.className = `page-num-btn ${p === currentPage ? 'active' : ''}`;
        btn.innerText = p;
        btn.onclick = () => goToPage(p);
        numContainer.appendChild(btn);
      }
    }

    function goToPage(p) {
      if (p < 1 || p > totalPages || p === currentPage) return;
      loadRecords(p);
    }

    function changePageSize(sz) {
      pageSize = parseInt(sz, 10) || 15;
      loadRecords(1);
    }

    // 渲染表格
    function renderTable(records) {
      const tbody = document.getElementById("recordsTableBody");
      updateBatchBar();

      const allCurrentSelected = records.length > 0 && records.every(r => selectedIds.has(r.id));
      document.getElementById("selectAllCheckbox").checked = allCurrentSelected;

      if (!records.length) {
        tbody.innerHTML = `
          <tr>
            <td colspan="8" class="empty-state">
              <div class="empty-icon">🎨</div>
              <div>暂无图片生成记录</div>
              <div style="font-size:0.75rem; color:var(--text-dim); margin-top:0.35rem;">
                在任意生图页面生成成功后，将自动在此记录并保存结果与参考图
              </div>
            </td>
          </tr>
        `;
        return;
      }

      tbody.innerHTML = records.map(r => {
        const isSelected = selectedIds.has(r.id);
        const firstGenImg = (r.images && r.images.length > 0) ? getApiUrl(r.images[0]) : "";
        const firstRefImg = (r.ref_images && r.ref_images.length > 0) ? getApiUrl(r.ref_images[0]) : "";
        const durationStr = r.duration ? `${r.duration}s` : "0s";
        const extraSize = r.extra && r.extra.size ? r.extra.size : "";

        // 生成图缩略
        const resultThumbHtml = firstGenImg ? `
          <div class="thumb-box" onclick="openLightbox('${r.id}', 0)">
            <img src="${firstGenImg}" class="thumb-img" alt="生成结果" loading="lazy" onerror="handleImgError(this)">
            <div class="thumb-hover-overlay">👁️ 预览</div>
          </div>
        ` : `<div class="no-ref-badge">无结果图</div>`;

        // 参考图缩略
        const refThumbHtml = firstRefImg ? `
          <div class="thumb-box" onclick="openLightbox('${r.id}', 'ref')">
            <img src="${firstRefImg}" class="thumb-img" alt="参考原图" loading="lazy" onerror="handleImgError(this)">
            <div class="thumb-hover-overlay">👁️ 原图</div>
          </div>
        ` : `<div class="no-ref-badge">无参考图</div>`;

        return `
          <tr id="row-${r.id}" class="${isSelected ? 'selected' : ''}">
            <td class="col-checkbox" style="text-align: center;">
              <input type="checkbox" class="row-checkbox" data-id="${r.id}" ${isSelected ? 'checked' : ''} onchange="toggleSelect('${r.id}')">
            </td>
            <td class="col-thumb-result">${resultThumbHtml}</td>
            <td class="col-thumb-ref">${refThumbHtml}</td>
            <td class="col-prompt">
              <div class="prompt-cell">
                <div class="prompt-text" title="${escapeHtml(r.prompt)}" onclick="openLightbox('${r.id}', 0)">
                  ${escapeHtml(r.prompt || '（无提示词）')}
                </div>
                <div class="tags-row">
                  ${r.source ? `<span class="badge badge-accent">${escapeHtml(r.source)}</span>` : ''}
                  ${extraSize ? `<span class="badge">${escapeHtml(extraSize)}</span>` : ''}
                  <span class="badge badge-time">⚡ ${durationStr}</span>
                </div>
                <div class="mobile-tags-row">
                  <span class="badge badge-primary">${escapeHtml(r.model)}</span>
                  <span>🕒 ${r.created_at ? r.created_at.slice(5) : '-'}</span>
                </div>
              </div>
            </td>
            <td class="col-model">
              <span class="badge badge-primary">${escapeHtml(r.model)}</span>
            </td>
            <td class="col-time" style="color:var(--text-muted); font-size:0.84rem;">
              ⚡ ${durationStr}
            </td>
            <td class="col-date" style="color:var(--text-muted); font-size:0.82rem;">
              ${r.created_at || '-'}
            </td>
            <td class="col-actions" style="text-align: right;">
              <div class="action-cell">
                <button class="btn btn-sm btn-primary" onclick="openLightbox('${r.id}', 0)" title="全景预览高清大图与提示词">👁️ 预览</button>
                <button class="btn btn-sm" onclick="copyRecordPrompt('${r.id}')" title="复制此生成提示词">📋 复制</button>
                ${firstGenImg ? `<a href="${firstGenImg}" class="btn btn-sm" download="image_${r.id.slice(0,8)}.png" title="直接下载高清结果原图">📥 下载</a>` : ''}
                <button class="btn btn-sm btn-danger" onclick="deleteSingle('${r.id}')" title="彻底删除此记录">🗑️</button>
              </div>
            </td>
          </tr>
        `;
      }).join("");
    }

    // 选中操作
    function toggleSelect(id) {
      if (selectedIds.has(id)) {
        selectedIds.delete(id);
      } else {
        selectedIds.add(id);
      }
      const row = document.getElementById("row-" + id);
      if (row) {
        if (selectedIds.has(id)) row.classList.add("selected");
        else row.classList.remove("selected");
      }
      updateBatchBar();
    }

    document.getElementById("selectAllCheckbox").addEventListener("change", function(e) {
      const checked = e.target.checked;
      const visibleCheckboxes = document.querySelectorAll(".row-checkbox");
      visibleCheckboxes.forEach(cb => {
        const id = cb.getAttribute("data-id");
        cb.checked = checked;
        if (checked) {
          selectedIds.add(id);
          document.getElementById("row-" + id)?.classList.add("selected");
        } else {
          selectedIds.delete(id);
          document.getElementById("row-" + id)?.classList.remove("selected");
        }
      });
      updateBatchBar();
    });

    document.getElementById("cancelSelectBtn").addEventListener("click", () => {
      selectedIds.clear();
      document.querySelectorAll(".row-checkbox").forEach(cb => cb.checked = false);
      document.querySelectorAll("tbody tr").forEach(tr => tr.classList.remove("selected"));
      document.getElementById("selectAllCheckbox").checked = false;
      updateBatchBar();
    });

    function updateBatchBar() {
      const bar = document.getElementById("batchBar");
      const text = document.getElementById("batchCountText");
      if (selectedIds.size > 0) {
        bar.classList.add("active");
        text.innerText = `已勾选 ${selectedIds.size} 条记录`;
      } else {
        bar.classList.remove("active");
      }
    }

    // 复制提示词
    function copyPrompt(text) {
      if (!text) {
        showToast("提示词为空", true);
        return;
      }
      navigator.clipboard.writeText(text).then(() => {
        showToast("已成功复制提示词到剪贴板");
      }).catch(() => {
        // 兼容降级
        const ta = document.createElement("textarea");
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
        showToast("已成功复制提示词");
      });
    }

    function copyRecordPrompt(id) {
      const r = allRecords.find(item => item.id === id);
      if (r) {
        copyPrompt(r.prompt || "");
      }
    }

    function copyCurrentPrompt() {
      if (currentLightbox.record) {
        copyPrompt(currentLightbox.record.prompt);
      }
    }

    // 删除单项
    async function deleteSingle(id) {
      if (!confirm("确定彻底删除此生图记录及保存的本地图片文件吗？\\n此操作不可撤销。")) {
        return;
      }
      try {
        const resp = await fetch(getApiUrl(`/admin/api/records/${encodeURIComponent(id)}`), {
          method: "DELETE",
          headers: getAuthHeaders()
        });
        if (resp.status === 401) {
          showAuthModal(true);
          return;
        }
        if (!resp.ok) throw new Error("删除失败");
        showToast("已成功删除记录");
        loadRecords();
      } catch (err) {
        showToast(err.message, true);
      }
    }

    // 批量删除
    document.getElementById("batchDeleteBtn").addEventListener("click", async () => {
      const count = selectedIds.size;
      if (count === 0) return;
      if (!confirm(`⚠️ 风险确认：\\n确定批量彻底删除选中的 ${count} 条生图记录及本地图片吗？`)) {
        return;
      }
      try {
        const resp = await fetch(getApiUrl("/admin/api/records/batch_delete"), {
          method: "POST",
          headers: getAuthHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({ ids: Array.from(selectedIds) })
        });
        if (resp.status === 401) {
          showAuthModal(true);
          return;
        }
        if (!resp.ok) throw new Error("批量删除失败 (" + resp.status + ")");
        const res = await resp.json();
        showToast(`已批量删除 ${res.deleted_count} 条记录`);
        selectedIds.clear();
        loadRecords();
      } catch (err) {
        showToast(err.message, true);
      }
    });

    // 搜索过滤
    document.getElementById("searchInput").addEventListener("input", function(e) {
      clearTimeout(searchDebounceTimer);
      searchQuery = e.target.value.trim();
      searchDebounceTimer = setTimeout(() => {
        loadRecords(1);
      }, 300);
    });

    document.getElementById("refreshBtn").addEventListener("click", () => loadRecords(1));

    // ==================== 全景预览灯箱 (Lightbox) ====================
    function openLightbox(recordId, initialIndex = 0) {
      const r = allRecords.find(item => item.id === recordId);
      if (!r) return;

      currentLightbox.record = r;
      currentLightbox.imagesList = [];

      // 填充可预览图片列表
      (r.images || []).forEach((url, i) => {
        currentLightbox.imagesList.push({
          url: getApiUrl(url),
          label: (r.images.length > 1) ? `🖼️ 生成图 #${i+1}` : `🖼️ 生成结果`,
          type: "gen"
        });
      });
      (r.ref_images || []).forEach((url, i) => {
        currentLightbox.imagesList.push({
          url: getApiUrl(url),
          label: (r.ref_images.length > 1) ? `📎 参考图 #${i+1}` : `📎 参考原图`,
          type: "ref"
        });
      });

      if (currentLightbox.imagesList.length === 0) {
        showToast("此记录无可用预览图片", true);
        return;
      }

      let targetIndex = 0;
      if (initialIndex === "ref") {
        const refIdx = currentLightbox.imagesList.findIndex(item => item.type === "ref");
        targetIndex = refIdx >= 0 ? refIdx : 0;
      } else if (typeof initialIndex === "number" && initialIndex < currentLightbox.imagesList.length) {
        targetIndex = initialIndex;
      }

      document.getElementById("lbPromptTitle").innerText = r.prompt || "（无提示词）";
      document.getElementById("lbFullPrompt").innerText = r.prompt || "（无提示词）";

      // 渲染顶部徽章
      const metaContainer = document.getElementById("lbMetaBadges");
      metaContainer.innerHTML = `
        <span class="badge badge-primary">${escapeHtml(r.model)}</span>
        <span class="badge badge-time">⚡ ${r.duration}s</span>
        ${r.source ? `<span class="badge badge-accent">${escapeHtml(r.source)}</span>` : ''}
        ${r.extra && r.extra.size ? `<span class="badge">${escapeHtml(r.extra.size)}</span>` : ''}
        <span class="badge">${r.created_at || ''}</span>
      `;

      // 渲染底部 Tab 切换
      const tabsContainer = document.getElementById("lbTabsContainer");
      tabsContainer.innerHTML = "";
      currentLightbox.imagesList.forEach((item, idx) => {
        const btn = document.createElement("button");
        btn.className = `tab-btn ${idx === targetIndex ? 'active' : ''}`;
        btn.innerText = item.label;
        btn.onclick = () => showLightboxImage(idx);
        tabsContainer.appendChild(btn);
      });

      showLightboxImage(targetIndex);
      document.getElementById("lightboxModal").classList.add("active");
    }

    function showLightboxImage(index) {
      if (index < 0 || index >= currentLightbox.imagesList.length) return;
      currentLightbox.currentIndex = index;

      const item = currentLightbox.imagesList[index];
      const mainImg = document.getElementById("lbMainImg");
      mainImg.style.opacity = "0.3";
      mainImg.src = item.url;
      mainImg.onload = () => { mainImg.style.opacity = "1"; };

      document.getElementById("lbDownloadBtn").href = item.url;
      document.getElementById("lbImageIndexIndicator").innerText = `${index + 1} / ${currentLightbox.imagesList.length}`;

      // 更新 Tab 按钮状态
      const tabBtns = document.querySelectorAll("#lbTabsContainer .tab-btn");
      tabBtns.forEach((btn, idx) => {
        if (idx === index) btn.classList.add("active");
        else btn.classList.remove("active");
      });
    }

    function prevLightboxImage() {
      const len = currentLightbox.imagesList.length;
      if (len <= 1) return;
      const nextIdx = (currentLightbox.currentIndex - 1 + len) % len;
      showLightboxImage(nextIdx);
    }

    function nextLightboxImage() {
      const len = currentLightbox.imagesList.length;
      if (len <= 1) return;
      const nextIdx = (currentLightbox.currentIndex + 1) % len;
      showLightboxImage(nextIdx);
    }

    document.getElementById("lbPrevBtn").addEventListener("click", prevLightboxImage);
    document.getElementById("lbNextBtn").addEventListener("click", nextLightboxImage);
    document.getElementById("lbCloseBtn").addEventListener("click", closeLightbox);
    document.getElementById("lightboxModal").addEventListener("click", (e) => {
      if (e.target.id === "lightboxModal") closeLightbox();
    });

    function closeLightbox() {
      document.getElementById("lightboxModal").classList.remove("active");
    }

    // 快捷键支持
    document.addEventListener("keydown", (e) => {
      if (document.getElementById("lightboxModal").classList.contains("active")) {
        if (e.key === "ArrowLeft") prevLightboxImage();
        else if (e.key === "ArrowRight") nextLightboxImage();
        else if (e.key === "Escape") closeLightbox();
      } else if (document.getElementById("authModal").classList.contains("active")) {
        if (e.key === "Escape") closeAuthModal();
      }
    });

    // 移动端左右滑动手势
    let touchStartX = 0;
    let touchStartY = 0;
    const lbModal = document.getElementById("lightboxModal");
    lbModal.addEventListener("touchstart", (e) => {
      if (e.changedTouches && e.changedTouches.length > 0) {
        touchStartX = e.changedTouches[0].screenX;
        touchStartY = e.changedTouches[0].screenY;
      }
    }, { passive: true });

    lbModal.addEventListener("touchend", (e) => {
      if (!lbModal.classList.contains("active")) return;
      if (e.changedTouches && e.changedTouches.length > 0) {
        const deltaX = e.changedTouches[0].screenX - touchStartX;
        const deltaY = e.changedTouches[0].screenY - touchStartY;
        if (Math.abs(deltaX) > 40 && Math.abs(deltaX) > Math.abs(deltaY) * 1.2) {
          if (deltaX < 0) nextLightboxImage();
          else prevLightboxImage();
        }
      }
    }, { passive: true });

    // ==================== 访问授权 (Auth Modal) ====================
    function showAuthModal(hasError = false) {
      const modal = document.getElementById("authModal");
      const err = document.getElementById("authErrorMsg");
      const input = document.getElementById("authKeyInput");
      input.value = getAuthKey();
      err.style.display = hasError ? "block" : "none";
      modal.classList.add("active");
      setTimeout(() => input.focus(), 100);
    }

    function closeAuthModal() {
      document.getElementById("authModal").classList.remove("active");
    }

    document.getElementById("authCloseBtn")?.addEventListener("click", closeAuthModal);
    document.getElementById("authModal")?.addEventListener("click", (e) => {
      if (e.target.id === "authModal") closeAuthModal();
    });

    document.getElementById("authBtn").addEventListener("click", () => showAuthModal(false));

    document.getElementById("authSubmitBtn").addEventListener("click", async () => {
      const key = document.getElementById("authKeyInput").value.trim();
      setAuthKey(key);
      document.getElementById("authErrorMsg").style.display = "none";
      try {
        const resp = await fetch(getApiUrl("/admin/api/verify"), {
          method: "POST",
          headers: getAuthHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({ key: key })
        });
        if (resp.status === 401) {
          document.getElementById("authErrorMsg").style.display = "block";
          return;
        }
        closeAuthModal();
        showToast("授权验证成功");
        document.getElementById("authBtn").style.display = "inline-flex";
        loadRecords();
      } catch (err) {
        document.getElementById("authErrorMsg").style.display = "block";
      }
    });

    document.getElementById("authKeyInput").addEventListener("keypress", (e) => {
      if (e.key === "Enter") {
        document.getElementById("authSubmitBtn").click();
      }
    });

    // 启动加载
    loadRecords();
  </script>
</body>
</html>
"""


def setup_admin(app: rx.App):
    """为 Reflex App 注册管理后台 Starlette 路由与静态资源服务。"""

    # 1. 管理页面 HTML
    async def serve_admin_page(req: Request):
        return HTMLResponse(get_admin_html())

    # 1.5 验证授权码 API
    async def api_verify(req: Request):
        auth_key_env = get_auth_key()
        if not auth_key_env:
            return JSONResponse({"ok": True, "auth_required": False})

        candidate = req.query_params.get("auth_key", "")
        if not candidate:
            try:
                body = await req.json()
                candidate = body.get("key", "")
            except Exception:
                pass
        if not candidate:
            auth_header = req.headers.get("Authorization", "")
            if auth_header:
                parts = auth_header.strip().split()
                if len(parts) == 2 and parts[0].lower() == "bearer":
                    candidate = parts[1]
                elif len(parts) == 1:
                    candidate = parts[0]

        if candidate == auth_key_env:
            return JSONResponse({"ok": True, "auth_required": True})
        return JSONResponse({"error": "Invalid AUTH_KEY", "auth_required": True}, status_code=401)

    # 2. 记录列表 API
    async def api_records(req: Request):
        if not is_auth_valid(req):
            return JSONResponse({"error": "Unauthorized", "auth_required": True}, status_code=401)
        page = int(req.query_params.get("page", 1))
        page_size = int(req.query_params.get("page_size", 15))
        search = req.query_params.get("search", "")
        data = get_records(page=page, page_size=page_size, search=search)
        data["auth_required"] = bool(get_auth_key())
        return JSONResponse(data)

    # 3. 大盘统计 API
    async def api_stats(req: Request):
        if not is_auth_valid(req):
            return JSONResponse({"error": "Unauthorized", "auth_required": True}, status_code=401)
        stats = get_stats()
        stats["auth_required"] = bool(get_auth_key())
        return JSONResponse(stats)

    # 4. 删除单条记录 API
    async def api_delete_single(req: Request):
        if not is_auth_valid(req):
            return JSONResponse({"error": "Unauthorized", "auth_required": True}, status_code=401)
        record_id = req.path_params.get("id")
        delete_record_by_id(record_id)
        return JSONResponse({"status": "ok", "deleted_id": record_id})

    # 5. 批量删除 API
    async def api_delete_batch(req: Request):
        if not is_auth_valid(req):
            return JSONResponse({"error": "Unauthorized", "auth_required": True}, status_code=401)
        try:
            body = await req.json()
            ids = body.get("ids", [])
            cnt = batch_delete_records(ids)
            return JSONResponse({"status": "ok", "deleted_count": cnt})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    # 注册 Starlette 路由（全部统一到 /admin 前缀，简化 Nginx 单规则反代）
    app._api.add_route("/admin", serve_admin_page, methods=["GET"])
    app._api.add_route("/admin/api/verify", api_verify, methods=["POST", "GET"])
    app._api.add_route("/admin/api/records", api_records, methods=["GET"])
    app._api.add_route("/admin/api/stats", api_stats, methods=["GET"])
    app._api.add_route("/admin/api/records/{id}", api_delete_single, methods=["DELETE"])
    app._api.add_route("/admin/api/records/batch_delete", api_delete_batch, methods=["POST"])

    # 兼容历史别名路由
    app._api.add_route("/manage", serve_admin_page, methods=["GET"])
    app._api.add_route("/history", serve_admin_page, methods=["GET"])
    app._api.add_route("/api/admin/verify", api_verify, methods=["POST", "GET"])
    app._api.add_route("/api/admin/records", api_records, methods=["GET"])
    app._api.add_route("/api/admin/stats", api_stats, methods=["GET"])
    app._api.add_route("/api/admin/records/{id}", api_delete_single, methods=["DELETE"])
    app._api.add_route("/api/admin/records/batch_delete", api_delete_batch, methods=["POST"])

    # 6. 挂载持久化图片静态资源服务（优先 /admin/history_files，同时保留 /history_files 兼容）
    app._api.mount(
        "/admin/history_files",
        StaticFiles(directory=str(HISTORY_DIR)),
        name="admin_history_files",
    )
    app._api.mount(
        "/history_files",
        StaticFiles(directory=str(HISTORY_DIR)),
        name="history_files",
    )


    # 7. 同时注册 Reflex 原生页面，方便在 Reflex 前端开发端口 (3000) 访问
    app.add_page(index, route="/admin", title="图片生成记录管理后台")


def index():
    """Reflex 原生页面封装，支持在端口 3000 或前端打包模式下流畅嵌入后台。"""
    return rx.box(
        rx.el.iframe(
            id="admin-frame",
            src="about:blank",
            style={
                "width": "100vw",
                "height": "100vh",
                "border": "none",
                "margin": "0",
                "padding": "0",
                "position": "fixed",
                "top": "0",
                "left": "0",
                "zIndex": "9999",
                "backgroundColor": "#0b0f19",
            },
        ),
        rx.script("""
            (function() {
                function initAdminFrame() {
                    var frame = document.getElementById('admin-frame');
                    if (frame) {
                        var baseTarget = (window.location.port === '3000')
                            ? (window.location.protocol + '//' + window.location.hostname + ':8000/admin')
                            : (window.location.protocol + '//' + window.location.host + '/admin');
                        var target = baseTarget + (window.location.search || '');
                        if (frame.src !== target) {
                            frame.src = target;
                        }
                    } else {
                        setTimeout(initAdminFrame, 50);
                    }
                }
                if (document.readyState === 'loading') {
                    document.addEventListener('DOMContentLoaded', initAdminFrame);
                } else {
                    initAdminFrame();
                }
            })();
        """),
        width="100%",
        height="100vh",
    )

