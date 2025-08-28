# 加载配置
import asyncio
import hashlib
import os

import aiohttp
import reflex as rx

from image_gen_page.tool.common_tool import translate, image_to_base64


class KontextState(rx.State):
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
        try:
            prompt = translate(self.prompt)
            print(self.prompt + ' => ' + prompt)
            param = {
                'prompt': prompt,
                'image_url': image_to_base64(rx.get_upload_dir(), self.upload_img),
            }
            async with aiohttp.ClientSession() as session:
                # 发起初始请求
                async with session.post(
                        'https://queue.fal.run/fal-ai/flux-pro/kontext/max',
                        json=param,
                        headers={
                            'Content-Type': 'application/json',
                            'Authorization': 'Key ' + os.getenv('FAL_KEY')
                        }
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        response_url = data['response_url']

                        # 轮询获取结果
                        url = await self._poll_for_result(session, response_url)

                        async with self:
                            self.image_urls = [url]
                    else:
                        error_text = await response.text()
                        yield rx.window_alert(f"图片生成失败！异常原因：{response.status}-{error_text}")

        except Exception as e:
            yield rx.window_alert("图片生成失败！异常原因：" + str(e))
        # 延迟状态更新
        async with self:
            self.processing = False
            self.complete = True

    async def _poll_for_result(self, session: aiohttp.ClientSession, response_url: str) -> str:
        """轮询获取生成结果"""
        max_wait_time = 60  # 最大等待时间（60秒）
        retry_interval = 2  # 每次请求间隔2秒
        start_time = asyncio.get_event_loop().time()

        while True:
            # 检查是否超时
            if asyncio.get_event_loop().time() - start_time > max_wait_time:
                raise Exception("等待超时，未能获取到图像数据")

            async with session.get(
                    response_url,
                    headers={
                        'Content-Type': 'application/json',
                        'Authorization': 'Key ' + os.getenv('FAL_KEY')
                    }
            ) as response:
                data = await response.json()
                if 'images' in data and data['images']:
                    return data['images'][0]['url']

            # 等待一段时间后重试
            await asyncio.sleep(retry_interval)

    def download_image(self, url: str):
        """下载指定URL的图片"""
        return rx.call_script(f"""
              (async function() {{
                  try {{
                      const res = await fetch('{url}');
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
                "基于flux-pro/kontext模型的智能图片编辑器",
                font_size=["1.2em", "1.5em"],
                text_align="center",
                width="100%"
            ),
            rx.upload.root(
                rx.box(
                    rx.cond(
                        KontextState.uploading,  # 判断是否正在上传
                        rx.spinner(size="3"),  # 显示加载动画
                        rx.cond(
                            KontextState.upload_img,
                            rx.image(
                                src=rx.get_upload_url(KontextState.upload_img),
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
                on_drop=KontextState.handle_upload(rx.upload_files(upload_id="upload")),
            ),
            rx.text(KontextState.error_msg, color="red"),
            rx.text_area(
                value=KontextState.prompt,
                placeholder="请输入提示词",
                on_change=KontextState.set_prompt,
                width=["20em", "25em"],
                rows='5',
                resize='vertical',
            ),
            rx.button(
                "生成图片",
                on_click=KontextState.get_image,
                width=["23em", "28.5em"],
                loading=KontextState.processing
            ),

            rx.cond(
                KontextState.complete,
                rx.flex(
                    rx.foreach(
                        KontextState.image_urls,
                        lambda url: rx.vstack(
                            image_modal(url),
                            rx.button(
                                "下载图片",
                                width=["23em", "28.5em"],
                                cursor="pointer",
                                on_click=KontextState.download_image(url)
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
                        ["/images/kontext/1.jpg", "/images/kontext/2.jpg", "/images/kontext/3.jpg", ],
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
