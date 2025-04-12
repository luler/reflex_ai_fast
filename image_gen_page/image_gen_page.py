import os

import dotenv
import reflex as rx
import requests

# 加载配置
dotenv.load_dotenv()

openai_base_url = os.getenv('OPENAI_BASE_URL')
openai_api_key = os.getenv('OPENAI_API_KEY')


class State(rx.State):
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
            response = requests.post(openai_base_url + '/images/generations',
                                     json={
                                         "model": "jimeng-3.0",
                                         'prompt': self.prompt,
                                         'height': height,
                                         'width': width,
                                     },
                                     headers={
                                         'Content-Type': 'application/json',
                                         'Authorization': 'Bearer ' + openai_api_key
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


def index():
    return rx.center(
        rx.vstack(
            rx.heading("智能提示词图片生成器（jimeng-3.0）", font_size="1.5em"),
            rx.text_area(
                placeholder="请输入提示词",
                on_blur=State.set_prompt,
                width="25em",
                rows='5',
            ),
            rx.select(
                State.size_options,
                value=State.size,
                on_change=State.set_size,
                width="28.5em",
                placeholder="选择图片尺寸",
            ),
            rx.button(
                "生成图片",
                on_click=State.get_image,
                width="28.5em",
                loading=State.processing
            ),
            rx.cond(
                State.complete,
                rx.hstack(
                    rx.foreach(
                        State.image_urls,
                        lambda url: rx.vstack(
                            rx.image(src=url, width="20em", height="20em", ),
                            rx.button(
                                "下载图片",
                                width="20em",
                                on_click=State.download_image(url)
                            ),
                            align='center',
                        ),
                    )
                )
            ),
            align="center",
        ),
        width="100%",
        height="100vh",
    )


# 创建reflex示例并添加路由页面
app = rx.App()
app.add_page(index, title="智能提示词图片生成器")
