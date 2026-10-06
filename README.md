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
sudo apt-get install -y xvfb x11vnc fonts-liberation libnss3 libatk-bridge2.0-0t64 libgtk-3-0t64 libgbm1 libasound2t64
```

> Ubuntu 24.04+ 上 `chromium-browser` 已经通过 apt 转成 snap 分发，服务器环境通常装不上。**推荐直接安装 Google Chrome**（见第 2 步），比折腾 Chromium 省事。

### 2. 安装 Google Chrome

```bash
wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo dpkg -i google-chrome-stable_current_amd64.deb
sudo apt-get -f install -y
```

验证：

```bash
google-chrome --version
# 例：Google Chrome 154.0.8037.97
```

### 3. 安装 chromedriver

chromedriver 的主版本号**必须与 Chrome 一致**（小版本可以不同），否则 Selenium 启动时会报 `session not created`。

**3.1 记录 Chrome 主版本号**

```bash
google-chrome --version
```

输出中的第一段数字即主版本号，例如 `154.0.8037.97` → 主版本是 **154**。

**3.2 从 npmmirror 下载对应版本**

打开镜像目录，找到与你的主版本号匹配的版本号：

```
https://registry.npmmirror.com/binary.html?path=chrome-for-testing/
```

假设你选中的是 `154.0.8037.57`，下载并安装：

```bash
wget https://cdn.npmmirror.com/binaries/chrome-for-testing/154.0.8037.57/linux64/chromedriver-linux64.zip
unzip chromedriver-linux64.zip
sudo mv chromedriver-linux64/chromedriver /usr/local/bin/chromedriver
sudo chmod +x /usr/local/bin/chromedriver
```

> 目录里不一定有和 Chrome 完全相同的完整版本号，选**主版本号相同**的任意一个即可，比如 Chrome 是 `154.0.8037.97`，chromedriver 用 `154.0.8037.57` 也能正常工作。

**3.3 清理可能冲突的旧驱动**

如果之前通过 apt 装过 `chromium-chromedriver`，`/usr/bin/chromedriver` 会和新的冲突，删掉它：

```bash
sudo rm -f /usr/bin/chromedriver
```

**3.4 验证**

```bash
which chromedriver
chromedriver --version
# 例：ChromeDriver 154.0.8037.57
```

### 4. Python 依赖

```bash
pip install -r requirements.txt
```

### 5. 准备模板图片

在 `capture/` 下放 6 张 PNG（**项目已准备英文版本的截图**，1920×1080 分辨率下，只裁剪按钮本身）：

| 文件            | 内容                    |
| --------------- | ----------------------- |
| `updates.png`   | "我知道了" 更新弹窗按钮 |
| `start.png`     | "进入游戏" 按钮         |
| `coin.png`      | 星云币 / 排队提示       |
| `loading.png`   | 加载中标志              |
| `accept.png`    | 用户协议"同意"按钮      |
| `completed.png` | 进入主界面的独有元素    |

### 6. 配置 `.env`

```bash
cp .env.example .env
```

最小配置：

```env
CHROMEDRIVER_PATH=/usr/local/bin/chromedriver
CHROME_USER_DATA_DIR=chrome_profile
Y_OFFSET=139
```

完整配置见 [`.env.example`](.env.example)，所有字段都有注释。

### 7. 首次登录（关键）

脚本不会帮你输密码，它只复用 `chrome_profile/` 里已有的登录态。

1. 临时把 `.env` 里 `TIMEOUT_STEP1` 改大，比如 `600`
2. 启动脚本：`python3.11 main.py`
3. 用 VNC 客户端连接 `你的IP:5900`（无密码），手动扫码登录
4. 登录成功后关闭 VNC，把 `TIMEOUT_STEP1` 改回 `30`
5. 之后每次运行都是自动的

> cookie 过期后脚本会在第 1 步超时，重做一次上面流程即可。

### 8. 运行

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

**Q：报 `The path is not a valid file: # ...`**
`.env` 里把注释写到了 `=` 后面。注释必须单独成行，`=` 后面只写值本身。

**Q：卡在第 1 步不动**
先看 `debug/` 里最新的截图，判断当前停在哪个页面：

- 是登录页 → cookie 过期，重做[首次登录](#7-首次登录关键)
- 是游戏页 → 模板不匹配，重截 `start.png` 或调低 `THRESHOLD`

**Q：点击位置不对，总是偏上或偏下**
`Y_OFFSET` 配置不对，见 [Y 轴补偿](#️-y-轴补偿易踩坑)。

**Q：`user data directory is already in use`**
`pkill -9 chrome && rm -f chrome_profile/Singleton*`。

**Q：能跑多个账号吗？**
目前的架构仅支持单个账号。但理论上通过修改可支持多个账号。

**Q：为什么不用 Selenium 直接点击？**
canvas 里没有 DOM 元素，只能在 X11 层模拟鼠标。

## 注意事项

- 强烈建议在首次登录完成后手动跑一遍整个流程，点掉所有一次性的弹窗等。
  程序已对可能出现的版本更新的免费时长赠送弹窗/隐私协议更新弹窗等做了处理，但仍不能覆盖所有可能出现的弹窗。-
- 建议将Chrome语言切换为English以直接使用项目预设的capture图片，否则需要自行截取并完成图片替换。

---

## 免责声明

本项目仅供个人学习和自动化技术研究。自动化登录云游戏**可能违反米哈游用户协议**，请自行评估风险。本项目不含任何绕过登录、破解账号的功能，仅代替你执行点击动作。作者不对账号封禁、数据丢失等后果负责。

## License

[MIT](LICENSE)

## 开发说明

本项目由 DeepSeek 4.1 Flash 完成开发，Huoiop 已对其可用性完成验证。
