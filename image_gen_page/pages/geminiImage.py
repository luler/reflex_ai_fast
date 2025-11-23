# 加载配置
import hashlib
import os
import re

import aiohttp
import reflex as rx

from image_gen_page.tool.common_tool import image_to_base64


class GeminiImageState(rx.State):
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
                # 文生图模式：只传提示词，不传图片
                content = [
                    {
                        "type": "text",
                        "text": f"""
请根据以下描述生成一张图片并直接返回生成的图片：
图片描述：{current_prompt}
请严格按照描述生成图片。直接返回生成的图片，无需额外说明。
"""
                    }
                ]
            else:
                # 图片编辑模式：传提示词和图片
                content = [
                    {
                        "type": "text",
                        "text": f"""
请根据以下要求编辑图片并直接返回编辑后的图片：
编辑要求：{current_prompt}
请严格按照要求对图片进行编辑。直接返回编辑后的图片，无需额外说明。
"""
                    },
                    *[
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": image_to_base64(rx.get_upload_dir(), img)
                            }
                        }
                        for img in self.upload_imgs
                    ],
                ]

            param = {
                'model': os.getenv('GEMINI_IMAGE_COVER_MODEL'),
                'messages': [
                    {
                        "role": "user",
                        "content": content
                    }
                ],
                "stream": False
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(
                        os.getenv('GEMINI_IMAGE_OPENAI_BASE_URL') + '/chat/completions',
                        json=param,
                        headers={
                            'Content-Type': 'application/json',
                            'Authorization': 'Bearer ' + os.getenv('GEMINI_IMAGE_OPENAI_API_KEY')
                        }
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        async with self:
                            images = []
                            if 'images' in data['choices'][0]['message']:
                                # 兼容第一种格式
                                images = [data['choices'][0]['message']['images'][0]['image_url']['url']]
                            elif isinstance(data['choices'][0]['message']['content'], list):
                                # 兼容第二种格式
                                for content in data['choices'][0]['message']['content']:
                                    if content.get('type', '').startswith('image/'):
                                        if content['image_url'].startswith('data:') or content['image_url'].startswith(
                                                'http'):
                                            images.append(content['image_url'])
                                        else:
                                            images.append(f"data:{content.get('type')};base64,{content['image_url']}")
                            else:  # content包含markdown格式图片
                                match = re.search(
                                    r'!\[[^\]]*\]\(([^)]*)\)',
                                    data['choices'][0]['message']['content']
                                )
                                if match:
                                    images.append(match.group(1))
                            # 根据模式存储到不同的变量
                            if self.current_mode == "text2img":
                                self.text2img_urls = images
                            else:
                                self.img2img_urls = images
                    else:
                        error_text = await response.text()
                        yield rx.window_alert(f"图片生成失败！异常原因：{response.status}-{error_text}")
        except Exception as e:
            yield rx.window_alert("图片生成失败！异常原因：" + str(e))
        # 延迟状态更新
        async with self:
            self.processing = False

    def download_image(self, index_num: int, mode: str):
        """下载指定URL的图片"""
        image_url = self.text2img_urls[index_num] if mode == "text2img" else self.img2img_urls[index_num]
        return rx.call_script(f"""
              (async function() {{
                  try {{
                      const res = await fetch('{image_url}');
                      const blob = await res.blob();
                      const a = document.createElement('a');
                      a.href = URL.createObjectURL(blob);
                      a.download = 'image.png';
                      a.click();
                      URL.revokeObjectURL(a.href);
                  }} catch (err) {{
                      console.error("下载失败:", err);
                  }}
              }})();
          """)


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
                    "基于 google/gemini-3-pro-image-preview 模型的智能图片编辑器",
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
                                value=GeminiImageState.text2img_prompt,
                                placeholder="请输入提示词描述你想生成的图片",
                                on_change=GeminiImageState.set_text2img_prompt,
                                width=["20em", "25em"],
                                rows='5',
                                resize='vertical',
                            ),
                            rx.button(
                                "生成图片",
                                on_click=GeminiImageState.get_image,
                                width=["23em", "28.5em"],
                                loading=GeminiImageState.processing
                            ),
                            rx.cond(
                                GeminiImageState.text2img_urls.length() > 0,
                                rx.flex(
                                    rx.foreach(
                                        GeminiImageState.text2img_urls,
                                        lambda url, index_num: rx.vstack(
                                            image_modal(url),
                                            rx.button(
                                                "下载图片",
                                                width=["23em", "28.5em"],
                                                cursor="pointer",
                                                on_click=GeminiImageState.download_image(index_num, "text2img")
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
                                        GeminiImageState.uploading,  # 判断是否正在上传
                                        rx.spinner(size="3"),  # 显示加载动画
                                        rx.cond(
                                            GeminiImageState.upload_imgs.length() > 0,
                                            rx.flex(
                                                rx.foreach(
                                                    GeminiImageState.upload_imgs,
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
                                multiple=GeminiImageState.max_files > 1,  # 允许多文件上传
                                max_files=GeminiImageState.max_files,  # 最多上传3张图片
                                width="auto",
                                style={
                                    "padding": 0,
                                    "margin": 0,
                                    "border": "2px dashed #60a5fa",
                                },
                                on_drop=GeminiImageState.handle_upload(rx.upload_files(upload_id="upload")),
                            ),
                            rx.text(GeminiImageState.error_msg, color="red"),
                            rx.text_area(
                                value=GeminiImageState.img2img_prompt,
                                placeholder="请输入编辑提示词",
                                on_change=GeminiImageState.set_img2img_prompt,
                                width=["20em", "25em"],
                                rows='5',
                                resize='vertical',
                            ),
                            rx.button(
                                "编辑图片",
                                on_click=GeminiImageState.get_image,
                                width=["23em", "28.5em"],
                                loading=GeminiImageState.processing
                            ),
                            rx.cond(
                                GeminiImageState.img2img_urls.length() > 0,
                                rx.flex(
                                    rx.foreach(
                                        GeminiImageState.img2img_urls,
                                        lambda url, index_num: rx.vstack(
                                            image_modal(url),
                                            rx.button(
                                                "下载图片",
                                                width=["23em", "28.5em"],
                                                cursor="pointer",
                                                on_click=GeminiImageState.download_image(index_num, "img2img")
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
                    on_change=GeminiImageState.set_mode,  # 切换 tab 时更新模式
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
                            "/images/geminiImage/4.png",
                            "/images/geminiImage/5.png",
                            "/images/geminiImage/6.png",
                            "/images/geminiImage/1.jpg",
                            "/images/geminiImage/2.jpg",
                            "/images/geminiImage/3.jpg",
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
