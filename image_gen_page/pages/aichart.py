# 加载配置
import json
import os

import reflex as rx
import requests

aichart_flowise_url = os.getenv('AICHART_FLOWISE_URL')


class AichartState(rx.State):
    """The app state."""

    prompt = ""
    image_urls = []
    processing = False
    complete = False
    uploading = False  # 新增上传状态变量

    upload_img: str = ''
    error_msg: str = ''

    def set_prompt(self, prompt: str):
        self.prompt = prompt

    chart_type = "条形图-展示不同类别之间的数值比较"  # 默认尺寸
    chart_type_options = [
        '条形图-展示不同类别之间的数值比较',
        '柱状图-适合比较分类数据',
        '饼图-展示部分与整体的比例',
        '直方图-展示特定范围内数据点的频率',
        '面积图-展示连续自变量下的数据趋势',
        '鱼骨图-展示问题的原因或结果',
        '流程图-展示过程或系统的步骤和决策点',
        '折线图-展示随时间变化的趋势',
        '思维导图-以层次结构展示信息',
        '网络图-展示实体之间的关系',
        '雷达图-展示多维数据',
        '散点图-展示两个变量之间的关系',
        '树形图-展示层次数据',
        '词云图-通过文本大小变化展示词频或权重',
        '双轴图-结合两种不同图表类型',
    ]

    def set_chart_type(self, chart_type: str):
        self.chart_type = chart_type

    def get_image(self):
        self.image_urls = []
        """调用大模型生成图片."""
        if self.prompt == "":
            return rx.window_alert("提示词不能为空！")

        self.processing, self.complete = True, False
        try:
            yield
            prompt = f"""您是一个统计图表设计生成器，必须根据用户的提示词画出”{self.chart_type.split('-')[0]}“，用户的提示词内容如下：
```
{self.prompt}
```
"""
            print(prompt)
            param = {
                'question': prompt,
            }
            response = requests.post(aichart_flowise_url,
                                     json=param,
                                     headers={
                                         'Content-Type': 'application/json',
                                     })
            if response.status_code == 200:
                data = response.json()
                for item in data['usedTools']:
                    if item['toolOutput'] != '':
                        outputs = json.loads(item['toolOutput'])  # 将JSON字符串转为数组
                        for output in outputs:
                            self.image_urls.append(output['text'])
                if len(self.image_urls) == 0:
                    yield rx.window_alert("图片生成失败！异常原因：" + response.text)
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


def image_modal(image_url):
    return rx.dialog.root(
        rx.dialog.trigger(
            rx.image(
                src=image_url,
                width=["20em", "25em"],
                # height="20em",
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
        rx.center(
            rx.vstack(
                rx.heading(
                    "AI统计图表生成器",
                    font_size=["1.2em", "1.5em"],
                    text_align="center",
                    width="100%"
                ),
                rx.text(AichartState.error_msg, color="red"),
                rx.text_area(
                    value=AichartState.prompt,
                    placeholder="请输入提示词",
                    on_change=AichartState.set_prompt,
                    width=["20em", "25em"],
                    rows='5',
                    resize='vertical',
                ),
                rx.select(
                    AichartState.chart_type_options,
                    value=AichartState.chart_type,
                    on_change=AichartState.set_chart_type,
                    width=["23em", "28.5em"],
                    placeholder="选择统计图类型",
                ),
                rx.button(
                    "生成图片",
                    on_click=AichartState.get_image,
                    width=["23em", "28.5em"],
                    loading=AichartState.processing
                ),

                rx.cond(
                    AichartState.complete,
                    rx.flex(
                        rx.foreach(
                            AichartState.image_urls,
                            lambda url: rx.vstack(
                                image_modal(url),
                                rx.button(
                                    "下载图片",
                                    width=["23em", "28.5em"],
                                    cursor="pointer",
                                    on_click=AichartState.download_image(url)
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
                        ["/images/aichart/1.jpg", "/images/aichart/2.jpg", "/images/aichart/3.jpg",
                         "/images/aichart/4.jpg",
                         "/images/aichart/5.jpg", "/images/aichart/6.jpg", "/images/aichart/7.jpg",
                         "/images/aichart/8.jpg",
                         "/images/aichart/9.jpg", "/images/aichart/10.jpg", ],
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
