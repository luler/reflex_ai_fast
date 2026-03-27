import os
from urllib.parse import parse_qs, urlparse

import aiohttp
import reflex as rx


class Text2ImageState(rx.State):
    prompt = ""
    image_urls = []
    processing = False
    complete = False
    initialized_from_url = False

    model_count_dict = {}

    model = ""
    model_options = []
    available_models = []

    size = "1024x1024x(1:1)"
    size_options = [
        "1024x1024x(1:1)",
        "1024x1536x(2:3)",
        "1536x1024x(3:2)",
    ]

    n = "1"
    n_options = ["1", "2", "3", "4"]

    title = "通用文生图生成器"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        default_model = os.getenv("TEXT2IMAGE_MODEL", "gpt-4o-image")
        model_options = [item.strip() for item in os.getenv("TEXT2IMAGE_MODELS", default_model).split(",") if
                         item.strip()]
        if not model_options:
            model_options = [default_model]
        if default_model not in model_options:
            model_options.insert(0, default_model)

        model_counts = [item.strip() for item in os.getenv("TEXT2IMAGE_N", self.n).split(",") if item.strip()]
        for index, model in enumerate(model_options):
            count_value = model_counts[index] if index < len(model_counts) else model_counts[0]
            try:
                parsed_count = int(count_value)
            except ValueError:
                parsed_count = 1
            if parsed_count < 1:
                parsed_count = 1
            self.model_count_dict[model] = str(parsed_count)

        size_options = [item.strip() for item in os.getenv("TEXT2IMAGE_SIZES", ",".join(self.size_options)).split(",")
                        if item.strip()]
        if not size_options:
            size_options = self.size_options.copy()

        default_n = self.model_count_dict.get(model_options[0], self.n)
        if default_n not in self.n_options:
            default_n = self.n_options[0]

        self.available_models = model_options
        self.model_options = [model_options[0]]
        self.model = model_options[0]
        self.size_options = size_options
        self.size = size_options[0]
        self.n = default_n
        self.title = os.getenv("TEXT2IMAGE_TITLE", self.title)[:60]

    def set_prompt(self, prompt: str):
        self.prompt = prompt

    def set_size(self, size: str):
        self.size = size

    def set_model(self, model: str):
        self.model = model
        self.n = self.model_count_dict.get(model, self.n_options[0])

    @rx.event
    async def init_from_url(self):
        if self.initialized_from_url:
            return

        page = getattr(self.router, "page", None)
        raw_path = ""
        if page is not None:
            raw_path = getattr(page, "full_raw_path", "") or getattr(page, "raw_path", "") or ""

        query_params = {}
        if raw_path:
            query_params = {
                key: values[0]
                for key, values in parse_qs(urlparse(raw_path).query).items()
                if values
            }

        params = query_params or dict(getattr(page, "params", {}) or {})

        model = str(params.get("model", "")).strip()
        size = str(params.get("size", "")).strip()
        title = str(params.get("title", "")).strip()[:60]

        async with self:
            if model and model in self.available_models:
                self.model = model
            else:
                self.model = self.available_models[0]
            self.model_options = [self.model]
            self.n = self.model_count_dict.get(self.model, self.n_options[0])
            if size and size in self.size_options:
                self.size = size
            if title:
                self.title = title

            self.initialized_from_url = True

    @rx.event(background=True)
    async def get_image(self):
        if self.prompt == "":
            yield rx.window_alert("提示词不能为空！")
            return

        async with self:
            self.processing = True
            self.complete = False
            self.image_urls = []

        try:
            async with aiohttp.ClientSession() as session:
                size = self.size.split("x")
                width = int(size[0])
                height = int(size[1])
                requested_count = int(self.n)
                image_urls = []

                for _ in range(requested_count):
                    async with session.post(
                            os.getenv("TEXT2IMAGE_OPENAI_BASE_URL") + "/images/generations",
                            json={
                                "model": self.model,
                                "prompt": self.prompt,
                                "size": f"{width}x{height}",
                                "n": 1,
                            },
                            headers={
                                "Content-Type": "application/json",
                                "Authorization": "Bearer " + os.getenv("TEXT2IMAGE_OPENAI_API_KEY")
                            }
                    ) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            yield rx.window_alert(f"图片生成失败！异常原因：{response.status}-{error_text}")
                            return

                        data = await response.json()
                        current_urls = []
                        for item in data.get("data", []):
                            image_url = item.get("url")
                            if image_url:
                                current_urls.append(image_url)
                                continue

                            b64_json = item.get("b64_json")
                            if b64_json:
                                current_urls.append(f"data:image/png;base64,{b64_json}")

                        if not current_urls:
                            yield rx.window_alert(f"图片生成失败！未返回可用图片数据：{data}")
                            return

                        image_urls.extend(current_urls)

                async with self:
                    self.image_urls = image_urls
                    self.complete = True
        except Exception as e:
            yield rx.window_alert("图片生成失败！异常原因：" + str(e))
        finally:
            async with self:
                self.processing = False

    def download_image(self, url: str):
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
                height="20em",
                object_fit="cover",
                cursor="pointer",
            )
        ),
        rx.dialog.content(
            rx.image(
                src=image_url,
                width="100%",
                height="auto",
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
    return rx.vstack(
        rx.center(
            rx.vstack(
                rx.heading(
                    Text2ImageState.title,
                    font_size=["1.2em", "1.5em"],
                    text_align="center",
                    width="100%",
                ),
                rx.text_area(
                    value=Text2ImageState.prompt,
                    placeholder="请输入提示词",
                    on_change=Text2ImageState.set_prompt,
                    width=["20em", "25em"],
                    rows="5",
                    resize="vertical",
                ),
                rx.select(
                    Text2ImageState.model_options,
                    value=Text2ImageState.model,
                    on_change=Text2ImageState.set_model,
                    width=["23em", "28.5em"],
                    placeholder="选择模型",
                ),
                rx.select(
                    Text2ImageState.size_options,
                    value=Text2ImageState.size,
                    on_change=Text2ImageState.set_size,
                    width=["23em", "28.5em"],
                    placeholder="选择图片尺寸",
                ),
                rx.button(
                    "生成图片",
                    on_click=Text2ImageState.get_image,
                    width=["23em", "28.5em"],
                    loading=Text2ImageState.processing,
                ),
                rx.cond(
                    Text2ImageState.complete,
                    rx.flex(
                        rx.foreach(
                            Text2ImageState.image_urls,
                            lambda url: rx.vstack(
                                image_modal(url),
                                rx.button(
                                    "下载图片",
                                    width=["23em", "28.5em"],
                                    cursor="pointer",
                                    on_click=Text2ImageState.download_image(url),
                                ),
                                align="center",
                            ),
                        ),
                        margin_top="1em",
                        wrap="wrap",
                        justify="center",
                        gap="2em",
                    ),
                ),
                align="center",
            ),
            width="100%",
        ),
        width="100%",
        min_height="100vh",
        padding_y="2em",
    )
