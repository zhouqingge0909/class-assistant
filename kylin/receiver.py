#!/usr/bin/env python3
"""班级助手 - 教室接收端 v2（银河麒麟 / Linux 一体机，也支持 Windows 一体机）

只用 Python 自带的库：tkinter 画界面，ctypes 读取"多久没人碰屏幕"；声音在麒麟上用 ffplay，在 Windows 上用系统自带的 winmm。
功能：清新风格弹窗、全屏计时器、随机抽号动画、空闲待机页、自动更新。
配置写在同目录的 config.txt 里；麒麟由 install.sh、Windows 由 install.ps1 安装并设置开机自启。
同一个文件两种系统通用（自动更新也是同一个文件），系统相关的地方都用 WIN 分开写。
"""
import ctypes
import ctypes.util
from collections import deque
import hashlib
import json
import os
import queue
import random
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
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
import urllib.error
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
AUTO_MODE = (CFG.get("mode", "").lower() == "auto") or not (CFG.get("class") and CFG.get("key"))
CLASS = ""
KEY = ""
BOUND_MAC = ""
Q_CLASS = Q_KEY = ""
POLL_URL = BOARD_URL = ACK_URL = TOOL_URL = COLLECT_URL = ATTEND_URL = EXAM_STATUS_URL = ""
SHOW_FLAG = os.path.join(HERE, "show-board")  # 桌面"班级看板"图标会创建这个文件
EXAM_AUDIO_DIR = os.path.join(HERE, "exam_audio")  # 考试与平时听力本地缓存目录（考完 7 天自动清理）
EXAM_AUDIO_INDEX = os.path.join(HERE, "exam_audio_index.json")
FFPLAY = None if WIN else shutil.which("ffplay")
APLAY = None if WIN or FFPLAY else (shutil.which("paplay") or shutil.which("aplay"))  # 没有 ffplay 时用系统自带的放 wav（局域网版的语音和叮咚都是 wav）
audio_lock = threading.Lock()


def audio_cap_level():
    """本机听力播放能力：2=完整支持 MP3/WAV 与暂停续播；1=仅 aplay/paplay（仅 WAV、不支持断点暂停）；0=无播放器"""
    return 2 if (WIN or FFPLAY) else (1 if APLAY else 0)


def set_binding(cls, key, mac=""):
    """更新当前绑定的班级、教室密钥和所有请求地址（单班模式启动设一次；桌面云模式自动绑定/解绑时切换）"""
    global CLASS, KEY, BOUND_MAC, Q_CLASS, Q_KEY, POLL_URL, BOARD_URL, ACK_URL, TOOL_URL, COLLECT_URL, ATTEND_URL, EXAM_STATUS_URL
    CLASS, KEY, BOUND_MAC = cls or "", key or "", mac or ""
    if CLASS and KEY:
        Q_CLASS, Q_KEY = urllib.parse.quote(CLASS), urllib.parse.quote(KEY)
        mac_q = f"&mac={urllib.parse.quote(BOUND_MAC)}" if BOUND_MAC else ""
        POLL_URL = f"{SERVER}/api/poll?class={Q_CLASS}&key={Q_KEY}&rv=6&acap={audio_cap_level()}{mac_q}"  # rv：3 卸载，4 文件，5 听力，6 可靠 ack
        BOARD_URL = f"{SERVER}/api/board?class={Q_CLASS}&key={Q_KEY}&fv=2"  # fv：认得中文字体名的新版
        ACK_URL = f"{SERVER}/api/ack?key={Q_KEY}"
        TOOL_URL = f"{SERVER}/api/screen/tool?key={Q_KEY}"  # 屏幕右边「工具」：在大屏上直接开计时、抽号
        COLLECT_URL = f"{SERVER}/collect?class={Q_CLASS}&key={Q_KEY}"
        ATTEND_URL = f"{SERVER}/attend?class={Q_CLASS}&key={Q_KEY}"
        EXAM_STATUS_URL = f"{SERVER}/api/exams/audio/status?key={Q_KEY}"
    else:
        Q_CLASS = Q_KEY = POLL_URL = BOARD_URL = ACK_URL = TOOL_URL = COLLECT_URL = ATTEND_URL = EXAM_STATUS_URL = ""


if not AUTO_MODE:
    set_binding(CFG.get("class", ""), CFG.get("key", ""))


def fetch_json(url, timeout=8):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def post_json(url, payload, timeout=8):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:
            body = {"error": f"服务器返回 {e.code}"}
        return e.code, body


# ---------------- 本机网卡 MAC 获取（桌面云 / 自动还原环境按硬件识别教室） ----------------
VIRTUAL_NIC_KW = (
    "virtual", "vmware", "vbox", "hyper-v", "tap-", "tap ", "tun", "vpn", "loopback",
    "bluetooth", "teredo", "isatap", "pseudo", "wan miniport", "wi-fi direct", "npcap",
    "docker", "veth", "virbr", "tailscale", "wireguard", "zerotier", "hamachi", "sangfor", "easyconnect",
)


def norm_mac(mac):
    """规范为 AA:BB:CC:DD:EE:FF；过滤全零、广播、组播（含 uuid 随机假地址）"""
    s = re.sub(r"[^0-9A-Fa-f]", "", str(mac or "")).upper()
    if len(s) != 12 or s in ("000000000000", "FFFFFFFFFFFF") or (int(s[:2], 16) & 1):
        return ""
    return ":".join(s[i:i + 2] for i in range(0, 12, 2))


def is_local_admin_mac(mac):
    return bool(mac) and bool(int(mac[:2], 16) & 2)


def is_virtual_desc(desc):
    low = str(desc or "").lower()
    return any(k in low for k in VIRTUAL_NIC_KW)


def route_local_ip(server_url):
    """找出本机连向服务器用的那块网卡 IP，用来优先选出真正联网的物理网卡"""
    try:
        u = urllib.parse.urlparse(server_url)
        host = u.hostname or ""
        port = u.port or (443 if u.scheme == "https" else 80)
        if not host:
            return ""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((host, port))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return ""


def linux_macs(local_ip="", net_dir="/sys/class/net", ip_out=None):
    """读取 Linux（麒麟/统信）物理网卡 MAC：优先连向服务器的网卡，其次物理有线网卡，最后物理无线网卡"""
    if not os.path.isdir(net_dir):
        return []
    iface_ips = {}
    if ip_out is None and shutil.which("ip"):
        try:
            r = subprocess.run(["ip", "-o", "-4", "addr", "show"], capture_output=True, text=True, timeout=5)
            ip_out = r.stdout if r.returncode == 0 else ""
        except Exception:
            ip_out = ""
    for line in (ip_out or "").splitlines():
        m = re.match(r"\s*\d+:\s+([^\s:]+)\s+inet\s+([0-9.]+)/", line)
        if m:
            iface_ips.setdefault(m.group(1), set()).add(m.group(2))
    cands = []
    for name in sorted(os.listdir(net_dir)):
        if name == "lo" or is_virtual_desc(name):
            continue
        p = os.path.join(net_dir, name)
        try:
            with open(os.path.join(p, "address"), encoding="utf-8") as f:
                mac = norm_mac(f.read().strip())
        except OSError:
            continue
        if not mac:
            continue
        try:
            with open(os.path.join(p, "type"), encoding="utf-8") as f:
                if f.read().strip() != "1":  # 1 = ARPHRD_ETHER，只认以太网/Wi-Fi
                    continue
        except OSError:
            pass
        physical = os.path.exists(os.path.join(p, "device"))
        wired = not (os.path.isdir(os.path.join(p, "wireless")) or name.startswith(("wl", "wlan")))
        try:
            with open(os.path.join(p, "operstate"), encoding="utf-8") as f:
                up = f.read().strip().lower() == "up"
        except OSError:
            up = False
        has_ip = bool(local_ip) and local_ip in iface_ips.get(name, ())
        rank = (0 if has_ip else 1, 0 if physical else 1, 0 if wired else 1,
                0 if up else 1, 0 if not is_local_admin_mac(mac) else 1, name)
        cands.append((rank, mac))
    cands.sort(key=lambda x: x[0])
    out = []
    for _, m in cands:
        if m not in out:
            out.append(m)
    return out[:8]


WIN_MAC_PS = (
    "Get-WmiObject Win32_NetworkAdapter -Filter 'MACAddress IS NOT NULL' | ForEach-Object {"
    "$c=Get-WmiObject Win32_NetworkAdapterConfiguration -Filter ('Index=' + $_.Index);"
    "$ips=if($c -and $c.IPAddress){$c.IPAddress -join ','}else{''};"
    "\"$($_.PhysicalAdapter)|$($_.NetConnectionStatus)|$($_.MACAddress)|$ips|$($_.Description)\"}"
)


def win_macs(local_ip="", wmi_out=None, getmac_out=None):
    """读取 Windows（Win7/10/11 均支持）物理网卡 MAC，排除虚拟网卡和 VPN"""
    if wmi_out is None:
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-Command", WIN_MAC_PS],
                               capture_output=True, text=True, timeout=15, creationflags=0x08000000)
            wmi_out = r.stdout if r.returncode == 0 else ""
        except Exception:
            wmi_out = ""
    cands = []
    for line in (wmi_out or "").splitlines():
        parts = line.split("|", 4)
        if len(parts) < 5:
            continue
        phys_s, status_s, mac_s, ips_s, desc = parts
        mac = norm_mac(mac_s)
        if not mac or is_virtual_desc(desc):
            continue
        physical = phys_s.strip().lower() == "true"
        up = status_s.strip() == "2"
        ips = {x.strip() for x in ips_s.split(",") if x.strip()}
        wifi = any(k in desc.lower() for k in ("wi-fi", "wifi", "wireless", "wlan", "无线"))
        has_ip = bool(local_ip) and local_ip in ips
        rank = (0 if has_ip else 1, 0 if physical else 1, 0 if not wifi else 1,
                0 if up else 1, 0 if not is_local_admin_mac(mac) else 1, mac)
        cands.append((rank, mac))
    if not cands:
        if getmac_out is None and shutil.which("getmac"):
            try:
                r = subprocess.run(["getmac", "/fo", "csv", "/nh"], capture_output=True, text=True,
                                   timeout=10, creationflags=0x08000000)
                getmac_out = r.stdout if r.returncode == 0 else ""
            except Exception:
                getmac_out = ""
        for line in (getmac_out or "").splitlines():
            m = re.search(r"([0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5})", line)
            mac = norm_mac(m.group(1)) if m else ""
            if mac:
                cands.append(((2, 1, 1, 1, 0 if not is_local_admin_mac(mac) else 1, mac), mac))
    cands.sort(key=lambda x: x[0])
    out = []
    for _, m in cands:
        if m not in out:
            out.append(m)
    return out[:8]


def get_machine_info(server_url=None):
    """返回 (主物理 MAC, 全部物理 MAC 列表, 主机名)"""
    local_ip = route_local_ip(server_url or SERVER)
    macs = win_macs(local_ip) if WIN else linux_macs(local_ip)
    if not macs:
        node = uuid.getnode()
        if not (node & (1 << 40)):  # 非随机生成的有效 MAC
            m = norm_mac("%012x" % node)
            if m:
                macs = [m]
    try:
        host = socket.gethostname()[:40]
    except Exception:
        host = ""
    return (macs[0] if macs else ""), macs, host


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


def send_ack_sync(msg_id, timeout=3, retries=2, ack_url=None):
    """同步可靠回执（用于远程卸载前确认服务端已真正记录，支持幂等重试与撤销识别）
    返回："acked"（确认可执行卸载）、"cancelled"（已撤销/过期/不存在，放弃卸载）、"failed"（网络失败，保留接收端重试）"""
    url = ack_url if ack_url is not None else ACK_URL
    if not url or not msg_id:
        return "failed"
    payload = json.dumps({"id": msg_id}).encode()
    for i in range(max(1, int(retries))):
        try:
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8", "replace")
            try:
                j = json.loads(raw) if raw.strip() else {}
            except ValueError:
                j = {}
            if j.get("cancelled") or j.get("status") in ("cancelled", "expired", "not_found"):
                return "cancelled"
            if j.get("ok"):
                return "acked"
        except urllib.error.HTTPError as e:
            if e.code == 403:
                log(f"同步确认被拒绝（403）：{e}")
                return "cancelled"
            log(f"同步确认失败（HTTP {e.code}）：{e}")
        except Exception as e:
            log(f"同步确认失败：{e}")
        if i + 1 < retries:
            time.sleep(0.3)
    return "failed"


def fmt_mmss(sec):
    s = max(0, int(round(float(sec or 0))))
    return f"{s // 60:02d}:{s % 60:02d}"


_STATUS_Q = queue.Queue(maxsize=64)
_STATUS_STARTED = [False]
_STATUS_LOCK = threading.Lock()


def _status_worker():
    while True:
        url, body = _STATUS_Q.get()
        try:
            post_json(url, body, timeout=8)
        except Exception:
            pass


def report_exam_audio_status(payload, url=None, cls=None):
    """单线程串行异步上报听力缓存进度或播控状态（保证顺序不覆盖，网络不通静默忽略，绝不卡界面）"""
    url = url if url is not None else EXAM_STATUS_URL
    cls = cls if cls is not None else CLASS
    if not url or not cls:
        return
    with _STATUS_LOCK:
        if not _STATUS_STARTED[0]:
            _STATUS_STARTED[0] = True
            threading.Thread(target=_status_worker, daemon=True).start()
    body = dict(payload, **{"class": cls, "acap": audio_cap_level()})
    try:
        _STATUS_Q.put_nowait((url, body))
    except queue.Full:
        try:
            _STATUS_Q.get_nowait()
        except queue.Empty:
            pass
        try:
            _STATUS_Q.put_nowait((url, body))
        except queue.Full:
            pass


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(256 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


class ExamAudioCache:
    """考试与平时听力本地缓存器：后台静默预下载 + SHA-256 校验（下坏自动重下）+ 7 天自动清理"""

    def __init__(self):
        self._lock = threading.Lock()
        self._test_lock = threading.Lock()
        self._wake = threading.Event()
        self._items = {}  # audio_id -> item dict (当前看板清单)
        self._prio = {}   # audio_id -> item dict (手动立刻请求的高优项)
        self._reported = {}  # audio_id -> last ready report ts
        self._retry_after = {}  # audio_id -> (next_ts, fail_cnt)
        self._index = self._load_index()
        self._test_prefetched = False
        self.clean_old()
        threading.Thread(target=self._worker, daemon=True).start()

    def _load_index(self):
        try:
            with open(EXAM_AUDIO_INDEX, encoding="utf-8") as f:
                d = json.load(f)
                return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_index(self):
        try:
            os.makedirs(EXAM_AUDIO_DIR, exist_ok=True)
            with open(EXAM_AUDIO_INDEX + ".tmp", "w", encoding="utf-8") as f:
                json.dump(self._index, f, ensure_ascii=False)
            os.replace(EXAM_AUDIO_INDEX + ".tmp", EXAM_AUDIO_INDEX)
        except OSError:
            pass

    def reset(self):
        """VOI 切换/解绑班级时清空旧班下载队列与上报记录，防串班"""
        with self._lock:
            self._items.clear()
            self._prio.clear()
            self._reported.clear()
            self._retry_after.clear()
            self._test_prefetched = False

    def clean_old(self):
        cutoff = time.time() - 14 * 86400
        with self._lock:
            old = [k for k, v in self._index.items() if v.get("at", 0) < cutoff and k not in self._items and k not in self._prio]
            for k in old:
                p = self._index[k].get("path")
                if p and os.path.isfile(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
                self._index.pop(k, None)
            if old:
                self._save_index()
                log(f"已清理 {len(old)} 个 14 天前的听力缓存文件")

    def get_ready_path(self, aid, expected_sha=""):
        if not aid:
            return None
        with self._lock:
            rec = self._index.get(aid)
        if not rec or not rec.get("path") or not os.path.isfile(rec["path"]):
            return None
        if expected_sha and rec.get("sha256") != expected_sha:
            return None
        return rec["path"]

    def sync(self, items):
        if not isinstance(items, list):
            return
        fresh = {}
        for it in items:
            aid = it.get("id")
            if aid:
                fresh[aid] = dict(it)
        with self._lock:
            # 保留正在退避重试的优先下载项，避免滞后看板把临时失败的新曲从清单中挤掉
            now_s = time.time()
            for k, t_wait in list(self._retry_after.items()):
                if k not in fresh and k in self._items and t_wait > now_s - 60:
                    fresh[k] = self._items[k]
            self._items = fresh
            # 清理不再在清单中的退避状态
            for k in list(self._retry_after):
                if k not in fresh and k not in self._prio:
                    self._retry_after.pop(k, None)
        if fresh and not self._test_prefetched:
            self._test_prefetched = True
            threading.Thread(target=self.fetch_test_audio, daemon=True).start()
        self._wake.set()

    def request_now(self, item):
        if item and item.get("id"):
            aid = item["id"]
            with self._lock:
                self._prio[aid] = dict(item)
                self._retry_after.pop(aid, None)
            self._wake.set()

    def fetch_test_audio(self):
        """下载 10 秒考前试音文件到本地缓存（线程安全，只下一次）"""
        with self._test_lock:
            cache = os.path.join(HERE, "sound")
            os.makedirs(cache, exist_ok=True)
            local = os.path.join(cache, "exam_test.wav")
            if os.path.isfile(local) and os.path.getsize(local) > 1000:
                return local
            tmp = f"{local}.{threading.get_ident()}.tmp"
            try:
                with urllib.request.urlopen(SERVER + "/audio/exam_test.wav", timeout=20) as r:
                    data = r.read()
                if len(data) > 1000:
                    with open(tmp, "wb") as f:
                        f.write(data)
                    os.replace(tmp, local)
                    return local
            except Exception as e:
                log(f"下载考前试音音频失败：{e}")
            finally:
                try:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                except OSError:
                    pass
            try:
                return fetch_sound("/audio/chime.wav")
            except Exception:
                return None

    def _download_one(self, it, force=False):
        aid, ext, want_sha = it["id"], it.get("ext") or "mp3", it.get("sha256") or ""
        total = int(it.get("size") or 0)
        snap_cls, snap_qcls, snap_qkey, snap_surl = CLASS, Q_CLASS, Q_KEY, EXAM_STATUS_URL
        if not snap_cls or not snap_qcls or not snap_qkey:
            return False
        os.makedirs(EXAM_AUDIO_DIR, exist_ok=True)
        dst = os.path.join(EXAM_AUDIO_DIR, f"{aid}.{ext}")
        # 1. 快速复用本地已校验缓存（比对 size + mtime，避免每分钟重算几十 MB 的 SHA-256；活跃命中超 1 天时续期防清理）
        if os.path.isfile(dst):
            try:
                st = os.stat(dst)
                now = time.time()
                fast_hit = False
                need_report = False
                with self._lock:
                    rec = self._index.get(aid)
                    if rec and rec.get("path") == dst and rec.get("size") == st.st_size and rec.get("mtime") == st.st_mtime and (not want_sha or rec.get("sha256") == want_sha):
                        fast_hit = True
                        self._prio.pop(aid, None)
                        if now - rec.get("at", 0) > 86400:
                            rec["at"] = now
                            self._save_index()
                        if now - self._reported.get(aid, 0) >= 55:
                            self._reported[aid] = now
                            need_report = True
                if fast_hit:
                    if need_report:
                        report_exam_audio_status({"audio_id": aid, "cache_status": "ready", "pct": 100}, url=snap_surl, cls=snap_cls)
                    return True
                got_sha = file_sha256(dst)
                if not want_sha or got_sha == want_sha:
                    now = time.time()
                    need_report = False
                    with self._lock:
                        old_at = (self._index.get(aid) or {}).get("at") or st.st_mtime
                        new_at = now if now - old_at > 86400 else old_at
                        self._index[aid] = {"path": dst, "sha256": got_sha, "size": st.st_size, "mtime": st.st_mtime, "at": new_at}
                        self._save_index()
                        self._prio.pop(aid, None)
                        if now - self._reported.get(aid, 0) >= 55:
                            self._reported[aid] = now
                            need_report = True
                    if need_report:
                        report_exam_audio_status({"audio_id": aid, "cache_status": "ready", "pct": 100}, url=snap_surl, cls=snap_cls)
                    return True
            except OSError:
                pass
        # 2. 非强制重试时检查单文件退避时间，防止单个损坏/超时文件阻塞整个队列
        now = time.time()
        with self._lock:
            nxt_ts, fails = self._retry_after.get(aid, (0.0, 0))
        if not force and now < nxt_ts:
            return False
        url = f"{SERVER}{it.get('url') or ('/exam-audio/' + aid)}?class={snap_qcls}&key={snap_qkey}"
        tmp = f"{dst}.{threading.get_ident()}.part"
        try:
            report_exam_audio_status({"audio_id": aid, "cache_status": "downloading", "pct": 0}, url=snap_surl, cls=snap_cls)
            h = hashlib.sha256()
            got, last_pct = 0, 0
            with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
                clen = int(r.headers.get("Content-Length") or total or 0)
                while True:
                    if CLASS != snap_cls:  # VOI 切班/解绑，立即中止
                        raise RuntimeError("班级已切换")
                    chunk = r.read(256 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    h.update(chunk)
                    got += len(chunk)
                    if clen > 0:
                        pct = min(99, int(got * 100 / clen))
                        if pct - last_pct >= 15:
                            last_pct = pct
                            report_exam_audio_status({"audio_id": aid, "cache_status": "downloading", "pct": pct}, url=snap_surl, cls=snap_cls)
            digest = h.hexdigest()
            if want_sha and digest != want_sha:
                raise ValueError("SHA-256 校验不一致")
            if CLASS != snap_cls:
                raise RuntimeError("班级已切换")
            os.replace(tmp, dst)
            st = os.stat(dst)
            with self._lock:
                self._index[aid] = {"path": dst, "sha256": digest, "size": st.st_size, "mtime": st.st_mtime, "at": time.time()}
                self._save_index()
                self._retry_after.pop(aid, None)
                self._prio.pop(aid, None)
                self._reported[aid] = time.time()
            report_exam_audio_status({"audio_id": aid, "cache_status": "ready", "pct": 100}, url=snap_surl, cls=snap_cls)
            log(f"听力预下载并校验通过：{it.get('name', aid)} ({got} B)")
            return True
        except urllib.error.HTTPError as e:
            log(f"听力下载 HTTP {e.code}：{it.get('name', aid)}")
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            if e.code in (403, 404):  # 无权或已删除：停止反复重试，不阻塞其他文件
                with self._lock:
                    self._retry_after[aid] = (time.time() + 300, fails + 1)
                    self._prio.pop(aid, None)
            else:
                with self._lock:
                    self._retry_after[aid] = (time.time() + min(60, 8 * (fails + 1)), fails + 1)
                    if CLASS == snap_cls:
                        self._items.setdefault(aid, dict(it))
            if CLASS == snap_cls:
                report_exam_audio_status({"audio_id": aid, "cache_status": "error", "pct": 0, "err": f"HTTP {e.code}"}, url=snap_surl, cls=snap_cls)
            return False
        except Exception as e:
            log(f"听力下载失败（第 {fails + 1} 次）：{e}")
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            if CLASS == snap_cls:
                with self._lock:
                    self._retry_after[aid] = (time.time() + min(60, 8 * (fails + 1)), fails + 1)
                    self._items.setdefault(aid, dict(it))
                report_exam_audio_status({"audio_id": aid, "cache_status": "error", "pct": 0, "err": f"重试中({fails + 1})"}, url=snap_surl, cls=snap_cls)
            return False

    def _worker(self):
        while True:
            self._wake.wait(15)
            self._wake.clear()
            self.clean_old()
            with self._lock:
                # 每次高优请求只跳过退避一次，取出即从 _prio 弹出；若暂时失败则转入 _items 走正常退避，防死循环卡队列
                prio_list = list(self._prio.values())
                self._prio.clear()
                prio_ids = {x["id"] for x in prio_list}
                norm_list = [x for aid, x in self._items.items() if aid not in prio_ids]
            for it in prio_list:
                if not CLASS or not KEY:
                    break
                self._download_one(it, force=True)
            for it in norm_list:
                if not CLASS or not KEY:
                    break
                self._download_one(it, force=False)


def _drain_stderr(pipe, tail):
    """持续抽干 ffplay 的 stderr 管道，避免长时间播放 MP3 警告写满 64KB 缓冲区导致播放器静默卡死"""
    try:
        for raw in pipe:
            line = raw.decode("utf-8", "ignore").strip()
            if line:
                tail.append(line[:80])
    except Exception:
        pass
    finally:
        try:
            pipe.close()
        except Exception:
            pass


class ExamAudioPlayer:
    """跨平台可控听力播放器：Windows 用系统自带 winmm MCI（单线程 + 音轨代际/操作序号防竞态），麒麟/Linux 用 ffplay（干净停启续播 + 管道防阻塞 + 异常码检测）"""

    def __init__(self):
        self._lock = threading.Lock()
        self._gen = 0      # 音轨代际号：仅在 play_file / stop 时递增，pause / resume 不改变音轨代际
        self._op_seq = 0   # 控制操作序号：play / pause / resume / stop 均递增，用于跳过已被后续操作覆盖的暂停/继续
        self._state = "idle"  # idle / playing / paused / done / test_done / error / test_error / testing
        self._mode = "exam"
        self._path = ""
        self._dur = 0.0
        self._pos = 0.0
        self._err = ""
        self._base_pos = 0.0
        self._started_mono = 0.0
        self._proc = None
        self._stderr_tail = deque(maxlen=6)
        self._stderr_thr = None
        self._win_q = None
        if WIN:
            self._win_q = queue.Queue()
            threading.Thread(target=self._win_loop, daemon=True).start()

    def can_mp3(self):
        return bool(WIN or FFPLAY)

    def can_seek(self):
        return bool(WIN or FFPLAY)

    def _err_state(self, mode=None):
        m = self._mode if mode is None else mode
        return "test_error" if m == "test" else "error"

    # ---- Windows 单线程 MCI 驱动 ----
    def _mci_call(self, cmd):
        buf = ctypes.create_unicode_buffer(256)
        err = ctypes.windll.winmm.mciSendStringW(cmd, buf, 255, None)
        return err, buf.value.strip()

    def _mci_err(self, code):
        try:
            buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.winmm.mciGetErrorStringW(int(code), buf, 255)
            return buf.value.strip() or f"错误码 {code}"
        except Exception:
            return f"错误码 {code}"

    def _win_short_path(self, path):
        try:
            buf = ctypes.create_unicode_buffer(1024)
            n = ctypes.windll.kernel32.GetShortPathNameW(path, buf, 1024)
            if 0 < n < 1024:
                return buf.value
        except Exception:
            pass
        return path

    def _win_loop(self):
        alias = "ccexam"
        opened = False
        opened_gen = 0
        opened_mode = "exam"
        poll_errs = 0
        while True:
            try:
                act, gen, seq, args = self._win_q.get(timeout=0.35)
            except queue.Empty:
                if opened and self._win_q.empty():
                    with self._lock:
                        st, cur_gen = self._state, self._gen
                    if cur_gen == opened_gen and st in ("playing", "testing"):
                        e1, pos_s = self._mci_call(f"status {alias} position")
                        e2, mode_s = self._mci_call(f"status {alias} mode")
                        if e2 != 0:
                            poll_errs += 1
                            if poll_errs >= 5:
                                self._mci_call(f"close {alias}")
                                opened = False
                                with self._lock:
                                    if self._gen == opened_gen:
                                        self._state, self._err = self._err_state(opened_mode), "播放设备异常中断"
                        else:
                            poll_errs = 0
                            need_close = False
                            with self._lock:
                                if self._gen == opened_gen:
                                    if e1 == 0 and pos_s.isdigit():
                                        self._pos = int(pos_s) / 1000.0
                                    if mode_s.lower() == "stopped" and self._state in ("playing", "testing"):
                                        need_close = True
                                        if opened_mode == "test":
                                            self._state, self._pos, self._dur, self._mode = "test_done", 0.0, 0.0, "exam"
                                        else:
                                            self._pos = self._dur if self._dur > 0 else self._pos
                                            self._state = "done"
                            if need_close:
                                self._mci_call(f"close {alias}")
                                opened = False
                continue
            try:
                with self._lock:
                    cur_gen, cur_seq = self._gen, self._op_seq
                if act == "play":
                    if gen != cur_gen:
                        continue  # 已有更新的播放/停止指令，跳过过期的旧音轨
                    path, dur, seek, mode = args
                    if opened:
                        self._mci_call(f"stop {alias}")
                        self._mci_call(f"close {alias}")
                        opened = False
                    safe = self._win_short_path(path).replace('"', '')
                    e, _ = self._mci_call(f'open "{safe}" type mpegvideo alias {alias}')
                    if e != 0 and path.lower().endswith(".wav"):
                        e, _ = self._mci_call(f'open "{safe}" type waveaudio alias {alias}')
                    if e != 0:
                        log(f"Windows MCI 打开失败：{self._mci_err(e)} ({path})")
                        with self._lock:
                            if self._gen == gen:
                                self._state, self._err = self._err_state(mode), "系统播放器打不开该音频"
                        continue
                    opened = True
                    opened_gen = gen
                    opened_mode = mode
                    poll_errs = 0
                    self._mci_call(f"set {alias} time format milliseconds")
                    e_len, len_s = self._mci_call(f"status {alias} length")
                    mci_dur = int(len_s) / 1000.0 if e_len == 0 and len_s.isdigit() and int(len_s) > 0 else 0.0
                    # 优先相信服务端准确计算的时长（DirectShow 对 VBR MP3 的 length 经常偏差 2-3 倍）
                    real_dur = float(dur) if float(dur or 0) > 0 else mci_dur
                    ms = max(0, int(seek * 1000))
                    if real_dur > 0 and ms >= int(real_dur * 1000):
                        ms = max(0, int(real_dur * 1000) - 500)
                    e_p, _ = self._mci_call(f"play {alias} from {ms}" if ms > 0 else f"play {alias}")
                    need_stop_close = False
                    need_close_err = False
                    need_pause_now = False
                    with self._lock:
                        if self._gen != gen:
                            need_stop_close = True
                        elif e_p == 0:
                            self._dur = real_dur
                            self._pos = ms / 1000.0
                            self._err = ""
                            if self._state == "paused":
                                # 老师在打开音频瞬间就点了暂停：保持暂停态并在锁外立即暂停 MCI
                                need_pause_now = True
                            else:
                                self._state = "testing" if mode == "test" else "playing"
                        else:
                            need_close_err = True
                            self._state, self._err = self._err_state(mode), "启动播放失败"
                    if need_stop_close:
                        self._mci_call(f"stop {alias}")
                        self._mci_call(f"close {alias}")
                        opened = False
                    elif need_close_err:
                        log(f"Windows MCI 播放失败：{self._mci_err(e_p)}")
                        self._mci_call(f"close {alias}")
                        opened = False
                    elif need_pause_now:
                        self._mci_call(f"pause {alias}")
                elif act == "pause" and opened:
                    if gen != cur_gen or gen != opened_gen or seq != cur_seq:
                        continue
                    self._mci_call(f"pause {alias}")
                    e1, pos_s = self._mci_call(f"status {alias} position")
                    with self._lock:
                        if self._gen == gen and self._op_seq == seq:
                            if e1 == 0 and pos_s.isdigit():
                                self._pos = int(pos_s) / 1000.0
                            if self._state == "playing":
                                self._state = "paused"
                elif act == "resume" and opened:
                    if gen != cur_gen or gen != opened_gen or seq != cur_seq:
                        continue
                    self._mci_call(f"resume {alias}")
                    with self._lock:
                        if self._gen == gen and self._op_seq == seq and self._state == "paused":
                            self._state = "playing"
                elif act == "stop":
                    if opened:
                        self._mci_call(f"stop {alias}")
                        self._mci_call(f"close {alias}")
                        opened = False
                    with self._lock:
                        if self._gen == gen:
                            self._state, self._pos, self._dur, self._mode, self._err = "idle", 0.0, 0.0, "exam", ""
            except Exception as exc:
                log(f"Windows 听力播放出错：{exc}")

    # ---- 通用控制接口 ----
    def _stop_posix_locked(self):
        p = self._proc
        self._proc = None
        self._stderr_thr = None
        if not p:
            return
        try:
            if p.poll() is None:
                try:
                    os.killpg(p.pid, signal.SIGTERM)
                except OSError:
                    p.terminate()
                try:
                    p.wait(timeout=1.0)
                except Exception:
                    try:
                        os.killpg(p.pid, signal.SIGKILL)
                    except OSError:
                        p.kill()
                    try:
                        p.wait(timeout=1.0)
                    except Exception:
                        pass
        except OSError:
            pass

    def _spawn_posix_locked(self, path, seek):
        cmd = None
        use_seek = seek
        if FFPLAY:
            cmd = [FFPLAY, "-nodisp", "-vn", "-autoexit", "-loglevel", "error"]
            if use_seek > 0.2:
                cmd += ["-ss", f"{use_seek:.2f}"]
            cmd.append(path)
        elif APLAY and path.lower().endswith(".wav"):
            cmd = [APLAY, path] if APLAY.endswith("paplay") else [APLAY, "-q", path]
            use_seek = 0.0  # paplay/aplay 不支持定位，进度条从 0 起算避免错位
        if not cmd:
            self._state, self._err = self._err_state(), "本机缺少 ffplay 播放器"
            return False
        try:
            tail = deque(maxlen=6)
            self._stderr_tail = tail
            p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.PIPE, start_new_session=True)
            self._proc = p
            thr = threading.Thread(target=_drain_stderr, args=(p.stderr, tail), daemon=True)
            self._stderr_thr = thr
            thr.start()
            self._base_pos = use_seek
            self._pos = use_seek
            self._started_mono = time.monotonic()
            self._state = "testing" if self._mode == "test" else "playing"
            return True
        except Exception as e:
            self._state, self._err = self._err_state(), f"启动播放器失败：{e}"
            return False

    def play_file(self, path, duration=0.0, seek=0.0, mode="exam"):
        if not path or not os.path.isfile(path):
            with self._lock:
                self._gen += 1
                self._op_seq += 1
                gen, seq = self._gen, self._op_seq
                if WIN:
                    self._win_q.put(("stop", gen, seq, None))
                else:
                    self._stop_posix_locked()
                self._mode = mode
                self._state, self._err = self._err_state(mode), "本地听力文件不存在"
            return False
        with self._lock:
            self._gen += 1
            self._op_seq += 1
            gen, seq = self._gen, self._op_seq
            self._path = path
            self._dur = float(duration or 0)
            self._mode = mode
            self._err = ""
            pos = max(0.0, float(seek or 0))
            if self._dur > 1.0 and pos >= self._dur - 0.5:
                pos = max(0.0, self._dur - 0.5)
            self._pos = pos
            if WIN:
                self._state = "testing" if mode == "test" else "playing"
                self._win_q.put(("play", gen, seq, (path, self._dur, pos, mode)))
                return True
            self._stop_posix_locked()
            return self._spawn_posix_locked(path, pos)

    def pause(self):
        if not self.can_seek():
            return  # 缺 ffplay 的 aplay/paplay 兜底路径不支持断点定位，禁止假暂停
        with self._lock:
            if self._state != "playing":
                return
            self._op_seq += 1
            gen, seq = self._gen, self._op_seq
            if WIN:
                self._win_q.put(("pause", gen, seq, None))
                self._state = "paused"
                return
            # 麒麟/Linux 端干净停止并记录断点位置，避免 SIGSTOP 引发 PulseAudio 断流或 ALSA 爆音
            elapsed = self._base_pos + max(0.0, time.monotonic() - self._started_mono)
            self._base_pos = min(self._dur, elapsed) if self._dur > 0 else elapsed
            self._pos = self._base_pos
            self._stop_posix_locked()
            self._state = "paused"

    def resume(self):
        if not self.can_seek():
            return
        with self._lock:
            if self._state != "paused":
                return
            self._op_seq += 1
            gen, seq = self._gen, self._op_seq
            if WIN:
                self._win_q.put(("resume", gen, seq, None))
                self._state = "playing"
                return
            if self._path and os.path.isfile(self._path):
                self._spawn_posix_locked(self._path, self._base_pos)
            else:
                self._state, self._err = self._err_state(), "本地听力文件不存在"

    def replay(self):
        with self._lock:
            path, dur, mode = self._path, self._dur, self._mode
        if path:
            return self.play_file(path, dur, 0.0, mode=mode)
        return False

    def stop(self):
        with self._lock:
            self._gen += 1
            self._op_seq += 1
            gen, seq = self._gen, self._op_seq
            if WIN:
                self._win_q.put(("stop", gen, seq, None))
            else:
                self._stop_posix_locked()
            self._state = "idle"
            self._pos = 0.0
            self._base_pos = 0.0
            self._dur = 0.0
            self._mode = "exam"
            self._err = ""

    def snapshot(self):
        with self._lock:
            if not WIN and self._state in ("playing", "testing"):
                run_sec = max(0.0, time.monotonic() - self._started_mono)
                elapsed = self._base_pos + run_sec
                rc = self._proc.poll() if self._proc is not None else -1
                if rc is not None:
                    thr = self._stderr_thr
                    self._stderr_thr = None
                    if thr is not None and thr.is_alive():
                        thr.join(timeout=0.05)
                    err_line = self._stderr_tail[-1] if self._stderr_tail else ""
                    self._proc = None
                    if rc != 0:
                        self._state = self._err_state()
                        self._err = err_line or f"播放器异常退出（代码 {rc}）"
                        log(f"Linux 听力播放异常退出 rc={rc}: {self._err}")
                    elif self._mode == "test":
                        self._state, self._pos, self._dur, self._mode = "test_done", 0.0, 0.0, "exam"
                    elif (run_sec < 2.5 and (self._dur - self._base_pos) > 15.0) or (
                        bool(err_line) and self._dur > 15.0 and elapsed < self._dur * 0.85
                    ):
                        self._state = "error"
                        self._err = err_line or "播放器异常提前停止，请点重播或检查音响"
                        log(f"Linux 听力提前结束：elapsed={elapsed:.1f}s dur={self._dur:.1f}s err={err_line}")
                    else:
                        self._pos = self._dur if self._dur > 0 else elapsed
                        self._state = "done"
                else:
                    self._pos = min(self._dur, elapsed) if self._dur > 0 else elapsed
            return {"state": self._state, "pos": self._pos, "dur": self._dur, "mode": self._mode, "err": self._err}


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
    UNINSTALLING.set()  # 让 guard_loop 别再把开机自启补回来
    if not WIN:
        set_cron(None)
    names = ("作业登记", "考勤登记", "班级看板")
    if WIN:
        set_win_run(False)
        desk = win_desktop()
        paths = [win_startup_lnk()] + [os.path.join(desk, n + (".lnk" if n == "班级看板" else ".url")) for n in names]
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


# ---------------- 开机自启看护（麒麟/Linux）----------------
# 有的一体机会被清理/管控软件清空 ~/.config/autostart，之后开机就不启动了（10-24 高三1班、10-27 网友那边都遇到过）。
# 运行时每分钟看一眼，被删了就补回去，并把发现的时间写进日志；另加一条 crontab @reboot 当备用入口（不在 ~/.config 里，不用 sudo）。
UNINSTALLING = threading.Event()
AUTOSTART = os.path.expanduser("~/.config/autostart/classcall.desktop")
AUTOSTART_ENTRY = (f"[Desktop Entry]\nType=Application\nName=班级助手接收端\nExec=python3 {SELF}\n"
                   "X-GNOME-Autostart-enabled=true\nNoDisplay=true\n")  # 和 install.sh 写的一样
CRON_TAG = "# classcall"
CRON_LINE = f"@reboot {sys.executable} {SELF} --boot >/dev/null 2>&1 {CRON_TAG}"


def ensure_autostart():
    try:
        if os.path.isfile(AUTOSTART):
            return
        d = os.path.dirname(AUTOSTART)
        if os.path.isdir(d):
            left = os.listdir(d)
            log(f"发现开机自启被删了：文件夹最后修改于 {time.strftime('%m-%d %H:%M', time.localtime(os.path.getmtime(d)))}，"
                f"里面还剩 {len(left)} 个文件{('：' + '、'.join(left[:8])) if left else ''}。已补回")
        else:
            log("发现开机自启文件夹整个没了，已补回")
        os.makedirs(d, exist_ok=True)
        with open(AUTOSTART, "w", encoding="utf-8") as f:
            f.write(AUTOSTART_ENTRY)
    except Exception as e:
        log(f"补回开机自启失败：{e}")


def set_cron(line):
    """把 crontab 里带 # classcall 的那行换成 line（None = 删掉）；没装 cron 就算了"""
    try:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
        old = r.stdout.splitlines() if r.returncode == 0 else []
        new = [x for x in old if CRON_TAG not in x] + ([line] if line else [])
        if new == old:
            return
        subprocess.run(["crontab", "-"], input="\n".join(new) + "\n", text=True, timeout=10, check=True)
        if line:
            log("开机备用入口（crontab）不见了，已补回" if getattr(set_cron, "done", False) else "已设置开机备用入口（crontab @reboot）")
        set_cron.done = bool(line)
    except Exception as e:
        if line and not getattr(set_cron, "failed", False):
            set_cron.failed = True
            log(f"设置开机备用入口失败（不影响使用）：{e}")


# ---------------- 开机自启看护（Windows）----------------
# Windows 的开机自启是「启动」文件夹里的 班级助手接收端.lnk，会被 360 之类的清理软件删掉（11-03 网友那边两台）。
# 同样每分钟看一眼，没了就补回；另在注册表 HKCU\...\Run 里登记一个备用入口（不用管理员）。两个都起来时，后起的那个拿不到锁自己退出。
RUN_KEY, RUN_NAME = r"Software\Microsoft\Windows\CurrentVersion\Run", "ClassCallReceiver"


def win_startup_lnk():
    buf = ctypes.create_unicode_buffer(260)
    d = buf.value if ctypes.windll.shell32.SHGetFolderPathW(None, 0x07, None, 0, buf) == 0 else ""  # CSIDL_STARTUP
    return os.path.join(d, "班级助手接收端.lnk") if d else ""


def win_pythonw():
    w = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return w if os.path.isfile(w) else sys.executable


def ensure_win_autostart():
    lnk = win_startup_lnk()
    if not lnk or os.path.isfile(lnk):
        return
    try:
        d = os.path.dirname(lnk)
        if os.path.isdir(d):
            left = os.listdir(d)
            log(f"发现开机自启被删了：「启动」文件夹最后修改于 {time.strftime('%m-%d %H:%M', time.localtime(os.path.getmtime(d)))}，"
                f"里面还剩 {len(left)} 个文件{('：' + '、'.join(left[:8])) if left else ''}")
        os.makedirs(d, exist_ok=True)
        q = lambda x: x.replace("'", "''")  # PowerShell 单引号字符串里的 ' 要写两遍
        ps = (f"$l=(New-Object -ComObject WScript.Shell).CreateShortcut('{q(lnk)}');$l.TargetPath='{q(win_pythonw())}';"
              f"$l.Arguments='\"{q(SELF)}\"';$l.WorkingDirectory='{q(HERE)}';$l.Save()")
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                       capture_output=True, timeout=60, creationflags=0x08000000)
        log("已补回开机自启" if os.path.isfile(lnk) else "补回开机自启失败（可能被杀毒软件拦了），注册表里的备用入口还在")
    except Exception as e:
        log(f"补回开机自启失败：{e}")


def set_win_run(on):
    """注册表 Run 里的备用入口：on=True 没有就写上，False 删掉"""
    try:
        import winreg
        cmd = f'"{win_pythonw()}" "{SELF}"'
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
            try:
                cur = winreg.QueryValueEx(k, RUN_NAME)[0]
            except OSError:
                cur = None
            if not on:
                if cur is not None:
                    winreg.DeleteValue(k, RUN_NAME)
                return
            if cur == cmd:
                return
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, cmd)
            log("开机备用入口（注册表）不见了，已补回" if getattr(set_win_run, "done", False) else "已设置开机备用入口（注册表 Run）")
            set_win_run.done = True
    except Exception as e:
        if on and not getattr(set_win_run, "failed", False):
            set_win_run.failed = True
            log(f"设置开机备用入口失败（不影响使用）：{e}")


def guard_loop():
    if os.path.basename(HERE) != ".classcall":  # 手动从别处运行（比如测试）时不动开机设置
        return
    n = 0
    while not UNINSTALLING.is_set():
        if WIN:
            ensure_win_autostart()
            if n % 10 == 0:
                set_win_run(True)
        else:
            ensure_autostart()
            if n % 10 == 0:  # crontab 要开子进程，十分钟看一次
                set_cron(CRON_LINE)
        n += 1
        UNINSTALLING.wait(60)


def desktop_env(proc="/proc"):
    """找一个本用户、已经在桌面里运行的程序，返回它的环境变量；桌面还没登录好就返回 None"""
    uid, best = os.getuid(), None
    for pid in os.listdir(proc):
        if not pid.isdigit() or int(pid) == os.getpid():
            continue
        try:
            if os.stat(os.path.join(proc, pid)).st_uid != uid:
                continue
            with open(os.path.join(proc, pid, "environ"), "rb") as f:
                env = dict(x.split(b"=", 1) for x in f.read().split(b"\0") if b"=" in x)
        except (OSError, ValueError):
            continue
        if b"DISPLAY" in env and (not best or len(env) > len(best)):  # 变量最全的那个，一般就是桌面会话本身
            best = env
    if not best:
        return None
    env = {k.decode(errors="replace"): v.decode(errors="replace") for k, v in best.items()}
    for k in ("CLASSCALL_TK", "PYTHONPATH", "LD_LIBRARY_PATH", "TCL_LIBRARY", "TK_LIBRARY"):
        env.pop(k, None)  # 缺 tkinter 的机器由开头那段重新带上
    return env


def boot_wait():
    """crontab 开机入口：等老师的桌面登录好，借用桌面程序的环境变量（DISPLAY、声音、DBus）再启动，效果和开机自启一样。
    开机自启已经把接收端开起来了的话，这里就直接退出"""
    while True:
        with open(f"/tmp/classcall-{os.getuid()}.lock", "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(lock, fcntl.LOCK_UN)
            except OSError:
                sys.exit(0)  # 已经在运行
        env = desktop_env()
        if env:
            time.sleep(20)  # 桌面刚出来，等开机自启先启动；它启动了的话，下面这个新进程拿不到锁会自己退出
            os.execve(sys.executable, [sys.executable, SELF], env)
        time.sleep(15)


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
    if not CLASS or not KEY:
        return
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


class DeskLayer:
    """桌面版看板的窗口：贴在桌面上、压在所有窗口下面、不抢焦点、不进任务栏；被"显示桌面"收起来了就自己再显示"""
    ALPHA = 0.93

    @staticmethod
    def box(app):
        """放在哪：桌面右侧，避开任务栏"""
        top, bottom = 0, app.sh
        if WIN:
            try:
                r = (ctypes.c_long * 4)()
                if ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0):  # SPI_GETWORKAREA
                    top, bottom = r[1], r[3]
            except Exception:
                pass
        else:
            top, bottom = int(app.sh * 0.03), int(app.sh * 0.93)  # 麒麟任务栏一般在下面，留出来
        m = int(app.sh * 0.025)
        w, h = int(app.sw * 0.48), bottom - top - 2 * m
        return app.sw - w - m, top + m, w, h

    @staticmethod
    def setup(win, box):
        x, y, w, h = box
        if WIN:
            win.overrideredirect(True)
            win.geometry(f"{w}x{h}+{x}+{y}")
            try:
                win.attributes("-alpha", DeskLayer.ALPHA)
                u = ctypes.windll.user32
                win.update_idletasks()
                hwnd = u.GetParent(win.winfo_id()) or win.winfo_id()
                GWL_EXSTYLE, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE, WS_EX_APPWINDOW = -20, 0x80, 0x08000000, 0x40000
                u.SetWindowLongW(hwnd, GWL_EXSTYLE, (u.GetWindowLongW(hwnd, GWL_EXSTYLE) | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE) & ~WS_EX_APPWINDOW)
                progman = u.FindWindowW("Progman", None)  # 让桌面当它的"主人"：按 Win+D 显示桌面时它还在
                if progman:
                    setp = getattr(u, "SetWindowLongPtrW", u.SetWindowLongW)
                    setp.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
                    setp(hwnd, -8, progman)  # GWLP_HWNDPARENT
                win._desk_hwnd = hwnd
            except Exception as e:
                log(f"桌面看板窗口设置失败：{e}")
        else:
            win.withdraw()  # 先藏起来，写好"不要边框、放最下面、不进任务栏"再显示，窗口管理器才认
            win.geometry(f"{w}x{h}+{x}+{y}")
            win.update_idletasks()
            try:
                DeskLayer.x11_hints(win)
            except Exception as e:
                log(f"桌面看板窗口设置失败：{e}")
            win.deiconify()
            win.geometry(f"{w}x{h}+{x}+{y}")
            try:
                win.attributes("-alpha", DeskLayer.ALPHA)
            except tk.TclError:
                pass
        DeskLayer.keep(win)

    @staticmethod
    def x11_hints(win):
        x11 = ctypes.cdll.LoadLibrary(ctypes.util.find_library("X11") or "libX11.so.6")
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        x11.XInternAtom.restype = ctypes.c_ulong
        x11.XChangeProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_void_p, ctypes.c_int]
        x11.XFlush.argtypes = [ctypes.c_void_p]
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        dpy = x11.XOpenDisplay(None)
        if not dpy:
            return
        A = lambda n: x11.XInternAtom(dpy, n.encode(), 0)
        xid = int(win.wm_frame(), 16)  # Tk 外层窗口（窗口管理器看的是它）
        XA_ATOM = 4
        states = (ctypes.c_ulong * 4)(*[A(n) for n in ("_NET_WM_STATE_BELOW", "_NET_WM_STATE_SKIP_TASKBAR",
                                                     "_NET_WM_STATE_SKIP_PAGER", "_NET_WM_STATE_STICKY")])
        x11.XChangeProperty(dpy, xid, A("_NET_WM_STATE"), XA_ATOM, 32, 0, states, 4)
        utype = (ctypes.c_ulong * 1)(A("_NET_WM_WINDOW_TYPE_UTILITY"))
        x11.XChangeProperty(dpy, xid, A("_NET_WM_WINDOW_TYPE"), XA_ATOM, 32, 0, utype, 1)
        motif = (ctypes.c_ulong * 5)(2, 0, 0, 0, 0)  # _MOTIF_WM_HINTS：不要标题栏和边框
        x11.XChangeProperty(dpy, xid, A("_MOTIF_WM_HINTS"), A("_MOTIF_WM_HINTS"), 32, 0, motif, 5)
        x11.XFlush(dpy)
        x11.XCloseDisplay(dpy)

    @staticmethod
    def keep(win):
        """每隔几秒：被收起来了就再显示，并放回最下面（不激活、不抢焦点）"""
        try:
            if WIN:
                u, hwnd = ctypes.windll.user32, getattr(win, "_desk_hwnd", None)
                if not hwnd:
                    return
                if not u.IsWindowVisible(hwnd) or u.IsIconic(hwnd):
                    u.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
                u.SetWindowPos(hwnd, 1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)  # HWND_BOTTOM，不移动、不改大小、不激活
            else:
                if win.state() != "normal":
                    win.deiconify()
                win.lower()
        except Exception:
            pass


class DayStandby:
    """白天版班级看板（浅色）：今天的课程、现在/下节课、今日作业、倒计时"""
    BG1, BG2, CARD, LINE, GREEN, GREEN_SOFT = "#f3f8fc", "#eaf6ef", "#ffffff", "#e3e9f0", "#16a571", "#e7f7f0"

    def __init__(self, app, board, desk=None):
        """desk=(x, y, 宽, 高)：桌面版小看板——同样的画法缩小放在桌面右侧，压在所有窗口下面，不用碰屏幕收起"""
        self.app, self.board, self.desk = app, board, desk
        sw, sh = (desk[2], desk[3]) if desk else (app.sw, app.sh)
        self.win = tk.Toplevel(app.root)
        if desk:
            DeskLayer.setup(self.win, desk)
        else:
            self.win.overrideredirect(True)
            self.win.attributes("-topmost", True)
            self.win.geometry(f"{sw}x{sh}+0+0")
            float_window(self.win)
        cv = self.cv = tk.Canvas(self.win, width=sw, height=sh, highlightthickness=0, bg=self.BG1)
        cv.pack()
        if not desk:
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
        # 下半：今日作业 + 倒计时/公告（桌面版地方窄，课程排两行、不放每日一句）
        n_cls = len([c for c in board.get("classes_today") or [] if c.get("subject")] or board.get("classes_today") or [])
        self.rows = 2 if desk and n_cls > 6 else 1
        top, quote = self.tiles_top + (22 if self.rows == 1 else 27) * u, None if desk else board.get("quote")
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
            f_n = app.font(int(6 * u), True, digits=True)
            room = R - 7 * u - f_n.measure(str(c["days"])) - 1.5 * u - (rx + 3 * u)  # 名字长了（如"冬季运动会"）就把字缩小，别压到天数
            name, px = f"距{c['name']}", 2.4
            while px > 1.7 and F(px).measure(name) > room:
                px -= 0.1
            cv.create_text(rx + 3 * u, y + 5.5 * u, text=name, anchor="w", fill=MUTED, font=F(px))
            cv.create_text(R - 7 * u, y + 5.5 * u, text=str(c["days"]), anchor="e", fill="#ef5a5a", font=f_n)
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
        if not desk:
            cv.create_text(sw / 2, sh - 2.6 * u, text="轻触屏幕返回", fill="#a3afbf", font=F(1.8, False))
            app.fade_in(self.win)
        self.tick()

    def draw_tiles(self, classes, cur_no, hm):
        cv, u, L, R = self.cv, self.u, self.L, self.R
        cv.delete("tiles")
        classes = [c for c in classes if c.get("subject")] or classes
        if not classes:
            return
        gap, rows = 1 * u, self.rows
        per = -(-len(classes) // rows)  # 每行几节
        w, th = (R - L - gap * (per - 1)) / per, (14 if rows == 1 else 10.5) * u
        for k, c in enumerate(classes):
            x1, y1 = L + (k % per) * (w + gap), self.tiles_top + 4 * u + (k // per) * (th + gap)
            now, past = c["no"] == cur_no, c["end"] <= hm
            fill = self.GREEN if now else "#f6f8fb" if past else self.CARD
            round_rect(cv, x1, y1, x1 + w, y1 + th, 2.2 * u, fill=fill, width=0, tags="tiles")
            ink, sub = ("#ffffff", "#d6f5e8") if now else ("#b3bdca", "#c4ccd6") if past else (INK, MUTED)
            cv.create_text(x1 + w / 2, y1 + th * 0.22, text=f"{c['no']}  {c['start']}", fill=sub, font=self.F(1.8, False), tags="tiles")
            subj = c.get("subject") or "—"
            color = "#ffffff" if now else "#c4ccd6" if past else SUBJ_COLOR.get(subj, INK)
            cv.create_text(x1 + w / 2, y1 + th * 0.64, text=subj, fill=color, font=self.F(3.6 if rows == 1 else 3.2), tags="tiles")

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
        if self.desk and t.tm_sec % 3 == 0:
            DeskLayer.keep(self.win)
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
    au = ex.get("audio") or {}
    return (ex["key"], ex["start_ts"], ex["end_ts"], ex["subject"], ex["name"], tuple(ex.get("notice") or []), str(ex.get("next")),
            au.get("id"), au.get("start_ts"))


EXAM_SAY = {"start": "考试开始，请开始答题", "warn": "距离考试结束还有十五分钟", "end": "考试结束，请停止作答"}


class ExamBoard:
    """考试大屏（整屏，深蓝）：开考前显示须知和开考倒计时，考试中显示科目和结束倒计时，结束后提示停笔。
    碰一下屏幕右下角出现"收起考试大屏"按钮（误碰不会收起），收起后右下角留一个小条，点它回到考试大屏；
    开考、剩 15 分钟、结束时自动回到大屏并各播报一次（配置听力时自动屏蔽开考提示音防冲突）。
    若本场配有听力，底部显示沉稳低调的听力进度条与监考应急按钮（暂停/继续、重播、停止，重播/停止需二次确认防误触）。"""
    BG, PANEL, LINE_, INK_, SOFT, BLUE, GOLD, RED = "#0f1e2e", "#172c42", "#24425f", "#eaf2fb", "#9fb6cc", "#7cc4ff", "#ffd166", "#ff6b6b"

    def __init__(self, app, ex, adopt=None):
        adopt = adopt or {}
        adopt_au = adopt.get("audio")
        if adopt_au and adopt_au.get("id"):
            ex = {**ex, "audio": dict(adopt_au)}
        self.app, self.ex, self.phase = app, ex, None
        self.hidden, self.mini, self.hide_btn = False, None, None
        self.key = exam_key(ex)
        self.audio_auto_done = bool(adopt.get("audio_auto_done"))
        self.audio_stopped_manual = False
        self.pending_play_at = adopt.get("pending_play_at")
        self.pending_audio_id = str(adopt.get("pending_audio_id") or "")
        self.pending_audio_sha = str(adopt.get("pending_audio_sha") or "")
        self._last_board_gen = getattr(app, "board_gen", 0)
        self._cmd_audio = (adopt_au["id"], dict(adopt_au), time.time(), self._last_board_gen) if (adopt_au and adopt_au.get("id")) else None  # 防慢看板把刚收到的新曲冲回旧录音
        self.confirm_act = None  # ("replay"|"stop"|"early", expire_ts)
        self.last_dock_sig = None
        self.last_report_at = 0.0
        self.last_reported_state = None
        self.last_reported_err = ""
        self.playing_audio_id = adopt.get("playing_audio_id") or ""
        self.test_gen = 0
        self._not_ready_logged = False
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
        self._bind_dock_tags()
        self.tick()

    def _bind_dock_tags(self):
        """固定按钮 tag 只绑定一次，避免每秒重绘泄漏上万个 Tcl 命令对象"""
        for act in ("pause_resume", "replay", "stop", "early", "stop_test"):
            tag = f"aud_btn_{act}"
            self.cv.tag_bind(tag, "<Button-1>", lambda e, a=act: self._dock_click(a))
            self.cv.tag_bind(tag, "<Enter>", lambda e: self.cv.config(cursor="hand2"))
            self.cv.tag_bind(tag, "<Leave>", lambda e: self.cv.config(cursor=""))

    def update_exam(self, ex):
        """同一场考试原地刷新文案/须知/下一场信息，绝不中断正在播放的考试听力，也不让滞后看板冲掉刚收到的新曲指令"""
        if not self.win.winfo_exists():
            return
        cur_bgen = getattr(self.app, "board_gen", 0)
        self._last_board_gen = cur_bgen
        cmd_rec = getattr(self, "_cmd_audio", None)
        if self.audio_stopped_manual:
            # 同一场考试停止后，普通看板刷新不能解除停止或换回旧曲；文案仍可更新。
            # 重新发令可采纳新录音，本地重播沿用停止时的录音，新考试另行初始化。
            ex = {**ex, "audio": dict(self.ex.get("audio") or {})}
        elif cmd_rec:
            c_aid, c_au, c_ts = cmd_rec[0], cmd_rec[1], cmd_rec[2]
            c_bgen = cmd_rec[3] if len(cmd_rec) > 3 else 0
            in_au = ex.get("audio") or {}
            if in_au.get("id") == c_aid and (not c_au.get("sha256") or in_au.get("sha256") == c_au.get("sha256")):
                self._cmd_audio = None  # 看板已追上最新换曲元数据
            elif cur_bgen <= c_bgen or time.time() - c_ts < 30.0:
                ex = {**ex, "audio": dict(c_au)}  # 仍未收到更新代次看板，或仍在途的旧看板响应：保留指令里的权威新录音，绝不回退播旧曲
            else:
                self._cmd_audio = None
        old_aid = (self.ex.get("audio") or {}).get("id")
        new_aid = (ex.get("audio") or {}).get("id")
        self.ex = ex
        self.key = exam_key(ex)
        if old_aid != new_aid:
            if getattr(self, "pending_audio_id", "") and self.pending_audio_id != new_aid:
                self.pending_play_at = None
                self.pending_audio_id = ""
                self.pending_audio_sha = ""
            if self.app.audio_player.snapshot()["state"] in ("idle", "test_done", "test_error", "done", "error"):
                self.audio_auto_done = False
                self._not_ready_logged = False
                self.playing_audio_id = ""
                self.app.audio_player.stop()
        if self.phase:
            now = time.time() + self.app.offset
            self.draw(self.phase)
            self._refresh_clock_count(now, self.phase)
            self.draw_audio_dock(now)

    def has_audio(self):
        return bool(self.ex.get("audio") and self.ex["audio"].get("id"))

    def on_touch(self, e):
        tags = self.cv.gettags("current")
        if "hidebtn" in tags or any(t.startswith("aud_") for t in tags):
            return  # 点的就是按钮或底部听力条：不弹收起按钮
        dock_vis = (self.has_audio() or self.app.audio_player.snapshot()["state"] in ("playing", "paused", "testing")) and self.phase in ("before", "during")
        if dock_vis and e.y >= 82.5 * self.u:
            return
        if self.hide_btn:
            self.cv.after_cancel(self.hide_btn)
        else:
            cv, u, f = self.cv, self.u, self.F(2.8)
            w, h = f.measure("收起考试大屏") + 5 * u, 5 * u
            x2 = self.R
            y2 = (81.5 * u) if dock_vis else (self.app.sh - 1.2 * u)
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
        self.phase, self.bar_fill, self.last_dock_sig = phase, None, None
        if self.hide_btn:
            cv.after_cancel(self.hide_btn)
            self.hide_btn = None
        self.head()
        mins = int(round((ex["end_ts"] - ex["start_ts"]) / 60))
        has_au = self.has_audio()
        if phase == "before":  # 须知 + 开考倒计时
            cv.create_text(L, 21 * u, text=ex["subject"], anchor="w", fill=self.INK_, font=self.F(10))
            sx = L + self.F(10).measure(ex["subject"]) + 3 * u
            cv.create_text(sx, 22.5 * u, text=f"{ex['start']} – {ex['end']}", anchor="w", fill="#cfe0f0", font=self.F(5, False, True))
            bx1 = R - 58 * u
            self.box(bx1, 13 * u, R, 37 * u)
            cv.create_text((bx1 + R) / 2, 18 * u, text="距离开考", fill=self.SOFT, font=self.F(3.4))
            self.count = cv.create_text((bx1 + R) / 2, 28.5 * u, text="", fill=self.BLUE, font=self.F(12, True, True))
            bot = 80.5 * u if has_au else 94 * u
            self.box(L, 42 * u, R, bot)
            cv.create_text(L + 4.5 * u, 48 * u, text="考场须知", anchor="w", fill=self.GOLD, font=self.F(3.8 if has_au else 4))
            lines = (ex.get("notice") or [])[:5 if has_au else 6]
            size = (3.4 if len(lines) <= 4 else 3.0) if has_au else (4 if len(lines) <= 4 else 3.4)
            for k, ln in enumerate(lines):
                cv.create_text(L + 5 * u, (55 if has_au else 58) * u + k * size * 1.65 * u, text="·  " + ellipsis(ln, self.F(size, False), R - L - 12 * u),
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
            self.bar = (L, 55.5 * u if has_au else 57 * u, R, 57.5 * u if has_au else 59 * u)
            round_rect(cv, *self.bar, 1 * u, fill="#1d3550", width=0)
            lines = (ex.get("notice") or [])[:3]
            if lines:
                cv.create_text(L, (62.5 if has_au else 66) * u, text="考场提醒", anchor="w", fill=self.SOFT, font=self.F(3.0 if has_au else 3.2))
                for k, ln in enumerate(lines):
                    cv.create_text(L + 1 * u, (68.2 if has_au else 73) * u + k * (5.0 if has_au else 6.2) * u,
                                   text="·  " + ellipsis(ln, self.F(3.2 if has_au else 3.6, False), R - L - 62 * u),
                                   anchor="w", fill=self.INK_, font=self.F(3.2 if has_au else 3.6, False))
            nt = self.next_text()
            if nt:
                nx1 = R - 54 * u
                ny1, ny2 = (61 * u, 80.5 * u) if has_au else (64 * u, 92 * u)
                self.box(nx1, ny1, R, ny2)
                cv.create_text(nx1 + 3.4 * u, ny1 + (ny2 - ny1) * 0.22, text="下一场", anchor="w", fill=self.SOFT, font=self.F(2.7 if has_au else 3))
                cv.create_text(nx1 + 3.4 * u, ny1 + (ny2 - ny1) * 0.52, text=nt[0], anchor="w", fill=self.INK_, font=self.F(4.4 if has_au else 4.8))
                cv.create_text(nx1 + 3.4 * u, ny1 + (ny2 - ny1) * 0.80, text=nt[1], anchor="w", fill="#cfe0f0", font=self.F(3.0 if has_au else 3.4, False, True))
        else:  # 结束：停笔
            cx = self.app.sw / 2
            cv.create_text(cx, 38 * u, text="考试结束", fill=self.INK_, font=self.F(15))
            cv.create_text(cx, 54 * u, text="请停止作答，等待监考老师收卷", fill=self.GOLD, font=self.F(5))
            nt = self.next_text()
            if nt:
                cv.create_text(cx, 70 * u, text=f"下一场　{nt[0]}　{nt[1]}", fill=self.SOFT, font=self.F(4, False))

    # ---- 考试听力控制与底部渲染 ----
    def _adopt_cmd_audio(self, data, start_at):
        """核对控制指令携带的音频编号与 SHA-256；若与旧看板不一致，立即采纳指令权威元数据，绝不静默播放旧录音"""
        cur_au = self.ex.get("audio") or {}
        cmd_aid = str(data.get("audio_id") or "").strip()
        cmd_sha = str(data.get("sha256") or "").strip()
        if not cmd_aid:
            return cur_au
        if cur_au.get("id") != cmd_aid or (cmd_sha and cur_au.get("sha256") != cmd_sha):
            new_au = {
                "id": cmd_aid,
                "name": data.get("title") or f"{self.ex.get('subject') or '考试'}听力",
                "ext": data.get("ext") or "mp3",
                "size": int(data.get("size") or 0),
                "sha256": cmd_sha,
                "duration": float(data.get("duration") or 0),
                "start": cur_au.get("start") or self.ex.get("start", ""),
                "start_ts": cur_au.get("start_ts") if cur_au.get("start_ts") is not None else start_at,
                "url": data.get("url") or f"/exam-audio/{cmd_aid}",
            }
            self.ex = {**self.ex, "audio": new_au}
            self.key = exam_key(self.ex)
            self._cmd_audio = (cmd_aid, dict(new_au), time.time(), getattr(self.app, "board_gen", 0))
            self._not_ready_logged = False
            if self.playing_audio_id and self.playing_audio_id != cmd_aid:
                self.app.audio_player.stop()
                self.playing_audio_id = ""
            if hasattr(self.app, "fetch_board_now"):
                threading.Thread(target=self.app.fetch_board_now, daemon=True).start()
            self.last_dock_sig = None
            if self.phase:
                self.draw(self.phase)
            log(f"检测到换曲指令（{cur_au.get('id') or '无'} -> {cmd_aid}），已按指令元数据优先切换，不播放旧录音")
            return new_au
        return cur_au

    def start_audio(self, seek=0.0, silent=False, trigger_download=True, expected_id="", expected_sha=""):
        au = self.ex.get("audio") or {}
        if not au.get("id"):
            return False
        if expected_id and au.get("id") != expected_id:
            log(f"音频编号不匹配（期望 {expected_id}，当前 {au.get('id')}），拒绝播放旧文件")
            return False
        if expected_sha and au.get("sha256") and au.get("sha256") != expected_sha:
            log(f"音频校验值不匹配（{au.get('id')}），拒绝播放旧文件")
            return False
        path = self.app.audio_cache.get_ready_path(au.get("id"), expected_sha or au.get("sha256", ""))
        if not path:
            if trigger_download:
                self.app.audio_cache.request_now(au)
            if not silent and not self._not_ready_logged:
                self._not_ready_logged = True
                log("本地听力文件尚未就绪，已触发优先下载")
            return False
        self._not_ready_logged = False
        self.test_gen += 1
        self.expand()
        if self.audio_stopped_manual:
            self._cmd_audio = (au["id"], dict(au), time.time())  # 停止后重播：重新保护当前录音，防迟到看板回退
        self.audio_auto_done = True
        self.audio_stopped_manual = False
        self.pending_play_at = None
        self.pending_audio_id = ""
        self.pending_audio_sha = ""
        self.confirm_act = None
        ok = self.app.audio_player.play_file(path, float(au.get("duration") or 0), seek=seek, mode="exam")
        self.playing_audio_id = au.get("id", "") if ok else ""
        log(f"考试听力开播：{self.ex['subject']} audio={au.get('id')} seek={seek:.1f}s ok={ok}")
        return ok

    def start_test_audio(self):
        snap = self.app.audio_player.snapshot()
        if snap["state"] in ("playing", "paused") and snap["mode"] == "exam":
            return  # 正式听力播放期间不打断
        self.expand()
        self.confirm_act = None
        self.test_gen += 1
        tgen, sgen = self.test_gen, self.app.session_gen

        def run():
            p = self.app.audio_cache.fetch_test_audio()

            def fire():
                if not self.win.winfo_exists() or sgen != self.app.session_gen or tgen != self.test_gen:
                    return
                if self.phase not in ("before", "during"):
                    return
                cur = self.app.audio_player.snapshot()
                if cur["state"] in ("playing", "paused") and cur["mode"] == "exam":
                    return
                self.playing_audio_id = ""
                if not p:
                    # 试音音频本身下载失败也如实上报异常，且不阻断正式听力自动起播
                    report_exam_audio_status({"exam_id": self.ex["exam_id"], "idx": self.ex["idx"],
                                              "audio_id": (self.ex.get("audio") or {}).get("id", ""),
                                              "play_state": "error", "play_err": "试音音频下载失败", "pos": 0, "dur": 0})
                    return
                self.app.audio_player.play_file(p, 10.0, 0.0, mode="test")
            self.app.root.after(0, fire)
        threading.Thread(target=run, daemon=True).start()
        log(f"开始 10 秒考前试音：{self.ex['subject']}")

    def handle_cmd(self, data):
        act = data.get("action")
        if act in ("test", "play") and self.phase == "after":
            return  # 考试已结束：不响应试音或开播指令
        if act == "test":
            self.start_test_audio()
        elif act == "play":
            start_at = float(data.get("start_at") or (time.time() + self.app.offset))
            now = time.time() + self.app.offset
            self.test_gen += 1
            self.audio_auto_done = True
            self.audio_stopped_manual = False
            au = self._adopt_cmd_audio(data, start_at)
            want_id = str(data.get("audio_id") or au.get("id") or "").strip()
            want_sha = str(data.get("sha256") or au.get("sha256") or "").strip()
            if not want_id or au.get("id") != want_id:
                return
            self._cmd_audio = (want_id, dict(au), time.time())  # 同曲重新发令也刷新防旧看板覆盖的保护
            self.pending_audio_id = want_id
            self.pending_audio_sha = want_sha
            if not self.app.audio_cache.get_ready_path(want_id, want_sha):
                self.app.audio_cache.request_now(au)
                self.pending_play_at = start_at
                self.expand()
            elif start_at <= now:
                if not self.start_audio(seek=max(0.0, now - start_at), expected_id=want_id, expected_sha=want_sha):
                    self.pending_play_at = start_at
                    self.expand()
            else:
                self.pending_play_at = start_at
                self.expand()
        elif act == "stop":
            self.test_gen += 1
            self.pending_play_at = None
            self.pending_audio_id = ""
            self.pending_audio_sha = ""
            self.audio_stopped_manual = True
            self.confirm_act = None
            self.playing_audio_id = ""
            self.app.audio_player.stop()

    def _dock_click(self, act):
        now = time.time()
        snap = self.app.audio_player.snapshot()
        if act == "pause_resume":
            if not self.app.audio_player.can_seek():
                return
            self.confirm_act = None
            if snap["state"] == "playing":
                self.app.audio_player.pause()
            elif snap["state"] == "paused":
                self.app.audio_player.resume()
        elif act == "stop_test":
            self.test_gen += 1
            self.confirm_act = None
            self.playing_audio_id = ""
            self.app.audio_player.stop()
        elif act in ("replay", "stop", "early"):
            if self.confirm_act and self.confirm_act[0] == act and now < self.confirm_act[1]:
                self.confirm_act = None
                if act in ("early", "replay"):
                    self.start_audio(0.0)
                elif act == "stop":
                    self.test_gen += 1
                    self.pending_play_at = None
                    self.pending_audio_id = ""
                    self.pending_audio_sha = ""
                    self.audio_stopped_manual = True
                    self.playing_audio_id = ""
                    self.app.audio_player.stop()
            else:
                self.confirm_act = (act, now + 4.0)
        self.last_dock_sig = None
        self.draw_audio_dock(time.time() + self.app.offset)

    def _dock_btn(self, x1, y1, x2, y2, text, act, bg="#1b344e", outline="#32577c", fg="#b7cee3"):
        cv, u = self.cv, self.u
        tag = f"aud_btn_{act}"
        round_rect(cv, x1, y1, x2, y2, 1.5 * u, fill=bg, outline=outline, width=1.8, tags=("aud_dock", tag))
        cv.create_text((x1 + x2) / 2, (y1 + y2) / 2, text=text, fill=fg, font=self.F(2.4, True), tags=("aud_dock", tag))

    def draw_audio_dock(self, now):
        snap = self.app.audio_player.snapshot()
        raw_st = snap["state"]
        # 试音结束后（test_done）自动恢复安静待播态，绝不误显“已播完”
        st = "idle" if raw_st == "test_done" else raw_st
        if (not self.has_audio() and st not in ("playing", "paused", "testing", "test_error", "error")) or self.phase not in ("before", "during"):
            self.cv.delete("aud_dock")
            self.last_dock_sig = None
            return
        au = self.ex.get("audio") or {}
        ready_path = self.app.audio_cache.get_ready_path(au.get("id"), au.get("sha256", "")) if au.get("id") else None
        pos = snap["pos"]
        dur = (10.0 if st == "testing" else (snap["dur"] or float(au.get("duration") or 0)))
        if self.confirm_act and time.time() >= self.confirm_act[1]:
            self.confirm_act = None
        cact = self.confirm_act[0] if self.confirm_act else ""
        # 构建签名：只在画面内容变化时重绘
        left_wait = max(0, int((self.pending_play_at or au.get("start_ts", now)) - now + 0.999)) if st == "idle" and au else 0
        sig = (st, int(pos), int(dur), bool(ready_path), left_wait, cact, self.audio_stopped_manual, self.pending_play_at is not None, snap.get("err", ""))
        if sig == self.last_dock_sig:
            return
        self.last_dock_sig = sig
        cv, u, L, R = self.cv, self.u, self.L, self.R
        cv.config(cursor="")
        cv.delete("aud_dock")
        y1, y2 = 83.5 * u, 96.5 * u
        mid_y = (y1 + y2) / 2
        border = "#695d36" if st == "paused" else "#4b7eb3" if st == "testing" else "#284b6e"
        fill = "#162d46" if st == "testing" else "#14273c"
        round_rect(cv, L, y1, R, y2, 2.2 * u, fill=fill, outline=border, width=2, tags="aud_dock")

        # 左侧状态图标（矢量绘制/纯中文，确保麒麟 Linux 与 Windows 均无缺字方块）
        ix1, ix2 = L + 2.4 * u, L + 8.4 * u
        ic_cx = (ix1 + ix2) / 2
        ic_bg = "#1b3a54" if st in ("playing", "testing") else "#2e2e20" if st == "paused" else "#153b35" if ready_path else "#243448"
        ic_fg = self.GOLD if st == "paused" else "#6ee7b7" if (st in ("idle", "done") and ready_path) else self.BLUE
        round_rect(cv, ix1, mid_y - 3 * u, ix2, mid_y + 3 * u, 1.5 * u, fill=ic_bg, outline=ic_fg, width=1.5, tags="aud_dock")
        if st == "paused":
            cv.create_rectangle(ic_cx - 1.15 * u, mid_y - 1.3 * u, ic_cx - 0.35 * u, mid_y + 1.3 * u, fill=ic_fg, width=0, tags="aud_dock")
            cv.create_rectangle(ic_cx + 0.35 * u, mid_y - 1.3 * u, ic_cx + 1.15 * u, mid_y + 1.3 * u, fill=ic_fg, width=0, tags="aud_dock")
        elif st == "playing":
            cv.create_polygon(ic_cx - 0.9 * u, mid_y - 1.35 * u, ic_cx - 0.9 * u, mid_y + 1.35 * u, ic_cx + 1.35 * u, mid_y, fill=ic_fg, width=0, tags="aud_dock")
        elif st == "testing":
            cv.create_text(ic_cx, mid_y, text="试", fill=ic_fg, font=self.F(2.8, True), tags="aud_dock")
        elif st in ("idle", "done") and ready_path:
            cv.create_line(ic_cx - 1.2 * u, mid_y + 0.1 * u, ic_cx - 0.25 * u, mid_y + 1.15 * u, ic_cx + 1.35 * u, mid_y - 1.05 * u,
                           fill=ic_fg, width=max(2, int(0.38 * u)), capstyle="round", joinstyle="round", tags="aud_dock")
        else:
            cv.create_text(ic_cx, mid_y, text="听", fill=ic_fg, font=self.F(2.8, True), tags="aud_dock")

        tx = ix2 + 2.0 * u
        subj = self.ex["subject"]
        bh = 6.2 * u
        by1, by2 = mid_y - bh / 2, mid_y + bh / 2
        rx = R - 2.4 * u

        if st == "testing":
            rem = max(0, int(10.0 - pos + 0.999))
            tf_, sf_ = self.F(2.7, True), self.F(1.9, False)
            bw = self.F(2.4, True).measure("结束试音") + 4.4 * u
            self._dock_btn(rx - bw, by1, rx, by2, "结束试音", "stop_test")
            max_tw = max(12 * u, rx - bw - tx - 20 * u)
            t1 = ellipsis("正在播放考前试音 · 请确认教室音响音量", tf_, max_tw)
            t2 = ellipsis(f"试音结束后自动恢复安静等待（剩余 {rem} 秒）", sf_, max_tw)
            cv.create_text(tx, mid_y - 1.6 * u, text=t1, anchor="w", fill="#bfe3ff", font=tf_, tags="aud_dock")
            cv.create_text(tx, mid_y + 2.0 * u, text=t2, anchor="w", fill=self.SOFT, font=sf_, tags="aud_dock")
            px1 = tx + max(tf_.measure(t1), sf_.measure(t2)) + 3.0 * u
            px2 = rx - bw - 3.0 * u
            if px2 > px1 + 10 * u:
                round_rect(cv, px1, mid_y - 0.7 * u, px2, mid_y + 0.7 * u, 0.7 * u, fill="#1e3752", width=0, tags="aud_dock")
                frac = min(1.0, max(0.05, pos / 10.0))
                round_rect(cv, px1, mid_y - 0.7 * u, px1 + (px2 - px1) * frac, mid_y + 0.7 * u, 0.7 * u, fill=self.BLUE, width=0, tags="aud_dock")
            return

        if st in ("playing", "paused"):
            can_sk = self.app.audio_player.can_seek()
            tf_, sf_ = self.F(2.7, True), self.F(1.9, False)
            t_lbl = f"{subj}听力已暂停" if st == "paused" else f"{subj}听力播放中"
            s_lbl = ("点右侧「继续」从断点恢复" if st == "paused"
                     else ("本地缓存 · 断网不中断" if can_sk else "本机缺 ffplay · 仅支持重播/停止"))
            t_col = self.GOLD if st == "paused" else self.INK_

            # 右侧控场键：停止、重播；仅具备断点定位能力时才显示「暂停/继续」
            stop_lbl = "再点确认停止" if cact == "stop" else "停止"
            sw_ = self.F(2.4, True).measure(stop_lbl) + 4.4 * u
            self._dock_btn(rx - sw_, by1, rx, by2, stop_lbl, "stop",
                           bg="#3d2229" if cact == "stop" else "#1b344e",
                           outline=self.RED if cact == "stop" else "#32577c",
                           fg="#ffc2c2" if cact == "stop" else "#b7cee3")
            rx -= sw_ + 1.4 * u

            rep_lbl = "再点确认重播" if cact == "replay" else "重播"
            rw_ = self.F(2.4, True).measure(rep_lbl) + 4.4 * u
            self._dock_btn(rx - rw_, by1, rx, by2, rep_lbl, "replay",
                           bg="#3b321d" if cact == "replay" else "#1b344e",
                           outline=self.GOLD if cact == "replay" else "#32577c",
                           fg=self.GOLD if cact == "replay" else "#b7cee3")
            rx -= rw_ + (1.4 * u if can_sk else 2.6 * u)

            if can_sk:
                pr_lbl = "继续" if st == "paused" else "暂停"
                pw_ = self.F(2.4, True).measure(pr_lbl) + 4.8 * u
                self._dock_btn(rx - pw_, by1, rx, by2, pr_lbl, "pause_resume",
                               bg="#3b321d" if st == "paused" else "#1b344e",
                               outline=self.GOLD if st == "paused" else "#32577c",
                               fg=self.GOLD if st == "paused" else "#b7cee3")
                rx -= pw_ + 2.6 * u

            # 时间与进度条（动态量测左侧文案宽度，杜绝任何字体下重叠）
            tm_txt = f"{fmt_mmss(pos)} / {fmt_mmss(dur)}"
            tm_f = self.F(2.8, True, digits=True)
            tw_ = tm_f.measure(tm_txt)
            cv.create_text(rx, mid_y, text=tm_txt, anchor="e", fill=self.GOLD if st == "paused" else "#cfe0f0", font=tm_f, tags="aud_dock")
            px2 = rx - tw_ - 2.4 * u
            max_lw = max(14 * u, min(36 * u, px2 - tx - 14 * u))
            t_lbl = ellipsis(t_lbl, tf_, max_lw)
            s_lbl = ellipsis(s_lbl, sf_, max_lw)
            cv.create_text(tx, mid_y - 1.6 * u, text=t_lbl, anchor="w", fill=t_col, font=tf_, tags="aud_dock")
            cv.create_text(tx, mid_y + 2.0 * u, text=s_lbl, anchor="w", fill=self.SOFT, font=sf_, tags="aud_dock")
            px1 = tx + max(tf_.measure(t_lbl), sf_.measure(s_lbl)) + 2.6 * u
            if px2 > px1 + 10 * u:
                round_rect(cv, px1, mid_y - 0.8 * u, px2, mid_y + 0.8 * u, 0.8 * u, fill="#1e3752", width=0, tags="aud_dock")
                frac = min(1.0, max(0.02, pos / max(1.0, dur)))
                round_rect(cv, px1, mid_y - 0.8 * u, px1 + (px2 - px1) * frac, mid_y + 0.8 * u, 0.8 * u,
                           fill=self.GOLD if st == "paused" else self.BLUE, width=0, tags="aud_dock")
            return

        # 空闲 / 待播 / 播完 / 异常
        if st == "done":
            title = f"{subj}听力已播完（{fmt_mmss(dur)}）"
            sub = "如需重新播放，监考老师可点右侧「重播」"
            t_col = "#d6f5e8"
        elif st == "test_error":
            title = f"考前试音失败：{snap['err'] or '请检查一体机音响'}"
            sub = "请检查音响连接；到点正式开考仍会自动尝试播放听力"
            t_col = "#ffd166"
        elif st == "error":
            title = f"{subj}听力播放失败：{snap['err'] or '请检查一体机音响'}"
            sub = "可点右侧重试，或改用本机播放器"
            t_col = "#ffb4b4"
        elif not ready_path:
            title = f"{subj}听力正在后台下载缓存…"
            sub = "下载完成并校验通过后将自动就绪"
            t_col = "#cfe0f0"
        elif self.pending_play_at:
            title = f"{subj}听力即将同步开始播放（{left_wait} 秒）"
            sub = "老师已在工作台下发同步开播指令"
            t_col = "#d6f5e8"
        elif self.audio_stopped_manual:
            title = f"{subj}听力已停止"
            sub = "如需重新开始，可点右侧按钮"
            t_col = self.SOFT
        else:
            title = f"{subj}听力已就绪 · 将于 {au.get('start', '')} 自动开始播放"
            sub = f"本地音频校验通过（{fmt_mmss(dur)}）" + (f" · 倒计时 {fmt_mmss(left_wait)}" if left_wait > 0 else "") + " · 起播时自动屏蔽开考提示音"
            t_col = "#d6f5e8"

        btn_space = 0.0
        if ready_path:
            act_k = "replay" if (st in ("done", "error") or self.audio_stopped_manual) else "early"
            lbl = ("再点确认重播" if cact == "replay" else "重播") if act_k == "replay" else ("再点确认开播" if cact == "early" else "提前开播")
            bw = self.F(2.4, True).measure(lbl) + 4.4 * u
            self._dock_btn(rx - bw, by1, rx, by2, lbl, act_k,
                           bg="#3b321d" if cact == act_k else "#1b344e",
                           outline=self.GOLD if cact == act_k else "#32577c",
                           fg=self.GOLD if cact == act_k else "#b7cee3")
            btn_space = bw + 2.4 * u

        avail_w = max(16 * u, rx - btn_space - tx)
        tf_, sf_ = self.F(2.6, True), self.F(1.9, False)
        cv.create_text(tx, mid_y - 1.6 * u, text=ellipsis(title, tf_, avail_w), anchor="w", fill=t_col, font=tf_, tags="aud_dock")
        cv.create_text(tx, mid_y + 2.0 * u, text=ellipsis(sub, sf_, avail_w), anchor="w", fill=self.SOFT, font=sf_, tags="aud_dock")

    def _sync_audio_schedule(self, now):
        snap = self.app.audio_player.snapshot()
        active_set = ("playing", "paused", "testing", "error", "test_error")
        active_audio = snap["state"] in active_set or self.last_reported_state in active_set
        if (not self.has_audio() and not active_audio) or self.phase not in ("before", "during"):
            return
        au = self.ex.get("audio") or {}
        if self.has_audio():
            dur = float(au.get("duration") or 0)
            # 1. 手动同步发令到点起播（超时 > 90s 或超过总时长则放弃防诈尸播放；等待期间只查缓存不反复触发 request_now）
            if self.pending_play_at is not None and now >= self.pending_play_at:
                lag = now - self.pending_play_at
                if lag > 90.0 or (dur > 0 and lag >= dur):
                    log(f"考试听力手动起播等待超时（{lag:.0f}s），已放弃")
                    self.pending_play_at = None
                    self.pending_audio_id = ""
                    self.pending_audio_sha = ""
                else:
                    self.start_audio(seek=max(0.0, lag), silent=True, trigger_download=False,
                                     expected_id=getattr(self, "pending_audio_id", ""),
                                     expected_sha=getattr(self, "pending_audio_sha", ""))
            # 2. 按排定起播时间自动同步起播（误差 < 1 秒；允许抢占试音，且绝不被试音结束 test_done 或试音报错 test_error/error 拦住）
            elif (not self.audio_auto_done and not self.audio_stopped_manual
                  and snap["state"] in ("idle", "test_done", "test_error", "testing", "error")
                  and au["start_ts"] <= now < au["start_ts"] + 90):
                if self.app.audio_cache.get_ready_path(au["id"], au.get("sha256", "")):
                    self.start_audio(seek=max(0.0, now - au["start_ts"]), silent=True, trigger_download=False)
        # 3. 定期上报当前播放状态给工作台（真实上报 error 及原因；播放中始终上报实际正在播放的 playing_audio_id）
        snap = self.app.audio_player.snapshot()
        raw_st = snap["state"]
        rep_st = ("idle" if raw_st == "test_done"
                  else "error" if raw_st in ("error", "test_error")
                  else (raw_st if raw_st in ("idle", "playing", "paused", "done", "testing") else "idle"))
        rep_err = (snap.get("err") or "") if rep_st == "error" else ""
        prev_err = getattr(self, "last_reported_err", "")
        if (rep_st != self.last_reported_state or rep_err != prev_err
                or (rep_st in ("playing", "paused", "testing") and time.time() - self.last_report_at >= 3.0)):
            self.last_reported_state = rep_st
            self.last_reported_err = rep_err
            self.last_report_at = time.time()
            rep_aid = self.playing_audio_id if (raw_st in ("playing", "paused", "done", "error") and self.playing_audio_id) else au.get("id", "")
            pl = {
                "exam_id": self.ex["exam_id"], "idx": self.ex["idx"], "audio_id": rep_aid,
                "play_state": rep_st,
                "pos": snap["pos"], "dur": snap["dur"] or au.get("duration", 0),
            }
            if rep_err:
                pl["play_err"] = rep_err
            report_exam_audio_status(pl)

    def _refresh_clock_count(self, now, phase):
        ex, u = self.ex, self.u
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
                if not self.mini.winfo_exists():
                    self.mini = None
                    if self.hidden:
                        self.expand()
                else:
                    try:
                        self.mini_cv.itemconfig(self.mini_count, text=("剩 " if phase == "during" else "距开考 ") + text,
                                                fill=self.RED if last else self.GOLD if phase == "during" else self.BLUE)
                    except tk.TclError:
                        self.mini = None
                        if self.hidden:
                            self.expand()
        elif self.mini:
            if not self.mini.winfo_exists():
                self.mini = None
                if self.hidden:
                    self.expand()
            else:
                try:
                    self.mini_cv.itemconfig(self.mini_count, text="考试结束", fill=self.GOLD)
                except tk.TclError:
                    self.mini = None
                    if self.hidden:
                        self.expand()

    def tick(self):
        if not self.win.winfo_exists():
            # 若考试大屏被系统/热键外部关闭，自动清理引用、停播音频并恢复息屏策略
            if self.app.exam is self:
                self.app.exam = None
                if (self.has_audio() or self.app.audio_player.snapshot()["state"] in ("playing", "paused", "testing", "error", "test_error")
                        or self.last_reported_state in ("playing", "paused", "testing", "error")):
                    self.test_gen += 1
                    self.playing_audio_id = ""
                    self.app.audio_player.stop()
                    report_exam_audio_status({"exam_id": self.ex["exam_id"], "idx": self.ex["idx"],
                                              "audio_id": (self.ex.get("audio") or {}).get("id", ""), "play_state": "idle", "pos": 0, "dur": 0})
                self.app.x11.allow_sleep()
            return
        now = time.time() + self.app.offset
        ex = self.ex
        phase = self.phase_of(now)
        if phase != self.phase:
            if self.phase and self.hidden:  # 开考、考试结束：自动回到大屏
                self.expand()
            if phase == "after" and (self.has_audio() or self.app.audio_player.snapshot()["state"] in ("playing", "paused", "testing", "error", "test_error")
                                     or self.last_reported_state in ("playing", "paused", "testing", "error")):
                self.test_gen += 1
                self.pending_play_at = None
                self.playing_audio_id = ""
                self.app.audio_player.stop()
                self.last_reported_state = "idle"
                self.last_reported_err = ""
                report_exam_audio_status({"exam_id": ex["exam_id"], "idx": ex["idx"],
                                          "audio_id": (ex.get("audio") or {}).get("id", ""), "play_state": "idle", "pos": 0, "dur": 0})
            self.draw(phase)
        self._refresh_clock_count(now, phase)
        self._sync_audio_schedule(now)
        self.draw_audio_dock(now)
        self.speak(now)
        fast_tick = (self.has_audio() or self.app.audio_player.snapshot()["state"] in ("playing", "paused", "testing", "error", "test_error")
                     or self.last_reported_state in ("playing", "paused", "testing", "error"))
        self.win.after(500 if fast_tick else 1000, self.tick)

    def speak(self, now):
        """开考、剩 15 分钟、结束各播报一次；晚了 90 秒以上（比如刚开机）就不补了。
        若本场配置了听力且正在播报或处于听力时间窗口内，自动屏蔽普通"考试开始"与"剩 15 分钟"语音防冲突"""
        ex = self.ex
        au = ex.get("audio")
        snap = self.app.audio_player.snapshot()
        moments = [("start", ex["start_ts"]), ("end", ex["end_ts"])]
        if ex["end_ts"] - ex["start_ts"] > 20 * 60:
            moments.append(("warn", ex["end_ts"] - 15 * 60))
        for name, at in moments:
            k = (ex["key"], name)
            if at <= now < at + 90 and k not in self.app.exam_played:
                self.app.exam_played.add(k)
                if name == "start" and (au or snap["state"] in ("playing", "paused", "testing")):
                    a_s = au["start_ts"] if au else at
                    a_e = a_s + max(60, float((au or {}).get("duration") or 0)) + 15
                    pend_near = self.pending_play_at is not None and abs(self.pending_play_at - at) <= 15
                    if (au and a_s - 10 <= at <= a_e) or pend_near or snap["state"] in ("playing", "paused", "testing"):
                        log(f"考试听力期间屏蔽普通开考语音：{ex['subject']}")
                        continue
                if name == "warn" and snap["state"] == "playing":
                    log(f"听力播放期间屏蔽剩 15 分钟语音（大屏文字照常变红提醒）：{ex['subject']}")
                    continue
                self.expand()  # 收起了的话，播报时自动回到大屏
                announce((ex.get("voice") or {}).get(name), speak=EXAM_SAY[name])
                log(f"考试播报：{ex['subject']} {EXAM_SAY[name]}")

    def close(self):
        self.test_gen += 1
        if (self.has_audio() or self.app.audio_player.snapshot()["state"] in ("playing", "paused", "testing", "error", "test_error")
                or self.last_reported_state in ("playing", "paused", "testing", "error")):
            self.playing_audio_id = ""
            self.app.audio_player.stop()
            report_exam_audio_status({"exam_id": self.ex["exam_id"], "idx": self.ex["idx"],
                                      "audio_id": (self.ex.get("audio") or {}).get("id", ""), "play_state": "idle", "pos": 0, "dur": 0})
        for w in (self.mini, self.win):
            try:
                if w:
                    w.destroy()
            except tk.TclError:
                pass


class ListenFloatBar:
    """平时「课堂 → 播放听力」（及考前提前试音/提前播）悬浮控制条：贴在右下角，支持 4K 等比缩放，不挡 PPT/电子白板，不抢翻页笔焦点"""
    BG, BORDER, INK_, SOFT, BLUE, GOLD, RED = "#122336", "#2b4f73", "#eaf2fb", "#9fb6cc", "#7cc4ff", "#ffd166", "#ff6b6b"

    def __init__(self, app, info):
        self.app = app
        self.info = dict(info)  # {listen_id?, exam_id?, idx?, exam_key?, title, audio_id, duration, sha256, ext, url}
        self.confirm_act = None
        self.pending_play_at = None
        self.last_sig = None
        self.done_at = None
        self.fail_msg = ""
        self.last_report_at = 0.0
        self.last_reported_state = None
        self.last_reported_err = ""
        self.test_gen = 0
        self._ticking = False
        sw, sh = app.sw, app.sh
        self.s = s = max(1.0, sh / 1080.0)  # 1080p=1.0, 4K=2.0 等比放大
        self.w = int(min(sw * 0.48, max(480 * s, 560 * s)))
        self.h = int(round(142 * s))
        self.win = tk.Toplevel(app.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.geometry(f"{self.w}x{self.h}+{sw - self.w - int(24 * s)}+{sh - self.h - int(72 * s)}")
        float_window(self.win)
        self.cv = tk.Canvas(self.win, width=self.w, height=self.h, bg=self.BG, highlightthickness=0)
        self.cv.pack()
        for act in ("pause_resume", "replay", "stop", "close"):
            tag = f"lf_{act}"
            self.cv.tag_bind(tag, "<Button-1>", lambda e, a=act: self._click(a))
            self.cv.tag_bind(tag, "<Enter>", lambda e: self.cv.config(cursor="hand2"))
            self.cv.tag_bind(tag, "<Leave>", lambda e: self.cv.config(cursor=""))
        app.fade_in(self.win)

    def start_tick(self):
        if not self._ticking:
            self._ticking = True
            self.tick()

    def lift(self):
        try:
            self.win.attributes("-topmost", True)
            self.win.lift()
        except tk.TclError:
            pass

    def _report_payload(self, play_state, pos=0, dur=0, play_err=""):
        lid = self.info.get("listen_id")
        eid = self.info.get("exam_id")
        idx = self.info.get("idx")
        out = None
        if lid is not None:
            out = {"listen_id": lid, "audio_id": self.info.get("audio_id", ""), "play_state": play_state, "pos": pos, "dur": dur}
        elif eid is not None and idx is not None:
            out = {"exam_id": eid, "idx": idx, "audio_id": self.info.get("audio_id", ""), "play_state": play_state, "pos": pos, "dur": dur}
        if out and play_err:
            out["play_err"] = play_err
        return out

    def ensure_cached(self):
        aid = self.info.get("audio_id") or ""
        sha = self.info.get("sha256") or ""
        if not aid:
            return None
        path = self.app.audio_cache.get_ready_path(aid, sha)
        if path:
            return path
        self.app.audio_cache.request_now({"id": aid, "ext": self.info.get("ext", "mp3"),
                                          "sha256": sha, "url": self.info.get("url") or f"/exam-audio/{aid}",
                                          "name": self.info.get("title", aid)})
        return None

    def start_play(self, seek=0.0, start_at=None):
        self.done_at = None
        self.fail_msg = ""
        self.pending_play_at = None
        self.confirm_act = None
        self.test_gen += 1
        path = self.ensure_cached()
        if path:
            self.app.audio_player.play_file(path, float(self.info.get("duration") or 0), seek=seek, mode="exam")
            return
        self.pending_play_at = float(start_at) if start_at else (time.time() + self.app.offset)

    def start_test(self):
        self.done_at = None
        self.fail_msg = ""
        self.pending_play_at = None
        self.confirm_act = None
        self.test_gen += 1
        tgen, sgen = self.test_gen, self.app.session_gen

        def run():
            p = self.app.audio_cache.fetch_test_audio()

            def fire():
                if not self.win.winfo_exists() or sgen != self.app.session_gen or tgen != self.test_gen:
                    return
                if self.app.listen_bar is not self:
                    return
                if not p:
                    self.fail_msg = "试音下载失败"
                    self.done_at = time.time()
                    self.last_sig = None
                    self.draw()
                    return
                cur = self.app.audio_player.snapshot()
                if cur["state"] in ("playing", "paused") and cur["mode"] == "exam":
                    return
                self.app.audio_player.play_file(p, 10.0, 0.0, mode="test")
            self.app.root.after(0, fire)
        threading.Thread(target=run, daemon=True).start()

    def _btn(self, x1, y1, x2, y2, text, act, bg="#1b344e", outline="#32577c", fg="#b7cee3"):
        tag = f"lf_{act}"
        round_rect(self.cv, x1, y1, x2, y2, 9 * self.s, fill=bg, outline=outline, width=1.5, tags=tag)
        self.cv.create_text((x1 + x2) / 2, (y1 + y2) / 2, text=text, fill=fg, font=self.app.font(int(15 * self.s), True), tags=tag)

    def _click(self, act):
        now = time.time()
        snap = self.app.audio_player.snapshot()
        if act == "pause_resume":
            self.confirm_act = None
            if snap["state"] == "playing":
                self.app.audio_player.pause()
            elif snap["state"] == "paused":
                self.app.audio_player.resume()
        elif act == "close":
            self.app.close_listen_bar()
            return
        elif act in ("replay", "stop"):
            if self.confirm_act and self.confirm_act[0] == act and now < self.confirm_act[1]:
                self.confirm_act = None
                if act == "replay":
                    self.start_play(0.0)
                else:
                    self.app.close_listen_bar()
                    return
            else:
                self.confirm_act = (act, now + 4.0)
        self.last_sig = None
        self.draw()

    def draw(self):
        snap = self.app.audio_player.snapshot()
        st, pos, dur = snap["state"], snap["pos"], snap["dur"] or float(self.info.get("duration") or 0)
        if self.confirm_act and time.time() >= self.confirm_act[1]:
            self.confirm_act = None
        cact = self.confirm_act[0] if self.confirm_act else ""
        waiting = self.pending_play_at is not None
        err_txt = snap.get("err", "") if st in ("error", "test_error") else (self.fail_msg if st == "idle" and self.fail_msg else "")
        show_err = (st in ("error", "test_error")) or bool(err_txt)
        sig = (st, int(pos), int(dur), cact, waiting, self.info.get("title"), err_txt)
        if sig == self.last_sig:
            return
        self.last_sig = sig
        cv, w, h, s, app = self.cv, self.w, self.h, self.s, self.app
        cv.config(cursor="")
        cv.delete("all")
        bcol = self.RED if show_err else self.GOLD if st == "paused" else self.BLUE if st == "testing" else self.BORDER
        cv.create_rectangle(1, 1, w - 2, h - 2, outline=bcol, width=max(2, int(2 * s)))
        cv.create_rectangle(0, 0, int(7 * s), h, fill=self.RED if show_err else self.GOLD if st == "paused" else self.BLUE, width=0)

        pad = int(22 * s)
        title_f = app.font(int(18 * s), True)
        st_lbl = ("试音中" if st == "testing" else "试音结束" if st == "test_done" else "试音失败" if st == "test_error"
                  else "已暂停" if st == "paused" else "已播完" if st == "done" else "播放失败" if show_err
                  else "准备中…" if waiting else "等待中" if st == "idle" else "播放中")
        tm_txt = (f"剩余 {max(0, int(10 - pos + 0.999))} 秒" if st == "testing"
                  else (err_txt[:14] if show_err and err_txt else f"{fmt_mmss(pos)} / {fmt_mmss(dur)}"))
        tm_f = app.font(int(16 * s), True, digits=True)
        tw = tm_f.measure(tm_txt)
        title = ellipsis(f"● {self.info.get('title') or '课堂听力'} · {st_lbl}", title_f, w - pad * 2 - tw - int(16 * s))
        cv.create_text(pad, int(28 * s), text=title, anchor="w",
                       fill=self.RED if show_err else self.GOLD if st == "paused" else self.INK_, font=title_f)
        cv.create_text(w - pad, int(28 * s), text=tm_txt, anchor="e",
                       fill=self.RED if show_err else self.GOLD if st == "paused" else "#cfe0f0", font=tm_f)

        # 进度条
        by = int(56 * s)
        bh_bar = int(10 * s)
        round_rect(cv, pad, by, w - pad, by + bh_bar, bh_bar / 2, fill="#1e3752", width=0)
        total = 10.0 if st == "testing" else max(1.0, dur)
        frac = 0.0 if st in ("idle", "error", "test_error") else (min(1.0, max(0.02, pos / total)) if not waiting else 0.08)
        if frac > 0:
            round_rect(cv, pad, by, pad + (w - pad * 2) * frac, by + bh_bar, bh_bar / 2,
                       fill=self.GOLD if st == "paused" else self.BLUE, width=0)

        # 底部触控按钮（纯中文标签，按缩放倍率自适应尺寸；仅具备定位能力时显示暂停/继续）
        y1, y2 = h - int(48 * s), h - int(12 * s)
        x = pad
        btn_f = app.font(int(15 * s), True)
        if st in ("playing", "paused"):
            can_sk = self.app.audio_player.can_seek()
            if can_sk:
                lbl = "继续" if st == "paused" else "暂停"
                bw = btn_f.measure(lbl) + int(34 * s)
                self._btn(x, y1, x + bw, y2, lbl, "pause_resume",
                          bg="#3b321d" if st == "paused" else "#1b344e",
                          outline=self.GOLD if st == "paused" else "#32577c",
                          fg=self.GOLD if st == "paused" else "#b7cee3")
                x += bw + int(10 * s)
            rlbl = "再点确认重播" if cact == "replay" else "重播"
            rw = btn_f.measure(rlbl) + int(32 * s)
            self._btn(x, y1, x + rw, y2, rlbl, "replay",
                      bg="#3b321d" if cact == "replay" else "#1b344e",
                      outline=self.GOLD if cact == "replay" else "#32577c",
                      fg=self.GOLD if cact == "replay" else "#b7cee3")
            x += rw + int(10 * s)
            slbl = "再点确认停止" if cact == "stop" else "停止"
            sw_ = btn_f.measure(slbl) + int(32 * s)
            self._btn(x, y1, x + sw_, y2, slbl, "stop",
                      bg="#3d2229" if cact == "stop" else "#1b344e",
                      outline=self.RED if cact == "stop" else "#32577c",
                      fg="#ffc2c2" if cact == "stop" else "#b7cee3")
            cv.create_text(w - pad, (y1 + y2) / 2, text="不挡课件" if can_sk else "仅重播/停止", anchor="e", fill="#6483a3", font=app.font(int(13 * s)))
        elif st == "testing":
            bw = btn_f.measure("结束试音") + int(32 * s)
            self._btn(x, y1, x + bw, y2, "结束试音", "close")
        else:
            if self.info.get("audio_id"):
                rlbl = "再点确认重播" if cact == "replay" else "重播"
                rw = btn_f.measure(rlbl) + int(32 * s)
                self._btn(x, y1, x + rw, y2, rlbl, "replay")
            cw = btn_f.measure("收起") + int(32 * s)
            self._btn(w - pad - cw, y1, w - pad, y2, "收起", "close")

    def tick(self):
        if not self.win.winfo_exists():
            # 若被一体机“一键清屏”外部关掉，清理引用并停播，防止音频失控及锁死自动更新
            if self.app.listen_bar is self:
                self.app.close_listen_bar()
            return
        now = time.time() + self.app.offset
        if self.pending_play_at is not None and now >= self.pending_play_at:
            dur = float(self.info.get("duration") or 0)
            lag = now - self.pending_play_at
            if lag > 90.0 or (dur > 0 and lag >= dur):
                log(f"课堂听力等待下载超时（{lag:.0f}s），已放弃")
                self.pending_play_at = None
                self.fail_msg = "音频下载超时"
                if self.done_at is None:
                    self.done_at = time.time()
            else:
                # 等待期间只查本地缓存，不反复调用 request_now 刷后台下载队列
                p = self.app.audio_cache.get_ready_path(self.info.get("audio_id") or "", self.info.get("sha256") or "")
                if p:
                    self.pending_play_at = None
                    self.fail_msg = ""
                    self.app.audio_player.play_file(p, dur, seek=max(0.0, lag), mode="exam")
        snap = self.app.audio_player.snapshot()
        raw_st = snap["state"]
        rep_st = ("idle" if raw_st == "test_done"
                  else "error" if (raw_st in ("error", "test_error") or (raw_st == "idle" and self.fail_msg))
                  else (raw_st if raw_st in ("idle", "playing", "paused", "done", "testing") else "idle"))
        rep_err = (snap.get("err") or self.fail_msg or "") if rep_st == "error" else ""
        if (rep_st != self.last_reported_state or rep_err != getattr(self, "last_reported_err", "")
                or (rep_st in ("playing", "paused", "testing") and time.time() - self.last_report_at >= 3.0)):
            pl = self._report_payload(rep_st, snap["pos"], snap["dur"] or self.info.get("duration", 0), play_err=rep_err)
            if pl:
                self.last_reported_state = rep_st
                self.last_reported_err = rep_err
                self.last_report_at = time.time()
                report_exam_audio_status(pl)
        if raw_st in ("done", "test_done") or (raw_st == "idle" and self.fail_msg):
            wait_sec = 1.5 if raw_st == "test_done" else 8.0
            if self.done_at is None:
                self.done_at = time.time()
            elif time.time() - self.done_at >= wait_sec:
                self.app.close_listen_bar()
                return
        self.draw()
        self.win.after(450, self.tick)

    def detach_without_stop(self):
        """无缝移交控制权给即将打开的 ExamBoard：只关闭浮窗，绝不掐断音频"""
        self.test_gen += 1
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    def close(self):
        self.test_gen += 1
        self.app.audio_player.stop()
        pl = self._report_payload("idle", 0, 0)
        if pl:
            report_exam_audio_status(pl)
        try:
            self.win.destroy()
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
        gen = app.session_gen

        def run():
            try:
                req = urllib.request.Request(TOOL_URL, data=json.dumps({**body, "class": CLASS}).encode(),
                                             headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=8).read()
                app.inbox.put(("poll", (gen, fetch_json(POLL_URL))))  # 不等下一轮，马上把计时 / 抽号拉回来显示
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


# ---------------- 桌面云大屏选班绑定窗口 ----------------
def extract_grade(cls_name):
    """从班级名里提取年级前缀（如 高一、高二、初三、七年级、2026级），班级多时自动分组显示"""
    m = re.match(r"^(高[一二三]|初[一二三]|[一二三四五六七八九]年级|\d{2,4}级)", str(cls_name or ""))
    return m.group(1) if m else ""


def group_classes(classes):
    """班级超过 12 个且能分成 2 个以上年级时返回分组标签列表；否则返回空列表"""
    if len(classes) <= 12:
        return []
    groups = {}
    order = []
    other = []
    for c in classes:
        g = extract_grade(c)
        if g:
            if g not in groups:
                groups[g] = []
                order.append(g)
            groups[g].append(c)
        else:
            other.append(c)
    if len(order) < 2:
        return []
    tabs = [("全部", classes)] + [(g, groups[g]) for g in order]
    if other:
        tabs.append(("其他", other))
    return tabs


class BindPill:
    """点"暂不绑定"后缩在右下角的小入口，不挡上课；点一下重新打开选班窗口"""

    def __init__(self, app):
        self.app = app
        self.w, self.h = 164, 44
        self.win = tk.Toplevel(app.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.geometry(f"{self.w}x{self.h}+{app.sw - self.w - 22}+{app.sh - self.h - 68}")
        float_window(self.win)
        cv = tk.Canvas(self.win, width=self.w, height=self.h, bg="#16a571", highlightthickness=0, cursor="hand2")
        cv.pack()
        cv.create_rectangle(0, 0, self.w - 1, self.h - 1, outline="#0f7a53")
        cv.create_text(self.w / 2, self.h / 2, text="绑定本机班级", fill="#ffffff", font=app.font(17, True))
        cv.bind("<Button-1>", lambda e: app.reopen_bind_window())

    def close(self):
        try:
            self.win.destroy()
        except tk.TclError:
            pass


class BindWindow:
    """桌面云 / 启动还原一体机首次开机选班绑定窗口：支持 70+ 班级按年级筛选和分页点选，无需键盘"""

    def __init__(self, app, state):
        self.app = app
        self.state = dict(state or {})
        self.selected = None
        self.active_tab = "全部"
        self.page = 0
        self.submitting = False
        self.status_msg = ""
        self.status_err = False
        sw, sh = app.sw, app.sh
        self.w, self.h = min(1320, int(sw * 0.84)), min(840, int(sh * 0.82))
        self.x, self.y = (sw - self.w) // 2, (sh - self.h) // 2
        self.backdrop = app.make_backdrop()
        self.win = tk.Toplevel(app.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.geometry(f"{self.w}x{self.h}+{self.x}+{self.y}")
        float_window(self.win)
        self.cv = tk.Canvas(self.win, width=self.w, height=self.h, bg="#ffffff", highlightthickness=0)
        self.cv.pack()
        self.draw()
        app.fade_in(self.win)

    def update_state(self, state):
        self.state = dict(state or {})
        avail = self.state.get("classes") or []
        if self.selected not in avail:
            self.selected = None
        self.submitting = False
        self.status_msg = ""
        self.status_err = False
        self.draw()

    def set_status(self, msg, err=False):
        self.status_msg = msg
        self.status_err = err
        self.submitting = False
        self.draw()

    def _pill_btn(self, x1, y1, x2, y2, text, fill, fg, cmd, px, outline="", tag=""):
        r = (y2 - y1) / 2
        tag = tag or f"b{id(cmd)}_{int(x1)}_{int(y1)}"
        round_rect(self.cv, x1, y1, x2, y2, r, fill=fill, outline=outline or fill, width=1.5 if outline else 0, tags=tag)
        self.cv.create_text((x1 + x2) / 2, (y1 + y2) / 2, text=text, fill=fg, font=self.app.font(px, True), tags=tag)
        if cmd:
            self.cv.tag_bind(tag, "<Button-1>", lambda e, fn=cmd: fn())
            self.cv.tag_bind(tag, "<Enter>", lambda e: self.cv.config(cursor="hand2"))
            self.cv.tag_bind(tag, "<Leave>", lambda e: self.cv.config(cursor=""))

    def draw(self):
        cv, w, h, app = self.cv, self.w, self.h, self.app
        cv.delete("all")
        band = max(58, int(h * 0.095))
        pad = int(w * 0.04)
        gradient(cv, 0, 0, w, band, "#3b9cf5", "#34d399")
        cv.create_rectangle(0, 0, w - 1, h - 1, outline=LINE)
        fs = max(18, int(band * 0.36))
        cv.create_oval(fs * 0.7, band / 2 - fs * 0.42, fs * 1.54, band / 2 + fs * 0.42, fill="#ffffff", width=0)
        cv.create_text(fs * 2.0, band / 2, text="班级助手 · 绑定本教室班级", anchor="w", fill="#ffffff", font=app.font(fs, True))
        mac = self.state.get("mac") or "检测中…"
        cv.create_text(w - pad, band / 2, text=f"本机 MAC：{mac}", anchor="e", fill="#ffffff", font=app.font(max(13, int(fs * 0.68))))

        is_open = bool(self.state.get("open"))
        classes = self.state.get("classes") or []
        total = self.state.get("total_classes") or len(classes)
        bound_n = self.state.get("bound_count") or 0
        sub_px = max(15, int(h * 0.024))
        sub_y = band + int(h * 0.048)

        foot_h = max(72, int(h * 0.125))
        foot_top = h - foot_h
        cv.create_line(pad, foot_top, w - pad, foot_top, fill=LINE, dash=(3, 3))

        pages = 1
        if not is_open:
            msg = self.state.get("message") or "管理员暂未开放大屏选班绑定，请在工作台「班级管理」里开启后重试"
            cx, cy = w / 2, (band + foot_top) / 2
            cv.create_oval(cx - 34, cy - 86, cx + 34, cy - 18, fill="#fff3dc", outline="#f5a524", width=2)
            cv.create_text(cx, cy - 52, text="!", fill="#d97706", font=app.font(34, True))
            cv.create_text(cx, cy + 16, text="暂未开放大屏选班绑定", fill=INK, font=app.font(max(22, int(h * 0.036)), True))
            cv.create_text(cx, cy + 62, text=msg, fill=MUTED, font=app.font(max(15, int(h * 0.023))), width=w - pad * 3, justify="center")
            cv.create_text(cx, cy + 104, text="开启后本页面会自动刷新（每 10 秒检查一次），也可点击下方「刷新」立即检查", fill="#94a3b8", font=app.font(max(13, int(h * 0.019))))
        elif not classes:
            cx, cy = w / 2, (band + foot_top) / 2
            cv.create_oval(cx - 34, cy - 86, cx + 34, cy - 18, fill="#e7f7f0", outline="#16a571", width=2)
            cv.create_line(cx - 14, cy - 52, cx - 3, cy - 41, cx + 16, cy - 62, fill="#16a571", width=4, capstyle="round", joinstyle="round")
            title = "所有班级都已绑定大屏" if total else "还没有创建任何班级"
            desc = (f"全校共 {total} 个班，已全部绑定。如需给某个班换教室，请管理员在工作台「班级管理」先点「解绑大屏」，再点下方刷新。"
                    if total else "请管理员先在工作台「班级管理」添加班级，再点下方「刷新」。")
            cv.create_text(cx, cy + 16, text=title, fill=INK, font=app.font(max(22, int(h * 0.036)), True))
            cv.create_text(cx, cy + 66, text=desc, fill=MUTED, font=app.font(max(15, int(h * 0.023))), width=w - pad * 3, justify="center")
        else:
            sub = f"请点选这台一体机所在的班级（共 {total} 个班，已绑 {bound_n} 个，待绑 {len(classes)} 个）"
            cv.create_text(pad, sub_y, text=sub, anchor="w", fill=MUTED, font=app.font(sub_px))

            tabs = group_classes(classes)
            grid_top = sub_y + int(h * 0.042)
            shown = classes
            if tabs:
                tab_names = [t[0] for t in tabs]
                if self.active_tab not in tab_names:
                    self.active_tab = "全部"
                tab_y = sub_y + int(h * 0.038)
                tab_h = max(36, int(h * 0.052))
                tab_px = max(14, int(h * 0.021))
                tf = app.font(tab_px, True)
                tx = pad
                for gname, gitems in tabs:
                    label = f"{gname} ({len(gitems)})"
                    tw = tf.measure(label) + 28
                    if tx + tw > w - pad:
                        break
                    on = gname == self.active_tab
                    self._pill_btn(tx, tab_y, tx + tw, tab_y + tab_h, label,
                                   "#16a571" if on else "#f1f5f9", "#ffffff" if on else "#334155",
                                   lambda g=gname: self.pick_tab(g), tab_px,
                                   outline="" if on else "#dde5ee", tag=f"tab_{gname}")
                    tx += tw + 10
                    if on:
                        shown = gitems
                grid_top = tab_y + tab_h + int(h * 0.024)

            status_room = int(h * 0.044) if self.status_msg else int(h * 0.018)
            grid_bottom = foot_top - status_room
            cols = 5 if w >= 960 else 4 if w >= 720 else 3
            rows = 5 if (grid_bottom - grid_top) >= 340 else 4 if (grid_bottom - grid_top) >= 250 else 3
            per_page = max(1, cols * rows)
            pages = max(1, (len(shown) + per_page - 1) // per_page)
            self.page = max(0, min(self.page, pages - 1))
            page_items = shown[self.page * per_page:(self.page + 1) * per_page]

            gx, gy = max(10, int(w * 0.013)), max(10, int(h * 0.016))
            cw = (w - 2 * pad - gx * (cols - 1)) / cols
            ch = min(68, (grid_bottom - grid_top - gy * (rows - 1)) / rows)
            cpx = max(15, min(22, int(ch * 0.36)))
            cf = app.font(cpx, True)
            for idx, cname in enumerate(page_items):
                r_i, c_i = divmod(idx, cols)
                x1 = pad + c_i * (cw + gx)
                y1 = grid_top + r_i * (ch + gy)
                sel = cname == self.selected
                tag = f"cls_{idx}"
                round_rect(cv, x1, y1, x1 + cw, y1 + ch, 12,
                           fill="#e7f7f0" if sel else "#ffffff",
                           outline="#16a571" if sel else "#dce5ee",
                           width=2.5 if sel else 1.5, tags=tag)
                if sel:  # 选中态用矢量小绿点对勾，避免老系统字体缺少符号变方框
                    r0 = min(10, ch * 0.18)
                    cx0, cy0 = x1 + 20 + r0, y1 + ch / 2
                    cv.create_oval(cx0 - r0, cy0 - r0, cx0 + r0, cy0 + r0, fill="#16a571", width=0, tags=tag)
                    cv.create_line(cx0 - r0 * 0.45, cy0, cx0 - r0 * 0.1, cy0 + r0 * 0.4, cx0 + r0 * 0.5, cy0 - r0 * 0.35,
                                   fill="#ffffff", width=2.2, capstyle="round", joinstyle="round", tags=tag)
                    label = ellipsis(cname, cf, cw - 36 - r0 * 2)
                    cv.create_text(x1 + cw / 2 + r0 * 0.6, y1 + ch / 2, text=label, fill="#0f7a53", font=cf, tags=tag)
                else:
                    label = ellipsis(cname, cf, cw - 18)
                    cv.create_text(x1 + cw / 2, y1 + ch / 2, text=label, fill=INK, font=cf, tags=tag)
                cv.tag_bind(tag, "<Button-1>", lambda e, c=cname: self.select_class(c))
                cv.tag_bind(tag, "<Enter>", lambda e: cv.config(cursor="hand2"))
                cv.tag_bind(tag, "<Leave>", lambda e: cv.config(cursor=""))

            if self.status_msg:
                cv.create_text(pad, foot_top - status_room / 2, text=self.status_msg, anchor="w",
                               fill="#dc2626" if self.status_err else "#0f7a53", font=app.font(max(14, int(h * 0.02))))

        # 底部操作栏：左侧分页或提示，右侧按钮
        bpx = max(15, min(21, int(foot_h * 0.27)))
        bf = app.font(bpx, True)
        bh = max(42, int(foot_h * 0.58))
        by1, by2 = foot_top + (foot_h - bh) / 2, foot_top + (foot_h + bh) / 2

        if pages > 1:
            px1 = pad
            pw = bf.measure("上一页") + 32
            self._pill_btn(px1, by1, px1 + pw, by2, "上一页",
                           "#f1f5f9" if self.page > 0 else "#f8fafc",
                           INK if self.page > 0 else "#cbd5e1",
                           self.prev_page if self.page > 0 else None, bpx, outline="#dce5ee", tag="pg_prev")
            mid_x = px1 + pw + 48
            cv.create_text(mid_x, (by1 + by2) / 2, text=f"{self.page + 1} / {pages}", fill=MUTED, font=bf)
            px2 = mid_x + 48
            self._pill_btn(px2, by1, px2 + pw, by2, "下一页",
                           "#f1f5f9" if self.page + 1 < pages else "#f8fafc",
                           INK if self.page + 1 < pages else "#cbd5e1",
                           self.next_page if self.page + 1 < pages else None, bpx, outline="#dce5ee", tag="pg_next")
        elif is_open and classes and not self.status_msg:
            cv.create_text(pad, (by1 + by2) / 2, text="绑定关系保存在服务器，电脑重启还原也不受影响",
                           anchor="w", fill="#94a3b8", font=app.font(max(13, int(bpx * 0.82))))

        rx = w - pad
        if is_open and classes:
            if self.submitting:
                lbl, bg, fn = "正在绑定…", "#86d3b3", None
            elif self.selected:
                lbl, bg, fn = f"确认绑定「{self.selected}」", "#16a571", self.confirm_bind
            else:
                lbl, bg, fn = "请先点选班级", "#b9d7ca", None
            bw = max(160, bf.measure(lbl) + 38)
            self._pill_btn(rx - bw, by1, rx, by2, lbl, bg, "#ffffff", fn, bpx, tag="btn_confirm")
            rx -= bw + 12

        rw = bf.measure("刷新") + 34
        self._pill_btn(rx - rw, by1, rx, by2, "刷新", "#eef3f8", INK, self.refresh, bpx, outline="#dce5ee", tag="btn_refresh")
        rx -= rw + 12

        hw = bf.measure("暂不绑定") + 30
        self._pill_btn(rx - hw, by1, rx, by2, "暂不绑定", "#f8fafc", MUTED, self.minimize, bpx, outline="#e2e8f0", tag="btn_later")

    def pick_tab(self, g):
        self.active_tab = g
        self.page = 0
        self.draw()

    def select_class(self, c):
        if self.submitting:
            return
        self.selected = c
        self.status_msg = ""
        self.status_err = False
        self.draw()

    def prev_page(self):
        if self.page > 0:
            self.page -= 1
            self.draw()

    def next_page(self):
        self.page += 1
        self.draw()

    def refresh(self):
        if self.submitting:
            return
        self.status_msg = "正在刷新班级列表…"
        self.status_err = False
        self.draw()
        self.app.trigger_device_check(force_show=True)

    def confirm_bind(self):
        if not self.selected or self.submitting:
            return
        self.submitting = True
        self.status_msg = f"正在绑定「{self.selected}」…"
        self.status_err = False
        self.draw()
        self.app.submit_device_bind(self.selected)

    def minimize(self):
        self.app.minimize_bind_window()

    def close(self):
        for w in (self.win, self.backdrop):
            try:
                if w:
                    w.destroy()
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
        self.deskb = self.desk_sig = None
        self.standby_forced = False
        self.standby_at = 0
        self.morning = None  # 早读页
        self.tooltab = None  # 屏幕右边「工具」小把手
        self.shooting = False  # 正在截图、上传（要一两秒，别重复截）
        self.morning_dismissed = None
        self.exam, self.exam_played, self.exam_rev, self.awake_at = None, set(), None, 0  # 考试大屏、已播报过的提示、考试安排的版本
        self.audio_cache = ExamAudioCache()
        self.audio_player = ExamAudioPlayer()
        self.listen_bar = None
        self.listen_fired = set()  # 已按定时自动播过的平时听力 (id, date, play_at)
        self.audio_cmds_done = set()
        self.handled = set()
        self.pending_uninstall_id = None
        self._uninstall_ack_inflight = None
        self.board_gen = 0
        self.pending_restart = False
        self.auto_mode = AUTO_MODE
        self.bound = bool(CLASS and KEY)
        self.session_gen = 0
        self.bind_win = None
        self.bind_pill = None
        self.bind_minimized = False
        self.bind_state = {}
        self.device_wake = threading.Event()
        self.bind_force_show = False
        self.fonts = {}
        fams = set(tkfont.families())
        fallback = next((f for f in ("Noto Sans CJK SC", "Source Han Sans SC", "WenQuanYi Micro Hei", "Droid Sans Fallback",
                                     "Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑")
                         if f in fams), "TkDefaultFont")
        self.families = fams
        self.family_fallback = fallback
        self.family = "MiSans" if "MiSans" in fams else fallback
        self.font_installing = set()
        if self.bound and "--restarted" not in sys.argv:  # 自动更新、装字体后的重启不再提示
            hello = MessageCard(self, {"id": 0, "text": f"{CLASS} 接收端已启动", "sender": "班级助手", "time": time.strftime("%H:%M"), "popup": False})
            hello.place(0)
            self.root.after(4000, hello.close)
        if self.bound:
            threading.Thread(target=ensure_desktop_icon, daemon=True).start()
        threading.Thread(target=clean_files, daemon=True).start()
        threading.Thread(target=guard_loop, daemon=True).start()
        threads = [self.poll_loop, self.board_loop, self.update_loop]
        if self.auto_mode:
            threads.append(self.device_loop)
        for target in threads:
            threading.Thread(target=target, daemon=True).start()
        self.root.after(200, self.process)
        self.root.after(3000, self.check_standby)
        log(f"接收端 v2 启动：模式={'桌面云自动认班' if self.auto_mode else '单班'} 班级={CLASS or '(待绑定)'} 服务器={SERVER} 字体={self.family} 声音={'系统自带（winmm）' if WIN else 'ffplay' if FFPLAY else os.path.basename(APLAY) + '（只能放 wav）' if APLAY else '无'} "
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
            if not self.bound or not POLL_URL:
                fails = 0
                time.sleep(1)
                continue
            gen, url, ack_url = self.session_gen, POLL_URL, ACK_URL
            try:
                data = fetch_json(url)
                if fails:
                    log("已重新连接服务器")
                fails = 0
                if data.get("rebind") and self.auto_mode:
                    if gen != self.session_gen:
                        time.sleep(1)
                        continue
                    p_uid = getattr(self, "pending_uninstall_id", None)
                    if p_uid:
                        if getattr(self, "_uninstall_ack_inflight", None) is not None:
                            time.sleep(1)
                            continue
                        # 上一次卸载回执可能已到服务端并解绑 MAC（只是响应丢包），先用本会话快照的 ack_url 幂等复核
                        ares = send_ack_sync(p_uid, ack_url=ack_url)
                        if ares == "acked":
                            self.inbox.put(("uninstall_ack_result", (gen, p_uid, "acked")))
                            time.sleep(1)
                            continue
                        if ares == "failed":
                            time.sleep(2)
                            continue
                        self.inbox.put(("uninstall_ack_result", (gen, p_uid, "cancelled")))
                    self.inbox.put(("rebind", (gen, "服务器已解除本机绑定")))
                    time.sleep(1)
                    continue
                self.inbox.put(("poll", (gen, data)))
            except urllib.error.HTTPError as e:
                if e.code == 403 and self.auto_mode:
                    self.inbox.put(("rebind", (gen, "教室密钥已变更，重新校验绑定")))
                    time.sleep(2)
                    continue
                fails += 1
                if fails == 1 or fails % 30 == 0:
                    log(f"连接服务器失败（第 {fails} 次）：{e}")
            except Exception as e:
                fails += 1
                if fails == 1 or fails % 30 == 0:
                    log(f"连接服务器失败（第 {fails} 次）：{e}")
            time.sleep(2)

    def fetch_board_now(self):
        if not self.bound or not BOARD_URL:
            return
        gen, url = self.session_gen, BOARD_URL
        try:
            self.inbox.put(("board", (gen, fetch_json(url))))
        except Exception as e:
            log(f"拉取看板失败：{e}")

    def board_loop(self):
        while True:
            if self.bound and BOARD_URL:
                gen, url = self.session_gen, BOARD_URL
                try:
                    self.inbox.put(("board", (gen, fetch_json(url))))
                except Exception:
                    pass
                time.sleep(60)
            else:
                time.sleep(2)

    def device_loop(self):
        """桌面云自动认班循环：开机上报本机网卡 MAC，已绑定直接接入，未绑定则弹出选班窗口"""
        fails = 0
        while True:
            if self.bound:
                self.device_wake.wait(5)
                self.device_wake.clear()
                fails = 0
                continue
            self.device_wake.clear()
            force = self.bind_force_show
            self.bind_force_show = False
            wait = 10
            try:
                mac, macs, host = get_machine_info(SERVER)
                if not mac:
                    log("未能读取本机网卡 MAC 地址，3 秒后重试")
                    wait = 3
                else:
                    code, data = post_json(f"{SERVER}/api/device/check", {"mac": mac, "macs": macs, "hostname": host})
                    if code == 200 and isinstance(data, dict):
                        fails = 0
                        data["_force"] = force
                        self.inbox.put(("device_check", data))
                        wait = 10 if not data.get("open") else 30
                    else:
                        fails += 1
                        if fails == 1 or fails % 20 == 0:
                            log(f"检查设备绑定失败（HTTP {code}）：{data}")
                        wait = 5
            except Exception as e:
                fails += 1
                if fails == 1 or fails % 20 == 0:
                    log(f"连接服务器检查绑定失败（第 {fails} 次）：{e}")
                wait = 3
            self.device_wake.wait(wait)

    def trigger_device_check(self, force_show=False):
        if force_show:
            self.bind_force_show = True
        self.device_wake.set()

    def submit_device_bind(self, cls_name):
        mac = self.bind_state.get("mac") or get_machine_info(SERVER)[0]
        try:
            host = socket.gethostname()[:40]
        except Exception:
            host = ""

        def run():
            try:
                code, data = post_json(f"{SERVER}/api/device/bind", {"mac": mac, "class": cls_name, "hostname": host})
                self.inbox.put(("device_bind", (code, data, cls_name)))
            except Exception as e:
                self.inbox.put(("device_bind", (0, {"error": f"网络异常，请稍后重试（{e}）"}, cls_name)))
        threading.Thread(target=run, daemon=True).start()

    def activate_class(self, cls_name, key, mac, manual=False):
        self.session_gen += 1
        set_binding(cls_name, key, mac)
        self.bound = True
        self.bind_minimized = False
        if self.bind_win:
            self.bind_win.close()
            self.bind_win = None
        if self.bind_pill:
            self.bind_pill.close()
            self.bind_pill = None
        threading.Thread(target=ensure_desktop_icon, daemon=True).start()
        threading.Thread(target=self.fetch_board_now, daemon=True).start()
        if manual or "--restarted" not in sys.argv:
            msg = f"已绑定 {cls_name}，接收端已启动" if manual else f"{cls_name} 接收端已启动"
            hello = MessageCard(self, {"id": 0, "text": msg, "sender": "班级助手", "time": time.strftime("%H:%M"), "popup": False})
            hello.place(0)
            self.root.after(4000, hello.close)
        log(f"大屏已关联班级：{cls_name}（MAC={mac}，方式={'手动选班' if manual else '开机自动认班'}）")

    def deactivate_class(self, reason=""):
        self.session_gen += 1
        # 先收起考试大屏和听力浮条，确保最后一次 play_state="idle" 上报时仍保留原班级的 URL/密钥
        if self.exam:
            self.exam.close()
            self.exam = None
            self.x11.allow_sleep()
        self.close_listen_bar()
        self.audio_player.stop()
        set_binding("", "", "")
        self.bound = False
        self.board = {}
        self.desk_sig = None
        self.exam_rev = None
        self.in_class = False
        for mid in list(self.cards):
            self.close(mid)
        self.hide_standby()
        self.hide_morning()
        if self.deskb:
            self.deskb.close()
            self.deskb = None
        self.audio_cache.reset()
        self.listen_fired.clear()
        self.audio_cmds_done.clear()
        self.exam_played.clear()
        self.pending_uninstall_id = None
        self._uninstall_ack_inflight = None
        self.board_gen = 0
        if self.tooltab:
            self.tooltab.destroy()
            self.tooltab = None
        self.bind_minimized = False
        log(f"大屏绑定已解除（{reason}），重新检查绑定状态")
        self.trigger_device_check(force_show=True)

    def minimize_bind_window(self):
        self.bind_minimized = True
        if self.bind_win:
            self.bind_win.close()
            self.bind_win = None
        if not self.bind_pill and not self.bound:
            self.bind_pill = BindPill(self)

    def reopen_bind_window(self):
        self.bind_minimized = False
        if self.bind_pill:
            self.bind_pill.close()
            self.bind_pill = None
        if not self.bound:
            if self.bind_state:
                self.bind_win = BindWindow(self, self.bind_state)
            self.trigger_device_check(force_show=True)

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
                    gen, payload = data if isinstance(data, tuple) else (self.session_gen, data)
                    if gen != self.session_gen or not self.bound:
                        continue
                    try:
                        self.apply(payload)
                    except Exception as e:  # 出错也不能停：否则之后的消息都不显示了
                        log(f"处理消息出错：{e}")
                elif kind == "rebind":
                    gen, reason = data
                    if gen == self.session_gen and self.bound:
                        self.deactivate_class(reason)
                elif kind == "uninstall_ack_result":
                    gen, uid_, res = data
                    if getattr(self, "_uninstall_ack_inflight", None) == uid_:
                        self._uninstall_ack_inflight = None
                    if gen == self.session_gen and self.bound:
                        if self.handle_uninstall_ack_result(uid_, res) == "acked":
                            return
                elif kind == "device_check":
                    if self.bound:
                        continue
                    force = bool(data.pop("_force", False))
                    if data.get("bound") and data.get("class") and data.get("key"):
                        self.activate_class(data["class"], data["key"], data.get("mac", ""), manual=False)
                        continue
                    prev_open = self.bind_state.get("open")
                    self.bind_state = data
                    if self.bind_minimized and not force:
                        if not self.bind_pill:
                            self.bind_pill = BindPill(self)
                    elif self.bind_win:
                        # 选班窗开着且用户正在挑选时不打断；只有手动点刷新、或开关状态变化时才刷新界面
                        if force or prev_open != data.get("open"):
                            self.bind_win.update_state(data)
                    else:
                        if self.bind_pill:
                            self.bind_pill.close()
                            self.bind_pill = None
                        self.bind_win = BindWindow(self, data)
                elif kind == "device_bind":
                    code, resp, cls_name = data
                    if code == 200 and isinstance(resp, dict) and resp.get("bound") and resp.get("key"):
                        self.activate_class(resp.get("class") or cls_name, resp["key"], resp.get("mac", ""), manual=True)
                    elif self.bind_win:
                        err = (resp or {}).get("error") or "绑定失败，请点刷新后重试"
                        self.bind_win.set_status(err, err=True)
                elif kind == "tip":  # 大屏工具出错（如没有名单）：角落小提示几秒
                    tip = MessageCard(self, {"id": 0, "text": data, "sender": "班级助手", "time": time.strftime("%H:%M"), "popup": False})
                    tip.place(0)
                    self.root.after(5000, tip.close)
                else:
                    gen, payload = data if isinstance(data, tuple) else (self.session_gen, data)
                    if gen != self.session_gen or not self.bound:
                        continue
                    old_st = (self.board or {}).get("server_time") or 0
                    new_st = (payload or {}).get("server_time") or 0
                    if old_st and new_st and new_st < old_st:
                        continue  # 丢弃乱序晚到的旧看板，防止覆盖已更新的新录音
                    self.board = payload
                    self.board_gen = getattr(self, "board_gen", 0) + 1
                    self.audio_cache.sync(payload.get("exam_audio_prefetch") or [])
                    if self.listen_bar and self.listen_bar.info.get("listen_id") is not None:
                        alive_lids = {t.get("id") for t in (payload.get("listen_tasks") or [])}
                        if self.listen_bar.info["listen_id"] not in alive_lids:
                            self.close_listen_bar()
                    key = payload.get("screen_font") or "misans"
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
        # 根窗口主循环巡检死窗口：即便外部“一键清屏”直接销毁考试窗或听力浮条，0.2 秒内立刻停播音频、上报空闲并解除更新锁
        if self.exam and not self.exam.win.winfo_exists():
            log("检测到考试大屏被外部关闭，已自动停播并清理")
            self.exam.close()
            self.exam = None
            self.x11.allow_sleep()
        if self.listen_bar and not self.listen_bar.win.winfo_exists():
            log("检测到课堂听力浮条被外部关闭，已自动停播并清理")
            self.close_listen_bar()
        if self.bound and not self.exam:
            try:
                self.check_listen_schedule()
            except Exception as e:
                log(f"平时听力调度出错：{e}")
        pstate = self.audio_player.snapshot()["state"]
        if (self.pending_restart and not self.cards and not self.exam and not self.listen_bar and not self.bind_win
                and not getattr(self, "pending_uninstall_id", None) and getattr(self, "_uninstall_ack_inflight", None) is None
                and pstate not in ("playing", "paused", "testing")):  # 考试、放听力、选班、待确认卸载时不重启
            self.audio_player.stop()
            log("重启以加载新版本 / 新字体")
            if WIN:  # Windows 的 execv 是另起一个进程：先放开"只运行一个"的锁，再启动新进程、退出自己
                MUTEX[1].CloseHandle(MUTEX[0])
                subprocess.Popen([sys.executable, SELF, "--restarted"], cwd=HERE, creationflags=0x08000000)
                os._exit(0)
            os.execv(sys.executable, [sys.executable, SELF, "--restarted"])
        self.root.after(200, self.process)

    def handle_uninstall_ack_result(self, mid, res):
        """主线程处理后台卸载回执结果：仅当服务端明确返回 acked 时才停播并自删；cancelled 放弃卸载；failed 保留待重试"""
        if getattr(self, "_uninstall_ack_inflight", None) == mid:
            self._uninstall_ack_inflight = None
        if res == "acked":
            self.pending_uninstall_id = None
            self.audio_player.stop()
            uninstall_self()
            return "acked"
        if res == "cancelled":
            if getattr(self, "pending_uninstall_id", None) == mid:
                self.pending_uninstall_id = None
            log(f"远程卸载 #{mid} 已被撤销或失效，取消卸载并继续运行")
            return "cancelled"
        if getattr(self, "pending_uninstall_id", None) in (None, mid):
            self.pending_uninstall_id = mid
        log(f"远程卸载 #{mid} 回执失败，保留接收端等待下次轮询重试确认")
        return "failed"

    def schedule_uninstall_ack(self, mid):
        """将卸载回执移至后台线程单飞执行（同时仅允许一个在途请求），绝不阻塞界面主线程与死窗口巡检"""
        if not mid:
            return False
        self.pending_uninstall_id = mid
        if getattr(self, "_uninstall_ack_inflight", None) is not None:
            return False
        self._uninstall_ack_inflight = mid
        gen = getattr(self, "session_gen", 0)
        ack_url = ACK_URL
        inbox = getattr(self, "inbox", None)

        def run():
            res = "failed"
            try:
                res = send_ack_sync(mid, ack_url=ack_url)
            except Exception as e:
                log(f"后台卸载确认异常：{e}")
            if inbox is not None:
                inbox.put(("uninstall_ack_result", (gen, mid, res)))
            else:
                self._uninstall_ack_inflight = None

        threading.Thread(target=run, daemon=True).start()
        return True

    def apply(self, data):
        if data.get("server_time"):
            self.offset = data["server_time"] - time.time()
        p_uid = getattr(self, "pending_uninstall_id", None)
        if p_uid and not any(m.get("kind") == "uninstall" and m.get("id") == p_uid for m in (data.get("messages") or [])):
            # 前一次回执可能已落库但响应丢包，导致轮询不再返回该卸载指令：后台单飞重试确认
            self.schedule_uninstall_ack(p_uid)
        rev = data.get("exam_rev")
        if rev and rev != self.exam_rev:  # 老师改了考试安排（或提前结束）：马上重新拉看板
            if self.exam_rev is not None or self.board:
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
            if m.get("kind") == "uninstall":  # 班主任/管理员在工作台点了"远程卸载"
                # 移到后台线程单飞确认，绝不阻塞主线程；主线程收到 acked 结果后再停播并自删
                self.schedule_uninstall_ack(m.get("id"))
                continue
            if m.get("kind") == "exam_audio":  # 考试或平时听力控制指令（试音 / 立即同步播放 / 停止）
                d = m.get("data") or {}
                cid = d.get("cmd_id") or m["id"]
                send_ack(m["id"])  # 可靠回执（若回执丢包导致服务端重发，cmd_id 保证只执行一次并补回执）
                if cid not in self.audio_cmds_done:
                    self.audio_cmds_done.add(cid)
                    self.handle_audio_cmd(d)
                continue
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
        if self.listen_bar:
            self.listen_bar.lift()

    def close_listen_bar(self):
        if self.listen_bar:
            self.listen_bar.close()
            self.listen_bar = None

    def open_listen_bar(self, info, action="play", start_at=None):
        self.close_listen_bar()
        self.listen_bar = ListenFloatBar(self, info)
        if action == "test":
            self.listen_bar.start_test()
        else:
            now = time.time() + self.offset
            self.listen_bar.ensure_cached()  # 无论是否延后起播，立即唤醒优先下载
            if start_at and start_at > now:
                self.listen_bar.pending_play_at = start_at
            else:
                self.listen_bar.start_play(seek=max(0.0, now - start_at) if start_at else 0.0, start_at=start_at)
        self.listen_bar.start_tick()

    def handle_audio_cmd(self, d):
        act = d.get("action")
        if d.get("mode") == "listen":
            if act == "stop":
                self.close_listen_bar()
                return
            if self.exam:  # 正在正式考试期间不弹出平时听力浮条
                return
            self.open_listen_bar(d, action=act, start_at=d.get("start_at"))
            return
        # 考试听力控制指令
        if self.exam:
            # 正在正式考试大屏中：仅响应本场考试指令，绝不被其他场次的停止/开播指令误伤或叠弹浮条
            if not d.get("exam_key") or self.exam.ex.get("key") == d.get("exam_key"):
                self.exam.handle_cmd(d)
            return
        if act == "stop":
            self.audio_player.stop()
            self.close_listen_bar()
            return
        # 若考试尚未到自动显示时间（如提前半小时试音或临时开播），借用悬浮播放条完成试音/播放，并带全元数据与 exam_id/idx 供上报和无缝移交
        ex = next((x for x in self.board.get("exams") or [] if x.get("key") == d.get("exam_key")), None)
        au = (ex or {}).get("audio") or {}
        info = {
            "exam_key": d.get("exam_key") or (ex or {}).get("key", ""),
            "exam_id": d.get("exam_id") if d.get("exam_id") is not None else (ex or {}).get("exam_id"),
            "idx": d.get("idx") if d.get("idx") is not None else (ex or {}).get("idx"),
            "title": d.get("title") or f"{(ex or {}).get('subject') or '考试'}听力",
            "audio_id": d.get("audio_id") or au.get("id", ""),
            "duration": d.get("duration") or au.get("duration", 0),
            "sha256": d.get("sha256") or au.get("sha256", ""),
            "ext": d.get("ext") or au.get("ext", "mp3"),
            "url": d.get("url") or au.get("url", ""),
        }
        self.open_listen_bar(info, action=act, start_at=d.get("start_at"))

    def check_listen_schedule(self):
        """检查平时定时听力：到设定时间自动弹出右下角悬浮播放条并同步起播（误差 < 1 秒）"""
        if self.exam:
            return
        now = time.time() + self.offset
        for t in self.board.get("listen_tasks") or []:
            pts = t.get("play_ts")
            if not pts or not (pts <= now < pts + 90):
                continue
            key = (t["id"], t["date"], t.get("play_at"))
            if key in self.listen_fired:
                continue
            au = t.get("audio") or {}
            if not au.get("id"):
                continue
            self.listen_fired.add(key)
            info = {"listen_id": t["id"], "title": t.get("title") or "英语听力", "audio_id": au["id"],
                    "duration": au.get("duration", 0), "sha256": au.get("sha256", ""), "ext": au.get("ext", "mp3"),
                    "url": au.get("url", "")}
            self.open_listen_bar(info, action="play", start_at=pts)
            log(f"定时平时听力开播：{info['title']}")
            break

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
            if self.audio_player.snapshot()["state"] not in ("playing", "testing"):
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
        elif active and self.morning_dismissed != key and not self.cards and not self.listen_bar:
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
        """按服务器时间找现在要显示的那场考试（正在进行 > 下一场即将/已到听力起播 > 前场考后收卷页 > 普通考前等待页）"""
        now = time.time() + self.offset
        cands = [x for x in self.board.get("exams") or [] if x["show_from"] <= now < x["hide_at"]]
        def _ex_prio(x):
            if x["start_ts"] <= now < x["end_ts"]:
                return (0, x["start_ts"])
            au = x.get("audio") or {}
            if now < x["end_ts"] and au.get("id") and au.get("start_ts") is not None and now >= au["start_ts"] - 120:
                return (1, au["start_ts"])  # 下场听力起播前 2 分钟起优先接管，绝不被前场 5 分钟结束页挡住漏播
            if now >= x["end_ts"]:
                return (2, x["end_ts"])
            return (3, x["start_ts"])
        ex = min(cands, key=_ex_prio) if cands else None
        if self.exam:
            if ex and self.exam.ex.get("key") == ex.get("key"):
                cur_bgen = getattr(self, "board_gen", 0)
                if self.exam.key != exam_key(ex) and getattr(self.exam, "_last_board_gen", -1) != cur_bgen:  # 仅在收到新代次看板且有改动时刷新，防旧看板反复触发重绘
                    self.exam.update_exam(ex)
                return
            if now >= self.exam.ex.get("end_ts", float("inf")) and hasattr(self.exam, "speak"):
                self.exam.speak(now)  # 连场无缝切换前，先确保前一场“考试结束，请停止作答”播报触发
            self.exam.close()
            self.exam = None
            if not ex:
                log("考试大屏收起")
                self.x11.allow_sleep()
        if ex and not self.exam:
            adopt = None
            if self.listen_bar and self.listen_bar.info.get("exam_key") == ex.get("key"):
                # 提前开播借用的浮条正是本场考试：仅当浮条处于正式播放/暂停/待播时移交音频元数据，避免“只试音”浮条把新曲顶回旧曲
                st_cur = self.audio_player.snapshot()["state"]
                lb_info = self.listen_bar.info or {}
                lb_aid = str(lb_info.get("audio_id") or "").strip()
                lb_sha = str(lb_info.get("sha256") or "").strip()
                cur_au = ex.get("audio") or {}
                has_pend = self.listen_bar.pending_play_at is not None
                active_play = st_cur in ("playing", "paused") or has_pend
                adopt_au = None
                if active_play and lb_aid:
                    if cur_au.get("id") == lb_aid and (not lb_sha or cur_au.get("sha256") == lb_sha):
                        adopt_au = dict(cur_au)  # 即使 ID 已一致也武装 _cmd_audio，防乱序旧看板回退
                    else:
                        adopt_au = {
                            "id": lb_aid,
                            "name": lb_info.get("title") or f"{ex.get('subject') or '考试'}听力",
                            "ext": lb_info.get("ext") or "mp3",
                            "size": int(lb_info.get("size") or 0),
                            "sha256": lb_sha,
                            "duration": float(lb_info.get("duration") or 0),
                            "start": cur_au.get("start") or ex.get("start", ""),
                            "start_ts": cur_au.get("start_ts") if cur_au.get("start_ts") is not None else (self.listen_bar.pending_play_at or ex.get("start_ts")),
                            "url": lb_info.get("url") or f"/exam-audio/{lb_aid}",
                        }
                        if hasattr(self, "fetch_board_now"):
                            threading.Thread(target=self.fetch_board_now, daemon=True).start()
                adopt = {
                    "audio_auto_done": active_play,
                    "pending_play_at": self.listen_bar.pending_play_at,
                    "pending_audio_id": lb_aid if has_pend else "",
                    "pending_audio_sha": lb_sha if has_pend else "",
                    "audio": adopt_au,
                    "playing_audio_id": lb_aid if st_cur in ("playing", "paused") else "",
                }
                self.listen_bar.detach_without_stop()
                self.listen_bar = None
            else:
                self.close_listen_bar()
            self.hide_morning()
            self.hide_standby()
            self.exam = ExamBoard(self, ex, adopt=adopt)
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

    def sync_desk(self):
        """桌面版看板：工作台打开了就一直放在桌面上；作业、课表、留言变了就重画"""
        want = bool(self.board.get("desk_board"))
        sig = board_sig(self.board) + json.dumps([self.board.get(k) for k in ("date", "classes_today")]) if want else None
        if self.deskb and (sig != self.desk_sig or not self.deskb.win.winfo_exists()):
            self.deskb.close()
            self.deskb = None
            if not want:
                log("桌面看板已关掉")
        if want and not self.deskb:
            self.deskb = DayStandby(self, self.board, desk=DeskLayer.box(self))
            if self.desk_sig is None:
                log("显示桌面看板")
        self.desk_sig = sig

    def check_standby(self):
        if not self.bound:
            self.root.after(3000, self.check_standby)
            return
        try:
            self.sync_tooltab()
        except Exception as e:
            log(f"工具把手出错：{e}")
        try:
            self.sync_desk()
        except Exception as e:
            log(f"桌面看板出错：{e}")
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
        if self.listen_bar and self.audio_player.snapshot()["state"] in ("playing", "testing"):
            if time.time() - self.awake_at > 30:
                self.awake_at = time.time()
                self.x11.keep_awake()
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
                if (touched and shown_for > 4) or (not window and not self.standby_forced) or self.listen_bar:
                    self.hide_standby()
                elif board_sig(self.board) != self.standby_sig:  # 显示着的时候老师改了作业、公告、时间安排：重画
                    self.show_standby(forced=self.standby_forced)
            elif (cfg.get("enabled") and self.x11.ok and not self.cards and window and not self.morning and not self.listen_bar
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
    if "--boot" in sys.argv and not WIN:
        boot_wait()
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
