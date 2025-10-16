# 加载配置
import os

import aiohttp
import reflex as rx


class JimengState(rx.State):
    """The app state."""

    prompt = ""
    image_urls = []
    processing = False
    complete = False

    size = "2048x2048x(1:1)"  # 默认尺寸
    size_options = [
        "3024x1296x(21:9)",
        "2560x1440x(16:9)",
        "2496x1664x(3:2)",
        "2304x1728x(4:3)",
        "2048x2048x(1:1)",
        "1728x2304x(3:4)",
        "1664x2496x(2:3)",
        "1440x2560x(9:16)",
    ]

    def set_size(self, size: str):
        self.size = size

    def set_prompt(self, prompt: str):
        self.prompt = prompt

    @rx.event(background=True)
    async def get_image(self):
        """调用大模型生成图片."""
        if self.prompt == "":
            yield rx.window_alert("提示词不能为空！")
            return
        async with self:
            self.processing = True
            self.complete = False
            self.image_urls = []
        try:
            size = self.size.split('x')
            ratio = size[2][1:-1]  # 移除两边的括号
            url = os.getenv('OPENAI_BASE_URL') + '/images/generations'
            payload = {
                "model": os.getenv('JIMENG_MODEL', 'jimeng'),
                "prompt": self.prompt,
                "ratio": ratio,
                "resolution": "2k",
            }
            headers = {
                'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + os.getenv('OPENAI_API_KEY')
            }
            async with aiohttp.ClientSession() as session:
                # 发起 POST 请求
                async with session.post(url, json=payload, headers=headers) as response:
                    # 检查 HTTP 错误状态码 (例如 4xx 或 5xx)
                    response.raise_for_status()
                    # 获取 JSON 响应体
                    data = await response.json()

            async with self:
                self.image_urls = [item["url"] for item in data["data"]]
        except Exception as e:
            yield rx.window_alert("图片生成失败！异常原因：" + str(e))
        # 延迟状态更新
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
                "智能提示词图片生成器（jimeng-4.0）",
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
