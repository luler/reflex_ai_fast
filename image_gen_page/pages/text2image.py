import hashlib
import os
from urllib.parse import parse_qs, urlparse

import aiohttp
import reflex as rx


def parse_size_options(value: str) -> list[str]:
    options = []
    for item in value.split(","):
        size = item.strip()
        parts = size.split("x", 2)
        if len(parts) < 2:
            continue
        try:
            width = int(parts[0])
            height = int(parts[1])
        except ValueError:
            continue
        if width > 0 and height > 0:
            options.append(size)
    return options


class Text2ImageState(rx.State):
    prompt = ""
    image_urls = []
    processing = False
    complete = False
    initialized_from_url = False
    uploading = False
    upload_imgs = []
    error_msg: str = ""
    max_files: int = 1

    model_count_dict = {}

    model = ""
    model_options = []
    available_models = []
    allow_edit = False

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

        size_options = parse_size_options(os.getenv("TEXT2IMAGE_SIZES", ",".join(self.size_options)))
        if not size_options:
            size_options = self.size_options.copy()

        default_n = self.model_count_dict.get(model_options[0], self.n)
        if default_n not in self.n_options:
            default_n = self.n_options[0]

        self.available_models = model_options
        self.model_options = [model_options[0]]
        self.model = model_options[0]
        self.allow_edit = False
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
        if not self.allow_edit:
            self.upload_imgs = []
            self.error_msg = ""

    @rx.event
    async def init_from_url(self):
        if self.initialized_from_url:
            return

        raw_url = getattr(self.router, "url", "") or ""
        query_params = {}
        if raw_url:
            query_params = parse_qs(urlparse(raw_url).query)

        params = query_params

        def get_param(name: str, default: str = "") -> str:
            value = params.get(name, default)
            if isinstance(value, list):
                return ",".join(str(item) for item in value)
            return str(value)

        model = get_param("model").strip()
        size = get_param("size").strip()
        url_size_options = parse_size_options(get_param("sizes"))
        title = get_param("title").strip()[:60]
        edit = get_param("edit", "0").strip()

        async with self:
            if model and model in self.available_models:
                self.model = model
            else:
                self.model = self.available_models[0]
            self.model_options = [self.model]
            self.n = self.model_count_dict.get(self.model, self.n_options[0])
            self.allow_edit = edit == "1"
            if url_size_options:
                self.size_options = url_size_options
            if not self.allow_edit:
                self.upload_imgs = []
                self.error_msg = ""
            size_options = list(self.size_options)
            parsed_size = parse_size_options(size)
            if parsed_size and parsed_size[0] not in size_options:
                size_options.append(parsed_size[0])
                self.size_options = size_options
            if parsed_size and parsed_size[0] in size_options:
                self.size = parsed_size[0]
            elif size_options:
                self.size = size_options[0]
            if title:
                self.title = title

            self.initialized_from_url = True

    @rx.event
    async def handle_upload(self, files: list[rx.UploadFile]):
        self.uploading = True
        try:
            if len(files) == 0:
                self.error_msg = "请选择一张PNG、JPG或WEBP格式的参考图片"
                return
            self.error_msg = ""
            self.upload_imgs = []
            file = files[0]
            data = await file.read()
            md5 = hashlib.md5(data).hexdigest()
            ext = file.name.split('.')[-1].lower()
            filename = f"{md5}.{ext}"
            path = rx.get_upload_dir() / filename
            with path.open("wb") as f:
                f.write(data)
            self.upload_imgs.append(filename)
        finally:
            self.uploading = False

    def clear_reference_image(self):
        self.upload_imgs = []
        self.error_msg = ""

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
                    if self.allow_edit and len(self.upload_imgs) > 0:
                        image_path = rx.get_upload_dir() / self.upload_imgs[0]
                        with open(image_path, "rb") as f:
                            image_data = f.read()

                        ext = self.upload_imgs[0].split('.')[-1].lower()
                        content_type_map = {
                            'png': 'image/png',
                            'jpg': 'image/jpeg',
                            'jpeg': 'image/jpeg',
                            'webp': 'image/webp',
                        }
                        content_type = content_type_map.get(ext, 'image/png')

                        form = aiohttp.FormData()
                        form.add_field("model", self.model)
                        form.add_field("prompt", self.prompt)
                        form.add_field("n", "1")
                        form.add_field("image", image_data, filename=self.upload_imgs[0], content_type=content_type)

                        headers = {
                            "Authorization": "Bearer " + os.getenv("TEXT2IMAGE_OPENAI_API_KEY")
                        }
                        request_kwargs = {
                            "data": form,
                            "headers": headers,
                        }
                        endpoint = "/images/edits"
                    else:
                        headers = {
                            "Content-Type": "application/json",
                            "Authorization": "Bearer " + os.getenv("TEXT2IMAGE_OPENAI_API_KEY")
                        }
                        request_kwargs = {
                            "json": {
                                "model": self.model,
                                "prompt": self.prompt,
                                "size": f"{width}x{height}",
                                "n": 1,
                            },
                            "headers": headers,
                        }
                        endpoint = "/images/generations"

                    async with session.post(
                            os.getenv("TEXT2IMAGE_OPENAI_BASE_URL") + endpoint,
                            **request_kwargs,
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
                    font_size=["1.05em", "1.2em", "1.35em"],
                    font_weight="600",
                    text_align="center",
                    width=["20em", "25em"],
                    max_width="100%",
                    line_height="1.35",
                    white_space="normal",
                    word_break="break-word",
                    overflow_wrap="anywhere",
                    padding_x="0.5em",
                    margin_bottom="0.25em",
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
                rx.cond(
                    Text2ImageState.allow_edit,
                    rx.vstack(
                        rx.text("参考图（可选）", font_size="0.9em", width=["20em", "25em"]),
                        rx.upload.root(
                            rx.box(
                                rx.cond(
                                    Text2ImageState.uploading,
                                    rx.spinner(size="3"),
                                    rx.cond(
                                        Text2ImageState.upload_imgs.length() > 0,
                                        rx.flex(
                                            rx.foreach(
                                                Text2ImageState.upload_imgs,
                                                lambda img: rx.box(
                                                    rx.image(
                                                        src=rx.get_upload_url(img),
                                                        height="10em",
                                                        object_fit="contain",
                                                    ),
                                                    rx.icon_button(
                                                        rx.icon("x", size=15),
                                                        on_click=Text2ImageState.clear_reference_image,
                                                        position="absolute",
                                                        top="0",
                                                        right="0",
                                                        size="1",
                                                        variant="solid",
                                                        color_scheme="red",
                                                    ),
                                                    position="relative",
                                                ),
                                            ),
                                            wrap="wrap",
                                            justify="center",
                                        ),
                                        rx.text("点击或拖拽上传参考图"),
                                    ),
                                ),
                                width=["20em", "25em"],
                                min_height="8em",
                                display="flex",
                                align_items="center",
                                justify_content="center",
                                border="1px dashed var(--gray-7)",
                                border_radius="12px",
                                padding="1em",
                            ),
                            id="text2image_upload",
                            max_size=10 * 1024 * 1024,
                            accept={
                                "image/png": [".png"],
                                "image/jpeg": [".jpg", ".jpeg"],
                                "image/webp": [".webp"],
                            },
                            multiple=False,
                            on_drop=Text2ImageState.handle_upload(rx.upload_files(upload_id="text2image_upload")),
                            width=["20em", "25em"],
                        ),
                        rx.cond(
                            Text2ImageState.error_msg != "",
                            rx.text(Text2ImageState.error_msg, color="red", font_size="0.85em", width=["20em", "25em"]),
                        ),
                        align="center",
                        spacing="1",
                        width=["20em", "25em"],
                    ),
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
