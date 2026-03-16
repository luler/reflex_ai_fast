# Mondo风格海报生成器 - 增强版
import base64
import os

import aiohttp
import reflex as rx

# 30+设计风格（英文描述，用于提示词生成）
ARTIST_STYLES = {
    "auto": "let AI choose best style",
    # === 海报艺术家 (20) ===
    "saul-bass": "Saul Bass minimalist geometric abstraction, 2-3 colors, visual metaphor",
    "olly-moss": "Olly Moss ultra-minimal negative space, clever hidden imagery, 2 colors",
    "tyler-stout": "Tyler Stout maximalist character collage, intricate line work, organized chaos",
    "martin-ansin": "Martin Ansin Art Deco elegance, refined vintage palette, sophisticated",
    "toulouse-lautrec": "Toulouse-Lautrec flat color blocks, Japanese influence, bold silhouettes",
    "alphonse-mucha": "Alphonse Mucha Art Nouveau flowing curves, ornate floral, decorative borders",
    "jules-cheret": "Jules Chéret Belle Époque bright joyful colors, dynamic feminine figures",
    "cassandre": "Cassandre modernist geometry, Cubist planes, dramatic perspective, Art Deco",
    "milton-glaser": "Milton Glaser psychedelic pop art, innovative typography, vibrant colors",
    "drew-struzan": "Drew Struzan painted realism, epic cinematic, warm nostalgic glow",
    "kilian-eng": "Kilian Eng geometric futurism, precise technical lines, cool sci-fi palette",
    "laurent-durieux": "Laurent Durieux visual puns, hidden imagery, mysterious atmospheric",
    "jay-ryan": "Jay Ryan folksy handmade, single focal image, warm textured simple",
    "dan-mccarthy": "Dan McCarthy ultra-flat geometric abstraction, 2-3 solid colors, no gradients",
    "jock": "Jock gritty expressive brushwork, dynamic action, high contrast, raw energy",
    "shepard-fairey": "Shepard Fairey propaganda style, red black cream, halftone, political",
    "steinlen": "Steinlen social realist, expressive lines, cat motifs, high contrast",
    "josef-muller-brockmann": "Josef Müller-Brockmann Swiss grid, Helvetica, mathematical precision",
    "paul-rand": "Paul Rand playful geometry, clever visual puns, witty intelligent",
    "paula-scher": "Paula Scher typographic maximalism, layered text, vibrant expressive letters",
    # === 书籍封面设计师 (6) ===
    "chip-kidd": "Chip Kidd conceptual book cover, single symbolic object, bold typography, photographic metaphor, witty visual pun, Random House literary aesthetic",
    "peter-mendelsund": "Peter Mendelsund abstract literary cover, deconstructed typography, minimal symbolic elements, intellectual negative space, Knopf literary elegance",
    "coralie-bickford-smith": "Coralie Bickford-Smith Penguin Clothbound Classics, repeating decorative patterns, Art Nouveau foil stamping, jewel-tone palette, ornamental borders, fabric texture",
    "david-pearson": "David Pearson Penguin Great Ideas, bold typographic-only cover, text as visual element, minimal color, intellectual and clean, type-driven design",
    "wang-zhi-hong": "Wang Zhi-Hong East Asian book design, restrained elegant typography, confident negative space, subtle texture, balanced asymmetry, literary sophistication",
    "jan-tschichold": "Jan Tschichold modernist Penguin typography, Swiss precision grid, clean serif fonts, understated elegance, timeless typographic hierarchy",
    # === 专辑封面设计师 (3) ===
    "reid-miles": "Reid Miles Blue Note Records, bold asymmetric typography, high contrast black and single accent color, jazz photography silhouette, dramatic negative space, vintage vinyl",
    "david-stone-martin": "David Stone Martin Verve Records, single gestural ink brushstroke, minimalist line drawing on cream, fluid calligraphic lines, maximum negative space, improvisational energy",
    "peter-saville": "Peter Saville Factory Records extreme minimalism, single abstract form in vast empty space, monochromatic, no text on cover, conceptual and mysterious, intellectual restraint",
    # === 社交媒体/中国美学风格 (4) ===
    "wenyi": "literary artistic style, soft muted tones, generous white space, delicate serif typography, watercolor texture, poetic atmosphere, refined and contemplative, editorial book review aesthetic",
    "guochao": "Chinese contemporary trend, traditional Chinese motifs reimagined modern, bold red and gold palette, ink wash meets graphic design, cultural symbols with street art energy",
    "rixi": "Japanese aesthetic, warm film grain, soft natural light, pastel muted palette, clean minimal layout, hand-drawn accents, cozy atmosphere, wabi-sabi imperfection, zakka lifestyle",
    "hanxi": "Korean aesthetic, clean bright pastel, soft gradient backgrounds, modern sans-serif typography, dreamy ethereal quality, sophisticated minimal, Instagram-worthy composition",
    # === 通用风格 ===
    "minimal": "minimalist, centered single focal point, 2-3 color palette, clean simple",
    "atmospheric": "single strong focal element with atmospheric background, 3-4 colors",
    "negative-space": "figure-ground inversion, negative space reveals hidden element, 2 colors"
}

# 设计类型
DESIGN_TYPES = {
    "movie": "电影海报",
    "book": "书籍封面",
    "album": "专辑封面",
    "event": "活动海报"
}

# 艺术风格（中文显示名 -> 英文key）
STYLE_DISPLAY_NAMES = {
    "自动选择": "auto",
    # === 海报艺术家 (20) ===
    "Saul Bass 极简几何": "saul-bass",
    "Olly Moss 负空间": "olly-moss",
    "Tyler Stout 极繁拼贴": "tyler-stout",
    "Martin Ansin 装饰艺术": "martin-ansin",
    "Toulouse-Lautrec 扁平色块": "toulouse-lautrec",
    "Alphonse Mucha 新艺术": "alphonse-mucha",
    "Jules Chéret 美好时代": "jules-cheret",
    "Cassandre 现代几何": "cassandre",
    "Milton Glaser 波普艺术": "milton-glaser",
    "Drew Struzan 史诗电影": "drew-struzan",
    "Kilian Eng 未来主义": "kilian-eng",
    "Laurent Durieux 视觉双关": "laurent-durieux",
    "Jay Ryan 民谣手工": "jay-ryan",
    "Dan McCarthy 扁平抽象": "dan-mccarthy",
    "Jock 粗犷动态": "jock",
    "Shepard Fairey 宣传风格": "shepard-fairey",
    "Steinlen 现实主义": "steinlen",
    "Josef Müller-Brockmann 瑞士网格": "josef-muller-brockmann",
    "Paul Rand 俏皮几何": "paul-rand",
    "Paula Scher 字体极繁": "paula-scher",
    # === 书籍封面设计师 (6) ===
    "Chip Kidd 概念书封": "chip-kidd",
    "Peter Mendelsund 抽象文学": "peter-mendelsund",
    "Coralie Bickford-Smith 装饰图案": "coralie-bickford-smith",
    "David Pearson 纯字体": "david-pearson",
    "王志弘 东方设计": "wang-zhi-hong",
    "Jan Tschichold 现代字体": "jan-tschichold",
    # === 专辑封面设计师 (3) ===
    "Reid Miles Blue Note": "reid-miles",
    "David Stone Martin 单一笔触": "david-stone-martin",
    "Peter Saville 极简抽象": "peter-saville",
    # === 社交媒体/中国美学风格 (4) ===
    "文艺风": "wenyi",
    "国潮风": "guochao",
    "日系": "rixi",
    "韩系": "hanxi",
    # === 通用风格 ===
    "极简主义": "minimal",
    "氛围感": "atmospheric",
    "负空间": "negative-space",
}

# 宽高比选项（保留数字）
ASPECT_RATIOS = {
    "9:16": "9:16 竖版 手机/社媒",
    "16:9": "16:9 横版 视频/宽屏",
    "21:9": "21:9 超宽 全景/横幅",
    "3:4": "3:4 竖版 经典",
    "4:3": "4:3 横版 标准",
    "1:1": "1:1 方形 专辑封面",
}


class MondoState(rx.State):
    """Mondo海报生成器状态"""

    # 提示词
    prompt = ""

    # 生成的图片
    image_urls = []

    # 状态
    processing = False

    # 设计类型
    design_type: str = "movie"

    # 艺术风格
    artist_style: str = "auto"

    # 宽高比
    aspect_ratio: str = "9:16"

    # 颜色偏好
    color_hint: str = ""

    # 增强后的提示词预览
    enhanced_prompt: str = ""

    # 提示词生成中
    enhancing: bool = False

    @rx.var(cache=True)
    def design_type_display(self) -> str:
        """获取设计类型的中文显示名"""
        return DESIGN_TYPES.get(self.design_type, self.design_type)

    @rx.var(cache=True)
    def artist_style_display(self) -> str:
        """获取艺术风格的中文显示名"""
        for display_name, key in STYLE_DISPLAY_NAMES.items():
            if key == self.artist_style:
                return display_name
        return "自动选择"

    @rx.var(cache=True)
    def aspect_ratio_display(self) -> str:
        """获取宽高比的中文显示名"""
        return ASPECT_RATIOS.get(self.aspect_ratio, self.aspect_ratio)

    def set_prompt(self, prompt: str):
        self.prompt = prompt
        # 修改主题后清除旧的增强提示词
        self.enhanced_prompt = ""

    def set_design_type(self, display_name: str):
        """设置设计类型"""
        if display_name in DESIGN_TYPES:
            self.design_type = display_name
        else:
            for key, name in DESIGN_TYPES.items():
                if name == display_name:
                    self.design_type = key
                    break

    def set_artist_style(self, display_name: str):
        """设置艺术家风格"""
        if display_name in STYLE_DISPLAY_NAMES.values():
            self.artist_style = display_name
        elif display_name in STYLE_DISPLAY_NAMES:
            self.artist_style = STYLE_DISPLAY_NAMES[display_name]

    def set_aspect_ratio(self, display_name: str):
        """设置宽高比"""
        if display_name in ASPECT_RATIOS:
            self.aspect_ratio = display_name
        else:
            for key, name in ASPECT_RATIOS.items():
                if name == display_name or display_name.startswith(key):
                    self.aspect_ratio = key
                    break

    def set_color_hint(self, colors: str):
        """设置颜色偏好"""
        self.color_hint = colors

    def get_format_description(self, aspect_ratio: str) -> str:
        """获取宽高比描述"""
        descriptions = {
            "9:16": "vertical 9:16 portrait format",
            "16:9": "horizontal 16:9 landscape format, wide cinematic composition",
            "21:9": "ultra-wide 21:9 panoramic banner format, horizontal landscape",
            "3:4": "vertical 3:4 portrait format",
            "4:3": "horizontal 4:3 landscape format",
            "1:1": "square 1:1 format",
        }
        return descriptions.get(aspect_ratio, f"{aspect_ratio} format")

    def generate_prompt_from_template(self, subject: str) -> str:
        """使用模板生成Mondo风格提示词"""
        base_elements = "Mondo poster style, screen print aesthetic, limited edition poster art"
        format_desc = self.get_format_description(self.aspect_ratio)
        style_desc = ARTIST_STYLES.get(self.artist_style, ARTIST_STYLES['minimal'])

        if self.design_type == "movie":
            prompt = f"{subject} in {base_elements}, {style_desc}, {format_desc}, clean focused composition, vintage poster aesthetic"
        elif self.design_type == "book":
            prompt = f"{subject} book cover in {base_elements}, {style_desc}, {format_desc}, clean typography, literary design"
        elif self.design_type == "album":
            prompt = f"{subject} album cover in {base_elements}, {style_desc}, square 1:1 format, vintage vinyl aesthetic"
        elif self.design_type == "event":
            prompt = f"{subject} event poster in {base_elements}, {style_desc}, {format_desc}, bold memorable design"
        else:
            prompt = f"{subject} in {base_elements}, {style_desc}, vintage print aesthetic"

        if self.color_hint:
            prompt += f", color palette: {self.color_hint}"

        return prompt

    async def ai_enhance_prompt(self, original_subject: str) -> str | None:
        """使用AI增强提示词"""
        api_key = os.getenv('MONDO_OPENAI_API_KEY')
        if not api_key:
            return None

        user_prefs = f"Style: {self.artist_style}, Colors: {self.color_hint}" if self.color_hint else f"Style: {self.artist_style}"

        enhancement_request = f"""Enhance this Mondo poster prompt while STRICTLY respecting the user's original intent:

Original Subject: {original_subject}
Design Type: {self.design_type}
User Preferences: {user_prefs if user_prefs else "None specified - AI can suggest"}

Create an optimized Mondo-style prompt that:
1. KEEPS the core idea from user's original subject
2. Adds ONE perfect symbolic visual element (not multiple)
3. Suggests 2-3 complementary colors (user can override)
4. Uses negative space or visual puns when possible
5. Maintains Mondo screen print aesthetic
6. Stays clean and minimal (not cluttered)

Return ONLY the enhanced prompt text, no explanations."""

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                        os.getenv('MONDO_OPENAI_BASE_URL') + '/chat/completions',
                        headers={
                            'Content-Type': 'application/json',
                            'Authorization': f'Bearer {api_key}'
                        },
                        json={
                            'model': os.getenv('MONDO_TEXT_MODEL'),
                            'messages': [{'role': 'user', 'content': enhancement_request}],
                        },
                        timeout=aiohttp.ClientTimeout(total=120)
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        if 'choices' in result and len(result['choices']) > 0:
                            message = result['choices'][0]['message']
                            # 优先使用content，如果为空则使用reasoning_content（推理模型）
                            content = message.get('content') or message.get('reasoning_content')
                            if content:
                                enhanced = content.strip()
                                return enhanced
        except Exception as e:
            import traceback
            print(f"[AI增强] 异常: {str(e)}")
            traceback.print_exc()
        return None

    @rx.event(background=True)
    async def enhance_current_prompt(self):
        """增强当前提示词"""
        if not self.prompt:
            yield rx.window_alert("请先输入主题描述！")
            return

        async with self:
            self.enhancing = True

        try:
            enhanced = await self.ai_enhance_prompt(self.prompt)
            if enhanced:
                format_desc = self.get_format_description(self.aspect_ratio)
                async with self:
                    self.enhanced_prompt = enhanced + f", Mondo poster style, screen print aesthetic, {format_desc}"
            else:
                async with self:
                    self.enhanced_prompt = self.generate_prompt_from_template(self.prompt)
        finally:
            async with self:
                self.enhancing = False

    @rx.event(background=True)
    async def get_image(self):
        """调用大模型生成图片"""
        if self.prompt == "":
            yield rx.window_alert("主题描述不能为空！")
            return

        async with self:
            self.processing = True
            self.image_urls = []

        try:
            # 构建最终提示词
            if self.enhanced_prompt:
                final_prompt = self.enhanced_prompt
            else:
                final_prompt = self.generate_prompt_from_template(self.prompt)

            image_model = os.getenv('MONDO_IMAGE_MODEL')

            # 解析宽高比为尺寸
            ratio_parts = self.aspect_ratio.split(':')
            if len(ratio_parts) == 2:
                base_size = 1024
                w_ratio, h_ratio = int(ratio_parts[0]), int(ratio_parts[1])
                if w_ratio > h_ratio:
                    size = f"{base_size}x{int(base_size * h_ratio / w_ratio)}"
                elif h_ratio > w_ratio:
                    size = f"{int(base_size * w_ratio / h_ratio)}x{base_size}"
                else:
                    size = f"{base_size}x{base_size}"
            else:
                size = "1024x1024"

            param = {
                'model': image_model,
                'prompt': final_prompt,
                'size': size,
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                        os.getenv('MONDO_OPENAI_BASE_URL') + '/images/generations',
                        json=param,
                        headers={
                            'Content-Type': 'application/json',
                            'Authorization': 'Bearer ' + os.getenv('MONDO_OPENAI_API_KEY')
                        }
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        async with self:
                            self.image_urls = [data['data'][0]['url']]
                    else:
                        error_text = await response.text()
                        print(f"[图片生成] 状态码: {response.status}, 返回内容: {error_text}")
                        yield rx.window_alert(f"图片生成失败！状态码: {response.status}, 原因: {error_text}")
        except Exception as e:
            print(f"[图片生成] 异常: {str(e)}")
            yield rx.window_alert("图片生成失败！异常原因：" + str(e))
        finally:
            async with self:
                self.processing = False

    @rx.event
    async def download_image(self, index_num: int):
        """下载指定URL的图片"""
        image_url = self.image_urls[index_num]

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url) as response:
                    if response.status == 200:
                        image_data = await response.read()
                        b64_data = base64.b64encode(image_data).decode('utf-8')
                        return rx.call_script(f"""
                            (function() {{
                                const a = document.createElement('a');
                                a.href = 'data:image/png;base64,{b64_data}';
                                a.download = 'mondo_poster.png';
                                document.body.appendChild(a);
                                a.click();
                                document.body.removeChild(a);
                            }})();
                        """)
                    else:
                        return rx.window_alert(f"下载失败：HTTP {response.status}")
        except Exception as e:
            return rx.window_alert(f"下载失败：{str(e)}")


def image_modal(image_url):
    """图片弹窗预览组件"""
    return rx.dialog.root(
        rx.dialog.trigger(
            rx.image(
                src=image_url,
                width=["20em", "25em"],
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
                    "Mondo 风格海报生成器",
                    font_size=["1.2em", "1.5em"],
                    text_align="center",
                    width="100%"
                ),
                rx.text(
                    "支持30+艺术家风格 · AI智能增强提示词 · 多种设计类型",
                    text_align="center",
                    color="gray",
                    font_size="0.9em",
                ),

                # 主题输入
                rx.text_area(
                    value=MondoState.prompt,
                    placeholder="输入主题描述（如：银翼杀手 赛博朋克电影）",
                    on_change=MondoState.set_prompt,
                    width="100%",
                    rows='3',
                    resize='vertical',
                ),

                # 设计选项行 - 移动端垂直堆叠，桌面端水平排列
                rx.box(
                    rx.flex(
                        rx.box(
                            rx.text("设计类型", font_weight="bold", font_size="0.85em"),
                            rx.select(
                                list(DESIGN_TYPES.values()),
                                value=MondoState.design_type_display,
                                on_change=MondoState.set_design_type,
                                placeholder="选择类型",
                                width="100%",
                            ),
                            class_name="mondo-param-item",
                        ),
                        rx.box(
                            rx.text("艺术风格", font_weight="bold", font_size="0.85em"),
                            rx.select(
                                list(STYLE_DISPLAY_NAMES.keys()),
                                value=MondoState.artist_style_display,
                                on_change=MondoState.set_artist_style,
                                placeholder="选择风格",
                                width="100%",
                            ),
                            class_name="mondo-param-item",
                        ),
                        rx.box(
                            rx.text("宽高比", font_weight="bold", font_size="0.85em"),
                            rx.select(
                                list(ASPECT_RATIOS.values()),
                                value=MondoState.aspect_ratio_display,
                                on_change=MondoState.set_aspect_ratio,
                                placeholder="选择比例",
                                width="100%",
                            ),
                            class_name="mondo-param-item",
                        ),
                        spacing="4",
                        direction="column",
                        class_name="mondo-params-flex",
                    ),
                    width="100%",
                ),

                # 响应式样式：桌面端水平排列，每个项目等宽填满
                rx.html(
                    "<style>.mondo-params-flex { } .mondo-param-item { } @media (min-width: 48em) { .mondo-params-flex { flex-direction: row !important; } .mondo-param-item { flex: 1; } }</style>"),

                # 颜色偏好
                rx.input(
                    value=MondoState.color_hint,
                    placeholder="颜色偏好（可选，如：橙色、青色、黑色）",
                    on_change=MondoState.set_color_hint,
                    width="100%",
                ),

                # 提示词预览按钮
                rx.button(
                    "生成提示词预览",
                    variant="soft",
                    size="1",
                    on_click=MondoState.enhance_current_prompt,
                    loading=MondoState.enhancing,
                    width="100%",
                ),

                # 增强后的提示词预览
                rx.cond(
                    MondoState.enhanced_prompt != "",
                    rx.box(
                        rx.text("增强后的提示词：", font_weight="bold", font_size="0.85em"),
                        rx.text(
                            MondoState.enhanced_prompt,
                            font_size="0.8em",
                            color="gray",
                            overflow_wrap="break-word",
                        ),
                        bg="gray.100",
                        padding="0.5em 1em",
                        border_radius="0.5em",
                        width="100%",
                        overflow_x="auto",
                    ),
                ),

                # 生成按钮
                rx.button(
                    "生成海报",
                    on_click=MondoState.get_image,
                    width="100%",
                    loading=MondoState.processing,
                    size="3",
                    color_scheme="blue",
                ),

                # 生成的图片
                rx.cond(
                    MondoState.image_urls.length() > 0,
                    rx.flex(
                        rx.foreach(
                            MondoState.image_urls,
                            lambda url, index_num: rx.vstack(
                                image_modal(url),
                                rx.button(
                                    "下载海报",
                                    width="100%",
                                    cursor="pointer",
                                    on_click=MondoState.download_image(index_num)
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
                spacing="4",
                padding_y="1em",
                width="100%",
                max_width="28.5em",
            ),
            width="100%",
        ),

        rx.spacer(),

        # 样例图片展示
        rx.box(
            rx.divider(margin_y="1em"),
            rx.heading(
                "效果示例",
                font_size="lg",
                margin_bottom="1em",
                text_align="center",
                width="100%",
            ),
            rx.flex(
                rx.foreach(
                    [
                        "/images/mondo/1.png",
                        "/images/mondo/2.png",
                        "/images/mondo/3.png",
                        "/images/mondo/4.png",
                        "/images/mondo/5.png",
                        "/images/mondo/6.png",
                        "/images/mondo/7.png",
                        "/images/mondo/8.png",
                        "/images/mondo/9.png",
                        "/images/mondo/10.png",
                        "/images/mondo/11.png",
                        "/images/mondo/12.png",
                    ],
                    lambda url: image_modal(url),
                ),
                wrap="wrap",
                justify="center",
                gap="2em",
            ),
            align="center",
            width="100%",
        ),

        # 风格说明
        rx.box(
            rx.divider(margin_y="1em"),
            rx.flex(
                rx.vstack(
                    rx.heading(
                        "支持的艺术家风格",
                        font_size="lg",
                        margin_bottom="1em",
                        text_align="center",
                        width="100%",
                    ),
                    rx.foreach(
                        [
                            ("经典海报",
                             "Saul Bass 极简几何, Toulouse-Lautrec 扁平色块, Alphonse Mucha 新艺术, Cassandre 现代几何"),
                            ("现代大师",
                             "Olly Moss 负空间, Tyler Stout 极繁拼贴, Drew Struzan 史诗电影, Milton Glaser 波普艺术"),
                            ("当代先锋",
                             "Kilian Eng 未来主义, Dan McCarthy 扁平抽象, Jock 粗犷动态, Shepard Fairey 宣传风格"),
                            ("中国美学", "文艺风 柔和诗意, 国潮风 传统现代, 日系 暖色极简, 韩系 明亮淡彩"),
                        ],
                        lambda item: rx.hstack(
                            rx.text(item[0], font_weight="bold", color="blue", flex_shrink="0"),
                            rx.text(item[1], font_size="0.8em", color="gray"),
                            spacing="2",
                            align="start",
                            flex_wrap="wrap",
                        ),
                    ),
                    spacing="2",
                ),
                justify="center",
                width="100%",
            ),
            width="100%",
            padding_x="2em",
        ),

        width="100%",
        min_height="100vh",
        padding_y="2em",
        padding_x=["2em", "2em"],
    )
