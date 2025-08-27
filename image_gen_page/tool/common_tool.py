import base64
import os

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
