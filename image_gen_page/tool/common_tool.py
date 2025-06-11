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
