import asyncio
import base64
import hashlib
import json
import os
import re
import time
from datetime import date
from urllib.parse import parse_qs, urlparse

import aiohttp
import reflex as rx

from image_gen_page.quota_service import (
    parse_quota,
    parse_model_quotas,
    resolve_model_quota,
    reserve_generation_quota,
    get_quota_exceeded_msg,
    reset_quota,
    _quota_usage,
)


DEFAULT_SIZE_OPTIONS = [
    # 16:9 横屏
    "1280x720 (16:9 高清横屏)",
    "1920x1080 (16:9 全高清横屏)",
    "1792x1024 (16:9 超清横屏)",
    "960x540 (16:9 标清横屏)",

    # 9:16 竖屏（短视频 / 手机）
    "720x1280 (9:16 高清竖屏)",
    "1080x1920 (9:16 全高清竖屏)",
    "1024x1792 (9:16 超清竖屏)",
    "540x960 (9:16 标清竖屏)",

    # 1:1 正方形（头像 / 社交动态 / 电商）
    "1024x1024 (1:1 超清正方形)",
    "720x720 (1:1 高清正方形)",
    "512x512 (1:1 标清正方形)",

    # 4:3 经典横屏（传统电视 / iPad / 复古画幅）
    "1024x768 (4:3 高清横屏)",
    "960x720 (4:3 标清横屏)",
    "1440x1080 (4:3 全清横屏)",

    # 3:4 人像竖屏（小红书卡片 / 人像摄影）
    "768x1024 (3:4 高清竖屏)",
    "720x960 (3:4 标清竖屏)",
    "1080x1440 (3:4 全清竖屏)",

    # 21:9 宽屏电影（大片质感 / 宽银幕）
    "1680x720 (21:9 宽屏电影)",
    "1920x820 (21:9 宽银幕电影)",

    # 3:2 单反摄影横屏
    "1080x720 (3:2 摄影横屏)",
    "1536x1024 (3:2 高清横屏)",

    # 2:3 海报摄影竖屏
    "720x1080 (2:3 摄影竖屏)",
    "1024x1536 (2:3 高清竖屏)",

    # 自定义输入
    "自定义尺寸...",
]

RATIO_TO_RESOLUTION_MAP = {
    "16:9": "1280x720",
    "9:16": "720x1280",
    "1:1": "1024x1024",
    "4:3": "1024x768",
    "3:4": "768x1024",
    "21:9": "1680x720",
    "9:21": "720x1680",
    "3:2": "1080x720",
    "2:3": "720x1080",
    "7:4": "1792x1024",
    "4:7": "1024x1792",
    "16:10": "1280x800",
    "10:16": "800x1280",
}


def normalize_video_size(value: str) -> str:
    val = (value or "").strip()
    if not val or "自定义" in val:
        return "1280x720"

    # 1. 优先匹配具体分辨率如 1280x720, 1920x1080, 1080*1920
    res_match = re.search(r"(\d+)\s*[xX*]\s*(\d+)", val)
    if res_match:
        w, h = res_match.group(1), res_match.group(2)
        return f"{w}x{h}"

    # 2. 匹配比例并映射到常用高清分辨率枚举
    ratio_match = re.search(r"(\d+:\d+)", val)
    if ratio_match:
        ratio = ratio_match.group(1)
        return RATIO_TO_RESOLUTION_MAP.get(ratio, "1280x720")

    clean_val = val.split("(")[0].strip()
    return clean_val or "1280x720"


def parse_video_size_options(value: str) -> list[str]:
    options = []
    for item in (value or "").split(","):
        s = item.strip()
        if s:
            options.append(s)
    return options


def find_video_size_option(options: list[str], size: str) -> str:
    if size in options:
        return size
    norm = normalize_video_size(size)
    for opt in options:
        if normalize_video_size(opt) == norm:
            return opt
    return ""


DEFAULT_DURATION_OPTIONS = ["5秒", "10秒"]


def normalize_duration(value: str, default: int = 5) -> int:
    try:
        nums = re.findall(r"\d+", str(value))
        if nums:
            d = int(nums[0])
            return d if d > 0 else default
    except Exception:
        pass
    return default


def parse_duration_options(value: str) -> list[str]:
    options = []
    for item in (value or "").split(","):
        d = item.strip()
        if d:
            options.append(d)
    return options


def find_duration_option(options: list[str], duration: str) -> str:
    if duration in options:
        return duration
    norm = normalize_duration(duration)
    for opt in options:
        if normalize_duration(opt) == norm:
            return opt
    return ""


def get_default_models() -> list[str]:
    default_model = os.getenv("TEXT2VIDEO_MODEL", "muse-video")
    models = [
        item.strip()
        for item in os.getenv("TEXT2VIDEO_MODELS", default_model).split(",")
        if item.strip()
    ]
    if not models:
        models = [default_model]
    if default_model not in models:
        models.insert(0, default_model)
    return models


def get_default_sizes() -> list[str]:
    raw_sizes = os.getenv("TEXT2VIDEO_SIZES", "")
    sizes = parse_video_size_options(raw_sizes) if raw_sizes else DEFAULT_SIZE_OPTIONS.copy()
    if not sizes:
        sizes = DEFAULT_SIZE_OPTIONS.copy()
    if "自定义尺寸..." not in sizes:
        sizes.append("自定义尺寸...")
    return sizes


def get_default_durations() -> list[str]:
    raw_durations = os.getenv("TEXT2VIDEO_DURATIONS", "")
    durations = parse_duration_options(raw_durations) if raw_durations else DEFAULT_DURATION_OPTIONS.copy()
    return durations if durations else DEFAULT_DURATION_OPTIONS.copy()


def get_video_endpoints(base_url: str) -> tuple[str, str]:
    base = (base_url or "").strip().rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return f"{base}/videos", f"{base}/videos"


def resolve_media_url(base_url: str, video_url: str) -> str:
    if not video_url:
        return ""
    if video_url.startswith("http://") or video_url.startswith("https://") or video_url.startswith("data:"):
        base = (base_url or "").strip().rstrip("/")
        if base.startswith("https://") and video_url.startswith("http://"):
            video_url = "https://" + video_url[7:]
        return video_url
    base = (base_url or "").strip().rstrip("/")
    parsed = urlparse(base)
    origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else base
    if video_url.startswith("/"):
        return f"{origin}{video_url}"
    return f"{base}/{video_url}"


def extract_video_url(data: dict) -> str | None:
    if not isinstance(data, dict):
        return None

    # Handle New-API wrapper {"code": "success", "data": {...}}
    inner_data = data.get("data")
    if isinstance(inner_data, dict):
        r_url = inner_data.get("result_url")
        if isinstance(r_url, str) and (r_url.startswith("http") or r_url.startswith("/")) and not r_url.startswith("Failed to"):
            return r_url
        nested = extract_video_url(inner_data)
        if nested:
            return nested

    # 1. Result object (standard in muse2api: {"result": {"url": "..."}})
    res = data.get("result")
    if isinstance(res, dict):
        url = res.get("url") or res.get("video_url")
        if url:
            return str(url)
    elif isinstance(res, str) and (res.startswith("http") or res.startswith("/") or res.endswith(".mp4")):
        return res

    # 2. Direct url fields
    for k in ("url", "video_url", "result_url"):
        val = data.get(k)
        if val and isinstance(val, str) and (val.startswith("http") or val.startswith("/")) and not val.startswith("Failed to"):
            return val

    # 3. Data array (OpenAI-like {"data": [{"url": "..."}]})
    items = data.get("data")
    if isinstance(items, list) and items:
        first = items[0]
        if isinstance(first, dict):
            url = first.get("url") or first.get("video_url")
            if url:
                return str(url)
        elif isinstance(first, str):
            return first

    # 4. Output field
    out = data.get("output")
    if isinstance(out, dict):
        url = out.get("url") or out.get("video_url")
        if url:
            return str(url)
    elif isinstance(out, str) and (out.startswith("http") or out.startswith("/")):
        return out

    return None


class Text2VideoState(rx.State):
    prompt: str = ""
    video_urls: list[str] = []
    processing: bool = False
    complete: bool = False
    status_text: str = ""
    initialized_from_url: bool = False
    uploading: bool = False
    upload_imgs: list[str] = []
    error_msg: str = ""
    max_files: int = 1

    model: str = get_default_models()[0]
    model_options: list[str] = get_default_models()
    available_models: list[str] = get_default_models()
    allow_edit: bool = os.getenv("TEXT2VIDEO_ALLOW_EDIT", "0") == "1"

    size: str = get_default_sizes()[0]
    size_options: list[str] = get_default_sizes()
    custom_size: str = ""

    duration: str = get_default_durations()[0]
    duration_options: list[str] = get_default_durations()

    title: str = os.getenv("TEXT2VIDEO_TITLE", "通用文生视频生成器")[:60]
    open_id: str = ""
    daily_limit: int = -1
    quota_raw: str = ""
    quota_extra: dict[str, str] = {}

    def set_prompt(self, prompt: str):
        self.prompt = prompt

    def set_size(self, size: str):
        self.size = size

    def set_custom_size(self, custom_size: str):
        self.custom_size = custom_size

    @rx.var
    def is_custom_size(self) -> bool:
        return "自定义" in self.size

    def set_duration(self, duration: str):
        self.duration = duration

    def set_model(self, model: str):
        self.model = model
        if not self.allow_edit:
            self.upload_imgs = []
            self.error_msg = ""

    @rx.event
    async def init_from_url(self):
        if self.initialized_from_url:
            return

        default_models = get_default_models()
        default_sizes = get_default_sizes()
        default_durations = get_default_durations()

        raw_url = getattr(self.router, "url", "") or ""
        query_params = {}
        if raw_url:
            query_params = parse_qs(urlparse(raw_url).query)

        params = query_params

        def get_param(name: str, default: str = "") -> str:
            value = params.get(name, default)
            if isinstance(value, list):
                return ",".join(str(item) for item in value)
            return str(value)

        model = get_param("model").strip()
        models_param = get_param("models").strip()
        size = get_param("size").strip()
        url_size_options = parse_video_size_options(get_param("sizes"))
        duration = get_param("duration").strip()
        url_duration_options = parse_duration_options(get_param("durations"))
        title = get_param("title").strip()[:60]
        edit = get_param("edit").strip()
        open_id = get_param("open_id").strip()[:128]
        quota_raw = get_param("quota").strip()
        extra_quota_params = {
            k.lower(): v[0] if isinstance(v, list) else str(v)
            for k, v in params.items()
            if k.lower().startswith("quota_") or k.lower().startswith("quota[")
        }

        async with self:
            available = default_models.copy()
            if models_param:
                parsed_models = [m.strip() for m in models_param.split(",") if m.strip()]
                if parsed_models:
                    available = parsed_models

            if model:
                if model not in available:
                    available.insert(0, model)
                self.available_models = available
                self.model_options = [model]
                self.model = model
            else:
                self.available_models = available
                self.model_options = available
                self.model = available[0]

            if edit != "":
                self.allow_edit = edit == "1"
            else:
                self.allow_edit = os.getenv("TEXT2VIDEO_ALLOW_EDIT", "0") == "1"

            size_options = url_size_options if url_size_options else default_sizes.copy()
            selected_size = find_video_size_option(size_options, size)
            if size and not selected_size:
                size_options.append(size)
                selected_size = size
            if "自定义尺寸..." not in size_options:
                size_options.append("自定义尺寸...")
            self.size_options = size_options
            self.size = selected_size or size_options[0]

            dur_options = url_duration_options if url_duration_options else default_durations.copy()
            selected_dur = find_duration_option(dur_options, duration)
            if duration and not selected_dur:
                dur_options.append(duration)
                selected_dur = duration
            self.duration_options = dur_options
            self.duration = selected_dur or dur_options[0]

            if title:
                self.title = title
            else:
                self.title = os.getenv("TEXT2VIDEO_TITLE", "通用文生视频生成器")[:60]

            self.open_id = open_id
            self.quota_raw = quota_raw
            self.quota_extra = extra_quota_params
            self.daily_limit = resolve_model_quota(
                model=self.model,
                quota_raw=quota_raw,
                extra_params=extra_quota_params,
                scope_category="video",
            )

            self.initialized_from_url = True

    @rx.event
    async def handle_upload(self, files: list[rx.UploadFile]):
        self.uploading = True
        try:
            if len(files) == 0:
                self.error_msg = "请选择一张PNG、JPG或WEBP格式的参考图片"
                return
            self.error_msg = ""
            self.upload_imgs = []
            file = files[0]
            data = await file.read()
            md5 = hashlib.md5(data).hexdigest()
            ext = file.name.split(".")[-1].lower()
            filename = f"{md5}.{ext}"
            path = rx.get_upload_dir() / filename
            with path.open("wb") as f:
                f.write(data)
            self.upload_imgs.append(filename)
        finally:
            self.uploading = False

    def clear_reference_image(self):
        self.upload_imgs = []
        self.error_msg = ""

    @rx.event(background=True)
    async def generate_video(self):
        if not self.prompt.strip():
            yield rx.window_alert("提示词不能为空！")
            return

        start_time = time.time()

        open_id = self.open_id
        current_model = (self.model or "default").strip()
        model_scope = current_model.lower()
        daily_limit = resolve_model_quota(
            model=current_model,
            quota_raw=self.quota_raw,
            extra_params=self.quota_extra,
            scope_category="video",
        )
        if daily_limit >= 0:
            allowed, used = await reserve_generation_quota(
                open_id=open_id,
                requested_count=1,
                daily_limit=daily_limit,
                scope=model_scope,
            )
            if not allowed:
                yield rx.window_alert(
                    get_quota_exceeded_msg(
                        scope=model_scope,
                        model=current_model,
                        limit=daily_limit,
                        used=used,
                    )
                )
                return

        async with self:
            self.processing = True
            self.complete = False
            self.video_urls = []
            self.status_text = "正在提交生成任务..."

        try:
            base_url = (
                os.getenv("TEXT2VIDEO_OPENAI_BASE_URL")
                or os.getenv("TEXT2IMAGE_OPENAI_BASE_URL")
                or os.getenv("OPENAI_BASE_URL")
                or "http://127.0.0.1:18610/v1"
            ).strip().rstrip("/")
            if not base_url.endswith("/v1"):
                base_url = f"{base_url}/v1"

            api_key = (
                os.getenv("TEXT2VIDEO_OPENAI_API_KEY")
                or os.getenv("TEXT2IMAGE_OPENAI_API_KEY")
                or os.getenv("OPENAI_API_KEY")
                or ""
            ).strip()

            headers = {
                "Content-Type": "application/json",
            }
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            if "自定义" in self.size and self.custom_size.strip():
                norm_size = normalize_video_size(self.custom_size.strip())
            else:
                norm_size = normalize_video_size(self.size)
            norm_dur = normalize_duration(self.duration)

            payload = {
                "prompt": self.prompt.strip(),
                "size": norm_size,
                "duration": norm_dur,
                "seconds": str(norm_dur),
            }
            if self.model:
                payload["model"] = self.model.strip()

            if self.allow_edit and len(self.upload_imgs) > 0:
                image_path = rx.get_upload_dir() / self.upload_imgs[0]
                if image_path.exists():
                    with open(image_path, "rb") as f:
                        img_bytes = f.read()
                    ext = self.upload_imgs[0].split(".")[-1].lower()
                    mime_map = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}
                    mime = mime_map.get(ext, "image/png")
                    b64 = base64.b64encode(img_bytes).decode("utf-8")
                    payload["image"] = f"data:{mime};base64,{b64}"

            create_url, poll_url_prefix = get_video_endpoints(base_url)

            async with aiohttp.ClientSession() as session:
                async with session.post(create_url, json=payload, headers=headers) as response:
                    if response.status not in (200, 201, 202):
                        error_text = await response.text()
                        yield rx.window_alert(f"视频创建失败！异常原因：{response.status}-{error_text}")
                        return
                    create_data = await response.json()

                immediate_video_url = extract_video_url(create_data)
                task_id = create_data.get("id") or create_data.get("task_id")

                final_video_url = None
                if immediate_video_url:
                    final_video_url = resolve_media_url(base_url, immediate_video_url)
                elif not task_id:
                    yield rx.window_alert(f"视频创建异常，未返回任务ID：{create_data}")
                    return

                if not final_video_url and task_id:
                    poll_url = f"{poll_url_prefix}/{task_id}"
                    poll_headers = {}
                    if api_key:
                        poll_headers["Authorization"] = f"Bearer {api_key}"

                    poll_interval = 2.5
                    max_poll_time = 600  # 10 minutes
                    start_poll = time.time()

                    async with self:
                        self.status_text = "任务已提交，排队中..."
                    yield

                    while time.time() - start_poll < max_poll_time:
                        await asyncio.sleep(poll_interval)
                        elapsed = int(time.time() - start_time)

                        try:
                            async with session.get(poll_url, headers=poll_headers) as poll_resp:
                                if poll_resp.status != 200:
                                    if poll_resp.status in (401, 403, 404):
                                        err_text = await poll_resp.text()
                                        yield rx.window_alert(f"查询任务状态失败：{poll_resp.status}-{err_text}")
                                        return
                                    continue

                                poll_data = await poll_resp.json()
                                status = str(poll_data.get("status", "")).lower()

                                url_found = extract_video_url(poll_data)

                                if status in ("completed", "success", "succeeded", "done") or url_found:
                                    if url_found:
                                        final_video_url = resolve_media_url(base_url, url_found)
                                        break
                                    elif status in ("completed", "success", "succeeded", "done"):
                                        yield rx.window_alert(f"视频生成完成，但未解析到视频链接：{poll_data}")
                                        return

                                if status in ("failed", "error", "interrupted", "cancelled"):
                                    err_msg = poll_data.get("error") or poll_data.get("message") or f"状态: {status}"
                                    try:
                                        alt_url = f"{base_url}/video/generations/{task_id}"
                                        async with session.get(alt_url, headers=poll_headers, timeout=aiohttp.ClientTimeout(total=3)) as alt_resp:
                                            if alt_resp.status == 200:
                                                alt_json = await alt_resp.json()
                                                alt_reason = alt_json.get("data", {}).get("fail_reason")
                                                if alt_reason:
                                                    err_msg = f"{alt_reason}"
                                    except Exception:
                                        pass
                                    yield rx.window_alert(f"视频生成失败！{err_msg}")
                                    return

                                # 当处于网关排队中时，周期性同步网关内部任务状态以提前捕获失败或结果
                                if elapsed > 10 and elapsed % 8 == 0:
                                    try:
                                        alt_url = f"{base_url}/video/generations/{task_id}"
                                        async with session.get(alt_url, headers=poll_headers, timeout=aiohttp.ClientTimeout(total=3)) as alt_resp:
                                            if alt_resp.status == 200:
                                                alt_json = await alt_resp.json()
                                                alt_inner = alt_json.get("data", {})
                                                alt_status = str(alt_inner.get("status", "")).lower()
                                                alt_url_found = extract_video_url(alt_inner)
                                                if alt_url_found:
                                                    final_video_url = resolve_media_url(base_url, alt_url_found)
                                                    break
                                                if alt_status in ("failure", "failed", "error") or alt_inner.get("fail_reason"):
                                                    fail_r = alt_inner.get("fail_reason") or alt_status
                                                    yield rx.window_alert(f"视频生成失败！{fail_r}")
                                                    return
                                    except Exception:
                                        pass

                                status_desc_map = {
                                    "queued": "排队中",
                                    "pending": "等待处理",
                                    "running": "正在渲染",
                                    "processing": "正在生成",
                                }
                                desc = status_desc_map.get(status, status or "处理中")
                                progress = poll_data.get("progress")
                                progress_str = f"，进度: {progress}%" if progress is not None and progress > 0 else ""
                                async with self:
                                    self.status_text = f"视频生成中（{desc}{progress_str}，已耗时 {elapsed} 秒）..."
                                yield
                        except aiohttp.ClientError:
                            continue
                    else:
                        yield rx.window_alert("视频生成超时（已超过10分钟），请稍后重试。")
                        return

                if final_video_url:
                    async with self:
                        self.video_urls = [final_video_url]
                        self.complete = True
                        self.status_text = "视频生成完成！"

                    try:
                        from image_gen_page.web_admin import record_history

                        ref_imgs = (
                            [str(rx.get_upload_dir() / img) for img in self.upload_imgs]
                            if (self.allow_edit and self.upload_imgs)
                            else []
                        )
                        record_history(
                            prompt=self.prompt,
                            model=self.model,
                            duration=time.time() - start_time,
                            images=[final_video_url],
                            ref_images=ref_imgs,
                            source="通用文生视频",
                            extra={"size": norm_size, "duration": self.duration},
                        )
                    except Exception as err:
                        print(f"[text2video] record_history error: {err}")
        except Exception as e:
            yield rx.window_alert(f"视频生成失败！异常原因：{str(e)}")
        finally:
            async with self:
                self.processing = False

    @rx.event
    async def download_video(self, index_num: int):
        if index_num < 0 or index_num >= len(self.video_urls):
            return rx.window_alert("下载失败：视频不存在")
        video_url = self.video_urls[index_num]
        return rx.call_script(f"""
            (function() {{
                const videoUrl = {json.dumps(video_url)};
                fetch(videoUrl)
                    .then(res => {{
                        if (!res.ok) throw new Error('Fetch failed: ' + res.status);
                        return res.blob();
                    }})
                    .then(blob => {{
                        const blobUrl = window.URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        a.href = blobUrl;
                        a.download = 'video.mp4';
                        document.body.appendChild(a);
                        a.click();
                        document.body.removeChild(a);
                        setTimeout(() => window.URL.revokeObjectURL(blobUrl), 1000);
                    }})
                    .catch(() => {{
                        const a = document.createElement('a');
                        a.href = videoUrl;
                        a.download = 'video.mp4';
                        a.target = '_blank';
                        document.body.appendChild(a);
                        a.click();
                        document.body.removeChild(a);
                    }});
            }})();
        """)


def video_preview_modal(video_url):
    return rx.dialog.root(
        rx.dialog.trigger(
            rx.button(
                "全屏预览",
                variant="outline",
                color_scheme="gray",
                cursor="pointer",
                flex="1",
            ),
        ),
        rx.dialog.content(
            rx.video(
                src=video_url,
                width="100%",
                height="auto",
                controls=True,
                auto_play=True,
            ),
            rx.flex(
                rx.dialog.close(
                    rx.button(
                        "关闭",
                        variant="soft",
                        color_scheme="gray",
                    ),
                ),
                margin_top="1em",
                justify="center",
                width="100%",
            ),
            spacing="4",
        ),
    )


def index():
    return rx.box(
        rx.center(
            rx.vstack(
                rx.heading(
                    Text2VideoState.title,
                    font_size=["1.05em", "1.2em", "1.35em"],
                    font_weight="600",
                    text_align="center",
                    width="100%",
                    line_height="1.35",
                    white_space="normal",
                    word_break="break-word",
                    overflow_wrap="anywhere",
                    margin_bottom="0.25em",
                ),
                rx.text_area(
                    value=Text2VideoState.prompt,
                    placeholder="请输入视频生成提示词，例如：枫叶在微风中缓缓飘落，阳光洒在林间小道上...",
                    on_change=Text2VideoState.set_prompt,
                    width="100%",
                    rows="5",
                    resize="vertical",
                ),
                rx.select(
                    Text2VideoState.model_options,
                    value=Text2VideoState.model,
                    on_change=Text2VideoState.set_model,
                    width="100%",
                    placeholder="选择模型",
                ),
                rx.select(
                    Text2VideoState.size_options,
                    value=Text2VideoState.size,
                    on_change=Text2VideoState.set_size,
                    width="100%",
                    placeholder="选择视频尺寸/比例",
                ),
                rx.cond(
                    Text2VideoState.is_custom_size,
                    rx.input(
                        value=Text2VideoState.custom_size,
                        placeholder="输入自定义尺寸（如 1920x1080、1024x1024 或 16:9）",
                        on_change=Text2VideoState.set_custom_size,
                        width="100%",
                    ),
                ),
                rx.select(
                    Text2VideoState.duration_options,
                    value=Text2VideoState.duration,
                    on_change=Text2VideoState.set_duration,
                    width="100%",
                    placeholder="选择视频时长",
                ),
                rx.cond(
                    Text2VideoState.allow_edit,
                    rx.vstack(
                        rx.text("参考图（可选，首帧/参考图生视频）", font_size="0.9em", width="100%"),
                        rx.upload.root(
                            rx.box(
                                rx.cond(
                                    Text2VideoState.uploading,
                                    rx.spinner(size="3"),
                                    rx.cond(
                                        Text2VideoState.upload_imgs.length() > 0,
                                        rx.flex(
                                            rx.foreach(
                                                Text2VideoState.upload_imgs,
                                                lambda img: rx.box(
                                                    rx.image(
                                                        src=rx.get_upload_url(img),
                                                        height="10em",
                                                        object_fit="contain",
                                                    ),
                                                    rx.icon_button(
                                                        rx.icon("x", size=15),
                                                        on_click=Text2VideoState.clear_reference_image,
                                                        position="absolute",
                                                        top="0",
                                                        right="0",
                                                        size="1",
                                                        variant="solid",
                                                        color_scheme="red",
                                                    ),
                                                    position="relative",
                                                ),
                                            ),
                                            wrap="wrap",
                                            justify="center",
                                        ),
                                        rx.text("拖拽或上传参考图"),
                                    ),
                                ),
                                width="100%",
                                min_height="8em",
                                display="flex",
                                align_items="center",
                                justify_content="center",
                                border="1px dashed var(--gray-7)",
                                border_radius="12px",
                                padding="1em",
                            ),
                            id="text2video_upload",
                            max_size=10 * 1024 * 1024,
                            accept={
                                "image/png": [".png"],
                                "image/jpeg": [".jpg", ".jpeg"],
                                "image/webp": [".webp"],
                            },
                            multiple=False,
                            on_drop=Text2VideoState.handle_upload(rx.upload_files(upload_id="text2video_upload")),
                            width="100%",
                        ),
                        rx.cond(
                            Text2VideoState.error_msg != "",
                            rx.text(Text2VideoState.error_msg, color="red", font_size="0.85em", width="100%"),
                        ),
                        align="center",
                        spacing="1",
                        width="100%",
                    ),
                ),
                rx.button(
                    "生成视频",
                    on_click=Text2VideoState.generate_video,
                    width="100%",
                    loading=Text2VideoState.processing,
                    disabled=Text2VideoState.processing,
                ),
                rx.cond(
                    Text2VideoState.processing,
                    rx.card(
                        rx.flex(
                            rx.spinner(size="3"),
                            rx.text(
                                Text2VideoState.status_text,
                                font_size="0.95em",
                                color_scheme="blue",
                                font_weight="500",
                            ),
                            spacing="3",
                            align="center",
                            justify="center",
                        ),
                        width="100%",
                        margin_top="0.5em",
                        padding="0.75em",
                    ),
                ),
                rx.cond(
                    Text2VideoState.complete & (Text2VideoState.video_urls.length() > 0),
                    rx.vstack(
                        rx.foreach(
                            Text2VideoState.video_urls,
                            lambda url, index: rx.vstack(
                                rx.box(
                                    rx.video(
                                        src=url,
                                        width="100%",
                                        height="auto",
                                        controls=True,
                                        border_radius="10px",
                                    ),
                                    width="100%",
                                    border_radius="10px",
                                    overflow="hidden",
                                    box_shadow="0 4px 12px rgba(0, 0, 0, 0.15)",
                                ),
                                rx.flex(
                                    video_preview_modal(url),
                                    rx.button(
                                        "下载视频",
                                        variant="solid",
                                        color_scheme="blue",
                                        cursor="pointer",
                                        on_click=Text2VideoState.download_video(index),
                                        flex="1",
                                    ),
                                    width="100%",
                                    spacing="3",
                                ),
                                align="center",
                                spacing="3",
                                width="100%",
                            ),
                        ),
                        width="100%",
                        margin_top="1.5em",
                        spacing="4",
                    ),
                ),
                width="100%",
                max_width="480px",
                spacing="3",
                align="center",
            ),
            width="100%",
            padding_x=["1em", "1.5em"],
        ),
        width="100%",
        min_height="100vh",
        padding_y="2em",
    )
