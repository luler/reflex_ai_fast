# 基于大模型的图片生成工具

旨在通过便捷的操作页面，迅速接入和体验大模型生图功能

## 大模型配置

新增编辑.env文件，内容如下：

```
OPENAI_BASE_URL=https://xxx/v1
OPENAI_API_KEY=sk-xxx
```

## 安装

### 第一步：打包并导出前端代码

一定要正确指定API_URL，保持与前端代码可以访问的域名、ip一致即可

``` 
API_URL=http://127.0.0.1:8080 reflex export --frontend-only
```

执行上面导出命令之后，会生成frontend.zip文件到目录下

### 第二步：docker-compose一键安装

```
docker-compose up -d
```

## 访问体验

http://127.0.0.1:8080/