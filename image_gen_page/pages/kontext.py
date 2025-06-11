# 加载配置
import base64
import hashlib
import os
import time

import reflex as rx
import requests

from image_gen_page.tool.common_tool import translate

fal_key = os.getenv('FAL_KEY')


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

    def image_to_base64(self):
        path = rx.get_upload_dir() / self.upload_img
        with path.open("rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode('utf-8')

        image_extension = self.upload_img.split('.')[-1].lower()
        if image_extension == 'png':
            mime_type = 'image/png'
        else:
            mime_type = 'image/jpeg'

        # 构建完整的 data URI
        return f"data:{mime_type};base64,{encoded_string}"

    def get_image(self):
        """调用大模型生成图片."""
        if self.prompt == "":
            return rx.window_alert("提示词不能为空！")
        if self.upload_img == "":
            return rx.window_alert("原图不能为空！")

        self.processing, self.complete = True, False
        try:
            yield
            prompt = translate(self.prompt)
            print(self.prompt + ' => ' + prompt)
            param = {
                'prompt': prompt,
                'image_url': self.image_to_base64(),
            }
            response = requests.post('https://queue.fal.run/fal-ai/flux-pro/kontext/max',
                                     json=param,
                                     headers={
                                         'Content-Type': 'application/json',
                                         'Authorization': 'Key ' + fal_key
                                     })
            if response.status_code == 200:
                data = response.json()
                # 最大等待时间（60秒）
                max_wait_time = 60
                start_time = time.time()
                retry_interval = 2  # 每次请求间隔2秒
                url = ''

                response_url = data['response_url']
                while True:
                    # 检查是否超时
                    if time.time() - start_time > max_wait_time:
                        raise Exception("等待超时，未能获取到图像数据")
                        break
                    response = requests.get(response_url,
                                            headers={
                                                'Content-Type': 'application/json',
                                                'Authorization': 'Key ' + fal_key
                                            })
                    data = response.json()
                    if 'images' in data and data['images']:
                        url = data['images'][0]['url']
                        break

                    # 等待一段时间后重试
                    time.sleep(retry_interval)
                self.image_urls = [url]
            else:
                yield rx.window_alert("图片生成失败！异常原因：" + response.status_code + '-' + response.text)
        except Exception as e:
            yield rx.window_alert("图片生成失败！异常原因：" + str(e))
        # 延迟状态更新
        yield self.setvar("processing", False)
        yield self.setvar("complete", True)

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
    return rx.center(
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
                max_size=10 * 1024 * 1024,  # 2MB
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

            # 空白区域填充，确保示例图片在底部
            rx.spacer(),

            rx.divider(margin_y="1em"),

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
        height="100%",
        padding_y="2em",
    )
