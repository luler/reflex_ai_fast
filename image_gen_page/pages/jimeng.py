# 加载配置
import os

import reflex as rx
import requests

class JimengState(rx.State):
    """The app state."""

    prompt = ""
    image_urls = []
    processing = False
    complete = False

    size = "1328x1328x(1:1)"  # 默认尺寸
    size_options = [
        "2016x864x(21:9)",
        "1664x936x(16:9)",
        "1584x1056x(3:2)",
        "1472x1104x(4:3)",
        "1328x1328x(1:1)",
        "1104x1472x(3:4)",
        "1056x1584x(2:3)",
        "936x1664x(9:16)",
    ]

    def set_size(self, size: str):
        self.size = size

    def set_prompt(self, prompt: str):
        self.prompt = prompt

    def get_image(self):
        """调用大模型生成图片."""
        if self.prompt == "":
            return rx.window_alert("提示词不能为空！")

        self.processing, self.complete = True, False
        try:
            yield
            size = self.size.split('x')
            width = int(size[0])
            height = int(size[1])
            response = requests.post(os.getenv('OPENAI_BASE_URL') + '/images/generations',
                                     json={
                                         "model": os.getenv('JIMENG_MODEL', 'jimeng-3.0'),
                                         'prompt': self.prompt,
                                         'height': height,
                                         'width': width,
                                     },
                                     headers={
                                         'Content-Type': 'application/json',
                                         'Authorization': 'Bearer ' + os.getenv('OPENAI_API_KEY')
                                     })
            if response.status_code == 200:
                data = response.json()
                self.image_urls = [item["url"] for item in data["data"]]
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
    return rx.vstack(rx.center(
        rx.vstack(
            rx.heading(
                "智能提示词图片生成器（jimeng-3.0）",
                font_size=["1.2em", "1.5em"],
                text_align="center",
                width="100%"
            ),
            rx.text_area(
                value=JimengState.prompt,
                placeholder="请输入提示词",
                on_change=JimengState.set_prompt,
                width=["20em", "25em"],
                rows='5',
                resize='vertical',
            ),
            rx.select(
                JimengState.size_options,
                value=JimengState.size,
                on_change=JimengState.set_size,
                width=["23em", "28.5em"],
                placeholder="选择图片尺寸",
            ),
            rx.button(
                "生成图片",
                on_click=JimengState.get_image,
                width=["23em", "28.5em"],
                loading=JimengState.processing
            ),
            rx.cond(
                JimengState.complete,
                rx.flex(
                    rx.foreach(
                        JimengState.image_urls,
                        lambda url: rx.vstack(
                            image_modal(url),
                            rx.button(
                                "下载图片",
                                width=["23em", "28.5em"],
                                cursor="pointer",
                                on_click=JimengState.download_image(url)
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
                        ["/images/jimeng/1.png", "/images/jimeng/2.png", "/images/jimeng/3.png", "/images/jimeng/4.png",
                         "/images/jimeng/5.png", "/images/jimeng/6.png", "/images/jimeng/7.png", "/images/jimeng/8.png",
                         "/images/jimeng/9.png", "/images/jimeng/10.png", ],
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
