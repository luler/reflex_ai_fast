# 加载配置
import os

import reflex as rx
import requests

openai_base_url = os.getenv('OPENAI_BASE_URL')
openai_api_key = os.getenv('OPENAI_API_KEY')


class Gpt4oState(rx.State):
    """The app state."""

    prompt = ""
    image_urls = []
    processing = False
    complete = False

    size = "1024x1024x(1:1)"  # 默认尺寸
    size_options = [
        "1024x1024x(1:1)",
        "1024x1536x(2:3)",
        "1536x1024x(3:2)",
    ]

    def set_size(self, size: str):
        self.size = size

    def set_prompt(self, prompt: str):
        self.prompt = prompt

    @rx.event(background=True)
    async def get_image(self):
        async with self:
            if self.prompt == "":
                yield rx.window_alert("提示词不能为空！")
                return

            self.processing, self.complete = True, False
            yield
        try:
            size = self.size.split('x')
            width = int(size[0])
            height = int(size[1])
            response = requests.post(openai_base_url + '/images/generations',
                                     json={
                                         "model": "gpt-image-1",
                                         'prompt': self.prompt,
                                         'height': height,
                                         'width': width,
                                         'n': 1,
                                     },
                                     headers={
                                         'Content-Type': 'application/json',
                                         'Authorization': 'Bearer ' + openai_api_key
                                     })
            if response.status_code == 200:
                data = response.json()
                async with self:
                    self.image_urls = [item["url"] for item in data["data"]]
            else:
                yield rx.window_alert("图片生成失败！异常原因：" + str(response.status_code) + '-' + response.text)
        except Exception as e:
            yield rx.window_alert("图片生成失败！异常原因：" + str(e))
        async with self:
            self.processing = False
            self.complete = True

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
                width="20em",
                height="20em",
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
                "智能提示词图片生成器（GPT-4o-Image）",
                font_size=["1.2em", "1.5em"],
                text_align="center",
                width="100%"
            ),
            rx.text_area(
                value=Gpt4oState.prompt,
                placeholder="请输入提示词",
                on_change=Gpt4oState.set_prompt,
                width=["20em", "25em"],
                rows='5',
                resize='vertical',
            ),
            rx.select(
                Gpt4oState.size_options,
                value=Gpt4oState.size,
                on_change=Gpt4oState.set_size,
                width=["23em", "28.5em"],
                placeholder="选择图片尺寸",
            ),
            rx.button(
                "生成图片",
                on_click=Gpt4oState.get_image,
                width=["23em", "28.5em"],
                loading=Gpt4oState.processing
            ),
            rx.cond(
                Gpt4oState.complete,
                rx.flex(
                    rx.foreach(
                        Gpt4oState.image_urls,
                        lambda url: rx.vstack(
                            image_modal(url),
                            rx.button(
                                "下载图片",
                                width="20em",
                                cursor="pointer",
                                on_click=Gpt4oState.download_image(url)
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
