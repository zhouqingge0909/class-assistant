#!/usr/bin/env python3
"""班级助手 - 教室接收端 v2（银河麒麟 / Linux 一体机，也支持 Windows 一体机）

只用 Python 自带的库：tkinter 画界面，ctypes 读取"多久没人碰屏幕"；声音在麒麟上用 ffplay，在 Windows 上用系统自带的 winmm。
功能：清新风格弹窗、全屏计时器、随机抽号动画、空闲待机页、自动更新。
配置写在同目录的 config.txt 里；麒麟由 install.sh、Windows 由 install.ps1 安装并设置开机自启。
同一个文件两种系统通用（自动更新也是同一个文件），系统相关的地方都用 WIN 分开写。
"""
import ctypes
import ctypes.util
import hashlib
import json
import os
import queue
import random
import re
import shutil
import subprocess
import sys
import threading
import time
try:
    import tkinter as tk
except ImportError:  # 没有 sudo 密码装不了 python3-tk 时，install.sh 把它解压到 ~/.classcall/tk：带上路径重新启动自己
    _tk = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tk")
    if sys.platform == "win32" or not os.path.isdir(_tk) or os.environ.get("CLASSCALL_TK"):
        raise
    import glob
    _py = os.path.join(_tk, "usr/lib/python%d.%d" % sys.version_info[:2])
    _env = {"CLASSCALL_TK": "1", "PYTHONPATH": _py + ":" + _py + "/lib-dynload",
            "LD_LIBRARY_PATH": ":".join(glob.glob(_tk + "/usr/lib/*-linux-gnu") + [_tk + "/usr/lib", os.environ.get("LD_LIBRARY_PATH", "")]),  # libBLT 在 usr/lib
            "TCL_LIBRARY": _tk + "/usr/share/tcltk/tcl8.6", "TK_LIBRARY": _tk + "/usr/share/tcltk/tk8.6"}
    os.environ.update(_env)
    os.execv(sys.executable, [sys.executable] + sys.argv)
import urllib.parse
import urllib.request
from tkinter import font as tkfont

WIN = sys.platform == "win32"
if not WIN:
    import fcntl
HERE = os.path.dirname(os.path.abspath(__file__))
SELF = os.path.abspath(__file__)
LOG = os.path.join(HERE, "receiver.log")
FONT_DIR = os.path.join(HERE, "fonts") if WIN else os.path.expanduser("~/.local/share/fonts")  # Windows：只给接收端自己用，不用管理员权限
SCREEN_FONTS = {  # 工作台"大屏字体"可选项 -> (字体名, 需要的文件)；中文系统里报的是中文名，所以两个名字都要认
    "misans": (("MiSans",), ["MiSans-Bold.otf", "MiSans-Regular.otf"]),
    "wenkai": (("LXGW WenKai Screen", "霞鹜文楷 屏幕阅读版"), ["LXGWWenKaiScreen.ttf"]),
}

INK, MUTED, LINE, GREEN = "#1d2b40", "#7a8699", "#e6ecf2", "#1fb57a"
THEME = {  # 类型 -> (标题, 渐变起点, 渐变终点)
    "call": ("老师喊话", "#3b9cf5", "#34d399"),
    "homework": ("今日作业", "#1fb57a", "#7dd3a8"),
    "reminder": ("温馨提醒", "#f5a524", "#f2706b"),
    "timer": ("课堂计时", "#f2706b", "#f5a524"),
    "pick": ("随机抽号", "#8b7cf6", "#3b9cf5"),
    "praise": ("表扬", "#f59e0b", "#f97316"),
    "collect": ("作业登记", "#1fb57a", "#3b9cf5"),
    "attend": ("考勤登记", "#3b9cf5", "#8b7cf6"),
    "image": ("题目投屏", "#6366f1", "#3b9cf5"),
    "prep": ("课前准备", "#16a571", "#14b8a6"),
    "file": ("老师发来文件", "#0ea5e9", "#6366f1"),
}
NO_LINE_START = "，。、；：！？）》」』,.;:!?)"


def log(text):
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > 1_000_000:
            os.remove(LOG)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S  ") + text + "\n")
    except OSError:
        pass


def load_config():
    cfg = {}
    with open(os.path.join(HERE, "config.txt"), encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    return cfg


CFG = load_config()
SERVER = CFG["server"].rstrip("/")
CLASS = CFG["class"]
KEY = CFG["key"]
Q_CLASS, Q_KEY = urllib.parse.quote(CLASS), urllib.parse.quote(KEY)
POLL_URL = f"{SERVER}/api/poll?class={Q_CLASS}&key={Q_KEY}&rv=4"  # rv：3 起认得远程卸载指令，4 起会下载老师发来的文件
BOARD_URL = f"{SERVER}/api/board?class={Q_CLASS}&key={Q_KEY}&fv=2"  # fv：认得中文字体名的新版
ACK_URL = f"{SERVER}/api/ack?key={Q_KEY}"
TOOL_URL = f"{SERVER}/api/screen/tool?key={Q_KEY}"  # 屏幕右边「工具」：在大屏上直接开计时、抽号
COLLECT_URL = f"{SERVER}/collect?class={Q_CLASS}&key={Q_KEY}"
ATTEND_URL = f"{SERVER}/attend?class={Q_CLASS}&key={Q_KEY}"
SHOW_FLAG = os.path.join(HERE, "show-board")  # 桌面"班级看板"图标会创建这个文件
FFPLAY = None if WIN else shutil.which("ffplay")
APLAY = None if WIN or FFPLAY else (shutil.which("paplay") or shutil.which("aplay"))  # 没有 ffplay 时用系统自带的放 wav（局域网版的语音和叮咚都是 wav）
audio_lock = threading.Lock()


def fetch_json(url, timeout=8):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# ---------------- 声音 ----------------
def play(path):
    if WIN:
        return win_play(path)
    if FFPLAY:
        subprocess.run([FFPLAY, "-nodisp", "-autoexit", "-loglevel", "quiet", SERVER + path],
                       stdin=subprocess.DEVNULL, timeout=120)
    elif APLAY and path.endswith(".wav"):
        local = fetch_sound(path)
        subprocess.run([APLAY, local] if APLAY.endswith("paplay") else [APLAY, "-q", local],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)


def fetch_sound(path):
    """先下载到本地再播放（叮咚只下载一次）"""
    cache = os.path.join(HERE, "sound")
    os.makedirs(cache, exist_ok=True)
    name = os.path.basename(path)
    local = os.path.join(cache, name if name == "chime.wav" else "speech" + os.path.splitext(name)[1])
    if name != "chime.wav" or not os.path.exists(local):
        with urllib.request.urlopen(SERVER + path, timeout=30) as r:
            data = r.read()
        with open(local + ".tmp", "wb") as f:
            f.write(data)
        os.replace(local + ".tmp", local)
    return local


def win_play(path):
    """Windows：用系统自带的 winmm 播放 mp3/wav"""
    local = fetch_sound(path)
    mci = ctypes.windll.winmm.mciSendStringW
    alias = "cc%d" % threading.get_ident()
    if mci(f'open "{local}" type mpegvideo alias {alias}', None, 0, None) != 0:
        raise OSError("winmm 打不开声音文件")
    try:
        mci(f"play {alias} wait", None, 0, None)
    finally:
        mci(f"close {alias}", None, 0, None)


def win_speak(text):
    """Windows 自带的中文语音（慧慧等）朗读：服务器生成不了自然人声时用。文字走环境变量，不用担心引号"""
    ps = ("Add-Type -AssemblyName System.Speech;$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
          "$v=$s.GetInstalledVoices()|Where-Object{$_.VoiceInfo.Culture.Name -like 'zh*'}|Select-Object -First 1;"
          "if(-not $v){exit};$s.SelectVoice($v.VoiceInfo.Name);$s.Rate=-1;$s.Speak($env:CC_SAY)")  # 没有中文语音就不读（英文语音读中文是乱的）
    subprocess.run(["powershell", "-nop", "-c", ps], env={**os.environ, "CC_SAY": text[:300]},
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   creationflags=0x08000000, timeout=120)


def announce(audio=None, chimes=1, speak=None):
    """先"叮—咚"，再播服务器生成的自然人声；没有的话 Windows 用本机语音朗读"""
    def run():
        with audio_lock:
            try:
                for _ in range(chimes):
                    play("/audio/chime.wav")
                if audio:
                    play(audio)
                elif speak and WIN:
                    win_speak(speak)
            except Exception as e:
                log(f"播放声音失败：{e}")
    threading.Thread(target=run, daemon=True).start()


def send_ack(msg_id):
    def run():
        try:
            req = urllib.request.Request(ACK_URL, data=json.dumps({"id": msg_id}).encode(),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=8).read()
        except Exception as e:
            log(f"确认失败：{e}")
    threading.Thread(target=run, daemon=True).start()


SHOT_PS = r"""Add-Type -AssemblyName System.Windows.Forms,System.Drawing
Add-Type 'using System.Runtime.InteropServices;public class Dpi{[DllImport("user32.dll")]public static extern bool SetProcessDPIAware();}'
[Dpi]::SetProcessDPIAware() | Out-Null
$b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
[System.Drawing.Graphics]::FromImage($bmp).CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
$h = [int]($b.Height * 1280 / $b.Width)
(New-Object System.Drawing.Bitmap $bmp, 1280, $h).Save('OUT', [System.Drawing.Imaging.ImageFormat]::Jpeg)"""


def grab_screen(sw, sh):
    """截一张屏幕图（老师在工作台点了"看一眼大屏"）：返回图片字节，截不了返回 None 和原因"""
    out = os.path.join(HERE, "shot.jpg")
    if WIN:
        cmds = [["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", SHOT_PS.replace("OUT", out.replace("'", "''"))]]
    else:  # 麒麟：系统里有哪个截图工具就用哪个
        disp = os.environ.get("DISPLAY", ":0")
        cmds = []
        if shutil.which("ffmpeg"):
            cmds.append(["ffmpeg", "-loglevel", "error", "-y", "-f", "x11grab", "-video_size", f"{sw}x{sh}", "-i", disp,
                         "-frames:v", "1", "-vf", "scale=1280:-2", "-q:v", "6", out])
        if shutil.which("import"):
            cmds.append(["import", "-window", "root", "-resize", "1280x", "-quality", "70", out])
        if shutil.which("scrot"):
            cmds.append(["scrot", "-o", out])
        if shutil.which("gnome-screenshot"):
            cmds.append(["gnome-screenshot", "-f", out])
        if not cmds:
            return None, "这台一体机上没有截图工具（ffmpeg / import / scrot）"
    err = ""
    for c in cmds:
        try:
            if os.path.exists(out):
                os.remove(out)
            r = subprocess.run(c, capture_output=True, timeout=20, creationflags=0x08000000 if WIN else 0)
            if os.path.exists(out) and os.path.getsize(out) > 0:
                with open(out, "rb") as f:
                    data = f.read()
                os.remove(out)
                return data, ""
            err = (r.stderr or b"").decode("utf-8", "replace").strip()[-150:] or f"{c[0]} 没有生成图片"
        except Exception as e:
            err = f"{c[0]}：{e}"
        log(f"截图失败：{err}")
    return None, err or "截图失败"


def send_shot(sw, sh):
    data, err = grab_screen(sw, sh)
    url = f"{SERVER}/api/screen/shot/upload?class={Q_CLASS}&key={Q_KEY}" + (f"&err={urllib.parse.quote(err)}" if err else "")
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=data or b"", headers={"Content-Type": "application/octet-stream"}), timeout=30).read()
    except Exception as e:
        log(f"上传截图失败：{e}")


def uninstall_self():
    """工作台点了"远程卸载"：取消开机自启、删掉桌面图标和程序文件夹，然后退出（和 uninstall.sh / uninstall.ps1 做的一样）"""
    log("收到远程卸载指令，开始卸载")
    names = ("作业登记", "考勤登记", "班级看板")
    if WIN:
        buf = ctypes.create_unicode_buffer(260)
        startup = buf.value if ctypes.windll.shell32.SHGetFolderPathW(None, 0x07, None, 0, buf) == 0 else ""  # CSIDL_STARTUP
        desk = win_desktop()
        paths = [os.path.join(startup, "班级助手接收端.lnk")] + [os.path.join(desk, n + (".lnk" if n == "班级看板" else ".url")) for n in names]
    else:
        paths = [os.path.expanduser("~/.config/autostart/classcall.desktop")]
        paths += [os.path.join(os.path.expanduser(d), n + ".desktop") for d in ("~/桌面", "~/Desktop") for n in names]
    for p in paths:
        try:
            if p and os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass
    if os.path.basename(HERE) != ".classcall":  # 不是装在 ~/.classcall 里（比如手动从别处运行）：不删文件夹，免得误删
        os._exit(0)
    if WIN:  # 自己正在运行、日志文件开着，删不掉：退出后让一个隐藏的 cmd 过 3 秒再删
        MUTEX[1].CloseHandle(MUTEX[0])
        subprocess.Popen(f'cmd /c ping -n 4 127.0.0.1 >nul & rmdir /s /q "{HERE}"', cwd=os.path.expanduser("~"),
                         creationflags=0x08000000)
    else:
        shutil.rmtree(HERE, ignore_errors=True)
    os._exit(0)


def open_url(url):
    try:
        if WIN:
            return os.startfile(url)
        subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except Exception as e:
        log(f"打开网页失败：{e}")


# ---------------- 老师发来的文件：存到桌面"班级文件"文件夹，7 天后自动删 ----------------
FILE_INDEX = os.path.join(HERE, "files.json")  # 文件 id -> {path, at}：只删自己下载的，不碰老师自己放进去的东西
file_lock = threading.Lock()


def files_dir():
    if WIN:
        base = win_desktop()
    else:
        base = next((os.path.expanduser(d) for d in ("~/桌面", "~/Desktop") if os.path.isdir(os.path.expanduser(d))), HERE)
    d = os.path.join(base, "班级文件")
    os.makedirs(d, exist_ok=True)
    return d


def file_index():
    try:
        with open(FILE_INDEX, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_file_index(idx):
    try:
        with open(FILE_INDEX, "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False)
    except OSError as e:
        log(f"保存文件索引失败：{e}")


def clean_files():
    with file_lock:
        idx = file_index()
        old = [k for k, v in idx.items() if time.time() - v.get("at", 0) > 7 * 86400]
        for k in old:
            try:
                os.remove(idx[k]["path"])
            except OSError:
                pass
            idx.pop(k)
        if old:
            save_file_index(idx)
            log(f"已删除 {len(old)} 个 7 天前的班级文件")


def download_file(fid, name):
    """下载到桌面"班级文件"，返回本地路径；已经下过就直接返回"""
    with file_lock:
        got = file_index().get(fid)
    if got and os.path.isfile(got["path"]):
        return got["path"]
    folder = files_dir()
    stem, ext = os.path.splitext(re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name) or "文件")
    path, n = os.path.join(folder, stem + ext), 2
    while os.path.exists(path):  # 同名文件不覆盖，加 (2)(3)
        path, n = os.path.join(folder, f"{stem}({n}){ext}"), n + 1
    tmp = path + ".part"
    for attempt in range(3):
        try:
            with urllib.request.urlopen(f"{SERVER}/files/{fid}?key={Q_KEY}", timeout=60) as r, open(tmp, "wb") as f:
                shutil.copyfileobj(r, f, 256 * 1024)
            os.replace(tmp, path)
            with file_lock:
                idx = file_index()
                idx[fid] = {"path": path, "at": time.time()}
                save_file_index(idx)
            log(f"已下载文件：{path}")
            return path
        except Exception as e:
            log(f"下载文件失败（第 {attempt + 1} 次）：{e}")
            time.sleep(5)
    try:
        os.remove(tmp)
    except OSError:
        pass
    return None


def open_path(path):
    try:
        if WIN:
            return os.startfile(path)
        subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except Exception as e:
        log(f"打开文件失败：{e}")


def ensure_desktop_icon():
    """在桌面放"作业登记""考勤登记"图标，课代表、班长随时可以打开登记页"""
    if WIN:  # Windows 用 .url 快捷方式；"班级看板"图标由 install.ps1 建（.lnk 要用 COM，标准库做不了）
        for name, url in (("作业登记", COLLECT_URL), ("考勤登记", ATTEND_URL)):
            write_url_shortcut(name, url)
        return
    for name, comment, url, icon in (("作业登记", "课代表登记作业收交情况", COLLECT_URL, "accessories-text-editor"),
                                     ("考勤登记", "班长登记迟到、请假、缺勤、早退", ATTEND_URL, "x-office-calendar")):
        write_desktop_entry(name, comment, url, icon)
    write_desktop_entry("班级看板", "在大屏上显示今日作业、倒计时等，碰一下屏幕收起", None, "video-display",
                        exec_line=f"sh -c 'touch \"{SHOW_FLAG}\"'")


def win_desktop():
    buf = ctypes.create_unicode_buffer(260)  # 桌面可能被移到 D 盘或 OneDrive，不能直接拼 ~/Desktop
    if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) == 0 and buf.value:
        return buf.value
    return os.path.join(os.path.expanduser("~"), "Desktop")


def write_url_shortcut(name, url):
    path = os.path.join(win_desktop(), f"{name}.url")
    entry = f"[InternetShortcut]\r\nURL={url}\r\n"
    try:
        old = open(path, encoding="utf-8", newline="").read() if os.path.exists(path) else ""
        if old != entry:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(entry)
            log(f"已在桌面放置“{name}”图标：{path}")
    except Exception as e:
        log(f"放置桌面图标失败：{e}")


def write_desktop_entry(name, comment, url, icon, exec_line=None):
    cmd = (exec_line or f'xdg-open "{url}"').replace("%", "%%")  # .desktop 里 % 是占位符，不转义的话"高三1班"编码后会被删成乱码
    entry = (f"[Desktop Entry]\nType=Application\nName={name}\nComment={comment}\n"
             f"Exec={cmd}\nIcon={icon}\nTerminal=false\n")
    for d in ("~/桌面", "~/Desktop"):
        d = os.path.expanduser(d)
        if not os.path.isdir(d):
            continue
        path = os.path.join(d, f"{name}.desktop")
        try:
            old = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
            if old != entry:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(entry)
                os.chmod(path, 0o755)
                if shutil.which("gio"):
                    subprocess.run(["gio", "set", path, "metadata::trusted", "true"], stderr=subprocess.DEVNULL, timeout=10)
                log(f"已在桌面放置“{name}”图标：{path}")
        except Exception as e:
            log(f"放置桌面图标失败：{e}")


# ---------------- X11：空闲时间、是否有窗口特效 ----------------
class X11:
    def __init__(self):
        self.ok = self.composite = False
        if WIN:  # Windows：GetLastInputInfo 读空闲时间；半透明窗口系统自带支持
            self.ok = self.composite = True
            return
        try:
            x11 = ctypes.cdll.LoadLibrary(ctypes.util.find_library("X11") or "libX11.so.6")
            x11.XOpenDisplay.restype = ctypes.c_void_p
            x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
            x11.XDefaultRootWindow.restype = ctypes.c_ulong
            x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
            x11.XInternAtom.restype = ctypes.c_ulong
            x11.XGetSelectionOwner.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            x11.XGetSelectionOwner.restype = ctypes.c_ulong
            self.dpy = x11.XOpenDisplay(None)
            if not self.dpy:
                return
            self.root = x11.XDefaultRootWindow(self.dpy)
            self.x11 = x11
            self.composite = x11.XGetSelectionOwner(self.dpy, x11.XInternAtom(self.dpy, b"_NET_WM_CM_S0", 0)) != 0

            class Info(ctypes.Structure):
                _fields_ = [("window", ctypes.c_ulong), ("state", ctypes.c_int), ("kind", ctypes.c_int),
                            ("til_or_since", ctypes.c_ulong), ("idle", ctypes.c_ulong), ("event_mask", ctypes.c_ulong)]
            xss = ctypes.cdll.LoadLibrary(ctypes.util.find_library("Xss") or "libXss.so.1")
            xss.XScreenSaverAllocInfo.restype = ctypes.POINTER(Info)
            xss.XScreenSaverQueryInfo.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(Info)]
            self.xss, self.info = xss, xss.XScreenSaverAllocInfo()
            self.ok = True
        except Exception as e:
            log(f"读取空闲时间不可用（待机页将不会自动出现）：{e}")

    def idle_seconds(self):
        if not self.ok:
            return 0
        if WIN:
            class LII(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
            lii = LII(ctypes.sizeof(LII), 0)
            if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
                return 0
            return ((ctypes.windll.kernel32.GetTickCount() - lii.dwTime) & 0xFFFFFFFF) / 1000
        self.xss.XScreenSaverQueryInfo(self.dpy, self.root, self.info)
        return self.info.contents.idle / 1000

    def keep_awake(self):
        """考试时每半分钟调用一次：不让系统屏保、黑屏、睡眠出来（假装有人轻轻动了一下鼠标，光标原地不动）"""
        try:
            if WIN:
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000003)  # ES_CONTINUOUS | ES_DISPLAY_REQUIRED | ES_SYSTEM_REQUIRED
                ctypes.windll.user32.mouse_event(0x0001, 0, 0, 0, 0)  # 移动 0 像素：重置空闲计时，屏保不会启动
                return
            if not self.ok:
                return
            if not hasattr(self, "xtst"):
                try:
                    self.xtst = ctypes.cdll.LoadLibrary(ctypes.util.find_library("Xtst") or "libXtst.so.6")
                    self.xtst.XTestFakeRelativeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
                except OSError:
                    self.xtst = None
                self.x11.XResetScreenSaver.argtypes = [ctypes.c_void_p]
                self.x11.XForceScreenSaver.argtypes = [ctypes.c_void_p, ctypes.c_int]
                self.x11.XFlush.argtypes = [ctypes.c_void_p]
            if self.xtst:  # 鼠标右移 1 像素再移回来：麒麟/统信的屏保按"多久没人操作"计时，这样就会重新计时
                self.xtst.XTestFakeRelativeMotionEvent(self.dpy, 1, 0, 0)
                self.xtst.XTestFakeRelativeMotionEvent(self.dpy, -1, 0, 0)
            self.x11.XForceScreenSaver(self.dpy, 0)  # ScreenSaverReset：已经黑屏的话点亮屏幕，并重新计时
            self.x11.XResetScreenSaver(self.dpy)
            self.x11.XFlush(self.dpy)
        except Exception as e:
            log(f"阻止屏保失败：{e}")

    def allow_sleep(self):
        """考试结束：Windows 恢复正常的黑屏、睡眠"""
        if WIN:
            try:
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
            except Exception:
                pass


# ---------------- 绘图工具 ----------------
def rgb(c):
    return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))


def mix(a, b, t):
    ra, rb = rgb(a), rgb(b)
    return "#%02x%02x%02x" % tuple(int(ra[i] + (rb[i] - ra[i]) * t) for i in range(3))


def gradient(cv, x1, y1, x2, y2, c1, c2, vertical=False, steps=80):
    span = (y2 - y1) if vertical else (x2 - x1)
    step = max(1, span / steps)
    i = 0
    while i * step < span:
        a, b = i * step, min(span, (i + 1) * step + 1)
        color = mix(c1, c2, i * step / max(1, span))
        if vertical:
            cv.create_rectangle(x1, y1 + a, x2, y1 + b, fill=color, width=0)
        else:
            cv.create_rectangle(x1 + a, y1, x1 + b, y2, fill=color, width=0)
        i += 1


def round_rect(cv, x1, y1, x2, y2, r, **kw):
    r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
           x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


def wrap(text, f, width):
    lines = []
    for para in text.split("\n"):
        cur = ""
        for ch in para:
            if cur and f.measure(cur + ch) > width and ch not in NO_LINE_START:
                lines.append(cur)
                cur = ch
            else:
                cur += ch
        lines.append(cur)
    return lines


def wrap_words(text, f, width):
    """和 wrap 一样，但英文单词、数字不从中间断开（早读内容常有英文）"""
    lines = []
    for para in text.split("\n"):
        cur = ""
        for tok in re.findall(r"[A-Za-z0-9'’\-]+|.", para):
            if f.measure(cur + tok) <= width or (cur and tok[0] in NO_LINE_START):
                cur += tok
            elif f.measure(tok) > width:  # 一个词比整行还长：只好按字母断
                for ch in tok:
                    if cur and f.measure(cur + ch) > width:
                        lines.append(cur)
                        cur = ""
                    cur += ch
            else:
                if cur.strip():
                    lines.append(cur.rstrip())
                cur = tok.lstrip()
        lines.append(cur)
    return lines


def board_sig(board):
    """看板上画死的内容（时钟、进度每秒自己更新，不算在内）"""
    return json.dumps([board.get(k) for k in ("homework", "eve_plan", "notice", "notes", "countdowns", "duty", "stars", "evening")], sort_keys=True)


def board_msgs(board):
    """看板上的"公告与留言"：班主任的公告在最前，后面是各科老师的留言，每条 (标签, 内容)"""
    out = [("公告", (board.get("notice") or "").replace("\n", " ").strip())]
    out += [(n.get("by") or "留言", (n.get("text") or "").strip()) for n in board.get("notes") or []]
    return [m for m in out if m[1]]


def wrap_after(text, f, first_w, width):
    """第一行前面有标签（只剩 first_w 宽），后面的行用整行宽"""
    first = wrap_words(text, f, first_w)[0] if first_w > f.measure("字") * 2 else ""
    rest = text[len(first):].lstrip()
    return [first] + (wrap_words(rest, f, width) if rest else [])


def ellipsis(text, f, width):
    if f.measure(text) <= width:
        return text
    while text and f.measure(text + "…") > width:
        text = text[:-1]
    return text + "…"


# ---------------- 弹窗 ----------------
def float_window(win):
    """Windows 上弹窗默认会抢走焦点（老师的翻页笔就翻不了 PPT）：加上"不激活"属性，并把焦点还给原来的窗口。麒麟上 overrideredirect 已经不抢焦点"""
    if not WIN:
        return
    try:
        u = ctypes.windll.user32
        prev = u.GetForegroundWindow()
        win.update_idletasks()
        hwnd = u.GetParent(win.winfo_id()) or win.winfo_id()
        GWL_EXSTYLE, WS_EX_TOPMOST, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = -20, 0x8, 0x80, 0x08000000
        u.SetWindowLongW(hwnd, GWL_EXSTYLE, u.GetWindowLongW(hwnd, GWL_EXSTYLE) | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
        u.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0040)  # HWND_TOPMOST，不移动、不改大小、不激活、显示
        if prev and prev != u.GetForegroundWindow():
            u.SetForegroundWindow(prev)
    except Exception as e:
        log(f"设置窗口不抢焦点失败：{e}")


class Card:
    """白色卡片 + 顶部渐变色条，所有弹窗的基础"""

    def __init__(self, app, kind, head_right, popup, size=None):
        self.app, self.kind, self.popup = app, kind, popup
        sw, sh = app.sw, app.sh
        if popup:
            self.w, self.h = size or (int(sw * 0.72), int(sh * 0.62))
            self.x, self.y = (sw - self.w) // 2, (sh - self.h) // 2
        else:
            self.w, self.h = min(700, int(sw * 0.42)), max(190, int(sh * 0.19))
            self.x, self.y = sw - self.w - 24, sh
        self.backdrop = app.make_backdrop() if popup else None
        self.win = tk.Toplevel(app.root)
        self.win.overrideredirect(True)  # 无边框，不抢焦点，不影响老师翻 PPT
        self.win.attributes("-topmost", True)
        self.win.geometry(f"{self.w}x{self.h}+{self.x}+{self.y}")
        float_window(self.win)
        self.cv = tk.Canvas(self.win, width=self.w, height=self.h, bg="#ffffff", highlightthickness=0)
        self.cv.pack()
        self.band = max(54, int(self.h * 0.13)) if popup else 46
        title, c1, c2 = THEME.get(kind, THEME["call"])
        gradient(self.cv, 0, 0, self.w, self.band, c1, c2)
        self.cv.create_rectangle(0, 0, self.w - 1, self.h - 1, outline=LINE)
        fs = int(self.band * 0.4)
        self.cv.create_oval(fs * 0.7, self.band / 2 - fs * 0.45, fs * 1.6, self.band / 2 + fs * 0.45, fill="#ffffff", width=0)
        self.cv.create_text(fs * 2.1, self.band / 2, text=title, anchor="w", fill="#ffffff", font=app.font(fs, True))
        self.cv.create_text(self.w - fs, self.band / 2, text=head_right, anchor="e", fill="#ffffff", font=app.font(int(fs * 0.8)))
        if popup:
            app.fade_in(self.win)

    def button(self, cx, cy, text, cmd, px, color=GREEN):
        f = self.app.font(px, True)
        w, h = f.measure(text) + px * 2.6, px * 2.1
        tag = f"btn{id(cmd)}"
        round_rect(self.cv, cx - w / 2, cy - h / 2 + 4, cx + w / 2, cy + h / 2 + 4, h / 2, fill=mix(color, "#000000", .15), width=0, tags=tag)
        round_rect(self.cv, cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, h / 2, fill=color, width=0, tags=tag)
        self.cv.create_text(cx, cy, text=text, fill="#ffffff", font=f, tags=tag)
        self.cv.tag_bind(tag, "<Button-1>", lambda e: cmd())
        self.cv.tag_bind(tag, "<Enter>", lambda e: self.cv.config(cursor="hand2"))
        return h

    def text_block(self, text, top, bottom, biggest, color=INK, pad=None, smallest=14):
        """字号从大往小试，放得下就画出来并返回 True；缩到 smallest 还放不下返回 False（调用方再截断或改成可拖动）"""
        pad = pad if pad is not None else int(self.w * 0.06)
        width, height = self.w - pad * 2, bottom - top
        for px in range(biggest, smallest - 1, -2):
            f = self.app.font(px, True)
            lines = wrap(text, f, width)
            if len(lines) * f.metrics("linespace") * 1.12 <= height:
                break
        else:
            if smallest > 14:
                return False
        lh = f.metrics("linespace") * 1.12
        y = top + (height - lh * len(lines)) / 2 + lh / 2
        left = len(lines) > 3  # 多行的作业左对齐，读起来顺
        for line in lines:
            self.cv.create_text(pad if left else self.w / 2, y, text=line, anchor="w" if left else "center", fill=color, font=f)
            y += lh
        return True

    def clipped_text(self, text, top, bottom, px, color=INK, pad=None):
        """右下角小卡片放不下：能放几行放几行，最后一行末尾加"…" """
        pad = pad if pad is not None else int(self.w * 0.06)
        f = self.app.font(px, True)
        lh = f.metrics("linespace") * 1.12
        lines = wrap(text, f, self.w - pad * 2)
        n = max(1, int((bottom - top) // lh))
        lines = lines[:n - 1] + [ellipsis(lines[n - 1] + "……", f, self.w - pad * 2)]
        y = top + (bottom - top - lh * len(lines)) / 2 + lh / 2
        for line in lines:
            self.cv.create_text(pad, y, text=line, anchor="w", fill=color, font=f)
            y += lh

    def scroll_text(self, text, top, bottom, px, color=INK, pad=None):
        """大窗也放不下：用 px 号字左对齐排好，放进可以用手指上下拖动（或滚轮）的区域"""
        pad = pad if pad is not None else int(self.w * 0.06)
        f = self.app.font(px, True)
        lh = f.metrics("linespace") * 1.12
        hint = self.app.font(max(16, int(px * 0.7)), True)
        hint_h = hint.metrics("linespace") * 1.6
        width, height = int(self.w - pad * 2), int(bottom - top - hint_h)
        inner = tk.Canvas(self.win, width=width, height=height, bg="#ffffff", highlightthickness=0)
        self.cv.create_window(pad, top, window=inner, anchor="nw")
        y = lh / 2
        for line in wrap(text, f, width):
            inner.create_text(0, y, text=line, anchor="w", fill=color, font=f)
            y += lh
        inner.configure(scrollregion=(0, 0, width, int(y)), yscrollincrement=1)
        inner.bind("<ButtonPress-1>", lambda e: inner.scan_mark(0, e.y))
        inner.bind("<B1-Motion>", lambda e: inner.scan_dragto(0, e.y, gain=1))
        inner.bind("<MouseWheel>", lambda e: inner.yview_scroll(-int(e.delta / 120) * int(lh), "units"))  # Windows
        inner.bind("<Button-4>", lambda e: inner.yview_scroll(-int(lh), "units"))  # 麒麟滚轮
        inner.bind("<Button-5>", lambda e: inner.yview_scroll(int(lh), "units"))
        self.cv.create_text(self.w / 2, bottom - hint_h / 2, text="内容较多，用手指按住文字上下拖动查看全部", fill=MUTED, font=hint)

    def place(self, slot):
        if not self.popup:
            self.y = self.app.sh - (self.h + 16) * (slot + 1) - 60
            self.win.geometry(f"{self.w}x{self.h}+{self.x}+{self.y}")

    def lift(self):
        for w in (self.backdrop, self.win):
            try:
                if w:
                    w.attributes("-topmost", True)
                    w.lift()
            except tk.TclError:  # 窗口被别的程序关掉了
                pass

    def close(self):
        for w in (self.win, self.backdrop):
            try:
                if w:
                    w.destroy()
            except tk.TclError:
                pass


class MessageCard(Card):
    def __init__(self, app, m):
        popup = bool(m.get("popup"))
        long = popup and len(m["text"]) > 60  # 长作业：大窗开得更大
        super().__init__(app, m.get("kind", "call"), f"{m['sender']} · {m['time']}", popup,
                         (int(app.sw * 0.84), int(app.sh * 0.86)) if long else None)
        btn_px = int(app.sh * 0.62 * 0.055) if self.popup else 17
        btn_y = self.h - btn_px * 2.2
        self.button(self.w / 2 if self.popup else self.w - btn_px * 3.4, btn_y, "收到", lambda: app.confirm(m["id"]), btn_px)
        bottom = btn_y - btn_px * 1.6 if self.popup else self.h - btn_px * 4.2
        big = 0.11 if m.get("kind") == "prep" else 0.16  # 课前提醒两行字，稍小一点免得一个词被拆开
        if self.popup:
            least = max(20, int(app.sh * 0.032))  # 大窗字号最小到这里（教室后排还看得清），再放不下就改成可拖动
            if not self.text_block(m["text"], self.band + 10, bottom, int(app.sh * 0.62 * big), smallest=least):
                self.scroll_text(m["text"], self.band + 16, bottom, least)
        elif not self.text_block(m["text"], self.band + 6, bottom, 34, smallest=20):
            self.clipped_text(m["text"], self.band + 6, bottom, 20)
            self.button(self.w - btn_px * 9.4, btn_y, "看全文", lambda: self.expand(app, m), btn_px, color="#3b82f6")

    def expand(self, app, m):
        """右下角小卡片点"看全文"：换成大窗（不再播报）"""
        app.close(m["id"])
        app.cards[m["id"]] = MessageCard(app, dict(m, popup=True, voice=False))
        app.relayout()


class CollectCard(Card):
    """老师在工作台点"让课代表登记 / 让班长登记考勤"后弹出，点"开始登记"打开对应的登记页"""

    def __init__(self, app, m):
        kind = m.get("kind", "collect")
        super().__init__(app, kind, f"{m['sender']} · {m['time']}", bool(m.get("popup")))
        data = m.get("data") or {}
        hw = data.get("hw_id")
        base = ATTEND_URL if kind == "attend" else COLLECT_URL
        px = int(self.h * 0.05) if self.popup else 16
        y = self.h - px * 2.4

        def start():
            open_url(base + (f"&hw={hw}" if hw else ""))
            app.confirm(m["id"])
        if self.popup:
            self.button(self.w / 2 - px * 5, y, "开始登记", start, px)
            self.button(self.w / 2 + px * 5, y, "稍后", lambda: app.confirm(m["id"]), px, color="#9aa6b5")
        else:
            self.button(self.w - px * 9.5, y, "开始登记", start, px)
            self.button(self.w - px * 3.4, y, "稍后", lambda: app.confirm(m["id"]), px, color="#9aa6b5")
        bottom = y - px * 1.6 if self.popup else self.h - px * 4.2
        self.text_block(m["text"], self.band + 10, bottom, int(self.h * 0.12) if self.popup else 30)


class ImageCard(Card):
    """老师把错题题图投到大屏；可带限时订正倒计时"""

    def __init__(self, app, m):
        d = m.get("data") or {}
        super().__init__(app, "image", "", True, (int(app.sw * 0.92), int(app.sh * 0.9)))
        self.mid, self.end_at, self.ended = m["id"], d.get("end_at"), False
        fs = int(self.band * 0.4)
        if d.get("caption"):
            self.cv.create_text(fs * 6.6, self.band / 2, text=d["caption"], anchor="w", fill="#ffffff", font=app.font(fs, True))
        self.clock = self.cv.create_text(self.w - fs, self.band / 2, text="", anchor="e", fill="#ffffff",
                                         font=app.font(int(fs * 1.25), True))
        px = int(self.h * 0.035)
        self.btn_h = self.button(self.w - px * 3.6, self.h - px * 1.7, "关闭", lambda: app.confirm(self.mid), px, color="#9aa6b5")
        self.area = (int(self.w - 40), int(self.h - self.band - px * 3.6 - 20))
        self.loading = self.cv.create_text(self.w / 2, self.h / 2, text="题图加载中…", fill=MUTED, font=app.font(int(self.h * 0.04), True))
        self.photo = None
        threading.Thread(target=self.fetch, args=(d.get("img", ""),), daemon=True).start()
        if self.end_at:
            self.tick()

    def fetch(self, img):
        try:
            import base64
            fmt = "png" if tk.TkVersion >= 8.6 else "gif"
            with urllib.request.urlopen(f"{SERVER}{img}?w={self.area[0]}&h={self.area[1]}&fmt={fmt}", timeout=30) as r:
                data = base64.b64encode(r.read())
            self.app.root.after(0, lambda: self.show_image(data))
        except Exception as e:
            log(f"题图下载失败：{e}")
            self.app.root.after(0, lambda: self.cv.itemconfig(self.loading, text="题图加载失败"))

    def show_image(self, data):
        try:
            self.photo = tk.PhotoImage(data=data)
            self.cv.delete(self.loading)
            self.cv.create_image(self.w / 2, self.band + 10 + self.area[1] / 2, image=self.photo)
        except tk.TclError as e:
            log(f"题图显示失败：{e}")

    def tick(self):
        if self.ended:
            return
        left = self.end_at - (time.time() + self.app.offset)
        if left <= 0:
            self.ended = True
            self.cv.itemconfig(self.clock, text="时间到！")
            announce(chimes=2)
            return
        mm, ss = divmod(int(left + 0.999), 60)
        self.cv.itemconfig(self.clock, text=f"限时订正 {mm:02d}:{ss:02d}")
        self.win.after(250, self.tick)


class FileCard(Card):
    """老师发来的文档：先下载到桌面"班级文件"，点"打开"用一体机自带的软件（WPS/Office）打开"""

    def __init__(self, app, m):
        d = m.get("data") or {}
        popup = bool(m.get("popup"))
        super().__init__(app, "file", f"{m['sender']} · {m['time']}", popup,
                         (int(app.sw * 0.5), int(app.sh * 0.42)) if popup else None)
        self.mid, self.path, self.want_open, self.failed = m["id"], None, False, False
        px = int(self.h * 0.06) if popup else 16
        y = self.h - px * 2.2
        if popup:
            self.button(self.w / 2 - px * 4, y, "打开", self.open, px)
            self.button(self.w / 2 + px * 4, y, "关闭", lambda: app.confirm(self.mid), px, color="#9aa6b5")
        else:
            self.button(self.w - px * 9, y, "打开", self.open, px)
            self.button(self.w - px * 3.4, y, "关闭", lambda: app.confirm(self.mid), px, color="#9aa6b5")
        name, size = d.get("name", "文件"), d.get("size", "")
        top, bottom = self.band + 8, y - px * 1.6
        if popup:
            self.text_block(name, top, top + (bottom - top) * 0.7, int(self.h * 0.09))
            sy = top + (bottom - top) * 0.85
            self.status = self.cv.create_text(self.w / 2, sy, text=f"{size}　下载中…", fill=MUTED, font=app.font(int(self.h * 0.045)))
        else:
            pad = 20
            self.cv.create_text(pad, self.band + 30, text=ellipsis(name, app.font(22, True), self.w - pad * 2), anchor="w",
                                fill=INK, font=app.font(22, True))
            self.status = self.cv.create_text(pad, self.band + 64, text=f"{size}　下载中…", anchor="w", fill=MUTED, font=app.font(15))
        self.size = size
        threading.Thread(target=self.fetch, args=(d.get("file", ""), name), daemon=True).start()

    def fetch(self, fid, name):
        path = download_file(fid, name)
        self.app.root.after(0, lambda: self.done(path))

    def done(self, path):
        self.path, self.failed = path, not path
        try:
            self.cv.itemconfig(self.status, text=f"{self.size}　已存到桌面「班级文件」" if path else "下载失败，请检查网络后让老师重发")
        except tk.TclError:
            return
        if path and self.want_open:
            self.open()

    def open(self):
        if self.path:
            open_path(self.path)
            self.app.confirm(self.mid)
        elif not self.failed:
            self.want_open = True
            self.cv.itemconfig(self.status, text=f"{self.size}　还在下载，下好自动打开…")


class TimerCard(Card):
    def __init__(self, app, m):
        d = m.get("data") or {}
        super().__init__(app, "timer", f"{m['sender']} · {m['time']}", True, (int(app.sw * 0.58), int(app.sh * 0.6)))
        self.mid, self.end_at, self.total = m["id"], d.get("end_at", time.time()), max(1, d.get("seconds", 60))
        self.ended = False
        cy = self.band + (self.h - self.band) * 0.12
        self.cv.create_text(self.w / 2, cy, text=d.get("label") or "倒计时", fill=MUTED, font=app.font(int(self.h * 0.055), True))
        self.num = self.cv.create_text(self.w / 2, self.band + (self.h - self.band) * 0.45, text="",
                                       fill=INK, font=app.font(int(self.h * 0.3), True, digits=True))
        self.bar_y = self.band + (self.h - self.band) * 0.72
        self.bx1, self.bx2 = self.w * 0.14, self.w * 0.86
        round_rect(self.cv, self.bx1, self.bar_y - 9, self.bx2, self.bar_y + 9, 9, fill="#f1f4f8", width=0)
        self.bar = round_rect(self.cv, self.bx1, self.bar_y - 9, self.bx2, self.bar_y + 9, 9, fill="#f2706b", width=0)
        self.button(self.w / 2, self.h - self.h * 0.09, "关闭", self.finish, int(self.h * 0.04), color="#9aa6b5")
        self.tick()

    def tick(self):
        if self.ended:
            return
        left = self.end_at - (time.time() + self.app.offset)
        if left <= 0:
            return self.time_up()
        m, s = divmod(int(left + 0.999), 60)
        self.cv.itemconfig(self.num, text=f"{m:02d}:{s:02d}", fill="#ef5a5a" if left <= 10 else INK)
        frac = max(0.0, min(1.0, left / self.total))
        self.cv.delete(self.bar)
        if frac > 0.02:
            color = "#ef5a5a" if left <= 10 else mix("#1fb57a", "#f5a524", 1 - frac)
            self.bar = round_rect(self.cv, self.bx1, self.bar_y - 9, self.bx1 + (self.bx2 - self.bx1) * frac, self.bar_y + 9, 9,
                                  fill=color, width=0)
        self.win.after(250, self.tick)

    def time_up(self):
        self.ended = True
        self.cv.itemconfig(self.num, text="时间到！", fill="#ef5a5a")
        self.cv.delete(self.bar)
        announce(chimes=2)
        self.win.after(10000, self.finish)

    def finish(self):
        self.ended = True
        self.app.confirm(self.mid)


class PickCard(Card):
    def __init__(self, app, m):
        d = m.get("data") or {}
        super().__init__(app, "pick", f"{m['sender']} · {m['time']}", True, (int(app.sw * 0.58), int(app.sh * 0.6)))
        self.m, self.final = m, d.get("name", "")
        self.names = d.get("names") or [self.final]
        cy = self.band + (self.h - self.band) * 0.44
        round_rect(self.cv, self.w * 0.16, cy - self.h * 0.2, self.w * 0.84, cy + self.h * 0.2, 36, fill="#f3f0ff", width=0)
        self.label = self.cv.create_text(self.w / 2, cy, text="", fill="#8b7cf6", font=app.font(int(self.h * 0.2), True))
        self.sub = self.cv.create_text(self.w / 2, cy + self.h * 0.27, text="抽号中…", fill=MUTED, font=app.font(int(self.h * 0.05), True))
        self.delays = [30 + int(i * i * 0.9) for i in range(22)]  # 越滚越慢，约 3.5 秒
        self.roll(0)

    def roll(self, i):
        if i < len(self.delays):
            self.cv.itemconfig(self.label, text=random.choice(self.names))
            self.win.after(self.delays[i], lambda: self.roll(i + 1))
        else:
            self.cv.itemconfig(self.label, text=self.final, fill="#5f4fd6")
            self.cv.itemconfig(self.sub, text="请这位同学回答", fill=GREEN)
            px = int(self.h * 0.045)
            self.button(self.w / 2, self.h - px * 2.4, "好的", lambda: self.app.confirm(self.m["id"]), px)
            if self.m.get("voice"):
                announce(self.m.get("audio"), speak=self.m.get("speak"))


# ---------------- 待机页 ----------------
SUBJ_COLOR = {"语文": "#ef5a5a", "数学": "#3b82f6", "英语": "#8b5cf6", "物理": "#14b8a6", "化学": "#22c55e", "生物": "#84cc16",
              "政治": "#f97316", "历史": "#d97706", "地理": "#0ea5e9"}


class Standby:
    """班级看板（夜空主题）：晚自习无人使用时自动显示，也可以手动调出"""
    BG1, BG2, PANEL, DIM, SOFT, GOLD, GREEN = "#0d1830", "#1c2f57", "#1a2c52", "#8ea3c7", "#c9d6ec", "#fbbf24", "#34d399"

    def __init__(self, app, board):
        self.app, self.board = app, board
        sw, sh = app.sw, app.sh
        self.win = tk.Toplevel(app.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.geometry(f"{sw}x{sh}+0+0")
        float_window(self.win)
        cv = self.cv = tk.Canvas(self.win, width=sw, height=sh, highlightthickness=0, bg=self.BG1)
        cv.pack()
        cv.bind("<Button-1>", lambda e: app.hide_standby())
        u = self.u = sh / 100
        F = lambda px, b=True: app.font(int(px * u), b)
        gradient(cv, 0, 0, sw, sh, self.BG1, self.BG2, vertical=True, steps=120)
        rnd = random.Random(7)  # 固定的星空
        for _ in range(70):
            x, y, r = rnd.uniform(0, sw), rnd.uniform(0, sh * 0.45), rnd.choice((1, 1, 1.5, 2))
            cv.create_oval(x - r, y - r, x + r, y + r, fill=rnd.choice(("#ffffff", "#c7d7ff", "#9fb4e6")), width=0)
        mx, my, mr = sw * 0.405, sh * 0.085, 3.6 * u  # 月牙
        cv.create_oval(mx - mr, my - mr, mx + mr, my + mr, fill="#fde68a", width=0)
        cv.create_oval(mx - mr + 1.6 * u, my - mr - 0.8 * u, mx + mr + 1.6 * u, my + mr - 0.8 * u, fill=mix(self.BG1, self.BG2, 0.06), width=0)

        L, R = sw * 0.055, sw * 0.945
        mid = sw * 0.47
        self.L_, self.mid_ = L, mid
        # ---- 左栏：状态、时钟、本节进度、倒计时、值日 ----
        self.pill_bg = round_rect(cv, L, 6 * u, L + 22 * u, 11 * u, 2.5 * u, fill="#123b34", width=0)
        self.pill = cv.create_text(L + 11 * u, 8.5 * u, text="", fill=self.GREEN, font=F(2.4))
        self.clock = cv.create_text(L - 0.6 * u, 22 * u, text="", anchor="w", fill="#ffffff", font=app.font(int(17 * u), True, digits=True))
        self.date = cv.create_text(L, 34.5 * u, text="", anchor="w", fill=self.DIM, font=F(2.6))
        py = 40 * u
        plan, cds = board.get("eve_plan") or [], board.get("countdowns") or []
        self.bar, self.rows = None, []
        if plan:  # 班主任排了晚自习时间安排：本节进度卡换成"今晚安排"，正在进行的一段高亮、带进度条
            room = sh - (15 if board.get("quote") else 8) * u - py - (8 * u if cds else 0) - 7 * u
            rh = max(4.4 * u, min(6.4 * u, room / len(plan)))
            round_rect(cv, L, py, mid - 2 * u, py + 6 * u + rh * len(plan), 2.6 * u, fill=self.PANEL, width=0)
            cv.create_text(L + 2.6 * u, py + 3.4 * u, text="今晚安排", anchor="w", fill=self.DIM, font=F(2.2))
            f_t, f_s = app.font(int(2.6 * u), True, digits=True), F(2.8)
            sx = L + 2.6 * u + f_t.measure("00:00–00:00") + 2.4 * u  # 科目紧跟在时间后面，按实际字宽排
            for k, it in enumerate(plan):
                y1 = py + 5.4 * u + k * rh
                c = next((v for s, v in SUBJ_COLOR.items() if it["text"].startswith(s)), None)
                self.rows.append({"a": it["start"], "e": it["end"], "y": (y1, y1 + rh - 0.5 * u),
                                  "color": mix(c, "#ffffff", 0.3) if c else self.SOFT,
                                  "hl": round_rect(cv, L + 1.2 * u, y1, mid - 3.2 * u, y1 + rh - 0.5 * u, 1.6 * u, fill=self.PANEL, width=0),
                                  "t": cv.create_text(L + 2.6 * u, y1 + rh / 2 - 0.25 * u, text=f"{it['start']}–{it['end']}", anchor="w", fill=self.DIM, font=f_t),
                                  "s": cv.create_text(sx, y1 + rh / 2 - 0.25 * u, anchor="w", font=f_s,
                                                      text=ellipsis(it["text"], f_s, mid - 18 * u - sx)),
                                  "r": cv.create_text(mid - 4.6 * u, y1 + rh / 2 - 0.25 * u, text="", anchor="e", fill=self.GREEN, font=F(2.2))})
            y = py + 6 * u + rh * len(plan) + 2 * u
        else:  # 本节进度卡
            round_rect(cv, L, py, mid - 2 * u, py + 15 * u, 2.6 * u, fill=self.PANEL, width=0)
            self.sess = cv.create_text(L + 2.6 * u, py + 4 * u, text="", anchor="w", fill=self.SOFT, font=F(2.6))
            self.left = cv.create_text(mid - 4.6 * u, py + 4 * u, text="", anchor="e", fill="#ffffff", font=F(3.2))
            self.bar_box = (L + 2.6 * u, py + 9.5 * u, mid - 4.6 * u, py + 11.3 * u)
            round_rect(cv, *self.bar_box, 0.9 * u, fill="#2a4073", width=0)
            y = py + 19 * u
        if cds and plan:  # 有时间安排时倒计时缩成一行
            x = L
            for c in cds[:3]:
                f_n = app.font(int(3.4 * u), True, digits=True)
                w = F(2.2).measure(f"距{c['name']}天") + f_n.measure(str(c["days"])) + 5 * u
                if x + w > mid - 2 * u:
                    break
                round_rect(cv, x, y, x + w, y + 6 * u, 2 * u, fill=self.PANEL, width=0)
                t = cv.create_text(x + 1.8 * u, y + 3 * u, text=f"距{c['name']}", anchor="w", fill=self.DIM, font=F(2.2))
                n = cv.create_text(cv.bbox(t)[2] + 0.8 * u, y + 3 * u, text=str(c["days"]), anchor="w", fill=self.GOLD, font=f_n)
                cv.create_text(cv.bbox(n)[2] + 0.6 * u, y + 3.3 * u, text="天", anchor="w", fill=self.DIM, font=F(2.2))
                x += w + 1.4 * u
            y += 9 * u
        elif cds:
            n = min(3, len(cds)); gap = 1.6 * u; w = (mid - 2 * u - L - gap * (n - 1)) / n
            for k, c in enumerate(cds[:3]):
                x1 = L + k * (w + gap)
                round_rect(cv, x1, y, x1 + w, y + 15 * u, 2.6 * u, fill=self.PANEL, width=0)
                cv.create_text(x1 + w / 2, y + 3.6 * u, text=f"距{c['name']}", fill=self.DIM, font=F(2.2))
                cv.create_text(x1 + w / 2 - 1.4 * u, y + 9.2 * u, text=str(c["days"]), fill=self.GOLD, font=app.font(int(6.4 * u), True, digits=True))
                cv.create_text(x1 + w / 2 + app.font(int(6.4 * u), True).measure(str(c["days"])) / 2 - 0.6 * u, y + 10.6 * u,
                               text="天", anchor="w", fill=self.DIM, font=F(2.2))
            y += 18 * u
        duty = board.get("duty") or []
        if duty and y < sh * 0.78:
            cv.create_text(L, y + 1.5 * u, text="今日值日", anchor="w", fill=self.DIM, font=F(2.2))
            x, f = L + 10 * u, F(2.4, False)
            for d in duty:
                t = d["name"] + (f" · {d['task']}" if d.get("task") else "")
                w = f.measure(t) + 2.8 * u
                if x + w > mid - 2 * u:
                    break
                round_rect(cv, x, y - 0.4 * u, x + w, y + 3.4 * u, 1.9 * u, fill="#243a68", width=0)
                cv.create_text(x + w / 2, y + 1.5 * u, text=t, fill=self.SOFT, font=f)
                x += w + 1 * u
        # ---- 右栏：今日作业（白卡片）、公告、本周之星 ----
        quote, stars, msgs = board.get("quote"), board.get("stars") or [], board_msgs(board)[:4]
        bottom = sh - (15 if quote else 8) * u
        rx = mid + 2 * u
        msg_h = (3.6 + 4.6 * len(msgs)) * u if msgs else 0
        extra = (11 * u if stars else 0) + (msg_h + 1.6 * u if msgs else 0)
        hw_bot = bottom - extra
        round_rect(cv, rx, 6 * u, R, hw_bot, 3 * u, fill="#ffffff", width=0)
        total = board.get("total_minutes") or 0
        cv.create_text(rx + 3 * u, 10.5 * u, text="今日作业", anchor="w", fill=INK, font=F(3.2))
        if total:
            cv.create_text(R - 3 * u, 10.5 * u, text=f"共约 {total} 分钟", anchor="e", fill="#0f7a53", font=F(2.6))
        items = board.get("homework") or []
        y, f_c = 16.5 * u, F(2.9, False)
        if not items:
            cv.create_text((rx + R) / 2, (6 * u + hw_bot) / 2, text="今天还没有布置作业", fill=MUTED, font=F(3))
        for hw in items:
            cw = R - rx - 24 * u
            lines = wrap(hw["content"], f_c, cw)[:2]
            h = max(6.2 * u, len(lines) * 4.2 * u + 2.2 * u)
            if y + h > hw_bot - 2 * u:
                cv.create_text(rx + 3 * u, y + 1.5 * u, text="……", anchor="w", fill=MUTED, font=f_c)
                break
            color = SUBJ_COLOR.get(hw["subject"], "#16a571")
            round_rect(cv, rx + 2.4 * u, y, R - 2.4 * u, y + h, 1.8 * u, fill="#f4f7fb", width=0)
            round_rect(cv, rx + 3.6 * u, y + h / 2 - 2 * u, rx + 11.6 * u, y + h / 2 + 2 * u, 1.4 * u, fill=color, width=0)
            cv.create_text(rx + 7.6 * u, y + h / 2, text=hw["subject"], fill="#ffffff", font=F(2.3))
            ty = y + h / 2 - (len(lines) - 1) * 2.1 * u
            for k, line in enumerate(lines):
                cv.create_text(rx + 13.6 * u, ty + k * 4.2 * u, text=line, anchor="w", fill=INK, font=f_c)
            if hw.get("minutes"):
                cv.create_text(R - 4 * u, y + h / 2, text=f"{hw['minutes']}′", anchor="e", fill=MUTED, font=F(2.4))
            y += h + 1.4 * u
        y = hw_bot + 1.6 * u
        if msgs:  # 公告与留言：一条一行，太长的结尾用省略号
            round_rect(cv, rx, y, R, y + msg_h, 2.4 * u, fill="#3a2f12", width=0)
            f_l, f_t = F(2.2), F(2.5, False)
            yy = y + 4.1 * u
            for label, text in msgs:
                cv.create_text(rx + 2.6 * u, yy, text=label, anchor="w", fill=self.GOLD, font=f_l)
                tx = rx + 2.6 * u + f_l.measure(label) + 1.8 * u
                cv.create_text(tx, yy, text=ellipsis(text, f_t, R - tx - 2.4 * u), anchor="w", fill="#fdf3d0", font=f_t)
                yy += 4.6 * u
            y += msg_h + 1.6 * u
        if stars:
            round_rect(cv, rx, y, R, y + 9.4 * u, 2.4 * u, fill=self.PANEL, width=0)
            cv.create_text(rx + 2.6 * u, y + 4.7 * u, text="本周之星", anchor="w", fill=self.GOLD, font=F(2.4))
            x = rx + 14 * u
            for k, (name, val) in enumerate(stars):
                medal = ("#fbbf24", "#cbd5e1", "#f59e0b88")[k] if k < 3 else "#2a4073"
                f = F(2.6)
                w = f.measure(name) + 9 * u
                if x + w > R - 2 * u:
                    break
                cv.create_oval(x, y + 3 * u, x + 3.4 * u, y + 6.4 * u, fill=medal[:7], width=0)
                cv.create_text(x + 1.7 * u, y + 4.7 * u, text=str(k + 1), fill="#1a2c52", font=F(1.9))
                cv.create_text(x + 4.4 * u, y + 4.7 * u, text=name, anchor="w", fill="#ffffff", font=f)
                cv.create_text(x + 4.6 * u + f.measure(name), y + 4.9 * u, text=f"+{val}", anchor="w", fill=self.GREEN, font=F(2))
                x += w + 1.2 * u
        # ---- 底部：每日一句 ----
        if quote:
            qy = sh - 8.6 * u
            f_b = F(2.3, False)
            by = f"—— {quote['by']}" if quote.get("by") else ""
            for px in range(int(3.4 * u), 13, -2):
                f_q = app.font(px, True)
                if f_q.measure(quote["text"]) + f_b.measure(by) + 6 * u <= R - L:
                    break
            wq, wb = f_q.measure(quote["text"]), f_b.measure(by)
            x0 = (sw - wq - wb - 2 * u) / 2
            cv.create_line(x0 - 6 * u, qy, x0 - 2 * u, qy, fill="#3d5488", width=2)
            cv.create_text(x0, qy, text=quote["text"], anchor="w", fill="#f3e3b5", font=f_q)
            if by:
                cv.create_text(x0 + wq + 2 * u, qy + 0.3 * u, text=by, anchor="w", fill=self.DIM, font=f_b)
            cv.create_line(x0 + wq + wb + 4 * u, qy, x0 + wq + wb + 8 * u, qy, fill="#3d5488", width=2)
        self.foot = cv.create_text(sw / 2, sh - 3 * u, text="自习中，请保持安静　·　轻触屏幕返回", fill="#5f74a0", font=F(1.9, False))
        app.fade_in(self.win)
        self.tick()

    def tick(self):
        cv, u = self.cv, self.u
        now = time.time() + self.app.offset
        t = time.gmtime(now + 8 * 3600)  # 北京时间
        cv.itemconfig(self.clock, text=time.strftime("%H:%M", t))
        cv.itemconfig(self.date, text=f"{t.tm_mon}月{t.tm_mday}日　星期{'一二三四五六日'[t.tm_wday]}　·　{self.app.board.get('class') or CLASS}")
        b = self.app.board or self.board
        sess, p = b.get("session"), b.get("period") or {}
        hm = t.tm_hour * 60 + t.tm_min + t.tm_sec / 60
        mins = lambda s_: int(s_[:2]) * 60 + int(s_[3:5])
        if sess:
            a, e = mins(sess["start"]), mins(sess["end"])
            if sess["label"] != "课间休息":  # 晚自习这节有课（课程表里填了科目）就显示科目
                title = f"{sess['label']}" + (f" · {sess['subject']}" if sess.get("subject") else "") + f"　{sess['start']}–{sess['end']}"
            else:
                title = f"课间休息　{sess['end']} 开始{sess.get('next', '')}" + (f" · {sess['next_subject']}" if sess.get("next_subject") else "")
            left = max(0, e - hm)
            frac = 0 if e <= a else min(1, max(0, (hm - a) / (e - a)))
            pill = "课间休息" if sess["label"] == "课间休息" else "上课中" if sess.get("subject") else "自习中"
        elif p.get("in_class"):
            e = mins(p["end"]); title, left, frac, pill = f"第 {p['period']} 节上课中", max(0, e - hm), None, "上课中"
        else:
            title, left, frac, pill = "课间", None, None, "课间"
        cv.itemconfig(self.pill, text=pill)
        cv.itemconfig(self.foot, text=("请保持安静　·　" if pill == "上课中" else "自习中，请保持安静　·　") + "轻触屏幕返回")
        if self.bar:
            cv.delete(self.bar)
            self.bar = None
        if self.rows:
            nxt = next((r for r in self.rows if hm < mins(r["a"])), None)
            for r in self.rows:
                a, e = mins(r["a"]), mins(r["e"])
                now, done = a <= hm < e, hm >= e
                cv.itemconfig(r["hl"], fill="#24406f" if now else self.PANEL)
                cv.itemconfig(r["t"], fill="#ffffff" if now else mix(self.DIM, self.PANEL, 0.55) if done else self.DIM)
                cv.itemconfig(r["s"], fill=mix(r["color"], self.PANEL, 0.55) if done else r["color"])
                soon = r is nxt and not any(mins(x["a"]) <= hm < mins(x["e"]) for x in self.rows) and a - hm <= 30
                cv.itemconfig(r["r"], text=f"还剩 {int(e - hm + 0.999)} 分钟" if now else f"{int(a - hm + 0.999)} 分钟后开始" if soon else "")
                if now:  # 这一段的进度条，贴在高亮行底部
                    y1, y2 = r["y"]
                    x1, x2 = self.L_ + 2.6 * u, self.mid_ - 4.6 * u
                    w = (x2 - x1) * min(1, max(0, (hm - a) / max(1, e - a)))
                    self.bar = round_rect(cv, x1, y2 - 1.1 * u, x1 + max(w, 1.2 * u), y2 - 0.4 * u, 0.35 * u, fill=self.GREEN, width=0)
        else:
            cv.itemconfig(self.sess, text=title)
            cv.itemconfig(self.left, text=f"还剩 {int(left + 0.999)} 分钟" if left is not None else "")
            x1, y1, x2, y2 = self.bar_box
            if frac is not None and frac > 0.01:
                self.bar = round_rect(cv, x1, y1, x1 + (x2 - x1) * frac, y2, 0.9 * u, fill=self.GREEN, width=0)
        self.win.after(1000, self.tick)

    def close(self):
        try:
            self.win.destroy()
        except tk.TclError:
            pass


class DayStandby:
    """白天版班级看板（浅色）：今天的课程、现在/下节课、今日作业、倒计时"""
    BG1, BG2, CARD, LINE, GREEN, GREEN_SOFT = "#f3f8fc", "#eaf6ef", "#ffffff", "#e3e9f0", "#16a571", "#e7f7f0"

    def __init__(self, app, board):
        self.app, self.board = app, board
        sw, sh = app.sw, app.sh
        self.win = tk.Toplevel(app.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.geometry(f"{sw}x{sh}+0+0")
        float_window(self.win)
        cv = self.cv = tk.Canvas(self.win, width=sw, height=sh, highlightthickness=0, bg=self.BG1)
        cv.pack()
        cv.bind("<Button-1>", lambda e: app.hide_standby())
        u = self.u = sh / 100
        F = self.F = lambda px, b=True: app.font(int(px * u), b)
        gradient(cv, 0, 0, sw, sh, self.BG1, self.BG2, steps=100)
        for x, y, r, c in ((sw * .97, sh * .02, 20 * u, "#dff0ff"), (sw * .02, sh * .98, 16 * u, "#daf3e6")):
            cv.create_oval(x - r, y - r, x + r, y + r, fill=c, width=0)
        self.L, self.R = L, R = sw * 0.05, sw * 0.95
        # 时钟、日期
        self.clock = cv.create_text(L - 0.5 * u, 12.5 * u, text="", anchor="w", fill=INK, font=app.font(int(13 * u), True, digits=True))
        self.date = cv.create_text(L, 22.5 * u, text="", anchor="w", fill=MUTED, font=F(2.5))
        # 右上：现在 / 下节课
        self.hero = (sw * 0.47, 4 * u, R, 25 * u)
        round_rect(cv, *self.hero, 3 * u, fill=self.GREEN, width=0)
        hx = self.hero[0] + 3.4 * u
        self.h_label = cv.create_text(hx, 9 * u, text="", anchor="w", fill="#d6f5e8", font=F(2.4))
        self.h_subj = cv.create_text(hx, 17 * u, text="", anchor="w", fill="#ffffff", font=F(7))
        self.h_right = cv.create_text(R - 3.4 * u, 14.5 * u, text="", anchor="e", fill="#ffffff", font=F(3.4))
        self.h_right2 = cv.create_text(R - 3.4 * u, 20 * u, text="", anchor="e", fill="#d6f5e8", font=F(2.2, False))
        # 今天的课程表（一排）
        self.tiles_top, self.cur_no = 29 * u, None
        cv.create_text(L, self.tiles_top + 1 * u, text="今天的课程", anchor="w", fill=MUTED, font=F(2.2))
        # 下半：今日作业 + 倒计时/公告
        top, quote = self.tiles_top + 22 * u, board.get("quote")
        bottom = sh - (12 if quote else 6) * u
        mid = sw * 0.62
        round_rect(cv, L, top, mid - 1.2 * u, bottom, 3 * u, fill=self.CARD, width=0)
        total = board.get("total_minutes") or 0
        cv.create_text(L + 3 * u, top + 4.6 * u, text="今日作业", anchor="w", fill=INK, font=F(2.9))
        if total:
            cv.create_text(mid - 4 * u, top + 4.6 * u, text=f"共约 {total} 分钟", anchor="e", fill="#0f7a53", font=F(2.3))
        items, y, f_c = board.get("homework") or [], top + 9.5 * u, F(2.5, False)
        if not items:
            cv.create_text((L + mid) / 2, (top + bottom) / 2 + 2 * u, text="今天还没有布置作业", fill=MUTED, font=F(2.6))
        for hw in items:
            line = wrap(hw["content"], f_c, mid - L - 22 * u)[0]
            if y > bottom - 4 * u:
                break
            color = SUBJ_COLOR.get(hw["subject"], self.GREEN)
            round_rect(cv, L + 3 * u, y - 1.7 * u, L + 10 * u, y + 1.7 * u, 1.2 * u, fill=color, width=0)
            cv.create_text(L + 6.5 * u, y, text=hw["subject"], fill="#ffffff", font=F(2))
            cv.create_text(L + 12 * u, y, text=line, anchor="w", fill=INK, font=f_c)
            if hw.get("minutes"):
                cv.create_text(mid - 4 * u, y, text=f"{hw['minutes']}′", anchor="e", fill=MUTED, font=F(2.1))
            y += 5.2 * u
        rx, y = mid + 1.2 * u, top
        for c in (board.get("countdowns") or [])[:2]:
            round_rect(cv, rx, y, R, y + 11 * u, 3 * u, fill=self.CARD, width=0)
            cv.create_text(rx + 3 * u, y + 5.5 * u, text=f"距{c['name']}", anchor="w", fill=MUTED, font=F(2.4))
            cv.create_text(R - 7 * u, y + 5.5 * u, text=str(c["days"]), anchor="e", fill="#ef5a5a", font=app.font(int(6 * u), True, digits=True))
            cv.create_text(R - 3 * u, y + 6.3 * u, text="天", anchor="e", fill=MUTED, font=F(2.2))
            y += 12.4 * u
        msgs = board_msgs(board)
        if msgs and y < bottom - 8 * u:
            round_rect(cv, rx, y, R, bottom, 3 * u, fill="#fff8e6", width=0)
            cv.create_text(rx + 3 * u, y + 3.4 * u, text="公告与留言" if board.get("notes") else "班级公告", anchor="w", fill="#b46d00", font=F(2.2))
            yy, f, f_l = y + 7.4 * u, F(2.2, False), F(2, True)
            for label, text in msgs:
                if yy > bottom - 1.8 * u:
                    break
                lw = f_l.measure(label) + 1.4 * u
                cv.create_text(rx + 3 * u, yy, text=label, anchor="w", fill="#b46d00", font=f_l)
                for k, ln in enumerate(wrap_after(text, f, R - rx - 6 * u - lw, R - rx - 6 * u)):
                    if yy > bottom - 1.8 * u:
                        break
                    cv.create_text(rx + 3 * u + (lw if k == 0 else 0), yy, text=ln, anchor="w", fill=INK, font=f)
                    yy += 3.4 * u
                yy += 0.8 * u
        if quote:
            qy, f_q, f_b = sh - 7.4 * u, F(2.9), F(2.1, False)
            by = f"—— {quote['by']}" if quote.get("by") else ""
            wq, wb = f_q.measure(quote["text"]), f_b.measure(by)
            x0 = (sw - wq - wb - 2 * u) / 2
            cv.create_text(x0, qy, text=quote["text"], anchor="w", fill="#2f4a6b", font=f_q)
            if by:
                cv.create_text(x0 + wq + 2 * u, qy + 0.3 * u, text=by, anchor="w", fill=MUTED, font=f_b)
        cv.create_text(sw / 2, sh - 2.6 * u, text="轻触屏幕返回", fill="#a3afbf", font=F(1.8, False))
        app.fade_in(self.win)
        self.tick()

    def draw_tiles(self, classes, cur_no, hm):
        cv, u, L, R = self.cv, self.u, self.L, self.R
        cv.delete("tiles")
        classes = [c for c in classes if c.get("subject")] or classes
        if not classes:
            return
        n, gap = len(classes), 1 * u
        w, y1 = (R - L - gap * (n - 1)) / n, self.tiles_top + 4 * u
        for k, c in enumerate(classes):
            x1 = L + k * (w + gap)
            now, past = c["no"] == cur_no, c["end"] <= hm
            fill = self.GREEN if now else "#f6f8fb" if past else self.CARD
            round_rect(cv, x1, y1, x1 + w, y1 + 14 * u, 2.2 * u, fill=fill, width=0, tags="tiles")
            ink, sub = ("#ffffff", "#d6f5e8") if now else ("#b3bdca", "#c4ccd6") if past else (INK, MUTED)
            cv.create_text(x1 + w / 2, y1 + 3 * u, text=f"{c['no']}  {c['start']}", fill=sub, font=self.F(1.8, False), tags="tiles")
            subj = c.get("subject") or "—"
            color = "#ffffff" if now else "#c4ccd6" if past else SUBJ_COLOR.get(subj, INK)
            cv.create_text(x1 + w / 2, y1 + 9 * u, text=subj, fill=color, font=self.F(3.6), tags="tiles")

    def tick(self):
        cv = self.cv
        now = time.time() + self.app.offset
        t = time.gmtime(now + 8 * 3600)
        hm = time.strftime("%H:%M", t)
        cv.itemconfig(self.clock, text=hm)
        cv.itemconfig(self.date, text=f"{t.tm_mon}月{t.tm_mday}日　星期{'一二三四五六日'[t.tm_wday]}　·　{self.app.board.get('class') or CLASS}")
        b = self.app.board or self.board
        classes, cur, nxt = b.get("classes_today") or [], None, None
        cur = next((c for c in classes if c["start"] <= hm < c["end"]), None)
        nxt = next((c for c in classes if c["start"] > hm), None)
        mins = lambda s_: int(s_[:2]) * 60 + int(s_[3:5])
        nowm = t.tm_hour * 60 + t.tm_min + t.tm_sec / 60
        if cur and cur.get("subject"):
            cv.itemconfig(self.h_label, text=f"正在上课 · 第 {cur['no']} 节")
            cv.itemconfig(self.h_subj, text=cur["subject"])
            cv.itemconfig(self.h_right, text=f"还剩 {int(mins(cur['end']) - nowm + 0.999)} 分钟")
            cv.itemconfig(self.h_right2, text=f"下节：{nxt['subject']}" if nxt and nxt.get("subject") else "")
        elif nxt and nxt.get("subject"):
            left = int(mins(nxt["start"]) - nowm + 0.999)
            cv.itemconfig(self.h_label, text=f"下节课 · 第 {nxt['no']} 节 · {nxt['start']}")
            cv.itemconfig(self.h_subj, text=nxt["subject"])
            cv.itemconfig(self.h_right, text=f"还有 {left} 分钟" if left <= 90 else nxt["start"])
            cv.itemconfig(self.h_right2, text="请做好课前准备" if left <= 10 else "")
        else:
            cv.itemconfig(self.h_label, text="今天的课已结束" if classes else "今天没有课程安排")
            cv.itemconfig(self.h_subj, text="好好休息")
            cv.itemconfig(self.h_right, text=""); cv.itemconfig(self.h_right2, text="")
        key = (cur["no"] if cur else None, hm)
        if key != self.cur_no:
            self.cur_no = key
            self.draw_tiles(classes, cur["no"] if cur else None, hm)
        self.win.after(1000, self.tick)

    def close(self):
        try:
            self.win.destroy()
        except tk.TclError:
            pass


class MorningBoard:
    """早读页（整屏，暖色晨光）：今天各科的早读内容；早读结束自动收起，碰一下屏幕收起"""
    BG1, BG2, INK2, SOFT, ACCENT = "#fffaf2", "#fdeedd", "#3b2f22", "#a8977f", "#e8902a"

    def __init__(self, app, mo):
        self.app, self.mo = app, mo
        self.key = (mo.get("date"), mo.get("start"), mo.get("end"), tuple((i["subject"], i["content"]) for i in mo.get("items") or []))
        sw, sh = app.sw, app.sh
        self.win = tk.Toplevel(app.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.geometry(f"{sw}x{sh}+0+0")
        float_window(self.win)
        cv = self.cv = tk.Canvas(self.win, width=sw, height=sh, highlightthickness=0, bg=self.BG1)
        cv.pack()
        cv.bind("<Button-1>", lambda e: app.dismiss_morning())
        u = self.u = sh / 100
        F = lambda px, b=True: app.font(int(px * u), b)
        gradient(cv, 0, 0, sw, sh, self.BG1, self.BG2, vertical=True, steps=100)
        cx, cy = sw * 0.9, -sh * 0.04  # 右上角一轮晨光
        for r, c in ((52 * u, "#fff3e2"), (40 * u, "#ffe9cc"), (29 * u, "#ffdcb0"), (19 * u, "#ffcf8f")):
            cv.create_oval(cx - r, cy - r, cx + r, cy + r, fill=c, width=0)
        self.L, self.R = L, R = sw * 0.06, sw * 0.94
        cv.create_text(L, 10.5 * u, text="早读", anchor="w", fill=self.INK2, font=F(7.5))
        tw = F(7.5).measure("早读")
        cv.create_text(L + tw + 2.2 * u, 12 * u, text="每天进步一点点", anchor="w", fill=self.SOFT, font=F(2.4, False))
        self.date = cv.create_text(L, 19 * u, text="", anchor="w", fill=self.SOFT, font=F(2.4, False))
        self.clock = cv.create_text(R, 10.5 * u, text="", anchor="e", fill=self.INK2, font=app.font(int(8 * u), True, digits=True))
        self.left = cv.create_text(R, 19 * u, text="", anchor="e", fill=self.ACCENT, font=F(2.4))
        self.bar = (L, 23.2 * u, R, 24.2 * u)
        round_rect(cv, *self.bar, 0.5 * u, fill="#f3e2ca", width=0)
        self.bar_fill = None
        items = (mo.get("items") or [])[:4]
        top, bottom, gap, n = 29 * u, sh - 8 * u, 2.4 * u, len(items)
        if n <= 2:  # 一两科：并排两大块
            w = (R - L - gap * (n - 1)) / max(1, n)
            boxes = [(L + k * (w + gap), top, L + k * (w + gap) + w, bottom) for k in range(n)]
        else:       # 三四科：一行一科
            h = (bottom - top - gap * (n - 1)) / n
            boxes = [(L, top + k * (h + gap), R, top + k * (h + gap) + h) for k in range(n)]
        for (x1, y1, x2, y2), it in zip(boxes, items):
            color = SUBJ_COLOR.get(it["subject"], self.ACCENT)
            round_rect(cv, x1 + 0.4 * u, y1 + 0.9 * u, x2 + 0.4 * u, y2 + 0.9 * u, 3.2 * u, fill="#f3e3cd", width=0)  # 淡淡的投影
            round_rect(cv, x1, y1, x2, y2, 3.2 * u, fill="#ffffff", width=0)
            row = n >= 3
            pad = 3.4 * u
            f_s = F(2.6 if row else 3)
            sw_ = f_s.measure(it["subject"]) + 3.2 * u
            px, py = x1 + pad, y1 + (y2 - y1) / 2 if row else y1 + pad + 2.2 * u
            round_rect(cv, px, py - 2.2 * u, px + sw_, py + 2.2 * u, 2.2 * u, fill=mix(color, "#ffffff", 0.86), width=0)
            cv.create_text(px + sw_ / 2, py, text=it["subject"], fill=color, font=f_s)
            if row:  # 科目在左，内容在右
                tx1, ty1, tx2, ty2 = px + sw_ + 3 * u, y1 + 1.6 * u, x2 - pad, y2 - 1.6 * u
            else:    # 科目在上，内容在下
                tx1, ty1, tx2, ty2 = x1 + pad, py + 4.6 * u, x2 - pad, y2 - pad
            big = {1: 8, 2: 5.6}.get(n, 4.2)
            size = big
            while True:  # 内容尽量大，但要装得下
                f = F(size)
                lines = wrap_words(it["content"], f, tx2 - tx1)
                lh = size * u * 1.5
                if len(lines) * lh <= ty2 - ty1 or size <= 2.2:
                    break
                size -= 0.2
            y = ty1 + max(0, (ty2 - ty1 - len(lines) * lh) / 2) + lh / 2
            for ln in lines:
                if y > ty2:
                    break
                cv.create_text(tx1, y, text=ln, anchor="w", fill="#2b2118", font=f)
                y += lh
        if not items:
            cv.create_text(sw / 2, (top + bottom) / 2, text="今天还没有布置早读内容", fill=self.SOFT, font=F(3.4))
        cv.create_text(sw / 2, sh - 3.4 * u, text="轻触屏幕收起", fill="#c2b29c", font=F(1.8, False))
        app.fade_in(self.win)
        self.tick()

    def tick(self):
        cv, u = self.cv, self.u
        now = time.time() + self.app.offset
        t = time.gmtime(now + 8 * 3600)
        cv.itemconfig(self.clock, text=time.strftime("%H:%M", t))
        cv.itemconfig(self.date, text=f"{t.tm_mon}月{t.tm_mday}日　星期{'一二三四五六日'[t.tm_wday]}　·　{self.app.board.get('class') or CLASS}")
        mins = lambda s_: int(s_[:2]) * 60 + int(s_[3:5])
        try:
            a, b = mins(self.mo["start"]), mins(self.mo["end"])
            nowm = t.tm_hour * 60 + t.tm_min + t.tm_sec / 60
            left = max(0, int(b - nowm + 0.999))
            cv.itemconfig(self.left, text=f"早读 {self.mo['start']}–{self.mo['end']}　还剩 {left} 分钟")
            frac = min(1, max(0, (nowm - a) / max(1, b - a)))
            x1, y1, x2, y2 = self.bar
            if self.bar_fill:
                cv.delete(self.bar_fill)
            if frac > 0:
                self.bar_fill = round_rect(cv, x1, y1, x1 + max(1.2 * u, (x2 - x1) * frac), y2, 0.5 * u, fill=self.ACCENT, width=0)
        except (KeyError, ValueError):
            pass
        self.win.after(1000, self.tick)

    def close(self):
        try:
            self.win.destroy()
        except tk.TclError:
            pass


def exam_key(ex):
    return (ex["key"], ex["start_ts"], ex["end_ts"], ex["subject"], ex["name"], tuple(ex.get("notice") or []), str(ex.get("next")))


EXAM_SAY = {"start": "考试开始，请开始答题", "warn": "距离考试结束还有十五分钟", "end": "考试结束，请停止作答"}


class ExamBoard:
    """考试大屏（整屏，深蓝）：开考前显示须知和开考倒计时，考试中显示科目和结束倒计时，结束后提示停笔。
    碰一下屏幕右下角出现"收起考试大屏"按钮（误碰不会收起），收起后右下角留一个小条，点它回到考试大屏；
    开考、剩 15 分钟、结束时自动回到大屏并各播报一次。到点自动关闭，或老师在工作台点"提前结束"。"""
    BG, PANEL, LINE_, INK_, SOFT, BLUE, GOLD, RED = "#0f1e2e", "#172c42", "#24425f", "#eaf2fb", "#9fb6cc", "#7cc4ff", "#ffd166", "#ff6b6b"

    def __init__(self, app, ex):
        self.app, self.ex, self.phase = app, ex, None
        self.hidden, self.mini, self.hide_btn = False, None, None
        self.key = exam_key(ex)
        sw, sh = app.sw, app.sh
        self.win = tk.Toplevel(app.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.geometry(f"{sw}x{sh}+0+0")
        float_window(self.win)
        self.cv = tk.Canvas(self.win, width=sw, height=sh, highlightthickness=0, bg=self.BG)
        self.cv.pack()
        self.u = sh / 100
        self.L, self.R = sw * 0.045, sw * 0.955
        self.cv.bind("<Button-1>", self.on_touch)
        self.tick()

    def on_touch(self, e):
        if "hidebtn" in self.cv.gettags("current"):
            return  # 点的就是按钮：交给按钮自己处理
        if self.hide_btn:
            self.cv.after_cancel(self.hide_btn)
        else:
            cv, u, f = self.cv, self.u, self.F(2.8)
            w, h = f.measure("收起考试大屏") + 5 * u, 5 * u
            x2, y2 = self.R, self.app.sh - 1.2 * u
            round_rect(cv, x2 - w, y2 - h, x2, y2, h / 2, fill="#2b5a85", outline=self.BLUE, width=2, tags="hidebtn")
            cv.create_text(x2 - w / 2, y2 - h / 2, text="收起考试大屏", fill="#ffffff", font=f, tags="hidebtn")
            cv.tag_bind("hidebtn", "<Button-1>", lambda ev: self.collapse())
        self.hide_btn = self.cv.after(6000, self.remove_hide_btn)  # 6 秒不点就消失

    def remove_hide_btn(self):
        if self.hide_btn:
            self.cv.after_cancel(self.hide_btn)
            self.hide_btn = None
        self.cv.delete("hidebtn")

    def collapse(self):
        """收起：整屏藏起来，右下角留一个小条（显示倒计时，点它回到考试大屏）"""
        self.remove_hide_btn()
        self.hidden = True
        self.win.withdraw()
        app, sw, sh = self.app, self.app.sw, self.app.sh
        w, h = int(min(620, sw * 0.36)), int(max(70, sh * 0.085))
        self.mini = tk.Toplevel(app.root)
        self.mini.overrideredirect(True)
        self.mini.attributes("-topmost", True)
        self.mini.geometry(f"{w}x{h}+{sw - w - 24}+{sh - h - 70}")
        float_window(self.mini)
        cv = self.mini_cv = tk.Canvas(self.mini, width=w, height=h, bg=self.BG, highlightthickness=0)
        cv.pack()
        cv.create_rectangle(0, 0, w - 1, h - 1, outline=self.BLUE, width=2)
        cv.create_rectangle(0, 0, int(h * 0.09), h, fill=self.GOLD, width=0)
        px = int(h * 0.3)
        title = self.ex["subject"] + (" · 考试中" if self.phase == "during" else "")
        cv.create_text(h * 0.3, h * 0.34, text=ellipsis(title, app.font(px, True), w * 0.5), anchor="w", fill=self.INK_, font=app.font(px, True))
        cv.create_text(h * 0.3, h * 0.74, text="点这里回到考试大屏", anchor="w", fill=self.SOFT, font=app.font(int(px * 0.72)))
        self.mini_count = cv.create_text(w - h * 0.25, h / 2, text="", anchor="e", fill=self.GOLD, font=app.font(int(px * 1.25), True, digits=True))
        cv.bind("<Button-1>", lambda e: self.expand())
        log("考试大屏已收起到右下角")

    def expand(self):
        if not self.hidden:
            return
        self.hidden = False
        if self.mini:
            self.mini.destroy()
            self.mini = None
        self.win.deiconify()
        self.win.geometry(f"{self.app.sw}x{self.app.sh}+0+0")
        self.win.attributes("-topmost", True)
        self.win.lift()
        float_window(self.win)
        for card in self.app.cards.values():  # 紧急消息照常在最前面
            card.lift()
        log("回到考试大屏")

    def F(self, size, bold=True, digits=False):
        return self.app.font(int(size * self.u), bold, digits=digits)

    def phase_of(self, now):
        ex = self.ex
        return "before" if now < ex["start_ts"] else "during" if now < ex["end_ts"] else "after"

    def head(self):
        cv, u, ex = self.cv, self.u, self.ex
        title = f"{ex['name']} · 第 {ex['no']} 场" if ex.get("total", 1) > 1 else ex["name"]
        cv.create_text(self.L, 7 * u, text=title, anchor="w", fill=self.SOFT, font=self.F(3.2))
        self.clock = cv.create_text(self.R, 7 * u, text="", anchor="e", fill=self.INK_, font=self.F(4.4, digits=True))
        cv.create_text(self.R - self.F(4.4, digits=True).measure("00:00") - 1.2 * u, 7.4 * u, text="现在", anchor="e", fill=self.SOFT, font=self.F(2.8, False))

    def next_text(self):
        nx = self.ex.get("next")
        if not nx:
            return None
        day = "" if nx["date"] == self.ex["date"] else f"{int(nx['date'][5:7])}月{int(nx['date'][8:10])}日 "
        return nx["subject"], f"{day}{nx['start']} – {nx['end']}"

    def box(self, x1, y1, x2, y2, r=None):
        round_rect(self.cv, x1, y1, x2, y2, r or 3 * self.u, fill=self.PANEL, outline=self.LINE_, width=2)

    def draw(self, phase):
        cv, u, ex, L, R = self.cv, self.u, self.ex, self.L, self.R
        cv.delete("all")
        self.phase, self.bar_fill = phase, None
        if self.hide_btn:
            cv.after_cancel(self.hide_btn)
            self.hide_btn = None
        self.head()
        mins = int(round((ex["end_ts"] - ex["start_ts"]) / 60))
        if phase == "before":  # 须知 + 开考倒计时
            cv.create_text(L, 21 * u, text=ex["subject"], anchor="w", fill=self.INK_, font=self.F(10))
            sx = L + self.F(10).measure(ex["subject"]) + 3 * u
            cv.create_text(sx, 22.5 * u, text=f"{ex['start']} – {ex['end']}", anchor="w", fill="#cfe0f0", font=self.F(5, False, True))
            bx1 = R - 58 * u
            self.box(bx1, 13 * u, R, 37 * u)
            cv.create_text((bx1 + R) / 2, 18 * u, text="距离开考", fill=self.SOFT, font=self.F(3.4))
            self.count = cv.create_text((bx1 + R) / 2, 28.5 * u, text="", fill=self.BLUE, font=self.F(12, True, True))
            self.box(L, 43 * u, R, 94 * u)
            cv.create_text(L + 4.5 * u, 50 * u, text="考场须知", anchor="w", fill=self.GOLD, font=self.F(4))
            lines = (ex.get("notice") or [])[:6]
            size = 4 if len(lines) <= 4 else 3.4
            for k, ln in enumerate(lines):
                cv.create_text(L + 5 * u, 58 * u + k * size * 1.75 * u, text="·  " + ellipsis(ln, self.F(size, False), R - L - 12 * u),
                               anchor="w", fill=self.INK_, font=self.F(size, False))
        elif phase == "during":  # 科目 + 结束倒计时 + 进度条
            cv.create_text(L, 26 * u, text=ex["subject"], anchor="w", fill=self.INK_, font=self.F(19))
            cv.create_text(L + 0.6 * u, 43 * u, text=f"{ex['start']} – {ex['end']}", anchor="w", fill="#cfe0f0", font=self.F(5.6, False, True))
            tw = self.F(5.6, False, True).measure(f"{ex['start']} – {ex['end']}")
            cv.create_text(L + tw + 2.4 * u, 43.6 * u, text=f"共 {mins} 分钟", anchor="w", fill=self.SOFT, font=self.F(3.4, False))
            bx1 = R - 66 * u
            self.box(bx1, 13 * u, R, 50 * u)
            cv.create_text((bx1 + R) / 2, 19 * u, text="距离考试结束", fill=self.SOFT, font=self.F(3.6))
            self.count = cv.create_text((bx1 + R) / 2, 31 * u, text="", fill=self.GOLD, font=self.F(16, True, True))
            self.status = cv.create_text((bx1 + R) / 2, 44 * u, text="", fill=self.GOLD, font=self.F(3.2))
            self.bar = (L, 57 * u, R, 59 * u)
            round_rect(cv, *self.bar, 1 * u, fill="#1d3550", width=0)
            lines = (ex.get("notice") or [])[:3]
            if lines:
                cv.create_text(L, 66 * u, text="考场提醒", anchor="w", fill=self.SOFT, font=self.F(3.2))
                for k, ln in enumerate(lines):
                    cv.create_text(L + 1 * u, 73 * u + k * 6.2 * u, text="·  " + ellipsis(ln, self.F(3.6, False), R - L - 62 * u),
                                   anchor="w", fill=self.INK_, font=self.F(3.6, False))
            nt = self.next_text()
            if nt:
                nx1 = R - 54 * u
                self.box(nx1, 64 * u, R, 92 * u)
                cv.create_text(nx1 + 3.4 * u, 70 * u, text="下一场", anchor="w", fill=self.SOFT, font=self.F(3))
                cv.create_text(nx1 + 3.4 * u, 78 * u, text=nt[0], anchor="w", fill=self.INK_, font=self.F(4.8))
                cv.create_text(nx1 + 3.4 * u, 86 * u, text=nt[1], anchor="w", fill="#cfe0f0", font=self.F(3.4, False, True))
        else:  # 结束：停笔
            cx = self.app.sw / 2
            cv.create_text(cx, 38 * u, text="考试结束", fill=self.INK_, font=self.F(15))
            cv.create_text(cx, 54 * u, text="请停止作答，等待监考老师收卷", fill=self.GOLD, font=self.F(5))
            nt = self.next_text()
            if nt:
                cv.create_text(cx, 70 * u, text=f"下一场　{nt[0]}　{nt[1]}", fill=self.SOFT, font=self.F(4, False))

    def tick(self):
        if not self.win.winfo_exists():
            return
        now = time.time() + self.app.offset
        ex, u = self.ex, self.u
        phase = self.phase_of(now)
        if phase != self.phase:
            if self.phase and self.hidden:  # 开考、考试结束：自动回到大屏
                self.expand()
            self.draw(phase)
        t = time.gmtime(now + 8 * 3600)
        self.cv.itemconfig(self.clock, text=time.strftime("%H:%M", t))
        if phase in ("before", "during"):
            left = int((ex["start_ts"] if phase == "before" else ex["end_ts"]) - now + 0.999)
            h, rest = divmod(max(0, left), 3600)
            text = f"{h}:{rest // 60:02d}:{rest % 60:02d}" if h else f"{rest // 60:02d}:{rest % 60:02d}"
            last = phase == "during" and left <= 15 * 60
            self.cv.itemconfig(self.count, text=text, fill=self.RED if last else self.GOLD if phase == "during" else self.BLUE)
            if phase == "during":
                self.cv.itemconfig(self.status, text="● 最后 15 分钟" if last else "● 考试进行中", fill=self.RED if last else self.GOLD)
                frac = min(1, max(0, (now - ex["start_ts"]) / max(1, ex["end_ts"] - ex["start_ts"])))
                x1, y1, x2, y2 = self.bar
                if self.bar_fill:
                    self.cv.delete(self.bar_fill)
                self.bar_fill = round_rect(self.cv, x1, y1, x1 + max(2 * u, (x2 - x1) * frac), y2, 1 * u, fill=self.RED if last else self.BLUE, width=0)
            if self.mini:
                self.mini_cv.itemconfig(self.mini_count, text=("剩 " if phase == "during" else "距开考 ") + text,
                                        fill=self.RED if last else self.GOLD if phase == "during" else self.BLUE)
        elif self.mini:
            self.mini_cv.itemconfig(self.mini_count, text="考试结束", fill=self.GOLD)
        self.speak(now)
        self.win.after(1000, self.tick)

    def speak(self, now):
        """开考、剩 15 分钟、结束各播报一次；晚了 90 秒以上（比如刚开机）就不补了"""
        ex = self.ex
        moments = [("start", ex["start_ts"]), ("end", ex["end_ts"])]
        if ex["end_ts"] - ex["start_ts"] > 20 * 60:
            moments.append(("warn", ex["end_ts"] - 15 * 60))
        for name, at in moments:
            k = (ex["key"], name)
            if at <= now < at + 90 and k not in self.app.exam_played:
                self.app.exam_played.add(k)
                self.expand()  # 收起了的话，播报时自动回到大屏
                announce((ex.get("voice") or {}).get(name), speak=EXAM_SAY[name])
                log(f"考试播报：{ex['subject']} {EXAM_SAY[name]}")

    def close(self):
        for w in (self.mini, self.win):
            try:
                if w:
                    w.destroy()
            except tk.TclError:
                pass


# ---------------- 主程序 ----------------
class ToolTab:
    """屏幕右边的「工具」小把手：老师站在一体机前直接点计时、抽号，不用掏手机。放 PPT 时也在最上面"""
    MINUTES = (1, 3, 5, 10, 15, 20)

    def __init__(self, app):
        self.app, self.panel, self.close_job = app, None, None
        sw, sh = app.sw, app.sh
        self.w, self.h = max(30, int(sh * 0.034)), max(84, int(sh * 0.1))
        self.win = tk.Toplevel(app.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.geometry(f"{self.w}x{self.h}+{sw - self.w}+{int(sh * 0.42)}")
        float_window(self.win)
        if app.x11.composite:
            self.win.attributes("-alpha", 0.72)
        cv = tk.Canvas(self.win, width=self.w, height=self.h, bg=INK, highlightthickness=0, cursor="hand2")
        cv.pack()
        cv.create_text(self.w / 2, self.h / 2, text="工\n具", fill="#ffffff", font=app.font(int(self.w * 0.52), True), justify="center")
        cv.bind("<Button-1>", lambda e: self.toggle())

    def lift(self):
        if self.panel and not self.panel.winfo_exists():  # 面板被别的程序关掉了
            self.panel = None
        for w in (self.win, self.panel):
            if w:
                w.attributes("-topmost", True)
                w.lift()

    def toggle(self):
        if self.panel:
            return self.close_panel()
        app, sh = self.app, self.app.sh
        u = sh / 100
        pw, ph = int(26 * u), int(36 * u)
        self.panel = tk.Toplevel(app.root)
        self.panel.overrideredirect(True)
        self.panel.attributes("-topmost", True)
        self.panel.geometry(f"{pw}x{ph}+{app.sw - self.w - pw}+{int(sh * 0.42 + self.h / 2 - ph / 2)}")
        float_window(self.panel)
        cv = tk.Canvas(self.panel, width=pw, height=ph, bg="#ffffff", highlightthickness=0)
        cv.pack()
        cv.create_rectangle(0, 0, pw - 1, ph - 1, outline=LINE)
        cv.create_text(2.4 * u, 3.6 * u, text="计时（分钟）", anchor="w", fill=MUTED, font=app.font(int(1.8 * u), True))
        gap, bw, bh = 1.2 * u, (pw - 4.8 * u - 2.4 * u) / 3, 6.4 * u
        for k, m in enumerate(self.MINUTES):
            x, y = 2.4 * u + (k % 3) * (bw + gap), 6.2 * u + (k // 3) * (bh + gap)
            tag = f"m{m}"
            round_rect(cv, x, y, x + bw, y + bh, 1.4 * u, fill="#f4f7fb", outline=LINE, width=2, tags=tag)
            cv.create_text(x + bw / 2, y + bh / 2, text=str(m), fill=INK, font=app.font(int(3 * u), True, digits=True), tags=tag)
            cv.tag_bind(tag, "<Button-1>", lambda e, m=m: self.use({"tool": "timer", "seconds": m * 60}))
        y = 6.2 * u + 2 * (bh + gap) + 1.2 * u
        round_rect(cv, 2.4 * u, y, pw - 2.4 * u, y + 7 * u, 1.6 * u, fill="#8b7cf6", width=0, tags="pick")
        cv.create_text(pw / 2, y + 3.5 * u, text="随机抽一位", fill="#ffffff", font=app.font(int(2.6 * u), True), tags="pick")
        cv.tag_bind("pick", "<Button-1>", lambda e: self.use({"tool": "pick"}))
        cv.create_text(pw / 2, ph - 3 * u, text="收起", fill=MUTED, font=app.font(int(2 * u), True), tags="close")
        cv.tag_bind("close", "<Button-1>", lambda e: self.close_panel())
        self.close_job = self.panel.after(10000, self.close_panel)  # 10 秒不点自动收起

    def close_panel(self):
        if self.panel:
            try:
                self.panel.destroy()
            except tk.TclError:
                pass
        self.panel = None

    def use(self, body):
        self.close_panel()
        app = self.app

        def run():
            try:
                req = urllib.request.Request(TOOL_URL, data=json.dumps({**body, "class": CLASS}).encode(),
                                             headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=8).read()
                app.inbox.put(("poll", fetch_json(POLL_URL)))  # 不等下一轮，马上把计时 / 抽号拉回来显示
            except urllib.error.HTTPError as e:
                try:
                    msg = json.loads(e.read().decode("utf-8")).get("error") or str(e)
                except Exception:
                    msg = str(e)
                app.inbox.put(("tip", msg))
            except Exception as e:
                log(f"大屏工具失败：{e}")
                app.inbox.put(("tip", "没连上服务器，请稍后再试"))
        threading.Thread(target=run, daemon=True).start()

    def destroy(self):
        self.close_panel()
        try:
            self.win.destroy()
        except tk.TclError:
            pass


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.sw, self.sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.x11 = X11()
        self.inbox = queue.Queue()
        self.cards = {}  # 消息 id -> 卡片
        self.offset = 0.0  # 服务器时间 - 本机时间
        self.in_class = False
        self.board = {}
        self.standby = None
        self.standby_forced = False
        self.standby_at = 0
        self.morning = None  # 早读页
        self.tooltab = None  # 屏幕右边「工具」小把手
        self.shooting = False  # 正在截图、上传（要一两秒，别重复截）
        self.morning_dismissed = None
        self.exam, self.exam_played, self.exam_rev, self.awake_at = None, set(), None, 0  # 考试大屏、已播报过的提示、考试安排的版本
        self.handled = set()
        self.pending_restart = False
        self.fonts = {}
        fams = set(tkfont.families())
        fallback = next((f for f in ("Noto Sans CJK SC", "Source Han Sans SC", "WenQuanYi Micro Hei", "Droid Sans Fallback",
                                     "Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑")
                         if f in fams), "TkDefaultFont")
        self.families = fams
        self.family_fallback = fallback
        self.family = "MiSans" if "MiSans" in fams else fallback
        self.font_installing = set()
        if "--restarted" not in sys.argv:  # 自动更新、装字体后的重启不再提示
            hello = MessageCard(self, {"id": 0, "text": f"{CLASS} 接收端已启动", "sender": "班级助手", "time": time.strftime("%H:%M"), "popup": False})
            hello.place(0)
            self.root.after(4000, hello.close)
        threading.Thread(target=ensure_desktop_icon, daemon=True).start()
        threading.Thread(target=clean_files, daemon=True).start()
        for target in (self.poll_loop, self.board_loop, self.update_loop):
            threading.Thread(target=target, daemon=True).start()
        self.root.after(200, self.process)
        self.root.after(3000, self.check_standby)
        log(f"接收端 v2 启动：班级={CLASS} 服务器={SERVER} 字体={self.family} 声音={'系统自带（winmm）' if WIN else 'ffplay' if FFPLAY else os.path.basename(APLAY) + '（只能放 wav）' if APLAY else '无'} "
            f"空闲检测={'可用' if self.x11.ok else '不可用'} 窗口特效={'有' if self.x11.composite else '无'}")

    def font(self, px, bold=False, digits=False):
        key = (px, bold, self.family)
        if key not in self.fonts:
            self.fonts[key] = tkfont.Font(family=self.family, size=-max(8, int(px)), weight="bold" if bold else "normal")
        return self.fonts[key]

    def fade_in(self, win):
        if not self.x11.composite:
            return
        try:
            win.attributes("-alpha", 0.0)
        except tk.TclError:
            return

        def step(a):
            try:
                win.attributes("-alpha", a)
                if a < 1:
                    win.after(16, lambda: step(min(1.0, a + 0.12)))
            except tk.TclError:
                pass
        step(0.1)

    def make_backdrop(self):
        if not self.x11.composite:
            return None  # 没有窗口特效时半透明会变成纯黑，干脆不加遮罩
        w = tk.Toplevel(self.root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.geometry(f"{self.sw}x{self.sh}+0+0")
        float_window(w)
        w.configure(bg="#0f1a2e")
        try:
            w.attributes("-alpha", 0.32)
        except tk.TclError:
            w.destroy()
            return None
        return w

    # ---------- 后台线程 ----------
    def poll_loop(self):
        fails = 0
        while True:
            try:
                data = fetch_json(POLL_URL)
                if fails:
                    log("已重新连接服务器")
                fails = 0
                self.inbox.put(("poll", data))
            except Exception as e:
                fails += 1
                if fails == 1 or fails % 30 == 0:
                    log(f"连接服务器失败（第 {fails} 次）：{e}")
            time.sleep(2)

    def fetch_board_now(self):
        try:
            self.inbox.put(("board", fetch_json(BOARD_URL)))
        except Exception as e:
            log(f"拉取看板失败：{e}")

    def board_loop(self):
        while True:
            try:
                self.inbox.put(("board", fetch_json(BOARD_URL)))
            except Exception:
                pass
            time.sleep(60)

    def update_loop(self):
        """自动更新：服务器上的接收程序变了，就下载新版并重启"""
        time.sleep(20)
        while True:
            try:
                remote = fetch_json(f"{SERVER}/kylin/version")["version"]
                with open(SELF, "rb") as f:
                    local = hashlib.sha256(f.read()).hexdigest()[:16]
                if remote != local:
                    with urllib.request.urlopen(f"{SERVER}/kylin/receiver.py", timeout=30) as r:
                        code = r.read()
                    if hashlib.sha256(code).hexdigest()[:16] == remote:
                        compile(code, SELF, "exec")  # 确认新版本没有语法错误
                        with open(SELF + ".new", "wb") as f:
                            f.write(code)
                        os.replace(SELF + ".new", SELF)
                        log(f"已下载新版本 {remote}，空闲时自动重启")
                        self.pending_restart = True
            except Exception as e:
                log(f"检查更新失败：{e}")
            time.sleep(600)

    def install_font(self, key):
        """把工作台选的大屏字体装到本机（每款只需一次），装好后重启以生效"""
        names, files = SCREEN_FONTS[key]
        name, fetched = names[0], False
        try:
            os.makedirs(FONT_DIR, exist_ok=True)
            for fn in files:
                path = os.path.join(FONT_DIR, fn)
                if os.path.exists(path):
                    continue
                fetched = True
                with urllib.request.urlopen(f"{SERVER}/fonts/ttf/{fn}", timeout=600) as r:
                    data = r.read()
                if len(data) < 500_000:
                    raise ValueError(f"{fn} 下载不完整")
                with open(path + ".tmp", "wb") as f:
                    f.write(data)
                os.replace(path + ".tmp", path)
            if not WIN and shutil.which("fc-cache"):
                subprocess.run(["fc-cache", "-f", FONT_DIR], timeout=120)
            if not fetched:  # 文件早就在了还是认不出来：别再重启，不然会每隔几秒重启一次
                log(f"字体文件已在本机，但系统里找不到「{name}」，先用默认字体")
                return
            log(f"已安装字体：{name}")
            self.pending_restart = True
        except Exception as e:
            log(f"安装字体失败：{e}")
            self.font_installing.discard(key)

    # ---------- 主线程 ----------
    def process(self):
        try:
            while True:
                kind, data = self.inbox.get_nowait()
                if kind == "poll":
                    try:
                        self.apply(data)
                    except Exception as e:  # 出错也不能停：否则之后的消息都不显示了
                        log(f"处理消息出错：{e}")
                elif kind == "tip":  # 大屏工具出错（如没有名单）：角落小提示几秒
                    tip = MessageCard(self, {"id": 0, "text": data, "sender": "班级助手", "time": time.strftime("%H:%M"), "popup": False})
                    tip.place(0)
                    self.root.after(5000, tip.close)
                else:
                    self.board = data
                    key = data.get("screen_font") or "misans"
                    want = self.family_fallback
                    if key in SCREEN_FONTS:
                        found = [n for n in SCREEN_FONTS[key][0] if n in self.families]
                        if found:
                            want = found[0]
                        elif key not in self.font_installing:
                            self.font_installing.add(key)
                            threading.Thread(target=self.install_font, args=(key,), daemon=True).start()
                    if want != self.family:
                        self.family, self.fonts = want, {}
        except queue.Empty:
            pass
        if self.pending_restart and not self.cards and not self.exam:  # 考试时不重启
            log("重启以加载新版本 / 新字体")
            if WIN:  # Windows 的 execv 是另起一个进程：先放开"只运行一个"的锁，再启动新进程、退出自己
                MUTEX[1].CloseHandle(MUTEX[0])
                subprocess.Popen([sys.executable, SELF, "--restarted"], cwd=HERE, creationflags=0x08000000)
                os._exit(0)
            os.execv(sys.executable, [sys.executable, SELF, "--restarted"])
        self.root.after(200, self.process)

    def apply(self, data):
        if data.get("server_time"):
            self.offset = data["server_time"] - time.time()
        rev = data.get("exam_rev")
        if rev and rev != self.exam_rev:  # 老师改了考试安排（或提前结束）：马上重新拉看板
            if self.exam_rev is not None:
                threading.Thread(target=self.fetch_board_now, daemon=True).start()
            self.exam_rev = rev
        self.in_class = bool(data.get("in_class"))
        if data.get("shot") and not self.shooting:  # 班主任在工作台点了"看一眼大屏"
            self.shooting = True

            def run():
                try:
                    send_shot(self.sw, self.sh)
                finally:
                    self.shooting = False
            threading.Thread(target=run, daemon=True).start()
        active = set()
        for m in data.get("messages") or []:
            if m.get("kind") == "uninstall":  # 班主任在工作台点了"远程卸载"
                uninstall_self()
            if m.get("kind") == "standby":  # 老师在工作台点了"在大屏显示看板"
                if m["id"] not in self.handled:
                    self.handled.add(m["id"])
                    send_ack(m["id"])
                    self.show_standby(forced=True)
                continue
            active.add(m["id"])
            if m["id"] not in self.cards:
                self.show(m)
        for mid in list(self.cards):
            card = self.cards[mid]
            if mid not in active and not (isinstance(card, TimerCard) and card.ended):
                self.close(mid)
        for card in self.cards.values():  # 保持在最前面（例如正在放 PPT）
            card.lift()

    def show(self, m):
        self.hide_standby()
        kind = m.get("kind", "call")
        if kind == "prep" and m.get("popup") and self.x11.ok and self.x11.idle_seconds() < 60:
            m = {**m, "popup": False, "voice": False}  # 老师还在用大屏（可能拖堂）：只在角落小提示
        try:
            card = {"timer": TimerCard, "pick": PickCard, "collect": CollectCard, "attend": CollectCard, "image": ImageCard, "file": FileCard}.get(kind, MessageCard)(self, m)
        except Exception as e:
            log(f"显示消息失败：{e}")
            return
        self.cards[m["id"]] = card
        self.relayout()
        if m.get("voice") and kind not in ("timer", "pick"):
            announce(m.get("audio"), speak=m.get("speak"))
        log(f"收到消息 #{m['id']}（{kind}）：{m['text']}")

    def relayout(self):
        slot = 0
        for card in self.cards.values():
            if not card.popup:
                card.place(slot)
                slot += 1

    def confirm(self, mid):
        if not mid:
            return
        self.close(mid)
        send_ack(mid)

    def close(self, mid):
        card = self.cards.pop(mid, None)
        if card:
            card.close()
            self.relayout()

    # ---------- 待机页 ----------
    def morning_state(self):
        """现在是不是早读时间、今天有没有早读内容（按服务器时间算）"""
        mo = self.board.get("morning") or {}
        t = time.gmtime(time.time() + self.offset + 8 * 3600)
        active = bool(mo.get("items")) and mo.get("date") == time.strftime("%Y-%m-%d", t) \
            and mo.get("start", "99") <= time.strftime("%H:%M", t) < mo.get("end", "00")
        return mo, active

    def check_morning(self):
        mo, active = self.morning_state()
        key = (mo.get("date"), mo.get("start"))
        if self.morning:
            if not active:
                self.hide_morning()
            elif self.morning.key[3] != tuple((i["subject"], i["content"]) for i in mo.get("items") or []):
                self.hide_morning()  # 老师刚改了早读内容：重画
                self.morning = MorningBoard(self, mo)
        elif active and self.morning_dismissed != key and not self.cards:
            self.hide_standby()
            self.morning = MorningBoard(self, mo)
            log("显示早读页")

    def dismiss_morning(self):
        mo, _ = self.morning_state()
        self.morning_dismissed = (mo.get("date"), mo.get("start"))  # 碰过一次，今天的早读就不再弹出
        self.hide_morning()

    def hide_morning(self):
        if self.morning:
            self.morning.close()
            self.morning = None

    def check_exam(self):
        """按服务器时间找现在要显示的那场考试（开考前 15 分钟到结束后 5 分钟）"""
        now = time.time() + self.offset
        ex = next((x for x in self.board.get("exams") or [] if x["show_from"] <= now < x["hide_at"]), None)
        if self.exam and (not ex or self.exam.key != exam_key(ex)):  # 收起，或者老师改了这场考试：重画
            self.exam.close()
            self.exam = None
            if not ex:
                log("考试大屏收起")
                self.x11.allow_sleep()
        if ex and not self.exam:
            self.hide_morning()
            self.hide_standby()
            self.exam = ExamBoard(self, ex)
            for card in self.cards.values():  # 紧急消息照常显示在最前面
                card.lift()
            log(f"显示考试大屏：{ex['subject']} {ex['start']}–{ex['end']}")

    def sync_tooltab(self):
        """有考试大屏、早读页、看板时不显示；工作台可以关掉"""
        want = self.board.get("tools_tab", True) and not self.exam and not self.morning and not self.standby
        if self.tooltab and not self.tooltab.win.winfo_exists():  # 把手被别的程序关掉了（如一体机"一键清屏"）：下面重新放一个
            self.tooltab.destroy()
            self.tooltab = None
        if want and not self.tooltab:
            self.tooltab = ToolTab(self)
        elif not want and self.tooltab:
            self.tooltab.destroy()
            self.tooltab = None
        elif self.tooltab:
            self.tooltab.lift()  # 保持在最前面（例如正在放 PPT）

    def check_standby(self):
        try:
            self.sync_tooltab()
        except Exception as e:
            log(f"工具把手出错：{e}")
        try:
            self.check_exam()
        except Exception as e:
            log(f"考试大屏出错：{e}")
        if self.exam:  # 考试时不显示早读页和看板，也不让系统屏保出来
            if time.time() - self.awake_at > 30:
                self.awake_at = time.time()
                self.x11.keep_awake()
            self.root.after(3000, self.check_standby)
            return
        try:
            self.check_morning()
        except Exception as e:
            log(f"早读页出错：{e}")
        try:
            cfg = self.board.get("standby") or {}
            idle = self.x11.idle_seconds()
            window = self.board.get("standby_window", not self.in_class)  # 服务器按作息算好：默认只在晚自习
            if os.path.exists(SHOW_FLAG):  # 双击了桌面上的"班级看板"
                os.remove(SHOW_FLAG)
                self.show_standby(forced=True)
            elif self.standby:
                shown_for = time.time() - self.standby_at
                touched = self.x11.ok and idle < max(2, shown_for - 3)  # 显示之后有人碰过屏幕
                if (touched and shown_for > 4) or (not window and not self.standby_forced):
                    self.hide_standby()
                elif board_sig(self.board) != self.standby_sig:  # 显示着的时候老师改了作业、公告、时间安排：重画
                    self.show_standby(forced=self.standby_forced)
            elif (cfg.get("enabled") and self.x11.ok and not self.cards and window and not self.morning
                  and idle >= max(1, cfg.get("idle_minutes", 5)) * 60):
                self.show_standby()
        except Exception as e:
            log(f"待机页出错：{e}")
        self.root.after(3000, self.check_standby)

    def show_standby(self, forced=False):
        if self.standby:
            self.standby.close()
        self.standby = (Standby if self.board.get("evening") else DayStandby)(self, self.board)  # 晚自习夜空版，白天浅色版
        self.standby_forced, self.standby_at = forced, time.time()
        self.standby_sig = board_sig(self.board)
        log("显示看板" + ("（手动）" if forced else ""))

    def hide_standby(self):
        if self.standby:
            self.standby.close()
            self.standby = None


MUTEX = [None, None]  # Windows："只运行一个"的互斥锁句柄、kernel32


def win_setup():
    """Windows：只运行一个、高分屏不模糊、载入下载好的大屏字体（只给本程序用）"""
    k32 = MUTEX[1] = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    for _ in range(20):  # 刚重启时旧进程可能还没退出，等一会儿
        MUTEX[0] = k32.CreateMutexW(None, False, "Local\\ClassCallReceiver")
        if ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
            break
        k32.CloseHandle(MUTEX[0])
        if "--restarted" not in sys.argv:
            sys.exit(0)  # 已经在运行
        time.sleep(0.5)
    else:
        sys.exit(0)
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    if os.path.isdir(FONT_DIR):
        for fn in os.listdir(FONT_DIR):
            if fn.lower().endswith((".ttf", ".otf")):
                ctypes.windll.gdi32.AddFontResourceExW(os.path.join(FONT_DIR, fn), 0x10, None)  # FR_PRIVATE


def main():
    if "--show-board" in sys.argv:  # Windows 桌面"班级看板"图标：通知正在运行的接收端显示看板
        open(SHOW_FLAG, "w").close()
        return
    if WIN:
        win_setup()
    else:
        lock = open(f"/tmp/classcall-{os.getuid()}.lock", "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            sys.exit(0)  # 已经在运行
    try:
        App().root.mainloop()
    except Exception:  # Windows 上用 pythonw 在后台运行，报错看不到：写进日志
        import traceback
        log("接收端出错退出：\n" + traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
