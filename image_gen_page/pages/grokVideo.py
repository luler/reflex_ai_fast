# 加载配置
import base64
import hashlib
import os

import aiohttp
import reflex as rx

# 尺寸选项列表（常量）
SIZE_OPTIONS = [
    "1280x720",
    "720x1280",
    "1792x1024",
    "1024x1792",
    "1024x1024",
]

# 时长选项（秒）
SECONDS_OPTIONS = ["6", "8", "10", "12", "15", "18", "20", "25", "30"]

# 质量选项
QUALITY_OPTIONS = ["standard", "high"]


class GrokVideoState(rx.State):
    """Grok视频生成状态管理."""

    prompt = ""  # 视频提示词
    video_urls = []  # 生成的视频URL列表
    processing = False
    uploading = False
    upload_imgs = []
    error_msg: str = ''
    max_files: int = 1  # 视频生成只支持单张参考图

    video_size: str = "1280x720"  # 视频尺寸
    video_seconds: str = "10"  # 视频时长（秒）
    video_quality: str = "standard"  # 视频质量

    @rx.event
    async def handle_upload(self, files: list[rx.UploadFile]):
        """处理参考图上传."""
        self.uploading = True
        try:
            if len(files) == 0:
                self.error_msg = "请选择一张PNG、JPG或WEBP格式的参考图片"
                return
            self.error_msg = ''

            # 只保留第一张图片
            self.upload_imgs = []
            file = files[0]
            data = await file.read()
            md5 = hashlib.md5(data).hexdigest()
            ext = file.name.split('.')[-1].lower()
            filename = f"{md5}.{ext}"
            path = rx.get_upload_dir() / filename
            with path.open("wb") as f:
                f.write(data)
            self.upload_imgs.append(filename)
        finally:
            self.uploading = False

    def set_prompt(self, prompt: str):
        """设置视频提示词."""
        self.prompt = prompt

    def set_video_size(self, size: str):
        """设置视频尺寸."""
        self.video_size = size

    def set_video_seconds(self, seconds: str):
        """设置视频时长."""
        self.video_seconds = seconds

    def set_video_quality(self, quality: str):
        """设置视频质量."""
        self.video_quality = quality

    def clear_reference_image(self):
        """清除参考图."""
        self.upload_imgs = []

    async def _fetch_video_result(self, session, base_url: str, video_url: str, headers: dict, task_id: str):
        """获取视频生成结果."""
        async with session.get(
                base_url + video_url + '/' + task_id,
                headers=headers
        ) as get_response:
            if get_response.status == 200:
                result = await get_response.json()
                # 尝试从不同字段获取视频URL
                video_url_found = None
                if 'url' in result:
                    video_url_found = result['url']
                elif 'video_url' in result:
                    video_url_found = result['video_url']
                elif 'data' in result:
                    data = result.get('data', [])
                    if data and 'url' in data[0]:
                        video_url_found = data[0]['url']

                if video_url_found:
                    async with self:
                        self.video_urls = [video_url_found]
                else:
                    yield rx.window_alert(f"视频生成完成，但未找到视频URL：{result}")
            else:
                error_text = await get_response.text()
                yield rx.window_alert(f"获取视频失败！异常原因：{get_response.status}-{error_text}")

    @rx.event(background=True)
    async def generate_video(self):
        """调用Grok视频生成API."""
        if self.prompt == "":
            yield rx.window_alert("提示词不能为空！")
            return

        async with self:
            self.processing = True
            self.video_urls = []

        try:
            video_model = os.getenv('GROK_VIDEO_MODEL', 'grok-imagine-1.0-video')
            video_url = '/videos'

            param = {
                'model': video_model,
                'prompt': self.prompt,
                'size': self.video_size,
                'seconds': str(self.video_seconds),  # API需要字符串类型
                'quality': self.video_quality,
            }

            headers = {
                'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + os.getenv('GROK_VIDEO_API_KEY', os.getenv('GROK_IMAGE_OPENAI_API_KEY', ''))
            }

            base_url = os.getenv('GROK_VIDEO_BASE_URL', os.getenv('GROK_IMAGE_OPENAI_BASE_URL', ''))

            async with aiohttp.ClientSession() as session:
                # 如果有参考图，使用multipart/form-data
                if len(self.upload_imgs) > 0:
                    image_path = rx.get_upload_dir() / self.upload_imgs[0]
                    with open(image_path, 'rb') as f:
                        image_data = f.read()

                    # 根据文件扩展名确定content_type
                    ext = self.upload_imgs[0].split('.')[-1].lower()
                    content_type_map = {
                        'png': 'image/png',
                        'jpg': 'image/jpeg',
                        'jpeg': 'image/jpeg',
                        'webp': 'image/webp',
                    }
                    content_type = content_type_map.get(ext, 'image/png')

                    form = aiohttp.FormData()
                    form.add_field('model', param['model'])
                    form.add_field('prompt', param['prompt'])
                    form.add_field('size', param['size'])
                    form.add_field('seconds', param['seconds'])  # 已经是字符串
                    form.add_field('quality', param['quality'])
                    form.add_field('input_reference', image_data, filename=self.upload_imgs[0],
                                   content_type=content_type)

                    post_headers = {k: v for k, v in headers.items() if k != 'Content-Type'}

                    async with session.post(
                            base_url + video_url,
                            data=form,
                            headers=post_headers
                    ) as response:
                        if response.status == 200:
                            data = await response.json()
                            task_id = data.get('task_id') or data.get('id')
                            if task_id:
                                async for result in self._fetch_video_result(session, base_url, video_url, headers,
                                                                             task_id):
                                    yield result
                            else:
                                yield rx.window_alert(f"视频创建失败：未返回task_id")
                        else:
                            error_text = await response.text()
                            yield rx.window_alert(f"视频生成失败！异常原因：{response.status}-{error_text}")
                else:
                    # 无参考图，使用JSON格式
                    async with session.post(
                            base_url + video_url,
                            json=param,
                            headers=headers
                    ) as response:
                        if response.status == 200:
                            data = await response.json()
                            task_id = data.get('task_id') or data.get('id')
                            if task_id:
                                async for result in self._fetch_video_result(session, base_url, video_url, headers,
                                                                             task_id):
                                    yield result
                            else:
                                yield rx.window_alert(f"视频创建失败：未返回task_id")
                        else:
                            error_text = await response.text()
                            yield rx.window_alert(f"视频生成失败！异常原因：{response.status}-{error_text}")
        except Exception as e:
            yield rx.window_alert("视频生成失败！异常原因：" + str(e))

        async with self:
            self.processing = False

    @rx.event
    async def download_video(self, index_num: int):
        """下载指定URL的视频."""
        video_url = self.video_urls[index_num]

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(video_url) as response:
                    if response.status == 200:
                        video_data = await response.read()
                        b64_data = base64.b64encode(video_data).decode('utf-8')
                        return rx.call_script(f"""
                            (function() {{
                                const a = document.createElement('a');
                                a.href = 'data:video/mp4;base64,{b64_data}';
                                a.download = 'grok_video.mp4';
                                document.body.appendChild(a);
                                a.click();
                                document.body.removeChild(a);
                            }})();
                        """)
                    else:
                        return rx.window_alert(f"下载失败：HTTP {response.status}")
        except Exception as e:
            return rx.window_alert(f"下载失败：{str(e)}")


def video_modal(video_url):
    """视频弹窗组件."""
    return rx.dialog.root(
        rx.dialog.trigger(
            rx.box(
                rx.video(
                    src=video_url,
                    width=["20em", "25em"],
                    controls=True,
                    cursor="pointer",
                ),
            )
        ),
        rx.dialog.content(
            rx.video(
                src=video_url,
                width="100%",
                height="auto",
                controls=True,
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
    """视频生成页面."""
    return rx.vstack(
        rx.center(
            rx.vstack(
                rx.heading(
                    "基于 Grok Imagine 模型的智能视频生成器",
                    font_size=["1.2em", "1.5em"],
                    text_align="center",
                    width="100%"
                ),

                # 提示词输入
                rx.text_area(
                    value=GrokVideoState.prompt,
                    placeholder="请输入提示词描述你想生成的视频，例如：霓虹雨夜街头，慢镜头追拍",
                    on_change=GrokVideoState.set_prompt,
                    width=["20em", "25em"],
                    rows='4',
                    resize='vertical',
                ),

                # 参数选择行
                rx.hstack(
                    # 尺寸选择
                    rx.vstack(
                        rx.text("画面比例", font_size="0.9em"),
                        rx.select(
                            SIZE_OPTIONS,
                            value=GrokVideoState.video_size,
                            on_change=GrokVideoState.set_video_size,
                            width="10em",
                        ),
                        spacing="1",
                    ),
                    # 时长选择
                    rx.vstack(
                        rx.text("时长（秒）", font_size="0.9em"),
                        rx.select(
                            SECONDS_OPTIONS,
                            value=GrokVideoState.video_seconds,
                            on_change=GrokVideoState.set_video_seconds,
                            width="7em",
                        ),
                        spacing="1",
                    ),
                    # 质量选择
                    rx.vstack(
                        rx.text("画质", font_size="0.9em"),
                        rx.select(
                            QUALITY_OPTIONS,
                            value=GrokVideoState.video_quality,
                            on_change=GrokVideoState.set_video_quality,
                            width="7em",
                        ),
                        spacing="1",
                    ),
                    spacing="4",
                    align="end",
                ),

                # 参考图上传（可选）
                rx.vstack(
                    rx.text("参考图（可选）", font_size="0.9em"),
                    rx.upload.root(
                        rx.box(
                            rx.cond(
                                GrokVideoState.uploading,
                                rx.spinner(size="3"),
                                rx.cond(
                                    GrokVideoState.upload_imgs.length() > 0,
                                    rx.flex(
                                        rx.foreach(
                                            GrokVideoState.upload_imgs,
                                            lambda img: rx.box(
                                                rx.image(
                                                    src=rx.get_upload_url(img),
                                                    height="10em",
                                                    object_fit="contain",
                                                ),
                                                rx.icon_button(
                                                    rx.icon("x", size=15),
                                                    on_click=GrokVideoState.clear_reference_image,
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
                                    rx.text(
                                        "点击或拖拽上传参考图",
                                        style={
                                            "color": "#888",
                                            "fontSize": "1em",
                                        },
                                    ),
                                ),
                            ),
                            style={
                                "width": "100%",
                                "minHeight": "8em",
                                "display": "flex",
                                "alignItems": "center",
                                "justifyContent": "center",
                            },
                        ),
                        id="video_upload",
                        max_size=10 * 1024 * 1024,
                        accept={
                            "image/png": [".png"],
                            "image/jpeg": [".jpg", ".jpeg"],
                            "image/webp": [".webp"],
                        },
                        multiple=False,
                        width=["20em", "25em"],
                        style={
                            "border": "2px dashed #60a5fa",
                            "padding": "1em",
                        },
                        on_drop=GrokVideoState.handle_upload(rx.upload_files(upload_id="video_upload")),
                    ),
                    align="center",
                    spacing="1",
                ),

                rx.text(GrokVideoState.error_msg, color="red"),

                # 生成按钮
                rx.button(
                    "生成视频",
                    on_click=GrokVideoState.generate_video,
                    width=["23em", "28.5em"],
                    loading=GrokVideoState.processing
                ),

                # 视频结果展示
                rx.cond(
                    GrokVideoState.video_urls.length() > 0,
                    rx.flex(
                        rx.foreach(
                            GrokVideoState.video_urls,
                            lambda url, index_num: rx.vstack(
                                video_modal(url),
                                rx.button(
                                    "下载视频",
                                    width=["23em", "28.5em"],
                                    cursor="pointer",
                                    on_click=GrokVideoState.download_video(index_num)
                                ),
                                align='center',
                            ),
                        ),
                        margin_top="1em",
                        wrap='wrap',
                        justify="center",
                        gap="2em",
                    )
                ),

                align="center",
                spacing="4",
            ),
            width="100%",
        ),

        rx.spacer(),

        # 说明信息
        rx.box(
            rx.vstack(
                rx.divider(margin_y="1em"),
                rx.heading(
                    "使用说明",
                    font_size="lg",
                    margin_bottom="1em",
                ),
                rx.text("• 支持生成 6-30 秒的视频", font_size="0.9em"),
                rx.text("• standard 质量为 480p，high 质量为 720p", font_size="0.9em"),
                rx.text("• 参考图功能可让视频以参考图为基准生成", font_size="0.9em"),
                align="center",
                spacing="2",
            ),
            width="100%",
        ),

        width="100%",
        min_height="100vh",
        padding_y="2em",
    )
