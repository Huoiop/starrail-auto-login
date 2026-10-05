# 云崩铁每日自动化

《崩坏：星穹铁道》云游戏网页版（[sr.mihoyo.com/cloud](https://sr.mihoyo.com/cloud/#/)）的每日自动登录脚本。基于 Selenium + OpenCV 模板匹配，在 Linux 上无头运行，可选推送通知与上报。

> ⚠️ 仅供个人学习与自动化技术研究，使用风险自负，详见文末[免责声明](#免责声明)。

---

## 工作流程

```
启动 Chrome → [第1步] 处理更新弹窗 / 点击"进入游戏"
            → [第2步] 处理排队 / 星云币 / loading
            → [第3步] 处理用户协议 / 等待 completed
            → 挂机 20s → 截图 → 上报（可选）→ 通知（可选）
```

任一步超时都会触发截图 + 通知；第3步超时会启动 60s 随机点击保险机制。

## 原理简述

云游戏画面是 `<canvas>`，Selenium 无法点内部元素，所以整个方案是：

```
Python ──WebDriver──▶ chromedriver ──CDP──▶ Chrome ──渲染──▶ Xvfb :99
   │
   ├─ 用 OpenCV 在截图上做模板匹配，得到按钮坐标
   └─ 用 python-xlib 在 X11 层模拟真实鼠标点击
```

登录态靠 Chrome 的 `--user-data-dir=chrome_profile` 持久化，**手动登录一次之后就一直免登录**，直到 cookie 过期。

---

## 部署

### 1. 系统依赖（Debian / Ubuntu）

```bash
sudo apt-get update
sudo apt-get install -y \
    chromium-browser chromium-chromedriver \
    xvfb x11vnc \
    fonts-liberation libnss3 libatk-bridge2.0-0 libgtk-3-0 \
    libgbm1 libasound2
```

> 用官方 Chrome 也行，但 **chromedriver 主版本号必须与 Chrome 一致**，否则启动报错。

### 2. Python 依赖

```bash
pip install -r requirements.txt
```

### 3. 准备模板图片

在 `capture/` 下放 6 张 PNG（**自己截取**，1920×1080 分辨率下，只裁剪按钮本身）：

| 文件            | 内容                    |
| --------------- | ----------------------- |
| `updates.png`   | "我知道了" 更新弹窗按钮 |
| `start.png`     | "进入游戏" 按钮         |
| `coin.png`      | 星云币 / 排队提示       |
| `loading.png`   | 加载中标志              |
| `accept.png`    | 用户协议"同意"按钮      |
| `completed.png` | 进入主界面的独有元素    |

### 4. 配置 `.env`

```bash
cp .env.example .env
```

最小配置：

```env
CHROMEDRIVER_PATH=/usr/bin/chromedriver
CHROME_USER_DATA_DIR=chrome_profile
Y_OFFSET=139
```

完整配置见 [`.env.example`](.env.example)，所有字段都有注释。

### 5. 首次登录（关键）

脚本不会帮你输密码，它只复用 `chrome_profile/` 里已有的登录态。

1. 临时把 `.env` 里 `TIMEOUT_STEP1` 改大，比如 `600`
2. 启动脚本：`python3.11 main.py`
3. 用 VNC 客户端连接 `你的IP:5900`（无密码），手动扫码登录
4. 登录成功后关闭 VNC，把 `TIMEOUT_STEP1` 改回 `30`
5. 之后每次运行都是自动的

> cookie 过期后脚本会在第 1 步超时，重做一次上面流程即可。

### 6. 运行

```bash
python3.11 main.py
```

定时执行示例：

```bash
# crontab -e，每天 8:00 跑一次
0 8 * * * cd /path/to/project && python3.11 main.py >> run.log 2>&1
```

---

## ⚠️ Y 轴补偿（易踩坑）

脚本用 OpenCV 在 **Selenium 截图**上找按钮，得到的是「网页视口坐标」；但点击是通过 X11 在**整个屏幕**上做的，两者差了一个 **Chrome 顶栏高度**（标签栏 + 地址栏 + 书签栏）。

所以 `.env` 里的 `Y_OFFSET` 就是用来补这个差的：

```
截图坐标 (x, y)  →  实际屏幕坐标 (x, y + Y_OFFSET)
```

**默认值 139 只对默认无书签栏、100% 缩放、Chrome 窗口从屏幕 (0, 0) 开始的情况有效。**

以下情况会导致点击整体偏移，需要重新测量：

- 开启了书签栏
- 改了系统字体 / DPI 缩放
- 换了不同版本或皮肤的 Chrome
- 窗口不是从屏幕 `(0, 0)` 开始

**怎么测量**：跑一次脚本，看 `debug/` 里最新的截图，找一个位置明显的按钮，和 VNC 里实际点到的位置对比。

- 点偏**上**了 → `Y_OFFSET` 调**大**
- 点偏**下**了 → `Y_OFFSET` 调**小**

改完 `.env` 重启即可，不用改代码。

---

## Docker 部署

```yaml
# docker-compose.yml
services:
  sr-cloud:
    build: .
    restart: unless-stopped
    env_file: .env
    ports:
      - "5900:5900" # VNC，首次登录用
    volumes:
      - ./chrome_profile:/app/chrome_profile # 持久化登录态
      - ./capture:/app/capture
      - ./debug:/app/debug
```

`Dockerfile` 要点：锁定 Chrome 与 chromedriver 相同版本（避免漂移），安装 `xvfb x11vnc` 及 Chrome 运行库。完整示例见仓库根目录 `Dockerfile`。

首次登录流程同上：先 `docker compose up -d`，VNC 连 `IP:5900` 手动登录，然后 `docker compose restart`。

---

## 可选模块

两个模块**互相独立、各自开关**，不配置就跳过，完全不影响主流程。

### AstrBot 通知

三字段都填则自动启用：

```env
ASTRBOT_BASE_URL=https://your-astrbot
ASTRBOT_API_KEY=abk_xxx
ASTRBOT_UMO=default_xxx:FriendMessage:xxx
```

### Webhook 上报

填了 URL 则自动启用，登录成功后 POST：

```env
WEBHOOK_URL=https://your-api/report
WEBHOOK_AUTH_KEY=your_key
```

Payload：

```json
{ "auth_key": "your_key", "timestamp": 1700000000 }
```

可用 `WEBHOOK_EXTRA_JSON={"game":"sr"}` 附加字段。

---

## 常见问题

**Q：报 `This version of ChromeDriver only supports Chrome version XXX`**
chromedriver 与 Chrome 主版本号不一致，换成同版本即可，或在 `.env` 里把 `CHROMEDRIVER_PATH` 留空让 Selenium 自动下载。

**Q：卡在第 1 步不动**
先看 `debug/` 里最新的截图，判断当前停在哪个页面：

- 是登录页 → cookie 过期，重做[首次登录](#5-首次登录关键)
- 是游戏页 → 模板不匹配，重截 `start.png` 或调低 `THRESHOLD`

**Q：点击位置不对，总是偏上或偏下**
`Y_OFFSET` 配置不对，见 [Y 轴补偿](#️-y-轴补偿易踩坑)。

**Q：`user data directory is already in use`**
`pkill -9 chrome && rm -f chrome_profile/Singleton*`。

**Q：能跑多个账号吗？**
可以，但每个账号要独立的 `CHROME_USER_DATA_DIR`。

**Q：为什么不用 Selenium 直接点击？**
canvas 里没有 DOM 元素，只能在 X11 层模拟鼠标。

---

## 免责声明

本项目仅供个人学习和自动化技术研究。自动化登录云游戏**可能违反米哈游用户协议**，请自行评估风险。本项目不含任何绕过登录、破解账号的功能，仅代替你执行点击动作。作者不对账号封禁、数据丢失等后果负责。

## License

[MIT](LICENSE)
