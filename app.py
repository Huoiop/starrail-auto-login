#!/usr/bin/env python3.11
"""
云崩铁每日自动化
- 通知（AstrBot）：可选，先发文字，1 秒后发图片
- 上报（Webhook）：可选，成功登录后触发
- X11 点击：内嵌（基于 python-xlib），带 Y_OFFSET 补偿
- 登录模式：LOGIN_ONLY=true 时只打开浏览器，供 VNC 手动登录
- 流程：弹窗/进入游戏 → 排队/星云币 → loading → 用户协议/completed → 挂机
"""

import os
import random
import shutil
import subprocess
import time

import cv2
import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

# python-xlib 在 HOME 缺失时会崩，提前兜底
os.environ.setdefault("HOME", "/root")


# ==================== .env 加载 ====================
def load_env_file(path: str = ".env") -> None:
    """零依赖 .env 解析；已存在的环境变量不会被覆盖。"""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key, val = key.strip(), val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                val = val[1:-1]
            else:
                # 去掉未加引号值的行尾注释（" #" 前需有空格，避免误伤 URL 里的 #）
                hash_pos = val.find(" #")
                if hash_pos != -1:
                    val = val[:hash_pos].rstrip()
            os.environ.setdefault(key, val)


load_env_file()

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(override=False)
except ImportError:
    pass


# ==================== 配置工具 ====================
def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


def _env_bool(key: str, default: bool = False) -> bool:
    v = os.getenv(key)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


# ==================== X / VNC ====================
DISPLAY = _env("DISPLAY", ":99")
os.environ["DISPLAY"] = DISPLAY
VNC_PORT = _env_int("VNC_PORT", 5900)
USE_VNC = _env_bool("USE_VNC", True)
WINDOW_W = _env_int("WINDOW_W", 1920)
WINDOW_H = _env_int("WINDOW_H", 1080)

# ==================== X11 点击 ====================
Y_OFFSET = _env_int("Y_OFFSET", 139)  # Chrome 顶栏高度补偿，见 README

# ==================== 登录模式 ====================
# true   = 只启动浏览器，供 VNC 手动登录，不执行任何自动化
# false  = 正常自动化流程
LOGIN_ONLY = _env_bool("LOGIN_ONLY", False)

# ==================== 模板路径 ====================
TEMPLATE_UPDATES = _env("TEMPLATE_UPDATES", "capture/updates.png")
TEMPLATE_START   = _env("TEMPLATE_START",   "capture/start.png")
TEMPLATE_COIN    = _env("TEMPLATE_COIN",    "capture/coin.png")
TEMPLATE_LOADING = _env("TEMPLATE_LOADING", "capture/loading.png")
TEMPLATE_ACCEPT  = _env("TEMPLATE_ACCEPT",  "capture/accept.png")
TEMPLATE_DONE    = _env("TEMPLATE_DONE",    "capture/completed.png")

# ==================== 匹配 / 调试 ====================
DEBUG_DIR = _env("DEBUG_DIR", "debug")
THRESHOLD = _env_float("THRESHOLD", 0.6)
POLL_INTERVAL = _env_float("POLL_INTERVAL", 0.5)
DEBUG_DRAW_RATIO = _env_float("DEBUG_DRAW_RATIO", 0.7)

# ==================== 流程超时（秒） ====================
TIMEOUT_STEP1 = _env_int("TIMEOUT_STEP1", 30)
QUICK_TIMEOUT = _env_int("QUICK_TIMEOUT", 10)
LOADING_TIMEOUT = _env_int("LOADING_TIMEOUT", 120)
TIMEOUT_COMPLETED = _env_int("TIMEOUT_COMPLETED", 180)
RANDOM_CLICK_DURATION = _env_int("RANDOM_CLICK_DURATION", 60)

# ==================== 挂机 / 点击 ====================
HANG_TIME_MIN = _env_float("HANG_TIME_MIN", 18)
HANG_TIME_MAX = _env_float("HANG_TIME_MAX", 22)
CLICK_CENTER_TIMES = _env_int("CLICK_CENTER_TIMES", 3)
CLICK_CENTER_INTERVAL = _env_float("CLICK_CENTER_INTERVAL", 3.0)
CLICK_WAIT_MIN = _env_float("CLICK_WAIT_MIN", 1.5)
CLICK_WAIT_MAX = _env_float("CLICK_WAIT_MAX", 2.5)

# ==================== 浏览器 ====================
GAME_URL = _env("GAME_URL", "https://sr.mihoyo.com/cloud/#/")
CHROMEDRIVER_PATH = _env("CHROMEDRIVER_PATH", "/usr/local/bin/chromedriver")
CHROME_BINARY = _env("CHROME_BINARY", "")  # 留空则用 Selenium 默认（google-chrome）
CHROME_USER_DATA_DIR = _env("CHROME_USER_DATA_DIR", "chrome_profile")


# ======================================================================
# 可选模块 1：AstrBot 通知
# ======================================================================
ASTRBOT_ENABLED = _env_bool("ASTRBOT_ENABLED", False)
ASTRBOT_BASE_URL = _env("ASTRBOT_BASE_URL", "").rstrip("/")
ASTRBOT_API_KEY = _env("ASTRBOT_API_KEY", "")
ASTRBOT_UMO = _env("ASTRBOT_UMO", "")
ASTRBOT_TIMEOUT = _env_int("ASTRBOT_TIMEOUT", 10)
ASTRBOT_IMAGE_DELAY = _env_float("ASTRBOT_IMAGE_DELAY", 1.0)

if not ASTRBOT_ENABLED and ASTRBOT_BASE_URL and ASTRBOT_API_KEY and ASTRBOT_UMO:
    ASTRBOT_ENABLED = True


def _astrbot_ready() -> bool:
    return bool(ASTRBOT_ENABLED and ASTRBOT_BASE_URL and ASTRBOT_API_KEY and ASTRBOT_UMO)


def astrbot_send_text(text: str) -> None:
    if not _astrbot_ready():
        return
    headers = {
        "Authorization": f"Bearer {ASTRBOT_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"umo": ASTRBOT_UMO, "message": text}
    try:
        resp = requests.post(
            f"{ASTRBOT_BASE_URL}/api/v1/im/message",
            json=payload, headers=headers, timeout=ASTRBOT_TIMEOUT,
        )
        resp.raise_for_status()
        print(f"  [AstrBot 文字已发送] {text}")
    except Exception as e:
        print(f"  [AstrBot 文字失败] {e}")


def astrbot_send_image(image_path: str) -> None:
    if not _astrbot_ready() or not image_path or not os.path.exists(image_path):
        return
    upload_url = f"{ASTRBOT_BASE_URL}/api/v1/file"
    headers = {"Authorization": f"Bearer {ASTRBOT_API_KEY}"}
    try:
        with open(image_path, "rb") as f:
            resp = requests.post(
                upload_url, headers=headers,
                files={"file": f}, timeout=ASTRBOT_TIMEOUT,
            )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "ok":
            raise Exception(f"上传失败: {data}")
        attachment_id = data["data"]["attachment_id"]
    except Exception as e:
        print(f"  [AstrBot 图片上传失败] {e}")
        return

    headers = {
        "Authorization": f"Bearer {ASTRBOT_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "umo": ASTRBOT_UMO,
        "message": [{"type": "image", "attachment_id": attachment_id}],
    }
    try:
        resp = requests.post(
            f"{ASTRBOT_BASE_URL}/api/v1/im/message",
            json=payload, headers=headers, timeout=ASTRBOT_TIMEOUT,
        )
        resp.raise_for_status()
        print(f"  [AstrBot 图片已发送] {image_path}")
    except Exception as e:
        print(f"  [AstrBot 图片失败] {e}")


def notify(text: str, image_path: str | None = None) -> None:
    """统一通知入口：AstrBot 未启用则静默跳过。"""
    if not _astrbot_ready():
        print(f"  [通知跳过] AstrBot 未启用 -> {text}")
        return
    astrbot_send_text(text)
    if image_path:
        time.sleep(ASTRBOT_IMAGE_DELAY)
        astrbot_send_image(image_path)


# ======================================================================
# 可选模块 2：Webhook 上报
# ======================================================================
WEBHOOK_ENABLED = _env_bool("WEBHOOK_ENABLED", False)
WEBHOOK_URL = _env("WEBHOOK_URL", "")
WEBHOOK_AUTH_KEY = _env("WEBHOOK_AUTH_KEY", "")
WEBHOOK_EXTRA_JSON = _env("WEBHOOK_EXTRA_JSON", "")  # 可选：附加字段，JSON 字符串
WEBHOOK_TIMEOUT = _env_int("WEBHOOK_TIMEOUT", 10)

if not WEBHOOK_ENABLED and WEBHOOK_URL:
    WEBHOOK_ENABLED = True


def _webhook_ready() -> bool:
    return bool(WEBHOOK_ENABLED and WEBHOOK_URL)


def report_checkin() -> None:
    """成功登录后上报。未配置则自动跳过。"""
    if not _webhook_ready():
        print("  [上报跳过] Webhook 未启用")
        return

    payload = {
        "auth_key": WEBHOOK_AUTH_KEY,
        "timestamp": int(time.time()),
    }
    if WEBHOOK_EXTRA_JSON:
        try:
            import json
            extra = json.loads(WEBHOOK_EXTRA_JSON)
            if isinstance(extra, dict):
                payload.update(extra)
        except Exception as e:
            print(f"  [上报] WEBHOOK_EXTRA_JSON 解析失败：{e}")

    try:
        resp = requests.post(
            WEBHOOK_URL, json=payload,
            headers={"Content-Type": "application/json"},
            timeout=WEBHOOK_TIMEOUT,
        )
        resp.raise_for_status()
        print(f"  [上报成功] {resp.text}")
    except Exception as e:
        print(f"  [上报失败] {e}")


# ======================================================================
# X11 点击（内嵌，基于 python-xlib）
# ======================================================================
_x_display = None  # 延迟初始化，等 Xvfb 起来后再建连接


def init_x11_display() -> None:
    """在 Xvfb 启动之后调用一次，建立全局 X 连接。"""
    global _x_display
    if _x_display is not None:
        return
    os.environ["DISPLAY"] = DISPLAY
    from Xlib.display import Display
    _x_display = Display()
    print(f"X11 连接已建立：DISPLAY={DISPLAY}, Y_OFFSET={Y_OFFSET}")


def x11_click(x: int, y: int) -> None:
    """把截图坐标 (x, y) 转换成屏幕坐标并点击。"""
    from Xlib import X
    from Xlib.ext.xtest import fake_input

    if _x_display is None:
        raise RuntimeError("X11 未初始化，请先调用 init_x11_display()")

    screen_x, screen_y = x, y + Y_OFFSET
    fake_input(_x_display, X.MotionNotify, x=screen_x, y=screen_y)
    _x_display.sync()
    time.sleep(0.03)
    fake_input(_x_display, X.ButtonPress, detail=1)
    _x_display.sync()
    time.sleep(0.03)
    fake_input(_x_display, X.ButtonRelease, detail=1)
    _x_display.sync()
    print(f"    X11 点击 截图({x},{y}) -> 屏幕({screen_x},{screen_y})")


# ======================================================================
# 启动信息
# ======================================================================
print(f"通知模块（AstrBot）：{'已启用' if _astrbot_ready() else '未启用'}")
print(f"上报模块（Webhook）：{'已启用' if _webhook_ready() else '未启用'}")
if LOGIN_ONLY:
    print("运行模式：LOGIN_ONLY（只启动浏览器，供 VNC 手动登录）")
else:
    print("运行模式：正常自动化")


# ==================== 通用工具 ====================
def today_str() -> str:
    return time.strftime("%Y年%m月%d日")


def save_debug_screenshot(name: str) -> str:
    ts = int(time.time())
    path = f"{DEBUG_DIR}/{name}_{ts}.png"
    driver.save_screenshot(path)
    print(f"验收截图已保存: {path}")
    return path


def cleanup_and_exit(code: int = 0) -> None:
    """统一退出：关闭浏览器、X 连接、Xvfb / x11vnc。"""
    global _x_display
    try:
        driver.quit()
        print("浏览器已关闭。")
    except Exception:
        pass
    if _x_display is not None:
        try:
            _x_display.close()
        except Exception:
            pass
        _x_display = None
        print("X11 连接已关闭。")
    subprocess.run(['pkill', 'x11vnc'], capture_output=True)
    subprocess.run(['pkill', 'Xvfb'], capture_output=True)
    print("已退出。")
    raise SystemExit(code)


# ==================== 强制清理并重启 X 环境 ====================
print("重置 X 环境...")
subprocess.run(['pkill', '-9', 'x11vnc'], capture_output=True)
subprocess.run(['pkill', '-9', 'Xvfb'], capture_output=True)
subprocess.run(['pkill', '-9', 'chrome'], capture_output=True)
subprocess.run(['pkill', '-9', 'chromedriver'], capture_output=True)
time.sleep(1)

X99_LOCK = f"/tmp/.X{DISPLAY.lstrip(':')}-lock"
subprocess.run(['rm', '-f', X99_LOCK], capture_output=True)
time.sleep(0.5)

subprocess.Popen(['Xvfb', DISPLAY, '-ac', '-noreset', '-screen', '0',
                  f'{WINDOW_W}x{WINDOW_H}x24'])
time.sleep(2)
print("Xvfb 已启动。")

init_x11_display()

if USE_VNC:
    print("启动 x11vnc...")
    subprocess.Popen(['x11vnc', '-display', DISPLAY, '-forever', '-nopw', '-q',
                      '-rfbport', str(VNC_PORT), '-noxdamage', '-reopen'],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"  x11vnc 已启动，端口 {VNC_PORT}")

print("再次确认 Chrome 进程已清理...")
subprocess.run(['pkill', '-9', 'chrome'], capture_output=True)
subprocess.run(['pkill', '-9', 'chromedriver'], capture_output=True)
time.sleep(1)


# ==================== 初始化浏览器 ====================
if os.path.exists(DEBUG_DIR):
    shutil.rmtree(DEBUG_DIR)
os.makedirs(DEBUG_DIR, exist_ok=True)

print("初始化浏览器（有头模式）...")
options = Options()
options.add_argument(f"--user-data-dir={CHROME_USER_DATA_DIR}")
options.add_argument('--no-sandbox')
options.add_argument('--disable-dev-shm-usage')
options.add_argument('--disable-gpu')
options.add_argument('--disable-features=VizDisplayCompositor')
options.add_argument('--max_old_space_size=512')
options.add_argument(f'--window-size={WINDOW_W},{WINDOW_H}')
options.add_argument('--window-position=0,0')
if CHROME_BINARY:
    options.binary_location = CHROME_BINARY

driver = webdriver.Chrome(service=Service(CHROMEDRIVER_PATH), options=options)
driver.set_window_size(WINDOW_W, WINDOW_H)
driver.set_window_position(0, 0)
driver.get(GAME_URL)
time.sleep(5)


# ==================== LOGIN_ONLY 模式 ====================
if LOGIN_ONLY:
    print("\n" + "=" * 60)
    print("【LOGIN_ONLY 模式】浏览器已启动，未执行任何自动化。")
    print(f"  请用 VNC 客户端连接 你的IP:{VNC_PORT} 手动登录。")
    print("  登录成功后：")
    print("    1. 确认已进入云游戏首页，可正常启动云游戏")
    print("    2. 回到本终端按 Ctrl+C 退出")
    print("    3. 把 .env 里 LOGIN_ONLY 改回 false，再运行正常流程")
    print("=" * 60 + "\n")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，准备退出...")
        cleanup_and_exit(0)


# ==================== 核心函数 ====================
def take_screenshot(path: str) -> None:
    driver.save_screenshot(path)


def find_template(tpl_path, label, threshold=THRESHOLD, timeout=30, poll=POLL_INTERVAL):
    tpl = cv2.imread(tpl_path)
    if tpl is None:
        return False, 0, 0
    th, tw = tpl.shape[:2]
    start = time.time()
    while time.time() - start < timeout:
        ts = int(time.time() * 1000)
        shot_path = f"{DEBUG_DIR}/{label}_{ts}_screen.png"
        debug_path = f"{DEBUG_DIR}/{label}_{ts}_debug.png"
        take_screenshot(shot_path)
        scr = cv2.imread(shot_path)
        if scr is None:
            time.sleep(poll)
            continue
        res = cv2.matchTemplate(scr, tpl, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        cx = max_loc[0] + tw // 2
        cy = max_loc[1] + th // 2
        if max_val >= threshold * DEBUG_DRAW_RATIO:
            dbg = scr.copy()
            color = (0, 255, 0) if max_val >= threshold else (0, 0, 255)
            cv2.rectangle(dbg, max_loc, (max_loc[0] + tw, max_loc[1] + th), color, 3)
            cv2.circle(dbg, (cx, cy), 10, (0, 0, 255), -1)
            cv2.putText(dbg, f"{label} conf:{max_val:.3f}", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, color, 2)
            cv2.imwrite(debug_path, dbg)
        elapsed = time.time() - start
        if max_val >= threshold:
            print(f"    [{elapsed:.1f}s] conf={max_val:.3f} OK ({cx},{cy})")
            return True, cx, cy
        if int(elapsed) != int(elapsed - poll):
            print(f"    [{elapsed:.0f}s] conf={max_val:.3f} ...")
        time.sleep(poll)
    return False, 0, 0


def click_center(times: int = CLICK_CENTER_TIMES, interval: float = CLICK_CENTER_INTERVAL) -> None:
    cx, cy = WINDOW_W // 2, WINDOW_H // 2
    for i in range(times):
        print(f"    点击中心 ({cx}, {cy}) 第{i + 1}次")
        x11_click(cx, cy)
        if i < times - 1:
            time.sleep(random.uniform(CLICK_WAIT_MIN, CLICK_WAIT_MAX))


def click_randomly(duration: int = RANDOM_CLICK_DURATION) -> None:
    print(f"  保险机制：接下来 {duration} 秒内随机点击屏幕中心...")
    start = time.time()
    while time.time() - start < duration:
        delay = random.randint(3, 8)
        time.sleep(delay)
        x11_click(WINDOW_W // 2, WINDOW_H // 2)
        print(f"    保险点击 屏幕中心 (间隔{delay}s)")


# ==================== 第1步：弹窗 + 进入游戏 ====================
print("\n[第1步] 同时检测更新弹窗和进入游戏按钮...")
start_time = time.time()
found_start = False

while time.time() - start_time < TIMEOUT_STEP1:
    found_update, cx_u, cy_u = find_template(TEMPLATE_UPDATES, "updates", timeout=1, poll=0.5)
    if found_update:
        wait_time = random.uniform(CLICK_WAIT_MIN, CLICK_WAIT_MAX)
        print(f"  检测到‘我知道了’弹窗，{wait_time:.1f}秒后点击 ({cx_u}, {cy_u})")
        time.sleep(wait_time)
        x11_click(cx_u, cy_u)
        time.sleep(random.uniform(CLICK_WAIT_MIN, CLICK_WAIT_MAX))
        continue

    found_start, cx_s, cy_s = find_template(TEMPLATE_START, "start", timeout=1, poll=0.5)
    if found_start:
        wait_time = random.uniform(CLICK_WAIT_MIN, CLICK_WAIT_MAX)
        print(f"  -> {wait_time:.1f}秒后点击进入游戏 ({cx_s}, {cy_s})")
        time.sleep(wait_time)
        x11_click(cx_s, cy_s)
        break

if not found_start:
    img = save_debug_screenshot("timeout_step1")
    notify("【星铁自动登录】在“等待进入游戏按钮”时超时", img)
    cleanup_and_exit(1)


# ==================== 第2步：排队/loading ====================
print("\n[第2步] 等待排队或加载（0.5s/次，10秒无动静则跳过）...")
start_time = time.time()
loading_found = False
skip_to_next = False

while time.time() - start_time < LOADING_TIMEOUT:
    elapsed = time.time() - start_time

    if elapsed >= QUICK_TIMEOUT and not loading_found:
        has_coin, _, _ = find_template(TEMPLATE_COIN, "coin", timeout=1, poll=0.5)
        has_load, _, _ = find_template(TEMPLATE_LOADING, "loading", timeout=1, poll=0.5)
        if not has_coin and not has_load:
            print("  -> 10秒内未检测到排队或加载，视为游戏已启动，直接进入下一步。")
            skip_to_next = True
            break

    found_coin, cx_coin, cy_coin = find_template(TEMPLATE_COIN, "coin", timeout=1, poll=0.5)
    if found_coin:
        wait_time = random.uniform(CLICK_WAIT_MIN, CLICK_WAIT_MAX)
        print(f"  -> {wait_time:.1f}秒后点击星云币 ({cx_coin}, {cy_coin})")
        time.sleep(wait_time)
        x11_click(cx_coin, cy_coin)
        time.sleep(random.uniform(CLICK_WAIT_MIN, CLICK_WAIT_MAX))

    found_load, _, _ = find_template(TEMPLATE_LOADING, "loading", timeout=1, poll=0.5)
    if found_load:
        print("  -> 检测到 loading，游戏加载中...")
        loading_found = True
        break

    if loading_found:
        break

if skip_to_next:
    pass
elif not loading_found and not skip_to_next:
    img = save_debug_screenshot("timeout_step2")
    notify("【星铁自动登录】在“等待排队/加载”时超时", img)
    cleanup_and_exit(1)


# ==================== 第3步：用户协议 + completed ====================
print("\n[第3步] 同时检测用户协议和 completed...")
start_time = time.time()
completed_found = False
accept_handled = False

while time.time() - start_time < TIMEOUT_COMPLETED:
    if not accept_handled:
        found_accept, cx_a, cy_a = find_template(TEMPLATE_ACCEPT, "accept", timeout=1, poll=0.5)
        if found_accept:
            accept_handled = True
            wait_time = random.uniform(CLICK_WAIT_MIN, CLICK_WAIT_MAX)
            print(f"  -> {wait_time:.1f}秒后点击用户协议 ({cx_a}, {cy_a})")
            time.sleep(wait_time)
            x11_click(cx_a, cy_a)
            time.sleep(random.uniform(CLICK_WAIT_MIN, CLICK_WAIT_MAX))
            continue

    found_done, cx_d, cy_d = find_template(TEMPLATE_DONE, "completed", timeout=1, poll=0.5)
    if found_done:
        completed_found = True
        wait_time = random.uniform(CLICK_WAIT_MIN, CLICK_WAIT_MAX)
        print(f"  -> {wait_time:.1f}秒后点击屏幕中心。")
        time.sleep(wait_time)
        click_center(times=CLICK_CENTER_TIMES, interval=CLICK_CENTER_INTERVAL)
        break
    time.sleep(0.5)

if completed_found:
    hang_time = random.uniform(HANG_TIME_MIN, HANG_TIME_MAX)
    print(f"\n  在线 {hang_time:.0f} 秒后关闭...")
    time.sleep(hang_time)
    final_img = save_debug_screenshot("final")
    report_checkin()
    notify(f"【星铁自动登录】{today_str()} · 已完成登录", final_img)
else:
    print("  -> 3分钟未检测到 completed.png，触发保险机制。")
    click_randomly(duration=RANDOM_CLICK_DURATION)
    hang_time = random.uniform(HANG_TIME_MIN, HANG_TIME_MAX)
    print(f"\n  保险操作完成，在线 {hang_time:.0f} 秒后关闭...")
    time.sleep(hang_time)
    final_img = save_debug_screenshot("final_timeout")
    notify("【星铁自动登录】在“等待进入游戏界面”时超时，已触发保险点击", final_img)


# ==================== 收尾 ====================
cleanup_and_exit(0)