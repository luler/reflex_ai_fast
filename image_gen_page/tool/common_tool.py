import base64
import os

import aiohttp
from deep_translator import GoogleTranslator


# 翻译中文为英文
def translate(text, source='zh-CN', target='en'):
    proxies = None
    # 检查环境变量是否存在
    if os.getenv('translate_proxy'):
        proxy_url = os.getenv('translate_proxy')
        proxies = {
            'http': proxy_url,
            'https': proxy_url
        }

    translator = GoogleTranslator(
        source=source,
        target=target,
        proxies=proxies
    )
    return translator.translate(text)


# 图片转base64
def image_to_base64(upload_dir, upload_img):
    path = upload_dir / upload_img
    with path.open("rb") as image_file:
        encoded_string = base64.b64encode(image_file.read()).decode('utf-8')

    image_extension = upload_img.split('.')[-1].lower()
    if image_extension == 'png':
        mime_type = 'image/png'
    else:
        mime_type = 'image/jpeg'

    # 构建完整的 data URI
    return f"data:{mime_type};base64,{encoded_string}"


# 图片链接转base64
def image_data_to_base64(image_data, content_type='image/png'):
    encoded_string = base64.b64encode(image_data).decode('utf-8')
    return f"data:{content_type};base64,{encoded_string}"


async def image_url_to_data_url(session: aiohttp.ClientSession, image_url: str) -> str:
    if image_url.startswith("data:image/"):
        return image_url

    async with session.get(image_url) as response:
        if response.status != 200:
            raise ValueError(f"HTTP {response.status}")

        image_data = await response.read()
        content_type = response.headers.get("Content-Type", "image/png").split(";")[0].strip().lower()
        if not content_type or content_type in ("application/octet-stream", "binary/octet-stream"):
            if image_data.startswith(b"RIFF") and b"WEBP" in image_data[:16]:
                content_type = "image/webp"
            elif image_data.startswith(b"\x89PNG"):
                content_type = "image/png"
            elif image_data.startswith(b"\xff\xd8\xff"):
                content_type = "image/jpeg"
            elif image_url.lower().split("?")[0].endswith(".webp"):
                content_type = "image/webp"
            elif image_url.lower().split("?")[0].endswith((".jpg", ".jpeg")):
                content_type = "image/jpeg"
            else:
                content_type = "image/png"
        return image_data_to_base64(image_data, content_type)
