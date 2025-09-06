# 加载配置
import hashlib
import os

import aiohttp
import reflex as rx

from image_gen_page.tool.common_tool import image_to_base64


class GeminiImageState(rx.State):
    """The app state."""

    prompt = ""
    image_urls = []
    processing = False
    complete = False
    uploading = False  # 新增上传状态变量

    upload_img: str = ''
    error_msg: str = ''

    @rx.event
    async def handle_upload(self, files: list[rx.UploadFile]):
        self.uploading = True  # 开始上传时设置状态
        try:
            if len(files) == 0:
                self.error_msg = "请选择10MB以内的图片"
            else:
                self.error_msg = ''
            for file in files:
                data = await file.read()
                md5 = hashlib.md5(data).hexdigest()
                ext = file.name.split('.')[-1]
                filename = f"{md5}.{ext}"
                path = rx.get_upload_dir() / filename
                with path.open("wb") as f:
                    f.write(data)
                self.upload_img = filename
        finally:
            self.uploading = False  # 上传完成后重置状态

    def set_prompt(self, prompt: str):
        self.prompt = prompt

    @rx.event(background=True)
    async def get_image(self):
        """调用大模型生成图片."""
        if self.prompt == "":
            yield rx.window_alert("提示词不能为空！")
            return
        if self.upload_img == "":
            yield rx.window_alert("原图不能为空！")
            return
        async with self:
            self.processing = True
            self.complete = False
            self.image_urls = []
        try:
            param = {
                'model': os.getenv('GEMINI_IMAGE_COVER_MODEL'),
                'messages': [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": f"""
请根据以下要求编辑图片并直接返回编辑后的图片：
编辑要求：{self.prompt}
请严格按照要求对图片进行编辑。直接返回编辑后的图片，无需额外说明。
"""
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": image_to_base64(rx.get_upload_dir(), self.upload_img)
                                }
                            }
                        ]
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
                            else:
                                # 兼容第二种格式
                                for content in data['choices'][0]['message']['content']:
                                    if content.get('type', '').startswith('image/'):
                                        if content['image_url'].startswith('data:') or content['image_url'].startswith(
                                                'http'):
                                            images.append(content['image_url'])
                                        else:
                                            images.append(f"data:{content.get('type')};base64,{content['image_url']}")
                            self.image_urls = images
                    else:
                        error_text = await response.text()
                        yield rx.window_alert(f"图片生成失败！异常原因：{response.status}-{error_text}")
        except Exception as e:
            yield rx.window_alert("图片生成失败！异常原因：" + str(e))
        # 延迟状态更新
        async with self:
            self.processing = False
            self.complete = True

    def download_image(self, index_num: int):
        """下载指定URL的图片"""
        return rx.call_script(f"""
              (async function() {{
                  try {{
                      const res = await fetch('{self.image_urls[index_num]}');
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
    return rx.vstack(rx.center(
        rx.vstack(
            rx.heading(
                "基于 google/gemini-2.5-flash-image-preview 模型的智能图片编辑器",
                font_size=["1.2em", "1.5em"],
                text_align="center",
                width="100%"
            ),
            rx.upload.root(
                rx.box(
                    rx.cond(
                        GeminiImageState.uploading,  # 判断是否正在上传
                        rx.spinner(size="3"),  # 显示加载动画
                        rx.cond(
                            GeminiImageState.upload_img,
                            rx.image(
                                src=rx.get_upload_url(GeminiImageState.upload_img),
                                width="100%",
                                height="100%",
                                style={
                                    "objectFit": "contain",
                                    "display": "block",
                                },
                            ),
                            rx.text(
                                "请上传图片",
                                style={
                                    "color": "#888",
                                    "fontSize": "1.2em",
                                },
                            ),
                        ),
                    ),
                    style={
                        "width": "100%",
                        "height": "100%",
                        "overflow": "hidden",  # 裁剪溢出部分
                        "display": "flex",  # 关键：flex 布局
                        "alignItems": "center",  # 垂直居中
                        "justifyContent": "center",  # 水平居中
                    },
                ),
                id="upload",
                max_size=10 * 1024 * 1024,  # 10MB
                accept={
                    "image/png": [".png"],
                    "image/jpeg": [".jpg", ".jpeg"],
                },
                multiple=False,
                width=["20em", "25em"],
                style={
                    # "width": "20em",
                    "height": "16em",
                    "padding": 0,
                    "margin": 0,
                    "border": "2px dashed #60a5fa",
                    "display": "flex",
                    "alignItems": "center",
                    "justifyContent": "center",
                    "overflow": "hidden",  # 如果希望整个上传区域都不溢出，也加上
                },
                on_drop=GeminiImageState.handle_upload(rx.upload_files(upload_id="upload")),
            ),
            rx.text(GeminiImageState.error_msg, color="red"),
            rx.text_area(
                value=GeminiImageState.prompt,
                placeholder="请输入提示词",
                on_change=GeminiImageState.set_prompt,
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
                GeminiImageState.complete,
                rx.flex(
                    rx.foreach(
                        GeminiImageState.image_urls,
                        lambda url, index_num: rx.vstack(
                            image_modal(url),
                            rx.button(
                                "下载图片",
                                width=["23em", "28.5em"],
                                cursor="pointer",
                                on_click=GeminiImageState.download_image(index_num)
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
                        ["/images/geminiImage/1.jpg", "/images/geminiImage/2.jpg", "/images/geminiImage/3.jpg", ],
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
