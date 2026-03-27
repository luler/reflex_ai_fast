import os

import dotenv
import reflex as rx

from image_gen_page.pages import jimeng, gpt4o, cover, kontext, aichart, geminiImage, grokImage, grokVideo, mondo, \
    text2image

# 初始化配置
dotenv.load_dotenv()

# 设置环境变量以禁用代理
os.environ["no_proxy"] = "localhost,127.0.0.1,::1"

# 创建reflex示例并添加路由页面
app = rx.App()
app.add_page(jimeng.index, route='/', title="智能提示词图片生成器")
app.add_page(gpt4o.index, route='/gpt4oimage', title="智能提示词图片生成器")
app.add_page(cover.index, route='/cover', title="在线制作文章封面图")
app.add_page(kontext.index, route='/kontext', title="基于 flux-pro/kontext 模型的智能图片编辑器")
app.add_page(geminiImage.index, route='/geminiImage',
             title="基于 google/gemini-3-pro-image-preview 模型的智能图片编辑器")
app.add_page(grokImage.index, route='/grokImage', title="基于 grok imagine 模型的智能图片生成器")
app.add_page(grokVideo.index, route='/grokVideo', title="基于 grok imagine 模型的智能视频生成器")
app.add_page(mondo.index, route='/mondo', title="基于 Nano Banana 模型的大师级海报生成器")
app.add_page(aichart.index, route='/aichart', title="AI 统计图表生成器")
app.add_page(text2image.index, route='/text2image', title="通用文生图生成器",
             on_load=text2image.Text2ImageState.init_from_url)
