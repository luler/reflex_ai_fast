# 加载配置
import base64
import hashlib
import os

import aiohttp
import reflex as rx


class GrokImageState(rx.State):
    """The app state."""

    text2img_prompt = ""  # 文生图提示词
    img2img_prompt = ""  # 图片编辑提示词
    text2img_urls = []  # 文生图生成的图片
    img2img_urls = []  # 图片编辑生成的图片
    processing = False
    uploading = False  # 新增上传状态变量

    upload_imgs = []
    error_msg: str = ''
    max_files: int = 3
    current_mode: str = "text2img"  # 当前模式：text2img 或 img2img
    text2img_size: str = "1024x1024x(1:1)"  # 文生图尺寸

    # 尺寸选项列表
    size_options: list = [
        "1280x720x(16:9)",
        "720x1280x(9:16)",
        "1792x1024x(16:9)",
        "1024x1792x(9:16)",
        "1024x1024x(1:1)",
    ]

    @rx.event
    async def handle_upload(self, files: list[rx.UploadFile]):
        self.uploading = True  # 开始上传时设置状态
        try:
            if len(files) == 0:
                self.error_msg = "请选择最多" + str(self.max_files) + "张10MB以内的图片"
                return
            self.error_msg = ''

            # 清空之前的图片
            self.upload_imgs = []

            for file in files:
                data = await file.read()
                md5 = hashlib.md5(data).hexdigest()
                ext = file.name.split('.')[-1]
                filename = f"{md5}.{ext}"
                path = rx.get_upload_dir() / filename
                with path.open("wb") as f:
                    f.write(data)
                self.upload_imgs.append(filename)
        finally:
            self.uploading = False  # 上传完成后重置状态

    def set_text2img_prompt(self, prompt: str):
        self.text2img_prompt = prompt

    def set_img2img_prompt(self, prompt: str):
        self.img2img_prompt = prompt

    def set_text2img_size(self, size: str):
        self.text2img_size = size

    def set_mode(self, mode: str):
        """切换模式"""
        self.current_mode = mode

    @rx.event(background=True)
    async def get_image(self):
        """调用大模型生成图片."""
        # 根据模式获取对应的提示词
        current_prompt = self.text2img_prompt if self.current_mode == "text2img" else self.img2img_prompt

        if current_prompt == "":
            yield rx.window_alert("提示词不能为空！")
            return
        # 只有图片编辑模式才需要检查上传图片
        if self.current_mode == "img2img" and len(self.upload_imgs) == 0:
            yield rx.window_alert("原图不能为空！")
            return
        async with self:
            self.processing = True
            # 根据模式清空对应的图片列表
            if self.current_mode == "text2img":
                self.text2img_urls = []
            else:
                self.img2img_urls = []
        try:
            # 根据模式构建不同的 content
            if self.current_mode == "text2img":
                image_model = os.getenv('GROK_IMAGE_IMAGE_MODEL')
                image_url = '/images/generations'
                # 文生图模式：只传提示词，不传图片
                param = {
                    'model': image_model,
                    'prompt': f"""
请根据以下描述生成一张图片并直接返回生成的图片：
图片描述：{current_prompt}
请严格按照描述生成图片。直接返回生成的图片，无需额外说明。
""",
                }
                # 解析尺寸
                size_parts = self.text2img_size.split('x')
                param['size'] = f"{size_parts[0]}x{size_parts[1]}"
            else:
                image_model = os.getenv('GROK_IMAGE_IMAGE_EDIT_MODEL')
                image_url = '/images/edits'
                # 图片编辑模式：使用 multipart/form-data 格式
                # 只支持单张图片，取第一张
                image_path = rx.get_upload_dir() / self.upload_imgs[0]
                param = {
                    'model': image_model,
                    'prompt': current_prompt,
                    'n': 1,
                }

            async with aiohttp.ClientSession() as session:
                if self.current_mode == "text2img":
                    # 文生图：JSON 格式
                    async with session.post(
                            os.getenv('GROK_IMAGE_OPENAI_BASE_URL') + image_url,
                            json=param,
                            headers={
                                'Content-Type': 'application/json',
                                'Authorization': 'Bearer ' + os.getenv('GROK_IMAGE_OPENAI_API_KEY')
                            }
                    ) as response:
                        if response.status == 200:
                            data = await response.json()
                            async with self:
                                images = [data['data'][0]['url']]
                                self.text2img_urls = images
                        else:
                            error_text = await response.text()
                            yield rx.window_alert(f"图片生成失败！异常原因：{response.status}-{error_text}")
                else:
                    # 图片编辑：multipart/form-data 格式
                    with open(image_path, 'rb') as f:
                        image_data = f.read()

                    # 构建 multipart/form-data
                    form = aiohttp.FormData()
                    form.add_field('model', param['model'])
                    form.add_field('prompt', param['prompt'])
                    form.add_field('n', str(param['n']))
                    # form.add_field('size', param['size'])
                    form.add_field('image', image_data, filename=self.upload_imgs[0], content_type='image/png')

                    async with session.post(
                            os.getenv('GROK_IMAGE_OPENAI_BASE_URL') + image_url,
                            data=form,
                            headers={
                                'Authorization': 'Bearer ' + os.getenv('GROK_IMAGE_OPENAI_API_KEY')
                            }
                    ) as response:
                        if response.status == 200:
                            data = await response.json()
                            async with self:
                                images = [data['data'][0]['url']]
                                self.img2img_urls = images
                        else:
                            error_text = await response.text()
                            yield rx.window_alert(f"图片编辑失败！异常原因：{response.status}-{error_text}")
        except Exception as e:
            yield rx.window_alert("图片生成失败！异常原因：" + str(e))
        # 延迟状态更新
        async with self:
            self.processing = False

    @rx.event
    async def download_image(self, index_num: int, mode: str):
        """下载指定URL的图片（通过后端代理绕过CORS限制）"""
        image_url = self.text2img_urls[index_num] if mode == "text2img" else self.img2img_urls[index_num]

        try:
            # 通过后端获取图片，绕过 CORS 限制
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url) as response:
                    if response.status == 200:
                        image_data = await response.read()
                        # 转换为 base64
                        b64_data = base64.b64encode(image_data).decode('utf-8')
                        # 使用 JavaScript 直接从 base64 数据下载
                        return rx.call_script(f"""
                            (function() {{
                                const a = document.createElement('a');
                                a.href = 'data:image/png;base64,{b64_data}';
                                a.download = 'grok_image.png';
                                document.body.appendChild(a);
                                a.click();
                                document.body.removeChild(a);
                            }})();
                        """)
                    else:
                        return rx.window_alert(f"下载失败：HTTP {response.status}")
        except Exception as e:
            return rx.window_alert(f"下载失败：{str(e)}")


def image_modal(image_url):
    return rx.dialog.root(
        rx.dialog.trigger(
            rx.image(
                src=image_url,
                width=["20em", "25em"],
                # height="20em",
                object_fit="cover",
                cursor="pointer",  # 鼠标悬停时显示手型光标
            )
        ),
        rx.dialog.content(
            rx.image(
                src=image_url,
                width="100%",  # 弹窗中的大图
                height="auto",
            ),
            rx.flex(  # 使用flex容器来居中按钮
                rx.dialog.close(
                    rx.button(
                        "关闭",
                        variant="soft",
                        color_scheme="gray",
                    ),
                ),
                margin_top="1em",  # 上间距
                # margin_bottom="1em",  # 下间距
                justify="center",  # 水平居中
                width="100%",  # 宽度100%确保居中效果
            ),
            spacing="4",  # vstack的组件间距
        ),
    )


def index():
    return rx.vstack(
        rx.center(
            rx.vstack(
                rx.heading(
                    "基于 grok imagine 模型的智能图片生成器",
                    font_size=["1.2em", "1.5em"],
                    text_align="center",
                    width="100%"
                ),

                # Tab 切换组件
                rx.tabs.root(
                    rx.center(
                        rx.tabs.list(
                            rx.tabs.trigger(
                                "文生图",
                                value="text2img",
                                font_size="1.1em",
                                padding="2em 3em",
                            ),
                            rx.tabs.trigger(
                                "图片编辑",
                                value="img2img",
                                font_size="1.1em",
                                padding="2em 3em",
                            ),
                            justify="center",
                        ),
                        width="100%",
                    ),

                    # 文生图 Tab 内容
                    rx.tabs.content(
                        rx.vstack(
                            rx.text_area(
                                value=GrokImageState.text2img_prompt,
                                placeholder="请输入提示词描述你想生成的图片",
                                on_change=GrokImageState.set_text2img_prompt,
                                width=["20em", "25em"],
                                rows='5',
                                resize='vertical',
                            ),
                            rx.select(
                                GrokImageState.size_options,
                                value=GrokImageState.text2img_size,
                                on_change=GrokImageState.set_text2img_size,
                                width=["23em", "28.5em"],
                                placeholder="选择图片尺寸",
                            ),
                            rx.button(
                                "生成图片",
                                on_click=GrokImageState.get_image,
                                width=["23em", "28.5em"],
                                loading=GrokImageState.processing
                            ),
                            rx.cond(
                                GrokImageState.text2img_urls.length() > 0,
                                rx.flex(
                                    rx.foreach(
                                        GrokImageState.text2img_urls,
                                        lambda url, index_num: rx.vstack(
                                            image_modal(url),
                                            rx.button(
                                                "下载图片",
                                                width=["23em", "28.5em"],
                                                cursor="pointer",
                                                on_click=GrokImageState.download_image(index_num, "text2img")
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
                            padding_y="1em",
                        ),
                        value="text2img",
                    ),

                    # 图片编辑 Tab 内容
                    rx.tabs.content(
                        rx.vstack(
                            rx.upload.root(
                                rx.box(
                                    rx.cond(
                                        GrokImageState.uploading,  # 判断是否正在上传
                                        rx.spinner(size="3"),  # 显示加载动画
                                        rx.cond(
                                            GrokImageState.upload_imgs.length() > 0,
                                            rx.flex(
                                                rx.foreach(
                                                    GrokImageState.upload_imgs,
                                                    lambda img: rx.image(
                                                        src=rx.get_upload_url(img),
                                                        height="16em",  # 设置固定高度
                                                        margin="0.2em",
                                                        style={
                                                            "objectFit": "contain",
                                                            "display": "block",
                                                        },
                                                    ),
                                                ),
                                                wrap="wrap",  # 允许换行显示
                                                justify="center",  # 居中对齐
                                                align_items="center",  # 垂直居中
                                            ),
                                            rx.text(
                                                "请上传图片（支持多图）",
                                                style={
                                                    "color": "#888",
                                                    "fontSize": "1.2em",
                                                },
                                            ),
                                        ),
                                    ),
                                    style={
                                        "width": "auto",
                                        "minWidth": ["19.5em", "24.5em"],
                                        "maxWidth": "80vw",
                                        "height": "fit-content",  # 自适应内容高度
                                        "minHeight": "16em",  # 最小高度
                                        "display": "flex",
                                        "alignItems": "center",
                                        "justifyContent": "center",
                                    },
                                ),
                                id="upload",
                                max_size=10 * 1024 * 1024,  # 10MB
                                accept={
                                    "image/png": [".png"],
                                    "image/jpeg": [".jpg", ".jpeg"],
                                },
                                multiple=GrokImageState.max_files > 1,  # 允许多文件上传
                                max_files=GrokImageState.max_files,  # 最多上传3张图片
                                width="auto",
                                style={
                                    "padding": 0,
                                    "margin": 0,
                                    "border": "2px dashed #60a5fa",
                                },
                                on_drop=GrokImageState.handle_upload(rx.upload_files(upload_id="upload")),
                            ),
                            rx.text(GrokImageState.error_msg, color="red"),
                            rx.text_area(
                                value=GrokImageState.img2img_prompt,
                                placeholder="请输入编辑提示词",
                                on_change=GrokImageState.set_img2img_prompt,
                                width=["20em", "25em"],
                                rows='5',
                                resize='vertical',
                            ),
                            rx.button(
                                "编辑图片",
                                on_click=GrokImageState.get_image,
                                width=["23em", "28.5em"],
                                loading=GrokImageState.processing
                            ),
                            rx.cond(
                                GrokImageState.img2img_urls.length() > 0,
                                rx.flex(
                                    rx.foreach(
                                        GrokImageState.img2img_urls,
                                        lambda url, index_num: rx.vstack(
                                            image_modal(url),
                                            rx.button(
                                                "下载图片",
                                                width=["23em", "28.5em"],
                                                cursor="pointer",
                                                on_click=GrokImageState.download_image(index_num, "img2img")
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
                            padding_y="1em",
                        ),
                        value="img2img",
                    ),

                    default_value="text2img",  # 默认显示文生图
                    width="100%",
                    on_change=GrokImageState.set_mode,  # 切换 tab 时更新模式
                ),

                align="center",
                spacing="4",
            ),
            width="100%",
        ),

        # 空白区域填充，确保示例图片在底部
        rx.spacer(),

        # 示例图片部分（始终保持在底部）
        rx.box(
            rx.vstack(
                rx.divider(margin_y="1em"),
                rx.heading(
                    "效果示例",
                    font_size="lg",
                    margin_bottom="1em",
                ),
                rx.flex(
                    rx.foreach(
                        [
                            "/images/grokimage/1.png",
                            "/images/grokimage/2.png",
                            "/images/grokimage/3.png",
                            "/images/grokimage/4.jpg",
                            "/images/grokimage/5.png",
                            "/images/grokimage/6.png",
                            "/images/grokimage/7.png",
                            "/images/grokimage/8.png",
                        ],
                        lambda url: image_modal(url),
                    ),
                    wrap="wrap",
                    justify="center",
                    gap="2em",
                ),
                align="center",
            ),
            width="100%",
        ),

        # 整体布局设置
        width="100%",
        min_height="100vh",  # 确保高度至少填满屏幕
        padding_y="2em",
    )
