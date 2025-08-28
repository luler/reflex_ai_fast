# 加载配置
import asyncio
import base64
import os
import re

import aiohttp  # 替换 requests 为 aiohttp
import reflex as rx


class PageState(rx.State):
    """The app state."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        cover_model = os.getenv('COVER_MODEL', '')
        cover_count = os.getenv('COVER_COUNT', '1')
        cover_models = cover_model.split(',') if cover_model else []
        cover_counts = cover_count.split(',')
        for index, model in enumerate(cover_models):
            count = int(cover_counts[index]) if index < len(cover_counts) else 1
            self.cover_counts_dict[model] = count

        if cover_models:
            self.model = cover_models[0]
            self.model_options = cover_models

    prompt = ""
    image_urls = []
    processing = False
    complete = False

    cover_counts_dict = {}
    model = ""  # 默认尺寸
    model_options = []

    def set_model(self, model: str):
        self.model = model

    size = "1024x576x(16:9)"  # 默认尺寸
    size_options = [
        "1024x1024x(1:1)",
        "1024x576x(16:9)",
        "576x1024x(9:16)",
        "1024x768x(4:3)",
        "768x1024x(3:4)",
        "800x1200x(2:3)",
        "1200x800x(3:2)",
    ]

    def set_size(self, size: str):
        self.size = size

    style = "现代简约风格，干净利落的线条和留白设计"  # 默认尺寸
    style_options = [
        "现代简约风格，干净利落的线条和留白设计",
        "高科技风格，带有未来感和数字元素",
        "渐变色背景，富有视觉层次感",
        "极简主义设计，最大程度简化元素",
        "抽象艺术风格，包含独特的形状和色彩组合",
        "毛玻璃质感，搭配现代渐变色",
        "3D立体元素与光影效果",
        "故障艺术(Glitch Art)风格",
        "孟菲斯风格(Memphis Design)",
        "蒸汽波(Vaporwave)美学",
        "新拟物化(Neumorphism)设计",
    ]

    def set_style(self, style: str):
        self.style = style

    def set_prompt(self, prompt: str):
        self.prompt = prompt

    @rx.event(background=True)
    async def get_image(self):
        if self.prompt == "":
            yield rx.window_alert("提示词不能为空！")
            return
        async with self:
            self.processing = True
            self.complete = False

        async with aiohttp.ClientSession() as session:
            try:
                size = self.size.split('x')
                width = int(size[0])
                height = int(size[1])
                content = f"""
# 请使用HTML、JS和CSS设计一个视觉吸引力强的封面图，确保设计既美观又专业，能够有效吸引受众的注意力

## 具有以下特点
- 尺寸规格：固定宽高为{width}px×{height}px
- 文字限制：只需要包含主题内容的文字，可以拆分关键词优化显示
- 主题内容："{self.prompt}"

## 设计风格
{self.style}
- 配色方案自动根据风格生成，确保视觉效果和谐

## 排版要求：
- 主标题字体大小合适，确保清晰可辨，最好居中显示
- 自动定位关键词，可特别突出，可考虑使用醒目颜色或特殊设计元素
- 整体布局平衡，视觉层次分明

## 额外元素：
- 可以根据主题内容，简单适配一些标签、图标或相关图形
- 考虑添加简约的装饰元素增强视觉吸引力

## 实用性考虑：
- 设计应适合截图分享到社交媒体
- 确保边缘有足够留白以适应不同平台的显示需求
- 文字对比度要高，确保在小尺寸下仍清晰可读

## 交付要求
- 只需要返回一个设计后的html代码，里面包含完整HTML、JS、CSS代码内容，页面元素不要交互和动画效果，浏览器打开页面渲染完就是最终的静态效果
- 封面图应该放在id=maincover的标签中，以便于我后续截图这个标签的内容作为封面图
                """
                # 模型校验
                if self.model not in self.model_options:
                    raise Exception('模型不存在')
                count = self.cover_counts_dict[self.model]
                # 并发执行多次请求
                tasks = [fetch_image(session, self.model, content) for _ in range(count)]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                image_urls = []
                for result in results:
                    if isinstance(result, Exception):
                        yield rx.window_alert(f"图片生成失败！异常原因1：{str(result)}")
                        continue
                    first_html_block = extract_first_html_code_block(result)
                    # 截图处理 - 改为异步
                    try:
                        image_base64 = await take_screenshot(session, first_html_block)
                        image_urls.append(f"data:image/png;base64,{image_base64}")
                    except Exception as e:
                        yield rx.window_alert(f"截图失败：{str(e)}")
                        continue

                async with self:
                    self.image_urls = image_urls
            except Exception as e:
                yield rx.window_alert("图片生成失败！异常原因2：" + str(e))

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


async def fetch_image(session, model, content):
    async with session.post(
            os.getenv('COVER_OPENAI_BASE_URL', os.getenv('OPENAI_BASE_URL')) + '/chat/completions',
            json={
                "model": model,
                "messages": [{"role": "user", "content": content}],
                "stream": False
            },
            headers={
                'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + os.getenv('COVER_OPENAI_API_KEY', os.getenv('OPENAI_API_KEY'))
            }
    ) as response:
        if response.status == 200:
            data = await response.json()
            return data['choices'][0]['message']['content']
        else:
            error_text = await response.text()
            raise Exception(f"Request failed: {response.status}-{error_text}")


async def take_screenshot(session, html_content):
    """异步截图函数"""
    screenshot_data = {
        "url": html_content,
        "viewport_width": 1920,
        "viewport_height": 1600,
        "element_selector": "#maincover",
        "wait_second": 3,
        "use_proxy": 1,
    }

    async with session.post(
            os.getenv('SCREEN_BASE_URL', 'http://10.8.0.2:14140') + '/screenshot',
            json=screenshot_data  # 使用 json 参数而不是 data
    ) as response:
        if response.status == 200:
            content = await response.read()
            return base64.b64encode(content).decode('utf-8')
        else:
            error_text = await response.text()
            raise Exception(f"Screenshot failed: {response.status}-{error_text}")


def extract_first_html_code_block(text):
    # 优先匹配包含<!DOCTYPE html>的完整HTML代码块
    doctype_pattern = r"<!DOCTYPE html>.*?<html.*?>.*?</html>"
    match = re.search(doctype_pattern, text, re.DOTALL | re.IGNORECASE)

    if match:
        return match.group(0)  # 返回匹配的完整HTML代码

    # 如果没有<!DOCTYPE html>，匹配<html>到</html>的完整HTML代码块
    html_pattern = r"<html.*?>.*?</html>"
    match = re.search(html_pattern, text, re.DOTALL | re.IGNORECASE)

    if match:
        return match.group(0)  # 返回匹配的完整HTML代码

    return None  # 如果没有匹配项，返回None


def image_modal(image_url):
    return rx.dialog.root(
        rx.dialog.trigger(
            rx.image(
                src=image_url,
                width=["20em", "25em"],
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
    return rx.vstack(
        # 上方内容（生成器交互部分）
        rx.center(
            rx.vstack(
                rx.heading(
                    "文章封面图生成器（AI大模型助力）",
                    font_size=["1.2em", "1.5em"],
                    text_align="center",
                    width="100%"
                ),
                rx.text_area(
                    value=PageState.prompt,
                    placeholder="请输入文章标题或主题内容",
                    on_change=PageState.set_prompt,
                    width=["20em", "25em"],
                    rows='5',
                    resize='vertical',
                ),
                rx.select(
                    PageState.size_options,
                    value=PageState.size,
                    on_change=PageState.set_size,
                    width=["23em", "28.5em"],
                    placeholder="选择图片尺寸",
                ),
                rx.select(
                    PageState.style_options,
                    value=PageState.style,
                    on_change=PageState.set_style,
                    width=["23em", "28.5em"],
                    placeholder="选择图片风格",
                ),
                rx.select(
                    PageState.model_options,
                    value=PageState.model,
                    on_change=PageState.set_model,
                    width=["23em", "28.5em"],
                    placeholder="选择大语言模型",
                ),
                rx.button(
                    "生成图片",
                    on_click=PageState.get_image,
                    width=["23em", "28.5em"],
                    loading=PageState.processing
                ),
                rx.cond(
                    PageState.complete,
                    rx.flex(
                        rx.foreach(
                            PageState.image_urls,
                            lambda url: rx.vstack(
                                image_modal(url),
                                rx.button(
                                    "下载图片",
                                    width="20em",
                                    cursor="pointer",
                                    on_click=PageState.download_image(url)
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
                        ["/images/cover/1.png", "/images/cover/2.png", "/images/cover/3.png", "/images/cover/4.png",
                         "/images/cover/5.png", "/images/cover/6.png", "/images/cover/7.png", "/images/cover/8.png",
                         "/images/cover/9.png", "/images/cover/10.png", ],
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
