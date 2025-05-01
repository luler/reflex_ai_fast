import dotenv
import reflex as rx

from image_gen_page.pages import jimeng, gpt4o, cover

# 初始化配置
dotenv.load_dotenv()

# 创建reflex示例并添加路由页面
app = rx.App()
app.add_page(jimeng.index, route='/', title="智能提示词图片生成器")
app.add_page(gpt4o.index, route='/gpt4oimage', title="智能提示词图片生成器")
app.add_page(cover.index, route='/cover', title="在线制作文章封面图")
