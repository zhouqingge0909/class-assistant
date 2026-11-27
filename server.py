#!/usr/bin/env python3
"""班级助手 - 服务器

只用 Python 标准库，不需要安装任何依赖。Mac、Windows、Linux 云服务器都能直接运行：
    python3 server.py
第一次运行会自动生成 config.json（含老师登录密码和教室密钥）。
"""
import asyncio
import csv
import hashlib
import hmac
import math
import json
import os
import random
import re
import secrets
import shutil
import socket
import threading
import struct
import time
import wave
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
STUDENTS_PATH = os.path.join(BASE, "students.csv")
DATA_DIR = os.path.join(BASE, "data")
MSG_PATH = os.path.join(DATA_DIR, "messages.json")
SESSION_PATH = os.path.join(DATA_DIR, "sessions.json")
HOMEWORK_PATH = os.path.join(DATA_DIR, "homework.json")
REMINDER_PATH = os.path.join(DATA_DIR, "reminders.json")
PHRASE_PATH = os.path.join(DATA_DIR, "phrases.json")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")  # 工作台里改的设置（部署不会覆盖）
POINTS_PATH = os.path.join(DATA_DIR, "points.json")
COLLECT_PATH = os.path.join(DATA_DIR, "collect.json")
MORNING_PATH = os.path.join(DATA_DIR, "morning.json")
NOTES_PATH = os.path.join(DATA_DIR, "notes.json")
ATTEND_PATH = os.path.join(DATA_DIR, "attend.json")
QUIZ_PATH = os.path.join(DATA_DIR, "quiz.json")
EXAM_PATH = os.path.join(DATA_DIR, "exams.json")
ROSTER_PATH = os.path.join(DATA_DIR, "roster.json")
USERS_PATH = os.path.join(DATA_DIR, "users.json")  # 老师账号、各班成员（班主任/科任）、邀请码
DEVICES_PATH = os.path.join(DATA_DIR, "devices.json")  # 桌面云 / 全校统一安装：大屏网卡 MAC -> 班级绑定关系
BACKUP_DIR = os.path.join(BASE, "backups")  # 每日数据备份（不在 data/ 里，避免备份套备份）  # 工作台里添加/修改的班级名单（优先于 students.csv）
IMG_DIR = os.path.join(DATA_DIR, "images")
FILE_DIR = os.path.join(DATA_DIR, "files")      # 老师发到大屏的文档（Word/PDF/PPT…），保留 7 天
FILE_PATH = os.path.join(DATA_DIR, "files.json")
FILE_EXTS = {"pdf", "doc", "docx", "ppt", "pptx", "pps", "ppsx", "xls", "xlsx", "wps", "dps", "et", "txt",
             "jpg", "jpeg", "png", "mp4", "mp3", "html", "htm"}  # 只收文档、网页、图片、音视频，不收 exe 之类的程序
FILE_MAX = 50 * 1024 * 1024
FILE_KEEP_DAYS = 7
FILE_TOTAL_MAX = 3 * 1024 ** 3  # 所有文件加起来最多 3GB，防止把硬盘塞满
FILE_CLASS_MAX = 500 * 1024 ** 2  # 公共服务器上每个班最多 500MB，免得几个班把空间占完
EXAM_AUDIO_DIR = os.path.join(DATA_DIR, "exam_audio")  # 考试与平时听力音频（多班共享只存一份，7 天自动清理）
EXAM_AUDIO_PATH = os.path.join(DATA_DIR, "exam_audio.json")
LISTEN_PATH = os.path.join(DATA_DIR, "listen.json")    # 平时听力播放任务（课堂 -> 播放听力）
EXAM_AUDIO_EXTS = {"mp3", "wav"}
EXAM_AUDIO_MAX = 80 * 1024 * 1024  # 单个听力文件上限 80MB
EXAM_AUDIO_TOTAL_MAX = 2 * 1024 ** 3  # 听力音频总容量上限 2GB
EXAM_AUDIO_ORPHAN_MAX = 10  # 单账号未绑定任何任务的临时上传文件上限
EXAM_AUDIO_ORPHAN_TTL = 1800  # 未保存临时上传音频保留 30 分钟
EXAM_AUDIO_KEEP_DAYS = 7
EXAM_AUDIO_EARLY_MAX = 15  # 允许听力起播时间早于开考最多 15 分钟（方便播放自带试音段）
SESSIONS = ["早读", "上午", "下午", "晚自习"]
ATTEND_TYPES = ["迟到", "请假", "缺勤", "早退"]
STATIC = os.path.join(BASE, "static")
AUDIO_DIR = os.path.join(DATA_DIR, "audio")
CN = timezone(timedelta(hours=8))  # 固定北京时间，部署到海外/UTC 云服务器也不会错

DEFAULT_CONFIG = {
    "port": 8080,
    "teacher_password": "",
    "classroom_key": "",
    "places": ["班主任办公室", "语文办公室", "数学办公室", "英语办公室", "物理办公室",
               "化学办公室", "生物办公室", "年级组办公室", "德育处"],
    "weekdays": [1, 2, 3, 4, 5, 6],
    "periods": [["08:00", "08:40"], ["08:50", "09:30"], ["09:50", "10:30"], ["10:40", "11:20"],
                ["11:30", "12:10"], ["14:00", "14:40"], ["14:50", "15:30"], ["15:40", "16:20"],
                ["16:30", "17:10"], ["19:00", "19:45"], ["19:55", "20:40"]],
    "expire_minutes": 120,
    # 真人感语音（微软神经网络语音，需联网并安装 edge-tts：python3 -m pip install --user edge-tts）
    # 可选：zh-CN-XiaoxiaoNeural 晓晓 / XiaoyiNeural 晓伊 / YunxiNeural 云希 / YunjianNeural 云健 / YunyangNeural 云扬
    "tts_voice": "zh-CN-YunyangNeural",
    "tts_rate": "-5%",
    "tts_repeat": 2,
    "subjects": ["语文", "数学", "英语", "物理", "化学", "生物", "政治", "历史", "地理"],
    "homework_limit_minutes": 180,  # 每天各科作业总时长超过这个数，工作台会提醒班主任
}

# 待机页"每日一句"内置语录（出处明确的劝学、励志诗文和科学家名言）
QUOTES = [
    ("长风破浪会有时，直挂云帆济沧海。", "李白《行路难》"),
    ("会当凌绝顶，一览众山小。", "杜甫《望岳》"),
    ("千磨万击还坚劲，任尔东西南北风。", "郑燮《竹石》"),
    ("不积跬步，无以至千里；不积小流，无以成江海。", "荀子《劝学》"),
    ("锲而舍之，朽木不折；锲而不舍，金石可镂。", "荀子《劝学》"),
    ("路漫漫其修远兮，吾将上下而求索。", "屈原《离骚》"),
    ("少壮不努力，老大徒伤悲。", "汉乐府《长歌行》"),
    ("黑发不知勤学早，白首方悔读书迟。", "颜真卿《劝学》"),
    ("纸上得来终觉浅，绝知此事要躬行。", "陆游《冬夜读书示子聿》"),
    ("业精于勤，荒于嬉；行成于思，毁于随。", "韩愈《进学解》"),
    ("莫等闲，白了少年头，空悲切。", "岳飞《满江红》"),
    ("千淘万漉虽辛苦，吹尽狂沙始到金。", "刘禹锡《浪淘沙》"),
    ("沉舟侧畔千帆过，病树前头万木春。", "刘禹锡《酬乐天扬州初逢席上见赠》"),
    ("山重水复疑无路，柳暗花明又一村。", "陆游《游山西村》"),
    ("天生我材必有用。", "李白《将进酒》"),
    ("苟日新，日日新，又日新。", "《礼记·大学》"),
    ("博学之，审问之，慎思之，明辨之，笃行之。", "《礼记·中庸》"),
    ("学而不思则罔，思而不学则殆。", "《论语·为政》"),
    ("知之者不如好之者，好之者不如乐之者。", "《论语·雍也》"),
    ("天行健，君子以自强不息。", "《周易》"),
    ("合抱之木，生于毫末；九层之台，起于累土；千里之行，始于足下。", "《老子》"),
    ("古之立大事者，不惟有超世之才，亦必有坚忍不拔之志。", "苏轼《晁错论》"),
    ("及时当勉励，岁月不待人。", "陶渊明《杂诗》"),
    ("盛年不重来，一日难再晨。", "陶渊明《杂诗》"),
    ("青春须早为，岂能长少年。", "孟郊《劝学》"),
    ("读书破万卷，下笔如有神。", "杜甫《奉赠韦左丞丈二十二韵》"),
    ("问渠那得清如许？为有源头活水来。", "朱熹《观书有感》"),
    ("欲穷千里目，更上一层楼。", "王之涣《登鹳雀楼》"),
    ("雄关漫道真如铁，而今迈步从头越。", "毛泽东《忆秦娥·娄山关》"),
    ("世上无难事，只要肯登攀。", "毛泽东《水调歌头·重上井冈山》"),
    ("时间就像海绵里的水，只要愿意挤，总还是有的。", "鲁迅"),
    ("在科学上没有平坦的大道，只有不畏劳苦沿着陡峭山路攀登的人，才有希望达到光辉的顶点。", "马克思"),
    ("如果说我比别人看得更远些，那是因为我站在巨人的肩上。", "牛顿"),
    ("给我一个支点，我就能撬动地球。", "阿基米德"),
    ("机遇只偏爱有准备的头脑。", "巴斯德"),
    ("想象力比知识更重要。", "爱因斯坦"),
    ("我们必须知道，我们必将知道。", "希尔伯特"),
    ("天才就是百分之一的灵感加上百分之九十九的汗水。", "爱迪生"),
]


def pick_quote(cls, now=None):
    """一天一句（北京时间零点换）：内置语录 + 老师自己加的句子"""
    cfg = ccfg(cls)["quotes"]
    if not cfg.get("enabled", True):
        return None
    pool = [{"text": t, "by": b} for t, b in QUOTES] if cfg.get("builtin", True) else []
    for line in cfg.get("custom", []):
        text, _, by = line.partition("——")
        if text.strip():
            pool.append({"text": text.strip(), "by": by.strip()})
    if not pool:
        return None
    day = datetime.fromtimestamp(now or time.time(), CN).date().toordinal()
    return pool[(day * 7) % len(pool)]  # 跳着取，相邻两天不会是同一首诗的两句


DEFAULT_PHRASES = ["请课代表下课到办公室领取作业", "请值日生课间把黑板擦干净", "请各位同学安静自习",
                   "请班长到班主任办公室", "下节课换教室，请提前做好准备"]


# 工作台里可选的播报声音
VOICES = [
    {"id": "zh-CN-YunyangNeural", "name": "云扬 · 新闻播音男声"},
    {"id": "zh-CN-YunjianNeural", "name": "云健 · 浑厚男声"},
    {"id": "zh-CN-YunxiNeural", "name": "云希 · 阳光男声"},
    {"id": "zh-CN-XiaoxiaoNeural", "name": "晓晓 · 温柔女声"},
    {"id": "zh-CN-XiaoyiNeural", "name": "晓伊 · 活泼女声"},
]
VOICE_IDS = {v["id"] for v in VOICES}

# 腾讯云语音合成（付费，服务器上 edge-tts 被微软拒绝时用）：密钥只放在服务器上的 tts.json，
# 不进 config.json、不随部署上传、不进备份。文件格式：{"secret_id": "...", "secret_key": "..."}
# 可选："voice" 默认音色编号、"daily_chars" 每天最多合成多少字（防止意外多扣费，默认 20000）
TTS_PATH = os.path.join(BASE, "tts.json")
TC_VOICES = [
    {"id": "tc:101013", "name": "智辉 · 新闻男声"},
    {"id": "tc:101021", "name": "智瑞 · 新闻男声"},
    {"id": "tc:101011", "name": "智燕 · 新闻女声"},
    {"id": "tc:101004", "name": "智云 · 通用男声"},
    {"id": "tc:101027", "name": "智梅 · 通用女声"},
    {"id": "tc:501000", "name": "智斌 · 阅读男声（大模型，更自然）"},
    {"id": "tc:501001", "name": "智兰 · 资讯女声（大模型，更自然）"},
]
TC = {}
if os.path.exists(TTS_PATH):
    try:
        with open(TTS_PATH, encoding="utf-8") as f:
            TC = json.load(f)
        if not (TC.get("secret_id") and TC.get("secret_key")):
            TC = {}
    except Exception as e:
        print("tts.json 读取失败：", e)
        TC = {}
if TC:  # 有腾讯云密钥就只列腾讯云音色
    VOICES = TC_VOICES
    VOICE_IDS = {v["id"] for v in VOICES}
TC_DEFAULT = "tc:" + str(TC.get("voice") or 101013)
tc_used = {"day": "", "chars": 0}

# 电脑自带语音（不联网、不花钱）：没有腾讯云密钥、也没装 edge-tts 时用。局域网版装在学校 Windows 电脑上就靠它。
# Windows 用系统自带的 System.Speech（中文版 Windows 一般有"慧慧"），Mac 用 say（方便在 Mac 上测试）
LOCAL_NAMES = {"Huihui": "慧慧 · 女声", "Yaoyao": "瑶瑶 · 女声", "Kangkang": "康康 · 男声", "Lili": "莉莉 · 女声",
               "Tingting": "婷婷 · 女声", "Meijia": "美佳 · 女声（台湾）", "Sinji": "善怡 · 女声（粤语）"}
SAPI_PS = r"""Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
try { $s.SelectVoice($args[0]) } catch {}
$s.Rate = -1
$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo 16000, ([System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen), ([System.Speech.AudioFormat.AudioChannel]::Mono)
$s.SetOutputToWaveFile($args[1], $f)
$s.Speak([IO.File]::ReadAllText($args[2], [Text.Encoding]::UTF8))
$s.Dispose()
"""
# 有些 Windows 的中文语音只装在 OneCore 里，System.Speech 读不到，要用 SAPI 指定 OneCore 的语音目录
ONECORE_PS = r"""$id = $args[0]
$c = New-Object -ComObject SAPI.SpObjectTokenCategory
$c.SetId('HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices', $false)
$t = $c.EnumerateTokens() | Where-Object { $_.Id -like ('*\' + $id) } | Select-Object -First 1
if (-not $t) { exit 1 }
$v = New-Object -ComObject SAPI.SpVoice
$v.Voice = $t
$v.Rate = -1
$st = New-Object -ComObject SAPI.SpFileStream
$st.Format.Type = 18
$st.Open($args[1], 3)
$v.AudioOutputStream = $st
[void]$v.Speak([IO.File]::ReadAllText($args[2], [Text.Encoding]::UTF8))
$st.Close()
"""
ONECORE_PREFIX = "onecore:"


def onecore_voices():
    """读 OneCore 目录里的中文语音，返回语音编号的列表（读注册表，不用管理员权限）"""
    try:
        import winreg
        path = r"SOFTWARE\Microsoft\Speech_OneCore\Voices\Tokens"
        out = []
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as tokens:
            for i in range(winreg.QueryInfoKey(tokens)[0]):
                key = winreg.EnumKey(tokens, i)
                try:
                    with winreg.OpenKey(tokens, key + r"\Attributes") as attrs:
                        langs = str(winreg.QueryValueEx(attrs, "Language")[0]).lower().split(";")
                except OSError:
                    continue
                if {s.strip() for s in langs} & {"804", "404", "c04", "1004", "1404"}:  # 简体、繁体、粤语等中文
                    out.append(ONECORE_PREFIX + key)
        return out
    except (ImportError, OSError):
        return []


def run_quiet(cmd, timeout=30):
    """运行一个命令，不弹黑窗口，返回输出文字（失败返回空）"""
    import subprocess
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           creationflags=0x08000000 if os.name == "nt" else 0)  # CREATE_NO_WINDOW
        return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def find_local_voices():
    out = []
    if os.name == "nt":
        names = run_quiet(["powershell", "-NoProfile", "-Command",
                           "Add-Type -AssemblyName System.Speech;(New-Object System.Speech.Synthesis.SpeechSynthesizer)"
                           ".GetInstalledVoices()|%{$_.VoiceInfo}|?{$_.Culture.Name -like 'zh*'}|%{$_.Name}"])
        out = [n.strip() for n in names.splitlines() if n.strip()] or onecore_voices()  # 桌面语音里有中文就不动，完全没有才用 OneCore
    elif shutil.which("say"):
        out = [line.split()[0] for line in run_quiet(["say", "-v", "?"]).splitlines()
               if "zh_CN" in line or "zh_TW" in line or "zh_HK" in line]
    out = list(dict.fromkeys(out))
    order = list(LOCAL_NAMES)
    out.sort(key=lambda n: next((i for i, k in enumerate(order) if k in n), 99))  # 慧慧排第一，认识的中文语音排前面
    voices = []
    for n in out:
        label = next((v for k, v in LOCAL_NAMES.items() if k in n), n)
        voices.append({"id": "local:" + n, "name": label + "（电脑自带）"})
    return voices


try:
    import edge_tts  # noqa: F401  云服务器版用的在线语音
    EDGE = True
except ImportError:
    EDGE = False
LOCAL_VOICES = [] if TC else find_local_voices()
if not TC and not EDGE and LOCAL_VOICES:
    VOICES = LOCAL_VOICES
    VOICE_IDS = {v["id"] for v in VOICES}


def tencent_tts(text, voice):
    """调腾讯云 TextToVoice（TC3-HMAC-SHA256 签名，只用标准库），返回 mp3 字节"""
    import base64
    import urllib.request
    host, action, version = "tts.tencentcloudapi.com", "TextToVoice", "2019-08-23"
    payload = json.dumps({"Text": text, "SessionId": secrets.token_hex(8), "VoiceType": int(voice[3:]),
                          "Codec": "mp3", "SampleRate": 16000, "ModelType": 1, "Speed": 0, "Volume": 3},
                         ensure_ascii=False).encode()
    ts = int(time.time())
    date = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
    ctype = "application/json; charset=utf-8"
    canonical = "\n".join(["POST", "/", "", f"content-type:{ctype}", f"host:{host}", f"x-tc-action:{action.lower()}", "",
                           "content-type;host;x-tc-action", hashlib.sha256(payload).hexdigest()])
    scope = f"{date}/tts/tc3_request"
    to_sign = "\n".join(["TC3-HMAC-SHA256", str(ts), scope, hashlib.sha256(canonical.encode()).hexdigest()])
    k = hmac.new(("TC3" + TC["secret_key"]).encode(), date.encode(), hashlib.sha256).digest()
    k = hmac.new(k, b"tts", hashlib.sha256).digest()
    k = hmac.new(k, b"tc3_request", hashlib.sha256).digest()
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    req = urllib.request.Request("https://" + host, data=payload, method="POST", headers={
        "Content-Type": ctype, "Host": host, "X-TC-Action": action, "X-TC-Version": version, "X-TC-Timestamp": str(ts),
        "Authorization": f"TC3-HMAC-SHA256 Credential={TC['secret_id']}/{scope}, "
                         f"SignedHeaders=content-type;host;x-tc-action, Signature={sig}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        resp = json.loads(r.read()).get("Response", {})
    if "Error" in resp:
        raise RuntimeError(f"{resp['Error'].get('Code')}：{resp['Error'].get('Message')}")
    return base64.b64decode(resp["Audio"])


def tts_chunks(text, size=140):
    """腾讯云一次最多 150 个字：按标点切成小段"""
    out, cur = [], ""
    for part in re.split(r"(?<=[。！？!?；;，,])", text):
        while len(part) > size:
            out.append(part[:size])
            part = part[size:]
        if cur and len(cur) + len(part) > size:
            out.append(cur)
            cur = ""
        cur += part
    if cur.strip():
        out.append(cur)
    return out


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8-sig") as f:  # 旧版记事本保存时会加 BOM，也要读得了
            cfg.update(json.load(f))
    changed = not os.path.exists(CONFIG_PATH)
    if not cfg["teacher_password"]:
        cfg["teacher_password"] = secrets.token_urlsafe(6)
        changed = True
    if not cfg["classroom_key"]:
        cfg["classroom_key"] = secrets.token_urlsafe(12)
        changed = True
    if changed:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    return cfg


def load_students():
    """students.csv（列：班级,学号,姓名）+ 工作台里改过的名单（data/roster.json，优先）"""
    classes = {}
    if os.path.exists(STUDENTS_PATH):
        with open(STUDENTS_PATH, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                cls = (row.get("班级") or "").strip()
                name = (row.get("姓名") or "").strip()
                if cls and name:
                    classes.setdefault(cls, []).append({"no": (row.get("学号") or "").strip(), "name": name})
    roster = read_json(os.path.join(BASE, "data", "roster.json"), {})
    for cls in roster.get("_deleted", []):
        classes.pop(cls, None)
    for cls, items in roster.items():
        if not cls.startswith("_"):
            classes[cls] = items
    return classes


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


CFG = load_config()
os.makedirs(AUDIO_DIR, exist_ok=True)
os.makedirs(os.path.join(DATA_DIR, "images"), exist_ok=True)
os.makedirs(FILE_DIR, exist_ok=True)
os.makedirs(EXAM_AUDIO_DIR, exist_ok=True)
lock = threading.Lock()
messages = read_json(MSG_PATH, [])
sessions = read_json(SESSION_PATH, {})  # token -> {name, exp}
homework = read_json(HOMEWORK_PATH, [])
reminders = read_json(REMINDER_PATH, [])
phrases = read_json(PHRASE_PATH, None)
if phrases is None:
    phrases = [{"id": i + 1, "text": t} for i, t in enumerate(DEFAULT_PHRASES)]
settings = read_json(SETTINGS_PATH, {"countdowns": [{"name": "高考", "date": "2027-06-07"}], "notices": {}})
settings.setdefault("standby", {"enabled": True, "idle_minutes": 5})
if settings.get("screen_font") not in ("misans", "wenkai", "system"):
    settings["screen_font"] = "misans"
files = read_json(FILE_PATH, [])         # 文件：{id, class, name, ext, size, by, uid, at}
exams = read_json(EXAM_PATH, [])        # 考试：{id, name, classes, sessions: [{subject, date, start, end, ...}], notice, creator, uid, created, ended: {"班级|场次": 时间戳}}
exam_audios = read_json(EXAM_AUDIO_PATH, {})  # audio_id -> {id, name, ext, size, sha256, duration, by, uid, at}
if not isinstance(exam_audios, dict):
    exam_audios = {}
listen_tasks = read_json(LISTEN_PATH, [])  # 平时听力：{id, title, classes, audio_id, date, play_at, creator, uid, created}
if not isinstance(listen_tasks, list):
    listen_tasks = []
exam_rev = [time.time()]                 # 考试/听力安排改了就变：一体机轮询时看到变化马上重新拉看板
quizzes = read_json(QUIZ_PATH, [])       # 学情：{id, class, title, date, questions: [题号], wrong: {题号: [姓名]}, source}
attends = read_json(ATTEND_PATH, [])     # 考勤 {id, class, date, session, marks: {姓名: 类型}, ts}
collects = read_json(COLLECT_PATH, [])   # 作业收交登记 {id, class, date, hw_id, subject, content, missing, ts}
points = read_json(POINTS_PATH, [])     # 积分记录 {id, class, name, delta, reason, teacher, ts}
mornings = read_json(MORNING_PATH, [])   # 早读内容 {id, class, date, subject, content, teacher, created}
notes = read_json(NOTES_PATH, [])         # 大屏留言 {id, class, text, subject, teacher, uid, created, until（YYYY-MM-DD，空＝一直显示）}
settings.setdefault("duty", {})          # 班级 -> {"1": [{"name", "task"}], ...}（星期几 -> 值日生）
settings.setdefault("board_points", True)
settings.setdefault("quotes", {"enabled": True, "builtin": True, "custom": {}})
settings.setdefault("my_classes", {})     # 老师称呼 -> 自己教的班级（没设置过的老师看到全部班级）
users = read_json(USERS_PATH, {"users": {}, "acl": {}, "invites": {}})
for _k in ("users", "acl", "invites", "class_keys", "aliases"):  # aliases：改名前的班级名 -> 新名（一体机还用旧名连）
    users.setdefault(_k, {})
users.setdefault("admins", [])  # 主管理员（用管理员密码登录的）指定的其他管理员：用自己的登录码登录，不用共用密码
devices = read_json(DEVICES_PATH, {"bindings": {}})
if not isinstance(devices.get("bindings"), dict):
    devices["bindings"] = {}


def is_owner(s):
    """主管理员：用管理员密码登录的。只有主管理员能改密码、指定管理员、下载备份（备份里有 config.json）"""
    return bool(s) and s["uid"] == "admin"


def is_admin(s):
    """管理员：主管理员，或者主管理员指定的老师"""
    return bool(s) and (s["uid"] == "admin" or s["uid"] in users["admins"])


def hash_pw(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex()


def check_password(pw):
    """在工作台改过密码就用改过的（只存加盐散列，在 data/settings.json，部署不会覆盖）；没改过用 config.json 里的"""
    h = settings.get("admin_pw")
    if h:
        return hmac.compare_digest(hash_pw(pw, h["salt"]), h["hash"])
    return hmac.compare_digest(pw, CFG["teacher_password"])
PUBLIC = bool(CFG.get("public"))         # 公共服务器（网友自助注册）；本校服务器没有这一项，行为不变
if PUBLIC:  # 公共服务器上全局教室密钥作废，每个班都要有自己的密钥
    for _c in load_students():
        users["class_keys"].setdefault(_c, secrets.token_urlsafe(12))
    write_json(USERS_PATH, users)
sessions = {t: v for t, v in sessions.items() if v.get("uid")}  # 旧版"公共密码"登录全部作废，需重新登录
settings.setdefault("cls_cfg", {})       # 班级 -> 大屏设置（倒计时、待机页、字体、每日一句……）
pick_history = {}                        # 班级 -> 最近被抽到的名字（避免连续重复）
last_seen = {}                           # 班级 -> 最近一次轮询时间
shots, shot_req = {}, {}                 # 看大屏：班级 -> {img, type, at, err}（只在内存里，2 分钟后作废）；班级 -> 老师点"看一眼"的时间
file_batches = {}                        # 群发文件预处理：batch_id -> {uid, classes, skipped, name, exp}（只在内存里，5 分钟过期）
exam_audio_ready = {}                    # audio_id -> {cls: {status, pct, at, err}}（一体机预下载与校验点名状态）
exam_audio_play = {}                     # "exam_id|idx|cls" -> {state, pos, dur, at, audio_id, err}（一体机当前听力播控状态）
screen_audio_cap = {}                    # cls -> {can_mp3, can_seek, can_wav, at}（一体机音频播放能力）
screen_rv = {}                           # cls -> {rv, at}（一体机接收端协议版本，rv>=6 支持可靠回执）
login_fails = {}                         # ip -> [失败次数, 首次失败时间]
reg_fails = {}                           # ip -> [注册码输错次数, 首次失败时间]
reg_counts = {}                          # ip -> [日期, 今天注册次数]
send_log = {}                            # 老师 uid -> 最近一小时上大屏的时间戳（公共模式限频）
SEND_LIMIT, HEAD_LIMIT, REMINDER_LIMIT, TEXT_LIMIT = 60, 3, 30, 200  # 公共模式的限制


def reg_enabled():
    """是否开放老师自助注册：公共模式看是否设了注册码且未暂停；本校/自建服务器还要先在工作台打开开关"""
    return bool((PUBLIC or settings.get("register_open")) and settings.get("register_code") and not settings.get("register_paused"))


def make_chime():
    """生成"叮—咚"提示音（data/audio/chime.wav）"""
    path = os.path.join(AUDIO_DIR, "chime.wav")
    if os.path.exists(path):
        return
    rate, frames = 22050, []
    for freq, start in ((784.0, 0.0), (659.3, 0.45)):  # G5 → E5
        for i in range(int(rate * 1.1)):
            t = i / rate
            env = math.exp(-3.2 * t)
            v = env * (math.sin(2 * math.pi * freq * t) + 0.3 * math.sin(4 * math.pi * freq * t))
            idx = int(start * rate) + i
            while len(frames) <= idx:
                frames.append(0.0)
            frames[idx] += v
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, f * 0.35)) * 32767)) for f in frames))


def make_speech(text, voice=None, repeat=None):
    """生成自然人声 mp3，返回文件名；失败返回 None（教室端会退回本机语音）。
    有 tts.json 用腾讯云，否则用 edge-tts"""
    if TC:
        return make_speech_tc(text, voice, repeat)
    if not EDGE:
        return make_speech_local(text, voice, repeat)
    import edge_tts
    voice = voice if voice in VOICE_IDS else CFG["tts_voice"]
    say = "。".join([text] * max(1, int(repeat or CFG["tts_repeat"])))
    name = hashlib.sha256((CFG["classroom_key"] + voice + CFG["tts_rate"] + say).encode()).hexdigest()[:24] + ".mp3"
    path = os.path.join(AUDIO_DIR, name)
    if os.path.exists(path):
        return name

    async def run():
        await edge_tts.Communicate(say, voice, rate=CFG["tts_rate"]).save(path + ".tmp")

    try:
        asyncio.run(asyncio.wait_for(run(), 15))
        os.replace(path + ".tmp", path)
        return name
    except Exception as e:  # 断网、服务不可用等
        print("语音生成失败，改用电脑自带语音：", e)
        return make_speech_local(text, None, repeat)


def default_voice():
    if TC:
        return TC_DEFAULT
    if not EDGE and LOCAL_VOICES:
        return LOCAL_VOICES[0]["id"]
    return CFG["tts_voice"]


def make_speech_local(text, voice=None, repeat=None):
    """用电脑自带语音生成 wav（不联网）；这台电脑没有中文语音就返回 None（Windows 一体机会自己朗读）"""
    if not LOCAL_VOICES:
        return None
    ids = {v["id"] for v in LOCAL_VOICES}
    voice = voice if voice in ids else LOCAL_VOICES[0]["id"]
    say = "。".join([text] * max(1, int(repeat or CFG["tts_repeat"])))
    name = hashlib.sha256((CFG["classroom_key"] + voice + say).encode()).hexdigest()[:24] + ".wav"
    path = os.path.join(AUDIO_DIR, name)
    if os.path.exists(path):
        return name
    tmp = path[:-4] + ".tmp.wav"
    if os.name == "nt":
        core = voice[6:].startswith(ONECORE_PREFIX)
        ps = os.path.join(AUDIO_DIR, "onecore.ps1" if core else "sapi.ps1")
        if not os.path.exists(ps):
            with open(ps, "w", encoding="utf-8-sig") as f:
                f.write(ONECORE_PS if core else SAPI_PS)
        txt = path[:-4] + ".txt"
        with open(txt, "w", encoding="utf-8") as f:
            f.write(say)
        run_quiet(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps,
                   voice[6 + len(ONECORE_PREFIX):] if core else voice[6:], tmp, txt], 60)
        try:
            os.remove(txt)
        except OSError:
            pass
    else:
        run_quiet(["say", "-v", voice[6:], "-r", "170", "--data-format=LEI16@16000", "-o", tmp, say], 60)
    if os.path.exists(tmp) and os.path.getsize(tmp) > 1000:
        os.replace(tmp, path)
        return name
    print("电脑自带语音生成失败，这条只响提示音")
    return None


def make_speech_tc(text, voice=None, repeat=None):
    voice = voice if voice in VOICE_IDS else TC_DEFAULT
    n = max(1, int(repeat or CFG["tts_repeat"]))
    name = hashlib.sha256((CFG["classroom_key"] + voice + "x" + str(n) + text).encode()).hexdigest()[:24] + ".mp3"
    path = os.path.join(AUDIO_DIR, name)
    if os.path.exists(path):  # 同一句话同一个声音只合成一次，省钱
        return name
    d, limit = today(), int(TC.get("daily_chars") or 20000)
    with lock:
        if tc_used["day"] != d:
            tc_used.update(day=d, chars=0)
        if tc_used["chars"] + len(text) > limit:
            print(f"语音：今天已合成 {tc_used['chars']} 字，超过每天上限 {limit}，这条不合成")
            return None
        tc_used["chars"] += len(text)
    try:
        once = b"".join(tencent_tts(c, voice) for c in tts_chunks(text + "。"))
        with open(path + ".tmp", "wb") as f:
            f.write(once * n)  # 读两遍：同一段 mp3 接两次（只付一遍的钱）
        os.replace(path + ".tmp", path)
        return name
    except Exception as e:
        print("腾讯云语音合成失败，教室端将使用本机语音：", e)
        return None


def make_exam_test_audio():
    """生成 10 秒温和清晰的考前试音音频（data/audio/exam_test.wav）：不泄露真题内容，离线也可用"""
    path = os.path.join(AUDIO_DIR, "exam_test.wav")
    if os.path.exists(path):
        return
    rate, total_s = 22050, 10.0
    nframes = int(rate * total_s)
    frames = [0.0] * nframes
    # 两轮舒缓清晰的三音节提示音（C5-E5-G5-C6，间隔呼吸），方便监考老师在教室前后排听清并调节音量
    notes = [
        (523.25, 0.2, 0.9), (659.25, 1.0, 0.9), (783.99, 1.8, 1.0), (1046.50, 2.7, 1.4),
        (783.99, 4.6, 0.8), (659.25, 5.4, 0.8), (523.25, 6.2, 0.9), (783.99, 7.2, 1.0), (1046.50, 8.2, 1.5),
    ]
    for freq, start, dur in notes:
        i0 = int(start * rate)
        cnt = int(dur * rate)
        for i in range(cnt):
            idx = i0 + i
            if idx >= nframes:
                break
            t = i / rate
            attack = min(1.0, t / 0.03)
            env = attack * math.exp(-2.6 * t)
            v = env * (math.sin(2 * math.pi * freq * t) + 0.28 * math.sin(4 * math.pi * freq * t)
                       + 0.08 * math.sin(6 * math.pi * freq * t))
            frames[idx] += v
    with wave.open(path + ".tmp", "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1.0, min(1.0, f * 0.42)) * 32767)) for f in frames))
    os.replace(path + ".tmp", path)


make_chime()
make_exam_test_audio()


def now_cn():
    return datetime.now(CN)


def school_schedule():
    sch = settings.get("schedule") or {}
    return {"periods": sch.get("periods", CFG["periods"]), "weekdays": sch.get("weekdays", CFG["weekdays"])}


def schedule_of(cls=None):
    """本班单独设置的作息优先（比如别的学校的班），没有就用全校作息"""
    own = settings.get("class_schedule", {}).get(cls) if cls else None
    return own or school_schedule()


def period_status(dt=None, cls=None):
    dt = dt or now_cn()
    sch = schedule_of(cls)
    if dt.isoweekday() in sch.get("weekdays", CFG["weekdays"]):
        t = dt.strftime("%H:%M")
        for i, (start, end) in enumerate(sch.get("periods", CFG["periods"])):
            if start <= t < end:
                return {"in_class": True, "period": i + 1, "end": end, "evening": start >= "18:00"}
    return {"in_class": False}


def evening_info(dt=None, cls=None):
    """晚自习时段（开始时间在 18:00 以后的节次）：返回 (是否在晚自习窗口内, 当前节信息)"""
    dt = dt or now_cn()
    sch = schedule_of(cls)
    if dt.isoweekday() not in sch.get("weekdays", CFG["weekdays"]):
        return False, None
    periods = sch.get("periods", CFG["periods"])
    eve = [p for p in periods if p[0] >= "18:00"]
    if not eve:
        return False, None
    rows = settings.get("timetable", {}).get(cls, {}).get(str(dt.isoweekday()), []) if cls else []
    nday = len(periods) - len(eve)
    subj = lambda i: (rows[nday + i] if nday + i < len(rows) else {}).get("subject", "")  # 课程表里晚自习那几行填的科目
    hm = dt.strftime("%H:%M")
    inside = eve[0][0] <= hm < eve[-1][1]
    for i, (a, b) in enumerate(eve):
        if a <= hm < b:
            return inside, {"label": f"晚自习 {i + 1}", "start": a, "end": b, "subject": subj(i)}
    nxt = next(((i, p) for i, p in enumerate(eve) if p[0] > hm), None)
    return inside, ({"label": "课间休息", "start": hm, "end": nxt[1][0], "next": f"晚自习 {nxt[0] + 1}", "next_subject": subj(nxt[0])}
                    if inside and nxt else None)


def today_classes(cls, dt=None):
    """今天的课程（课程表第 n 行对应作息表里的第 n 节，晚自习的节次也算），以及现在/下一节"""
    dt = dt or now_cn()
    day_periods = schedule_of(cls)["periods"]
    rows = settings.get("timetable", {}).get(cls, {}).get(str(dt.isoweekday()), [])
    out = []
    for i, (a, b) in enumerate(day_periods):
        cell = rows[i] if i < len(rows) else {}
        out.append({"no": i + 1, "start": a, "end": b, "subject": cell.get("subject", ""), "evening": a >= "18:00"})
    hm = dt.strftime("%H:%M")
    cur = next((c for c in out if c["start"] <= hm < c["end"]), None)
    nxt = next((c for c in out if c["start"] > hm), None)
    return out, cur, nxt


def is_active_uninstall(m, now=None):
    """未可靠回执、未撤销、未过期的远程卸载任务（无论是否已通过轮询发出，即 pending 与 delivered 均算待处理）"""
    if m.get("kind") != "uninstall" or m.get("recalled") or m.get("acked_at"):
        return False
    now = now if now is not None else time.time()
    if m.get("expire_at") and now > m["expire_at"]:
        return False
    return msg_status(m, now) in ("pending", "delivered", "scheduled")


def save_messages():
    """每个班各留最近 200 条；未完成、未撤回、未过期的远程卸载任务不参与裁剪；
    已回执 24 小时内或已撤销但未过期的卸载记录也保留宽限期，保证网络抖动重发回执幂等与撤销竞态准确识别"""
    now = time.time()
    seen, keep = {}, []
    for m in reversed(messages):
        if m.get("kind") == "uninstall":
            if (is_active_uninstall(m, now)
                    or (m.get("acked_at") and now - m["acked_at"] < 86400)
                    or (m.get("recalled") and m.get("expire_at") and now < m["expire_at"])):
                keep.append(m)
                continue
        seen[m["class"]] = seen.get(m["class"], 0) + 1
        if seen[m["class"]] <= 200:
            keep.append(m)
    messages[:] = keep[::-1]
    write_json(MSG_PATH, messages)


def msg_status(m, now):
    if m.get("recalled"):
        return "recalled"
    if m.get("acked_at"):
        return "acked"
    if m["deliver_at"] > now:
        return "scheduled"
    if (now > m["expire_at"]) if m.get("expire_at") else (now - m["deliver_at"] > CFG["expire_minutes"] * 60):
        return "expired"
    return "delivered" if m.get("delivered_at") else "pending"


def next_id(items):
    return max((x["id"] for x in items), default=0) + 1


def today():
    return now_cn().strftime("%Y-%m-%d")


def session_sid(token):
    """给登录会话生成短指纹（不存明文 token），用来精确区分不同设备的发送者"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12] if token else ""


def create_messages(classes, text, sender, voice=False, urgent=False, defer=False, voice_name=None,
                    kind="call", data=None, expire_at=None, say=None, repeat=None, who=None, loud=False, send_at=None,
                    data_by_class=None, sender_uid=None, sender_sid=None):
    """所有推送到教室大屏的内容（喊话、作业、提醒、课堂工具）都走这里；send_at：老师定时发送的时间"""
    now = time.time()
    def deliver_time(cls):  # 上课时间的普通消息，等到这个班下课再送（晚自习不等：值班老师要马上看到）
        ex = exam_now(cls, send_at or now) if not urgent and kind not in ("uninstall", "standby", "exam_audio") else None
        if ex:  # 正在考试：普通消息、提醒等考完（结束后 5 分钟）再送；紧急消息照常
            return ex["hide_at"]
        if send_at:  # 定时发送：到点就送，上课时一体机照常只在角落显示
            return send_at
        st = period_status(cls=cls)
        if defer and not urgent and st["in_class"] and not st.get("evening"):
            end = datetime.strptime(st["end"], "%H:%M").time()
            return datetime.combine(now_cn().date(), end, CN).timestamp()
        return now
    deliver_at = now
    audio = make_speech(say or text, voice_name, repeat) if voice else None
    ids = []
    with lock:
        nid = next_id(messages)
        for cls in classes:
            if kind == "prep" and exam_now(cls, now):
                continue  # 考试时不提醒"下节课带什么"
            at = deliver_time(cls)
            deliver_at = max(deliver_at, at)
            messages.append({"id": nid, "class": cls, "text": text, "sender": sender,
                             "sender_uid": sender_uid or "", "sender_sid": sender_sid or "", "kind": kind,
                             "data": data_by_class[cls] if data_by_class else data, "voice": bool(voice), "urgent": bool(urgent), "audio": audio,
                             "created": now, "deliver_at": at, "expire_at": expire_at,
                             "delivered_at": None, "acked_at": None, "who": who, "loud": bool(loud and voice), "send_at": send_at,
                             "speak": "。".join([say or text] * max(1, int(repeat or CFG["tts_repeat"]))) if voice else None})
            if at > now and not urgent and exam_now(cls, send_at or now):
                messages[-1]["exam_hold"] = True  # 因为考试压住的：考试提前结束、删除时要放出来
            ids.append(nid)
            nid += 1
        save_messages()
    return ids, deliver_at > now


def pending_uninstalls():
    """找出当前未可靠确认（含待发送与已下发未回执）的远程卸载指令：返回 {班级: [msg_id, ...]}"""
    now = time.time()
    out = {}
    with lock:
        snap = list(messages)
    for m in snap:
        if is_active_uninstall(m, now):
            out.setdefault(m["class"], []).append(m["id"])
    return out


def homework_of(cls, date=None):
    date = date or today()
    return [h for h in homework if h["class"] == cls and h["date"] == date]


WEEKDAY_CN = "一二三四五六日"


def due_date(h):
    """这项作业哪天交（课代表那天登记收交）：今天交＝当天；没写或"明天交"＝下一个上课日；本周五/下周一等按日期算"""
    if h.get("due_date"):
        return h["due_date"]
    base = datetime.strptime(h["date"], "%Y-%m-%d").date()
    due = h.get("due") or ""
    days = schedule_of(h.get("class")).get("weekdays") or list(range(1, 8))

    def school_day(n):  # 往后数第 n 个上课日（周六不上课的学校，周五的"明天交"就是下周一）
        d = base
        for _ in range(n):
            d += timedelta(days=1)
            for _ in range(7):
                if d.isoweekday() in days:
                    break
                d += timedelta(days=1)
        return d
    m = re.search(r"(下周|本周|周)([一二三四五六日天])", due)
    if due.startswith(("今天", "今晚")):
        d = base
    elif due.startswith("后天"):
        d = school_day(2)
    elif m:
        wd = WEEKDAY_CN.index(m.group(2).replace("天", "日"))
        d = base + timedelta(days=wd - base.weekday())
        if m.group(1) == "下周":
            d += timedelta(days=7)
        elif d < base:
            d += timedelta(days=7)
    else:
        d = school_day(1)
    return d.strftime("%Y-%m-%d")


def next_school_day(cls, d=None):
    """d 之后的下一个上课日（YYYY-MM-DD）：周六不上课的班，周五的"明天"是下周一"""
    d = datetime.strptime(d or today(), "%Y-%m-%d").date()
    days = schedule_of(cls).get("weekdays") or list(range(1, 8))
    for _ in range(7):
        d += timedelta(days=1)
        if d.isoweekday() in days:
            break
    return d.strftime("%Y-%m-%d")


def morning_window(cls):
    """早读时间：班主任设过就用设的，没设就是第一节课前 40 分钟到第一节课"""
    own = ccfg(cls).get("morning") or {}
    if own.get("start") and own.get("end"):
        return own["start"], own["end"]
    first = (schedule_of(cls).get("periods") or CFG["periods"])[0][0]
    t = datetime.strptime(first, "%H:%M") - timedelta(minutes=40)
    return t.strftime("%H:%M"), first


EXAM_BEFORE, EXAM_AFTER = 15 * 60, 5 * 60  # 开考前 15 分钟整屏显示须知；结束后 5 分钟收起
EXAM_NOTICE = "手机、智能手表等电子设备关机，放到讲台\n按考号对号入座，桌面只留准考证和文具\n答题卡用 2B 铅笔填涂，写清姓名、考号\n开考 15 分钟后不得进场，考试结束前不得离场"
EXAM_VOICE = {"start": "考试开始，请开始答题", "warn": "距离考试结束还有十五分钟", "end": "考试结束，请停止作答"}


def parse_audio_info(path, ext):
    """纯标准库校验 MP3 / WAV 文件头并估算时长（秒），返回 {"duration": int}；非法文件返回 None"""
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if size < 64:
        return None
    if ext == "wav":
        try:
            with wave.open(path, "rb") as w:
                rate = w.getframerate()
                nframes = w.getnframes()
                ch = w.getnchannels()
                sw = w.getsampwidth()
                if rate <= 0 or nframes <= 0 or ch <= 0 or sw <= 0:
                    return None
                max_frames = max(1, (size - 44) // (ch * sw))
                real_frames = min(nframes, max_frames)
                return {"duration": max(1, int(round(real_frames / float(rate))))}
        except Exception:
            return None
    if ext == "mp3":
        try:
            with open(path, "rb") as f:
                # 逐个跳过文件开头可能连续存在的 ID3v2 标签
                audio_offset = 0
                for _ in range(8):
                    f.seek(audio_offset)
                    hdr = f.read(10)
                    if len(hdr) < 10 or hdr[:3] != b"ID3":
                        break
                    if (hdr[6] | hdr[7] | hdr[8] | hdr[9]) & 0x80:
                        break
                    tag_size = ((hdr[6] & 0x7F) << 21) | ((hdr[7] & 0x7F) << 14) | ((hdr[8] & 0x7F) << 7) | (hdr[9] & 0x7F)
                    step = 10 + tag_size + (10 if (hdr[3] == 4 and (hdr[5] & 0x10)) else 0)
                    if step <= 0 or audio_offset + step >= size:
                        return None
                    audio_offset += step
                f.seek(audio_offset)
                head = f.read(min(size - audio_offset, 65536))
        except OSError:
            return None
        if len(head) < 16:
            return None
        # MPEG 音频帧头查表（仅严格匹配 Layer III，即真实 MP3）
        br_v1_l3 = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0]
        br_v2_l3 = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0]
        sr_tbl = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}
        end = min(len(head) - 4, 32768)
        found = None
        i = 0
        while i < end:
            if head[i] == 0xFF and (head[i + 1] & 0xE0) == 0xE0:
                b1, b2, b3 = head[i + 1], head[i + 2], head[i + 3]
                ver = (b1 >> 3) & 0x03
                layer = (b1 >> 1) & 0x03
                br_i = (b2 >> 4) & 0x0F
                sr_i = (b2 >> 2) & 0x03
                pad = (b2 >> 1) & 0x01
                chan = (b3 >> 6) & 0x03
                if ver in sr_tbl and layer == 1 and 1 <= br_i <= 14 and sr_i <= 2:
                    sr = sr_tbl[ver][sr_i]
                    kbps = br_v1_l3[br_i] if ver == 3 else br_v2_l3[br_i]
                    if kbps > 0 and sr > 0:
                        spf = 1152 if ver == 3 else 576
                        frame_len = (int((144000 * kbps) // sr) if ver == 3 else int((72000 * kbps) // sr)) + pad
                        nxt = i + frame_len
                        # 严格验证第二帧头存在且版本、层、采样率一致，杜绝伪帧头误判
                        if nxt + 3 < len(head) and head[nxt] == 0xFF and (head[nxt + 1] & 0xE0) == 0xE0:
                            nver = (head[nxt + 1] >> 3) & 0x03
                            nlayer = (head[nxt + 1] >> 1) & 0x03
                            nbr = (head[nxt + 2] >> 4) & 0x0F
                            nsr = (head[nxt + 2] >> 2) & 0x03
                            if nver == ver and nlayer == 1 and 1 <= nbr <= 14 and nsr == sr_i:
                                found = (i, ver, kbps, sr, spf, chan)
                                break
            i += 1
        if not found:
            return None
        fi, ver, kbps, sr, spf, chan = found
        # 优先检查 VBR Xing / Info 头获取准确帧数（若带 CRC16 保护位则偏移 +2 字节）
        crc_len = 2 if (head[fi + 1] & 0x01) == 0 else 0
        side = (17 if chan == 3 else 32) if ver == 3 else (9 if chan == 3 else 17)
        xi = fi + 4 + crc_len + side
        dur = 0
        if xi + 12 <= len(head) and head[xi:xi + 4] in (b"Xing", b"Info"):
            flags = struct.unpack(">I", head[xi + 4:xi + 8])[0]
            if flags & 0x01:
                frames_n = struct.unpack(">I", head[xi + 8:xi + 12])[0]
                if frames_n > 0:
                    dur = int(round(frames_n * spf / float(sr)))
        if not dur and fi + 54 <= len(head) and head[fi + 36:fi + 40] == b"VBRI":
            frames_n = struct.unpack(">I", head[fi + 50:fi + 54])[0]
            if frames_n > 0:
                dur = int(round(frames_n * spf / float(sr)))
        if not dur:
            # 缺少 Xing/Info/VBRI 可靠时长头时，流式扫描整文件帧头精准数帧（不凭前 32 帧猜 CBR，彻底避免片头静音 VBR 算错时长）
            br_tbl = br_v1_l3 if ver == 3 else br_v2_l3
            mult = 144000 if ver == 3 else 72000
            sr_idx = sr_tbl[ver].index(sr)
            try:
                frames_n = 0
                with open(path, "rb") as f:
                    f.seek(audio_offset + fi)
                    buf = b""
                    pos = 0
                    eof = False
                    while True:
                        if len(buf) - pos < 4096 and not eof:
                            chunk = f.read(262144)
                            if chunk:
                                buf = buf[pos:] + chunk
                                pos = 0
                            else:
                                eof = True
                        blen = len(buf)
                        if blen - pos < 4:
                            break
                        if buf[pos] == 0xFF and (buf[pos + 1] & 0xE0) == 0xE0:
                            sv = (buf[pos + 1] >> 3) & 0x03
                            sl = (buf[pos + 1] >> 1) & 0x03
                            sb = (buf[pos + 2] >> 4) & 0x0F
                            ssr = (buf[pos + 2] >> 2) & 0x03
                            spad = (buf[pos + 2] >> 1) & 0x01
                            if sv == ver and sl == 1 and 1 <= sb <= 14 and ssr == sr_idx:
                                flen = int((mult * br_tbl[sb]) // sr) + spad
                                if flen >= 4 and pos + flen <= blen:
                                    frames_n += 1
                                    pos += flen
                                    continue
                        # 跳过非音频字节（如末尾 ID3v1/APE 标签）
                        nx_ff = buf.find(b"\xff", pos + 1)
                        if nx_ff > 0:
                            pos = nx_ff
                        else:
                            pos = blen
                if frames_n > 0:
                    dur = int(round(frames_n * spf / float(sr)))
            except OSError:
                pass
            if not dur:
                tail_tag = 0
                try:
                    if size >= 128:
                        with open(path, "rb") as f:
                            f.seek(size - 128)
                            if f.read(3) == b"TAG":
                                tail_tag = 128
                except OSError:
                    pass
                data_bytes = max(0, size - tail_tag - (audio_offset + fi))
                dur = max(1, int(round(data_bytes * 8.0 / (kbps * 1000.0))))
        return {"duration": max(1, dur)}
    return None


def purge_exam_audio():
    """清理考完/创建超过 7 天的听力音频，以及上传超过 2 小时仍未被任何考试或平时听力引用的孤儿/残留文件（调用时已持有 lock）"""
    now = time.time()
    cutoff_day = (now_cn() - timedelta(days=EXAM_AUDIO_KEEP_DAYS)).strftime("%Y-%m-%d")
    # 顺带清理 7 天前的平时听力任务
    if listen_tasks:
        before_n = len(listen_tasks)
        listen_tasks[:] = [t for t in listen_tasks if t.get("date", "") >= cutoff_day]
        if len(listen_tasks) != before_n:
            write_json(LISTEN_PATH, listen_tasks)
    active_ids = set()
    for e in exams:
        for x in e.get("sessions") or []:
            aid = x.get("audio_id")
            if aid and x.get("date", "") >= cutoff_day:
                active_ids.add(aid)
    for t in listen_tasks:
        aid = t.get("audio_id")
        if aid and t.get("date", "") >= cutoff_day:
            active_ids.add(aid)
    gone = []
    for aid, info in list(exam_audios.items()):
        if aid in active_ids:
            continue
        used_any = any(x.get("audio_id") == aid for e in exams for x in e.get("sessions") or []) or any(t.get("audio_id") == aid for t in listen_tasks)
        if now - info.get("at", 0) < EXAM_AUDIO_ORPHAN_TTL and not used_any and not info.get("bound"):
            continue  # 刚上传还在编辑表单里且从未保存过的音频先留 30 分钟；曾绑定后主动删掉/替换的立刻清理
        gone.append(aid)
    if gone:
        for aid in gone:
            info = exam_audios.pop(aid, None)
            exam_audio_ready.pop(aid, None)
            if info:
                p = os.path.join(EXAM_AUDIO_DIR, f"{aid}.{info.get('ext', 'mp3')}")
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except OSError:
                    pass
        write_json(EXAM_AUDIO_PATH, exam_audios)
    # 同步清扫磁盘上异常中断残留的 .part 或未登记孤立文件（超过 2 小时）
    known_files = {f"{aid}.{info.get('ext', 'mp3')}" for aid, info in exam_audios.items()}
    try:
        if os.path.isdir(EXAM_AUDIO_DIR):
            for fn in os.listdir(EXAM_AUDIO_DIR):
                if fn in known_files:
                    continue
                fp = os.path.join(EXAM_AUDIO_DIR, fn)
                try:
                    if os.path.isfile(fp) and now - os.path.getmtime(fp) > 7200:
                        os.remove(fp)
                except OSError:
                    pass
    except OSError:
        pass
    # 清理内存中过期的播控记录（超过 6 小时）
    for k in [k for k, v in exam_audio_play.items() if now - v.get("at", 0) > 6 * 3600]:
        exam_audio_play.pop(k, None)


def listen_tasks_for_board(cls, date=None):
    """本班近 7 天内的平时听力任务：今天定时的带 play_ts 供自动起播，其他天的仅用于同步存活校验"""
    if PUBLIC:
        return []
    date = date or today()
    cutoff_day = (now_cn() - timedelta(days=EXAM_AUDIO_KEEP_DAYS)).strftime("%Y-%m-%d")
    out = []
    with lock:
        tasks_snap = [dict(t, classes=list(t.get("classes", []))) for t in listen_tasks if cls in t.get("classes", []) and t.get("date", "") >= cutoff_day]
        aud_snap = {aid: dict(info) for aid, info in exam_audios.items()}
    for t in tasks_snap:
        aid = t.get("audio_id")
        a = aud_snap.get(aid) if aid else None
        if not a:
            continue
        pat = t.get("play_at") or ""
        play_ts = None
        if t.get("date") == date and pat and re_hm(pat):
            try:
                play_ts = datetime.combine(datetime.strptime(date, "%Y-%m-%d").date(), datetime.strptime(pat, "%H:%M").time(), CN).timestamp()
            except ValueError:
                play_ts = None
        out.append({"id": t["id"], "title": t["title"], "date": t["date"], "play_at": pat, "play_ts": play_ts,
                    "audio": {"id": aid, "name": a["name"], "ext": a["ext"], "size": a["size"],
                              "sha256": a["sha256"], "duration": a["duration"], "url": f"/exam-audio/{aid}"}})
    return out


def exam_audio_prefetch_for(cls):
    """本班今天及未来 7 天内未结束考试，以及近 7 天内平时听力所需的音频清单（给一体机后台静默预下载）"""
    if PUBLIC:
        return []
    d0 = today()
    d_past = (now_cn() - timedelta(days=EXAM_AUDIO_KEEP_DAYS)).strftime("%Y-%m-%d")
    d1 = (now_cn() + timedelta(days=7)).strftime("%Y-%m-%d")
    out, seen = [], set()
    with lock:
        ex_snap = [{**e, "classes": list(e.get("classes", [])), "sessions": list(e.get("sessions") or []),
                    "ended": dict(e.get("ended") or {})} for e in exams if cls in e.get("classes", [])]
        lt_snap = [dict(t, classes=list(t.get("classes", []))) for t in listen_tasks if cls in t.get("classes", [])]
        aud_snap = {aid: dict(info) for aid, info in exam_audios.items()}
    for e in ex_snap:
        for i, x in enumerate(e.get("sessions") or []):
            aid = x.get("audio_id")
            a = aud_snap.get(aid) if aid else None
            if not a or aid in seen:
                continue
            if not (d0 <= x.get("date", "") <= d1):
                continue
            if (e.get("ended") or {}).get(f"{cls}|{i}"):
                continue
            seen.add(aid)
            out.append({"id": aid, "name": a["name"], "ext": a["ext"], "size": a["size"],
                        "sha256": a["sha256"], "duration": a["duration"],
                        "url": f"/exam-audio/{aid}", "date": x["date"]})
    for t in lt_snap:
        aid = t.get("audio_id")
        a = aud_snap.get(aid) if aid else None
        if not a or aid in seen:
            continue
        if not (d_past <= t.get("date", "") <= d1):
            continue
        seen.add(aid)
        out.append({"id": aid, "name": a["name"], "ext": a["ext"], "size": a["size"],
                    "sha256": a["sha256"], "duration": a["duration"],
                    "url": f"/exam-audio/{aid}", "date": t["date"]})
    return out


def exam_sessions(cls, date=None):
    """这个班某天（默认今天）的各场考试，按开考时间排好；每场带上大屏要用的时间戳和可选听力信息"""
    date = date or today()
    out = []
    for e in list(exams):
        if cls not in e["classes"]:
            continue
        ss = list(e["sessions"])
        ended_map = dict(e.get("ended") or {})
        order = sorted(range(len(ss)), key=lambda i: (ss[i]["date"], ss[i]["start"]))
        for n, i in enumerate(order):
            x = ss[i]
            if x["date"] != date:
                continue
            ts = lambda hm: datetime.combine(datetime.strptime(date, "%Y-%m-%d").date(), datetime.strptime(hm, "%H:%M").time(), CN).timestamp()
            start, end = ts(x["start"]), ts(x["end"])
            ended = ended_map.get(f"{cls}|{i}")
            nx = next((ss[j] for j in order[n + 1:]), None)
            audio = None
            show_from = start - EXAM_BEFORE
            aid = x.get("audio_id") if not PUBLIC else None
            a = exam_audios.get(aid) if aid else None
            if a:
                astart = x.get("audio_start") or x["start"]
                try:
                    astart_ts = ts(astart)
                except ValueError:
                    astart, astart_ts = x["start"], start
                audio = {"id": aid, "name": a["name"], "ext": a["ext"], "size": a["size"],
                         "sha256": a["sha256"], "duration": a["duration"],
                         "start": astart, "start_ts": astart_ts, "url": f"/exam-audio/{aid}"}
                show_from = min(show_from, astart_ts - 120)
            out.append({"key": f"{e['id']}-{i}", "exam_id": e["id"], "idx": i, "name": e["name"], "no": n + 1, "total": len(order),
                        "subject": x["subject"], "date": date, "start": x["start"], "end": x["end"], "start_ts": start, "end_ts": end,
                        "show_from": show_from, "hide_at": min(end + EXAM_AFTER, ended) if ended else end + EXAM_AFTER,
                        "ended": bool(ended), "notice": [l for l in (e.get("notice") or "").split("\n") if l.strip()],
                        "audio": audio,
                        "next": {"subject": nx["subject"], "date": nx["date"], "start": nx["start"], "end": nx["end"]} if nx else None})
    return sorted(out, key=lambda x: x["start_ts"])


def exam_now(cls, now=None):
    """这个班现在是不是在考试（开考前 15 分钟到结束后 5 分钟），优先正在进行 > 即将/已到听力起播 > 考后收卷 > 考前等待"""
    now = now or time.time()
    cands = [x for x in exam_sessions(cls) if x["show_from"] <= now < x["hide_at"]]
    if not cands:
        return None
    def _prio(x):
        if x["start_ts"] <= now < x["end_ts"]:
            return (0, x["start_ts"])
        au = x.get("audio") or {}
        if now < x["end_ts"] and au.get("id") and au.get("start_ts") is not None and now >= au["start_ts"] - 120:
            return (1, au["start_ts"])
        if now >= x["end_ts"]:
            return (2, x["end_ts"])
        return (3, x["start_ts"])
    return min(cands, key=_prio)


_exam_voice = {"at": 0, "urls": {}}


def exam_voice():
    """考试三句提示语的语音地址（合成一次就缓存；合成失败 10 分钟后再试，免得每次拉看板都卡住）"""
    if not _exam_voice["urls"] or (len(_exam_voice["urls"]) < len(EXAM_VOICE) and time.time() - _exam_voice["at"] > 600):
        _exam_voice["at"] = time.time()
        urls = {}
        for k, t in EXAM_VOICE.items():
            a = make_speech(t)
            if a:
                urls[k] = "/audio/" + a
        _exam_voice["urls"] = urls or {"_": None}
    return {k: v for k, v in _exam_voice["urls"].items() if v}


def save_exams():
    """考试安排改了（调用时已持有 lock）：通知一体机重新拉看板；被考试压住的消息按新安排重新算发送时间"""
    exam_rev[0] = now = time.time()
    write_json(EXAM_PATH, exams)
    moved = False
    for m in messages:
        if m.get("exam_hold") and m["deliver_at"] > now:
            start = max(now, m.get("send_at") or 0)  # 定时发送的不能早于老师定的时间
            ex = exam_now(m["class"], start)
            at = ex["hide_at"] if ex else start
            if at != m["deliver_at"]:
                m["deliver_at"], moved = at, True
    if moved:
        save_messages()


def morning_of(cls, date=None):
    date = date or today()
    return sorted((m for m in mornings if m["class"] == cls and m["date"] == date), key=lambda m: m.get("created", 0))


def notes_of(cls):
    """这个班还没过期的大屏留言（先发的在前）"""
    d = today()
    return [n for n in notes if n["class"] == cls and (not n.get("until") or n["until"] >= d)]


def migrate_notices():
    """10-23 起「班级公告」并进大屏留言：旧公告搬成一条"一直显示"的留言（启动时跑一次）"""
    old = {c: t for c, t in settings.get("notices", {}).items() if t.strip()}
    if not old:
        return
    for c, t in old.items():
        notes.append({"id": next_id(notes), "class": c, "text": re.sub(r"\s+", " ", t).strip(), "subject": "", "teacher": "班主任",
                      "uid": "", "created": time.time(), "until": ""})
    settings["notices"] = {}
    write_json(NOTES_PATH, notes)
    write_json(SETTINGS_PATH, settings)


def note_by(n):
    return f"{n['subject']} · {n['teacher']}" if n.get("subject") else n["teacher"]


def homework_due(cls, date=None):
    """这一天要交的作业（按布置先后）"""
    date = date or today()
    return sorted((h for h in homework if h["class"] == cls and due_date(h) == date), key=lambda h: (h["date"], h.get("created", 0)))


def make_xlsx(sheets):
    """生成 xlsx（只用标准库）：sheets = [(表名, [[单元格, ...], ...]), ...]，第一行加粗当表头"""
    import io
    import zipfile
    from xml.sax.saxutils import escape

    def cell(v, r, c, head):
        ref = ""
        while c >= 0:
            ref = chr(65 + c % 26) + ref
            c = c // 26 - 1
        ref += str(r)
        st = ' s="1"' if head else ""
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return f'<c r="{ref}"{st}><v>{v}</v></c>'
        v = escape(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(v if v is not None else "")))
        return f'<c r="{ref}" t="inlineStr"{st}><is><t xml:space="preserve">{v}</t></is></c>'

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        n = len(sheets)
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   + "".join(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(n))
                   + "</Types>")
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                   + "".join(f'<sheet name="{escape(name[:31])}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, (name, _) in enumerate(sheets))
                   + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>' for i in range(n))
                   + f'<Relationship Id="rId{n + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr("xl/styles.xml", '<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   '<fonts count="2"><font><sz val="11"/></font><font><b/><sz val="11"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
                   '<borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs>'
                   '<cellXfs count="2"><xf/><xf fontId="1" applyFont="1"/></cellXfs></styleSheet>')
        for i, (_, rows) in enumerate(sheets):
            widths = {}
            for row in rows:
                for c, v in enumerate(row):
                    w = sum(2 if ord(ch) > 255 else 1 for ch in str(v if v is not None else ""))
                    widths[c] = min(60, max(widths.get(c, 6), w + 2))
            cols = "".join(f'<col min="{c + 1}" max="{c + 1}" width="{w}" customWidth="1"/>' for c, w in sorted(widths.items()))
            body = "".join(f'<row r="{r + 1}">' + "".join(cell(v, r + 1, c, r == 0) for c, v in enumerate(row)) + "</row>"
                           for r, row in enumerate(rows))
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                       + (f"<cols>{cols}</cols>" if cols else "") + f"<sheetData>{body}</sheetData></worksheet>")
    return buf.getvalue()


def export_range(rng, dt=None):
    """导出的时间范围：本周 / 上周 / 本月 / 全部 → (开始时间戳, 结束时间戳, 说明)"""
    dt = dt or now_cn()
    wk = week_start_ts(dt)
    if rng == "last":
        return wk - 7 * 86400, wk, "上周"
    if rng == "month":
        return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp(), 2e10, "本月"
    if rng == "all":
        return 0, 2e10, "全部"
    return wk, 2e10, "本周"


def export_sheets(cls, full, rng="week"):
    """导出本班数据：任课老师只有作业记录；班主任/管理员再加积分、考勤（导出"全部"时再加班级设置）"""
    fmt = lambda ts: datetime.fromtimestamp(ts, CN).strftime("%Y-%m-%d %H:%M")
    t0, t1, label = export_range(rng)
    d0, d1 = datetime.fromtimestamp(t0, CN).strftime("%Y-%m-%d"), datetime.fromtimestamp(min(t1, 4e9), CN).strftime("%Y-%m-%d")
    in_d = lambda d: d0 <= d < d1 if t1 < 2e10 else d >= d0
    col = {c["hw_id"]: c for c in collects if c["class"] == cls}
    hw = [["日期", "科目", "作业内容", "用时（分钟）", "交作业时间", "布置老师", "收交登记", "未交人数", "未交名单"]]
    for h in sorted((h for h in homework if h["class"] == cls and in_d(h["date"])), key=lambda h: (h["date"], h.get("created", 0))):
        c = col.get(h["id"])
        hw.append([h["date"], h["subject"], h["content"], h.get("minutes") or "", f'{h.get("due") or "明天交"}（{due_date(h)[5:]}）', h.get("teacher", ""),
                   "已登记" if c else "未登记", len(c["missing"]) if c else "", "、".join(c["missing"]) if c else ""])
    miss = {}
    for r in hw[1:]:
        for n in (r[8].split("、") if r[8] else []):
            miss.setdefault(n, []).append(f"{r[0][5:]} {r[1]}")
    sheets = [("作业记录", hw), ("未交统计", [["姓名", "未交次数", "哪几项"]]
                                    + [[n, len(v), "；".join(v)] for n, v in sorted(miss.items(), key=lambda x: -len(x[1]))])]
    if not full:
        return sheets
    names = [s["name"] for s in load_students().get(cls, [])]
    dt = now_cn()
    month = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    wk, mo, al = point_totals(cls, week_start_ts(dt)), point_totals(cls, month), point_totals(cls)
    extra = [n for n in al if n not in names]
    rg = {}
    for p in points:
        if p["class"] == cls and t0 <= p["ts"] < t1:
            rg[p["name"]] = rg.get(p["name"], 0) + p["delta"]
    rows_p = [[n] + ([rg.get(n, 0)] if label in ("上周",) else []) + [wk.get(n, 0), mo.get(n, 0), al.get(n, 0)] for n in names + extra]
    hdr = ["姓名"] + (["上周"] if label == "上周" else []) + ["本周", "本月", "累计"]
    sort_i = 1 if label == "上周" else {"本周": 1, "本月": 2, "全部": 3}[label]
    sheets.append(("积分汇总", [hdr] + sorted(rows_p, key=lambda r: -r[sort_i])))
    sheets.append(("积分明细", [["时间", "姓名", "加减分", "理由", "老师"]]
                   + [[fmt(p["ts"]), p["name"], p["delta"], p.get("reason", ""), p.get("teacher", "")]
                      for p in sorted((p for p in points if p["class"] == cls and t0 <= p["ts"] < t1), key=lambda p: p["ts"])]))
    recs = sorted((a for a in attends if a["class"] == cls and in_d(a["date"])), key=lambda a: (a["date"], SESSIONS.index(a["session"]) if a["session"] in SESSIONS else 9))
    det = [["日期", "时段", "姓名", "情况"]]
    cnt = {}
    for a in recs:
        for n, t in a["marks"].items():
            det.append([a["date"], a["session"], n, t])
            cnt.setdefault(n, {}).setdefault(t, 0)
            cnt[n][t] += 1
    sheets.append(("考勤统计", [["姓名"] + ATTEND_TYPES + ["合计"]]
                   + sorted(([n] + [cnt.get(n, {}).get(t, 0) for t in ATTEND_TYPES] + [sum(cnt.get(n, {}).values())]
                             for n in names + [x for x in cnt if x not in names]), key=lambda r: -r[-1])))
    sheets.append(("考勤明细", det))
    if rng != "all":
        return sheets
    sch = schedule_of(cls)
    tt = settings.get("timetable", {}).get(cls, {})
    day_p = sch["periods"]
    duty = settings.get("duty", {}).get(cls, {})
    cfg = ccfg(cls)
    rows = [["学生名单"], ["序号", "姓名"]] + [[i + 1, n] for i, n in enumerate(names)]
    rows += [[], ["值日表"], ["星期", "值日生（任务）"]]
    rows += [["周" + "一二三四五六日"[d - 1], "、".join(x["name"] + (f"（{x['task']}）" if x.get("task") else "") for x in duty.get(str(d), []))]
             for d in range(1, 8) if duty.get(str(d))]
    rows += [[], ["作息时间" + ("（本班单独设置）" if settings.get("class_schedule", {}).get(cls) else "（全校作息）")],
             ["节次", "开始", "结束"]] + [[i + 1, a, b] for i, (a, b) in enumerate(sch["periods"])]
    rows += [["上课日", "、".join("周" + "一二三四五六日"[d - 1] for d in sch["weekdays"])]]
    rows += [[], ["课程表"], ["节次", "时间"] + ["周" + "一二三四五六"[d - 1] for d in range(1, 7)]]
    rows += [[i + 1, f"{a}-{b}"] + [((tt.get(str(d)) or [])[i] if i < len(tt.get(str(d)) or []) else {}).get("subject", "") for d in range(1, 7)]
             for i, (a, b) in enumerate(day_p)]
    rows += [[], ["定时提醒"], ["时间", "重复", "内容", "状态"]]
    rows += [[r["time"], "、".join("周" + "一二三四五六日"[d - 1] for d in r.get("days", [])), r["text"], "开" if r.get("enabled") else "关"]
             for r in reminders if cls in r.get("classes", [])]
    rows += [[], ["班级公告"], [settings.get("notices", {}).get(cls, "")]]
    rows += [[], ["倒计时"], ["名称", "日期"]] + [[c["name"], c["date"]] for c in cfg["countdowns"]]
    rows += [[], ["自定义每日一句"]] + [[q] for q in cfg["quotes"]["custom"]]
    rows += [[], [f"导出时间：{dt.strftime('%Y-%m-%d %H:%M')}"]]
    sheets.append(("班级设置", rows))
    return sheets


def read_xlsx(raw):
    """读取 xlsx（只用标准库）：返回 {工作表名: [[单元格文字, ...], ...]}"""
    import io
    import xml.etree.ElementTree as ET
    import zipfile
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    tag = "{%s}" % ns["m"]
    z = zipfile.ZipFile(io.BytesIO(raw))
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
            shared.append("".join(t.text or "" for t in si.iter(tag + "t")))
    names = [sh.get("name") for sh in ET.fromstring(z.read("xl/workbook.xml")).find("m:sheets", ns)]
    files = sorted((n for n in z.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)),
                   key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))

    def col(ref):
        n = 0
        for ch in re.match(r"[A-Z]+", ref).group(0):
            n = n * 26 + ord(ch) - 64
        return n - 1
    out = {}
    for name, f in zip(names, files):
        rows = []
        for r in ET.fromstring(z.read(f)).iter(tag + "row"):
            vals = {}
            for c in r.findall("m:c", ns):
                v, t = c.find("m:v", ns), c.get("t")
                if t == "s" and v is not None:
                    val = shared[int(v.text)]
                elif t == "inlineStr":
                    val = "".join(x.text or "" for x in c.iter(tag + "t"))
                else:
                    val = v.text if v is not None else ""
                vals[col(c.get("r"))] = (val or "").strip()
            if vals:
                rows.append([vals.get(i, "") for i in range(max(vals) + 1)])
        out[name] = rows
    return out


def qlabel(x):
    """智学网题号 "01" -> "1"，"12(1)(2)" 保持"""
    x = str(x).strip()
    return x.lstrip("0") or "0" if re.fullmatch(r"\d+", x) else x


def current_session(dt=None, cls=None):
    hm = (dt or now_cn()).strftime("%H:%M")
    first = (schedule_of(cls)["periods"] or [["08:00"]])[0][0]
    return "早读" if hm < first else "上午" if hm < "12:30" else "下午" if hm < "18:00" else "晚自习"


def attend_today(cls):
    d = today()
    return {a["session"]: a for a in attends if a["class"] == cls and a["date"] == d}


def collect_of(cls, hw_id):
    return next((c for c in collects if c["class"] == cls and c["hw_id"] == hw_id), None)


def homework_summary(cls):
    items = homework_of(cls)
    if not items:
        return None
    total = sum(h["minutes"] for h in items)
    lines = [f"{h['subject']}：{h['content']}" + (f"（{h['due']}）" if h.get("due") else "") for h in items]
    return f"今日作业 · 共约 {total} 分钟\n" + "\n".join(lines)


def duty_today(cls, dt=None):
    dt = dt or now_cn()
    return settings.get("duty", {}).get(cls, {}).get(str(dt.isoweekday()), [])


def duty_text(cls):
    items = duty_today(cls)
    return "、".join(d["name"] + (f"（{d['task']}）" if d.get("task") else "") for d in items) or "今天没有安排值日"


def week_start_ts(dt=None):
    dt = dt or now_cn()
    monday = (dt - timedelta(days=dt.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return monday.timestamp()


def point_totals(cls, since=0):
    totals = {}
    for p in points:
        if p["class"] == cls and p["ts"] >= since:
            totals[p["name"]] = totals.get(p["name"], 0) + p["delta"]
    return totals


def save_users():
    write_json(USERS_PATH, users)


def save_devices():
    write_json(DEVICES_PATH, devices)


def norm_mac(mac):
    """统一 MAC 格式为 AA:BB:CC:DD:EE:FF；过滤空地址、广播地址、组播地址"""
    s = re.sub(r"[^0-9A-Fa-f]", "", str(mac or "")).upper()
    if len(s) != 12 or s in ("000000000000", "FFFFFFFFFFFF") or (int(s[:2], 16) & 1):
        return ""
    return ":".join(s[i:i + 2] for i in range(0, 12, 2))


_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def class_sort_key(name):
    """按学校常见顺序排班级（高一1班、高一2班……高一10班、高二1班……），70 个班也一目了然"""
    parts = []
    for tok in re.split(r"(\d+|[一二三四五六七八九十])", str(name)):
        if not tok:
            continue
        if tok.isdigit():
            parts.append((0, int(tok), ""))
        elif tok in _CN_NUM:
            parts.append((0, _CN_NUM[tok], tok))
        else:
            parts.append((1, 0, tok))
    return parts


def device_map_by_class():
    """班级 -> 已绑定的大屏信息（供工作台班级列表显示）"""
    out = {}
    for mac, b in devices["bindings"].items():
        cls = resolve_class(b.get("class") or "")
        if cls:
            out[cls] = {"mac": mac, "hostname": b.get("hostname", ""),
                        "bound_at": datetime.fromtimestamp(b.get("bound_at", 0), CN).strftime("%m-%d %H:%M") if b.get("bound_at") else ""}
    return out


def device_list_info():
    """所有已绑定大屏的清单（按班级顺序排好）"""
    now = time.time()
    items = []
    for mac, b in devices["bindings"].items():
        cls = resolve_class(b.get("class") or "")
        items.append({"mac": mac, "class": cls, "hostname": b.get("hostname", ""),
                      "bound_at": datetime.fromtimestamp(b.get("bound_at", 0), CN).strftime("%m-%d %H:%M") if b.get("bound_at") else "",
                      "online": now - last_seen.get(cls, 0) < 15})
    items.sort(key=lambda x: (class_sort_key(x["class"]), x["mac"]))
    return items


def class_key(cls):
    """这个班的教室密钥：有独立密钥用它；没有的话本校服务器用全局密钥，公共服务器没有"""
    return users["class_keys"].get(cls) or (None if PUBLIC else CFG["classroom_key"])


def files_disk_bytes():
    """发到大屏的文件实际占用的硬盘字节数（群发硬链接共享同一份磁盘块，只算一次）"""
    seen, total = set(), 0
    for f in files:
        k = f.get("blob") or f["id"]
        if k not in seen:
            seen.add(k)
            total += f["size"]
    return total


def purge_files(cls=None):
    """删掉过期（7 天）的文件；给了 cls 就把这个班的文件全删（调用时已持有 lock）"""
    cutoff = time.time() - FILE_KEEP_DAYS * 86400
    keep = []
    for f in files:
        if f["at"] < cutoff or (cls and f["class"] == cls):
            try:
                os.remove(os.path.join(FILE_DIR, f["id"]))
            except OSError:
                pass
        else:
            keep.append(f)
    if len(keep) != len(files):
        gone = {f["id"] for f in files} - {f["id"] for f in keep}
        files[:] = keep
        write_json(FILE_PATH, files)
        hit = False
        for m in messages:  # 文件删了，大屏上还没点开的那条也撤回，免得一体机去下载一个已经没有的文件
            if m.get("kind") == "file" and (m.get("data") or {}).get("file") in gone and not m.get("recalled"):
                m["recalled"] = hit = True
        if hit:
            save_messages()


def resolve_class(cls):
    """一体机、登记页里写的可能是改名前的班级名：换成现在的名字"""
    for _ in range(5):
        if cls not in users["aliases"] or cls in load_students():
            break
        cls = users["aliases"][cls]
    return cls


def rename_class(old, new):
    """改班级名：成员、密钥、邀请码、设置和全部记录（作业、消息、考勤、积分……）都跟着改（调用时已持有 lock）"""
    for d in (users["acl"], users["class_keys"]):
        if old in d:
            d[new] = d.pop(old)
    for inv in users["invites"].values():
        if inv["class"] == old:
            inv["class"] = new
    al = users["aliases"]
    for k in [k for k, v in al.items() if v == old]:
        al[k] = new
    al[old] = new
    al.pop(new, None)
    save_users()
    for items, path in ((messages, MSG_PATH), (homework, HOMEWORK_PATH), (collects, COLLECT_PATH), (attends, ATTEND_PATH),
                        (points, POINTS_PATH), (quizzes, QUIZ_PATH), (mornings, MORNING_PATH), (notes, NOTES_PATH), (files, FILE_PATH)):
        for x in items:
            if x.get("class") == old:
                x["class"] = new
        write_json(path, items)
    for r in reminders:
        r["classes"] = [new if c == old else c for c in r["classes"]]
    write_json(REMINDER_PATH, reminders)
    for e in exams:
        if old in e.get("classes", []):
            e["classes"] = [new if c == old else c for c in e["classes"]]
            e["ended"] = {(new + k[len(old):] if k.startswith(old + "|") else k): v for k, v in (e.get("ended") or {}).items()}
    write_json(EXAM_PATH, exams)
    for key in ("notices", "duty", "cls_cfg", "timetable", "class_schedule"):
        if old in settings.get(key, {}):
            settings[key][new] = settings[key].pop(old)
    custom = settings.get("quotes", {}).get("custom", {})
    if old in custom:
        custom[new] = custom.pop(old)
    for i in settings.get("recent_images", []):
        if i.get("class") == old:
            i["class"] = new
    for name, cl in settings.get("my_classes", {}).items():
        settings["my_classes"][name] = [new if c == old else c for c in cl]
    write_json(SETTINGS_PATH, settings)
    for d in (last_seen, pick_history):
        if old in d:
            d[new] = d.pop(old)
    dev_changed = False
    for b in devices["bindings"].values():
        if b.get("class") == old:
            b["class"] = new
            dev_changed = True
    if dev_changed:
        save_devices()
    for m_ in exam_audio_ready.values():
        if old in m_:
            m_[new] = m_.pop(old)
    for k in list(exam_audio_play):
        parts = k.split("|", 2)
        if len(parts) == 3 and parts[2] == old:
            exam_audio_play[f"{parts[0]}|{parts[1]}|{new}"] = exam_audio_play.pop(k)
    lt_changed = False
    for t in listen_tasks:
        if old in t.get("classes", []):
            t["classes"] = [new if c == old else c for c in t["classes"]]
            lt_changed = True
    if lt_changed:
        write_json(LISTEN_PATH, listen_tasks)


def drop_class(cls):
    """删班：这个班的名单、成员、密钥、消息、作业、考勤、积分、错题、提醒、设置全部清掉（调用时已持有 lock）"""
    roster = read_json(ROSTER_PATH, {})
    roster.pop(cls, None)
    if cls not in roster.get("_deleted", []):
        roster.setdefault("_deleted", []).append(cls)
    write_json(ROSTER_PATH, roster)
    users["acl"].pop(cls, None)
    users["class_keys"].pop(cls, None)
    for k in [k for k, v in users["aliases"].items() if v == cls]:
        users["aliases"].pop(k)
    for code in [k for k, v in users["invites"].items() if v["class"] == cls]:
        users["invites"].pop(code)
    save_users()
    for items, path in ((messages, MSG_PATH), (homework, HOMEWORK_PATH), (collects, COLLECT_PATH), (attends, ATTEND_PATH),
                        (points, POINTS_PATH), (quizzes, QUIZ_PATH), (mornings, MORNING_PATH), (notes, NOTES_PATH)):
        items[:] = [x for x in items if x.get("class") != cls]
        write_json(path, items)
    for r in reminders:
        r["classes"] = [c for c in r["classes"] if c != cls]
    reminders[:] = [r for r in reminders if r["classes"]]
    write_json(REMINDER_PATH, reminders)
    for key in ("notices", "duty", "cls_cfg", "timetable", "class_schedule"):
        settings.get(key, {}).pop(cls, None)
    settings.get("quotes", {}).get("custom", {}).pop(cls, None)
    settings["recent_images"] = [i for i in settings.get("recent_images", []) if i.get("class") != cls]
    write_json(SETTINGS_PATH, settings)
    last_seen.pop(cls, None)
    pick_history.pop(cls, None)
    drop_macs = [m for m, b in devices["bindings"].items() if b.get("class") == cls]
    if drop_macs:
        for m in drop_macs:
            devices["bindings"].pop(m, None)
        save_devices()
    for m_ in exam_audio_ready.values():
        m_.pop(cls, None)
    for k in [k for k in exam_audio_play if k.endswith("|" + cls)]:
        exam_audio_play.pop(k, None)
    ex_changed = False
    for e in exams:
        if cls in e.get("classes", []):
            e["classes"] = [c for c in e["classes"] if c != cls]
            e["ended"] = {k: v for k, v in (e.get("ended") or {}).items() if not k.startswith(cls + "|")}
            ex_changed = True
    if ex_changed:
        exams[:] = [e for e in exams if e.get("classes")]
        save_exams()
    lt_changed = False
    for t in listen_tasks:
        if cls in t.get("classes", []):
            t["classes"] = [c for c in t["classes"] if c != cls]
            lt_changed = True
    if lt_changed:
        listen_tasks[:] = [t for t in listen_tasks if t.get("classes")]
        write_json(LISTEN_PATH, listen_tasks)
    purge_exam_audio()
    purge_files(cls)


def ccfg(cls):
    """某个班的大屏设置：默认值 <- 旧的全局设置 <- 这个班自己的设置"""
    q = settings.get("quotes", {})
    base = {"countdowns": settings.get("countdowns", [{"name": "高考", "date": "2027-06-07"}]),
            "standby": settings.get("standby", {"enabled": True, "idle_minutes": 5}),
            "screen_font": settings.get("screen_font", "wenkai"), "board_points": settings.get("board_points", True), "eve_plan": [], "tools_tab": True, "desk_board": False,
            "quotes": {"enabled": q.get("enabled", True), "builtin": q.get("builtin", True),
                       "custom": q.get("custom", {}).get(cls, [])}}
    own = settings.get("cls_cfg", {}).get(cls, {})
    out = {**base, **{k: v for k, v in own.items() if k != "quotes"}}
    out["quotes"] = {**base["quotes"], **own.get("quotes", {})}
    return out


def subjects_of(cls):
    """这个班的科目：班主任在工作台勾选过就用自己的（理科班、文科班不一样），没设过就用全校的"""
    return settings.get("cls_cfg", {}).get(cls, {}).get("subjects") or CFG["subjects"]


def make_backup():
    """把所有数据打包成 backups/data-日期-时间.tgz，保留最近 30 天。语音、缩略图是可重新生成的缓存，发到大屏的文件只留 7 天，都不备份"""
    import tarfile
    os.makedirs(BACKUP_DIR, exist_ok=True)
    name = f"data-{now_cn().strftime('%Y%m%d-%H%M%S')}.tgz"
    path = os.path.join(BACKUP_DIR, name)

    def skip_cache(info):
        rel = info.name.replace("\\", "/")
        if rel.startswith(("data/audio", "data/files/", "data/exam_audio/")) or re.search(r"data/images/.+_\d+x\d+\.(png|gif)$", rel) or rel.endswith(".tmp"):
            return None
        return info
    with lock:  # 打包时不让别的请求写数据，保证备份是完整的
        with tarfile.open(path + ".tmp", "w:gz") as tar:
            for item in ("data", "config.json", "students.csv"):
                if os.path.exists(os.path.join(BASE, item)):
                    tar.add(os.path.join(BASE, item), arcname=item, filter=skip_cache)
        os.replace(path + ".tmp", path)
    cutoff = time.time() - 30 * 86400
    for f in os.listdir(BACKUP_DIR):
        fp = os.path.join(BACKUP_DIR, f)
        if f.startswith("data-") and f.endswith(".tgz") and os.path.getmtime(fp) < cutoff:
            os.remove(fp)
    return name


def list_backups():
    if not os.path.isdir(BACKUP_DIR):
        return []
    files = sorted((f for f in os.listdir(BACKUP_DIR) if f.startswith("data-") and f.endswith(".tgz")), reverse=True)
    return [{"name": f, "size": os.path.getsize(os.path.join(BACKUP_DIR, f)),
             "time": datetime.fromtimestamp(os.path.getmtime(os.path.join(BACKUP_DIR, f)), CN).strftime("%m-%d %H:%M")}
            for f in files]


def backup_loop():
    """每天北京时间 3 点以后，如果今天还没备份就备份一次；顺带定期清理 7 天前的考试听力文件"""
    while True:
        try:
            with lock:
                purge_exam_audio()
            dt = now_cn()
            tag = "data-" + dt.strftime("%Y%m%d")
            if dt.hour >= 3 and not any(b["name"].startswith(tag) for b in list_backups()):
                print("每日备份完成：", make_backup())
        except Exception as e:
            print("备份出错：", e)
        time.sleep(600)


PREP_DEFAULT = {"enabled": True, "voice": False, "after": 3, "default": "课本、学案", "items": {"体健": "运动鞋，准时到操场集合"}}
prep_fired = set()  # (班级, 日期, 第几节)，同一个课间只提醒一次


def prep_cfg(cls):
    return {**PREP_DEFAULT, **(settings.get("cls_cfg", {}).get(cls, {}).get("prep") or {})}


def check_prep(dt):
    """课前准备提醒：下课后几分钟（默认 3 分钟，避开老师拖堂）提醒下节课带什么，上课时自动消失。
    前面没有课或者是长休息（超过 15 分钟，如大课间、午饭）时，改在上课前 10 分钟提醒"""
    hm = dt.strftime("%H:%M")
    mins = lambda t: int(t[:2]) * 60 + int(t[3:])
    now_m = dt.hour * 60 + dt.minute
    for cls in list(settings.get("timetable", {})):
        cfg = prep_cfg(cls)
        if not cfg["enabled"]:
            continue
        classes, _, _ = today_classes(cls, dt)
        for i, c in enumerate(classes):
            if not c["subject"] or c["subject"] in ("自习", "班会") or (cls, today(), i) in prep_fired:
                continue
            start = mins(c["start"])
            prev_end = mins(classes[i - 1]["end"]) if i > 0 else None
            if prev_end is not None and start - prev_end <= 15:
                fire_at = min(prev_end + int(cfg.get("after") or 3), start - 1)
            else:
                fire_at = start - 10
            if fire_at <= now_m < start and now_m - fire_at <= 2:
                prep_fired.add((cls, today(), i))
                items = cfg["items"].get(c["subject"]) or cfg["default"]
                text = f"下节课：{c['subject']}　{c['start']} 上课\n请准备好：{items}"
                start_ts = datetime.combine(dt.date(), datetime.strptime(c["start"], "%H:%M").time(), CN).timestamp()
                create_messages([cls], text, "课前提醒", voice=cfg["voice"], kind="prep", expire_at=start_ts,
                                say=f"下节课{c['subject']}，请准备好{items}", repeat=1)


def reminder_loop():
    """每 15 秒检查一次定时提醒"""
    while True:
        try:
            dt = now_cn()
            hm, d = dt.strftime("%H:%M"), today()
            due = []
            with lock:
                for r in reminders:
                    if r.get("enabled") and dt.isoweekday() in r["days"] and r["time"] <= hm \
                            and r.get("last_fired") != d and r["time"] >= (dt - timedelta(minutes=3)).strftime("%H:%M"):
                        r["last_fired"] = d
                        due.append(dict(r))
                if due:
                    write_json(REMINDER_PATH, reminders)
            check_prep(dt)
            for r in due:
                ruid = r.get("uid") or ""
                if "{值日}" in r["text"]:
                    for cls in r["classes"]:
                        create_messages([cls], r["text"].replace("{值日}", duty_text(cls)),
                                        r.get("creator", "定时提醒"), voice=r.get("voice"),
                                        urgent=r.get("urgent"), voice_name=r.get("voice_name"), kind="reminder",
                                        sender_uid=ruid, sender_sid="")
                else:
                    create_messages(r["classes"], r["text"], r.get("creator", "定时提醒"),
                                    voice=r.get("voice"), urgent=r.get("urgent"),
                                    voice_name=r.get("voice_name"), kind="reminder",
                                    sender_uid=ruid, sender_sid="")
        except Exception as e:
            print("定时提醒出错：", e)
        time.sleep(15)


class Handler(BaseHTTPRequestHandler):
    server_version = "ClassCall/1.0"
    timeout = 60  # 连上来 60 秒没发数据就断开：手机浏览器预先开的空连接在学校网络里断了服务器察觉不到，不设就会越攒越多，到上限后打不开文件

    def log_message(self, fmt, *args):
        pass  # 轮询请求太多，不打印访问日志

    # ---------- 工具 ----------
    def host(self):
        """本服务器的地址（写进二维码、安装命令）。在服务器电脑上用 localhost 打开工作台时，换成局域网地址，
        不然手机和一体机拿到的是 localhost，连不上"""
        h = self.headers.get("Host", "")
        name, sep, port = h.rpartition(":") if not h.endswith("]") else (h, "", "")
        if not sep:
            name, port = h, ""
        if name in ("localhost", "127.0.0.1", "[::1]"):
            ip = lan_ips()[0]
            if ip != "127.0.0.1":
                return ip + (":" + port if port else "")
        return h

    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, name):
        path = os.path.join(STATIC, name)
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_audio(self, name):
        path = os.path.join(AUDIO_DIR, name)
        if not name.endswith((".mp3", ".wav")) or not os.path.isfile(path):
            return self.send_json({"error": "not found"}, 404)
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg" if name.endswith(".mp3") else "audio/wav")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=86400")
        self.end_headers()
        self.wfile.write(body)

    def send_font(self, path):
        """工作台字体（由 fonts.sh 下载到 fonts/ 目录）"""
        parts = path.split("/")
        if len(parts) != 4 or not re.fullmatch(r"[a-z]+", parts[2]) or not re.fullmatch(r"[\w.-]+\.(css|woff2|ttf|otf)", parts[3]):
            return self.send_json({"error": "not found"}, 404)
        file = os.path.join(BASE, "fonts", parts[2], parts[3])
        if not os.path.isfile(file):
            return self.send_json({"error": "not found"}, 404)
        with open(file, "rb") as f:
            body = f.read()
        is_css = file.endswith(".css")
        self.send_response(200)
        self.send_header("Content-Type", "text/css; charset=utf-8" if is_css else "font/ttf" if file.endswith(".ttf") else "font/otf" if file.endswith(".otf") else "font/woff2")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=86400" if is_css else "max-age=31536000, immutable")
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if length <= 0:
            return {}
        try:
            limit = 12_000_000 if self.path.startswith(("/api/upload", "/api/quiz/import", "/api/roster/parse")) else 100_000
            data = json.loads(self.rfile.read(min(length, limit)).decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}

    def client_ip(self):
        ip = self.client_address[0]  # 服务器直接对外，只有本机反向代理转发的才认 X-Forwarded-For（否则谁都能伪造，限频就失效了）
        return (self.headers.get("X-Forwarded-For") or ip).split(",")[0].strip() if ip in ("127.0.0.1", "::1") else ip

    def token(self):
        return (self.headers.get("Authorization") or "").replace("Bearer ", "")

    def sid(self):
        return session_sid(self.token())

    def sess(self):
        s = sessions.get(self.token())
        return s if s and s.get("uid") and s["exp"] > time.time() else None

    def teacher(self):
        s = self.sess()
        return s["name"] if s else None

    def create_msg(self, classes, text, sender, **kw):
        """带当前登录老师账号 uid 与会话指纹发送大屏消息，防止送达通知串人和越权撤回"""
        s = self.sess()
        kw.setdefault("sender_uid", s["uid"] if s else "")
        kw.setdefault("sender_sid", self.sid() if s else "")
        return create_messages(classes, text, sender, **kw)

    def is_msg_owner(self, m, s):
        """判断这条消息是不是当前登录老师本人发的（升级前缺少 sender_uid 的旧消息无法可靠归属，仅允许管理员撤回）"""
        if not s:
            return False
        suid = m.get("sender_uid")
        if not suid:
            return False
        if s["uid"] == "admin":
            msid = m.get("sender_sid") or ""
            return suid == "admin" and (not msid or msid == self.sid())
        return suid == s["uid"]

    def role(self, cls):
        """admin（管理员）/ head（班主任）/ teacher（科任老师）/ None"""
        s = self.sess()
        if not s:
            return None
        if is_admin(s):
            return "admin"
        acl = users["acl"].get(cls, {})
        return "head" if s["uid"] in acl.get("heads", []) else "teacher" if s["uid"] in acl.get("teachers", []) else None

    def allowed(self, cls, level):
        r = self.role(cls)
        return r in ("admin", "head") or (level == "teacher" and r == "teacher")

    def can_edit_exam(self, e):
        """班主任（或管理员）能改删本班所有考试；任课老师只能改删自己建的"""
        s = self.sess()
        return all(self.allowed(c, "head") for c in e["classes"]) or bool(
            s and e.get("uid") == s["uid"] and all(self.allowed(c, "teacher") for c in e["classes"]))

    def my_classes(self):
        return [c for c in load_students() if self.role(c)]

    def multi_class_denied(self, classes):
        """公共模式禁止管理员多班群发（防误发其他学校）；普通老师允许同时发给自己加入的多个班"""
        if len(classes) <= 1:
            return None
        if PUBLIC and is_admin(self.sess()):
            return "公共服务器管理员不支持多班群发，防止误发其他学校"
        return None

    def deny(self, level="teacher"):
        msg = {
            "own": "只能修改或删除自己发的内容",
            "recall": "只能撤回自己发送的消息",
            "public_forbid": "公共服务器不支持此功能",
            "owner": "这个操作只有主管理员（用管理员密码登录）可以做",
            "head": "这个操作只有班主任可以做",
            "admin": "管理员才能做这个操作",
        }.get(level, "你不是这个班的老师")
        return self.send_json({"error": msg}, 403)

    def post_check(self, path, body):
        """POST 接口的权限检查：返回 None 表示通过，否则返回 (级别) 表示拒绝"""
        s = self.sess()
        admin, owner = is_admin(s), is_owner(s)
        cls_of = lambda items, key="id": next((x["class"] for x in items if x["id"] == body.get(key)), None)
        teacher_level = {"/api/board/show", "/api/morning/add", "/api/homework/push", "/api/collect/start", "/api/collect/remind",
                         "/api/tools/timer", "/api/tools/pick", "/api/image/push", "/api/quiz/import", "/api/quiz/save"}
        head_level = {"/api/screen/shot", "/api/points/add", "/api/duty/push", "/api/attend/start", "/api/settings"}
        if path in teacher_level:
            return None if self.allowed(str(body.get("class") or ""), "teacher") else "teacher"
        if path in head_level:
            return None if self.allowed(str(body.get("class") or ""), "head") else "head"
        if path == "/api/classroom/uninstall":
            if body.get("all") or isinstance(body.get("classes"), list):
                if PUBLIC:
                    return "public_forbid"
                return None if admin else "admin"
            return None if self.allowed(str(body.get("class") or ""), "head") else "head"
        if path == "/api/classroom/uninstall/cancel":
            if PUBLIC:
                return "public_forbid"
            return None if admin else "admin"
        if path == "/api/exams/audio/cmd":
            if PUBLIC:
                return "public_forbid"
            cs = body.get("classes") if isinstance(body.get("classes"), list) else ([body.get("class")] if body.get("class") else [])
            return None if cs and all(self.allowed(str(c), "teacher") for c in cs) else "teacher"
        if path in ("/api/listen/save", "/api/listen/cmd"):
            if PUBLIC:
                return "public_forbid"
            cs = body.get("classes") if isinstance(body.get("classes"), list) else ([body.get("class")] if body.get("class") else [])
            return None if cs and all(self.allowed(str(c), "teacher") for c in cs) else "teacher"
        if path == "/api/listen/delete":
            if PUBLIC:
                return "public_forbid"
            t_ = next((x for x in listen_tasks if x["id"] == body.get("id")), None)
            if not t_:
                return None
            return None if all(self.allowed(c, "head") for c in t_["classes"]) or (s and t_.get("uid") == s["uid"] and all(self.allowed(c, "teacher") for c in t_["classes"])) else "own"
        if path in ("/api/notes/add", "/api/files/prepare", "/api/homework/add"):
            cs = body.get("classes") if isinstance(body.get("classes"), list) else ([body.get("class")] if body.get("class") else [])
            return None if cs and all(self.allowed(str(c), "teacher") for c in cs) else "teacher"
        if path == "/api/send":
            cs = body.get("classes") if isinstance(body.get("classes"), list) else []
            return None if cs and all(self.allowed(str(c), "teacher") for c in cs) else "teacher"
        if path == "/api/recall":
            ids = body.get("ids") if isinstance(body.get("ids"), list) else ([body.get("id")] if body.get("id") is not None else [])
            if not ids or not all(type(i) is int for i in ids):
                return "recall"
            with lock:
                by_id = {m["id"]: m for m in messages}
            for i in ids:
                m = by_id.get(i)
                if not m or not self.allowed(m["class"], "teacher"):
                    return "teacher"
                if not (admin or self.is_msg_owner(m, s)):
                    return "recall"
            return None
        if path in ("/api/notes/delete", "/api/notes/edit"):  # 自己的留言自己删改；班主任能删改本班所有留言
            n = next((x for x in notes if x["id"] == body.get("id")), None)
            return None if n and (self.allowed(n["class"], "head") or (s and n.get("uid") == s["uid"])) else "own"
        if path == "/api/files/delete":  # 自己发的自己删；班主任能删本班全部
            f = next((x for x in files if x["id"] == body.get("id")), None)
            return None if f and (self.allowed(f["class"], "head") or (s and f.get("uid") == s["uid"])) else "own"
        if path in ("/api/exams/save", "/api/exams/delete"):  # 本班老师都能安排考试（任课老师也会安排监考）；改删：班主任全部、任课老师自己建的
            raw_ss = body.get("sessions") if isinstance(body.get("sessions"), list) else []
            if path.endswith("save") and PUBLIC and any(isinstance(x, dict) and x.get("audio_id") for x in raw_ss):
                return "public_forbid"
            old = next((e for e in exams if e["id"] == body.get("id")), None)
            want = [str(c) for c in (body.get("classes") if isinstance(body.get("classes"), list) else [])] if path.endswith("save") else []
            if not all(self.allowed(c, "teacher") for c in want):
                return "teacher"
            return None if not old or self.can_edit_exam(old) else "own"
        if path == "/api/exams/end":  # 本班老师（如监考老师）都能提前结束
            return None if self.allowed(str(body.get("class") or ""), "teacher") else "teacher"
        if path == "/api/morning/delete":
            c = cls_of(mornings)
            return None if c and self.allowed(c, "teacher") else "teacher"
        if path in ("/api/homework/delete", "/api/homework/edit", "/api/quiz/delete"):
            items = homework if "homework" in path else quizzes
            it = next((x for x in items if x["id"] == body.get("id")), None)
            if not it:
                return None
            own = it.get("teacher") == s["name"]
            return None if self.allowed(it["class"], "head") or (own and self.allowed(it["class"], "teacher")) else "head"
        if path in ("/api/reminders/save", "/api/reminders/delete"):
            r = next((x for x in reminders if x["id"] == body.get("id")), None)
            if r:  # 改/删已有提醒：自己建的，或者是这个班的班主任
                own = (r.get("uid") == s["uid"]) if r.get("uid") else (r.get("creator") == s["name"])
                if not all(self.allowed(c, "head") or (own and self.allowed(c, "teacher")) for c in r["classes"]):
                    return "head"
            if path.endswith("save"):
                cs = body.get("classes") if isinstance(body.get("classes"), list) else []
                return None if cs and all(self.allowed(str(c), "teacher") for c in cs) else "teacher"
            return None
        if path == "/api/points/delete":
            c = cls_of(points)
            return None if c is None or self.allowed(c, "head") else "head"
        if path == "/api/attend/delete":
            c = cls_of(attends)
            return None if c is None or self.allowed(c, "head") else "head"
        if path == "/api/roster/save":
            target = str(body.get("old") or body.get("class") or "")
            exists = target in load_students()
            return None if (exists and self.allowed(target, "head")) or admin else ("head" if exists else "admin")
        if path == "/api/schedule":  # 全校作息只有非公共模式管理员能改；本班作息班主任也能改
            scope = body.get("scope")
            if scope == "class":
                if PUBLIC and "apply_all" in body and body.get("apply_all") not in (None, False):
                    return "public_forbid"
                return None if self.allowed(str(body.get("class") or ""), "head") else "head"
            if PUBLIC:
                return "public_forbid"
            return None if admin else "admin"
        if path == "/api/roster/delete":  # 公共服务器上班主任可以删自己的班
            return None if admin or (PUBLIC and self.role(str(body.get("class") or "")) == "head") else "admin"
        if path in ("/api/backup/now", "/api/register/code", "/api/admin/password", "/api/admins/set"):
            return None if owner else "owner"
        if path == "/api/invites/new":
            if body.get("role") == "head":
                return None if admin else "admin"
            return None if self.allowed(str(body.get("class") or ""), "head") else "head"
        if path == "/api/members/role":  # 班主任和任课老师互换：只有管理员能做（免得班主任之间互相降）
            return None if admin else "admin"
        if path in ("/api/device/toggle", "/api/device/unbind"):  # 桌面云大屏 MAC 绑定管理：只有管理员能做
            return None if admin else "admin"
        if path == "/api/members/remove":
            return None if self.allowed(str(body.get("class") or ""), "head") else "head"
        return None  # 其余（上传图片、试听、常用语、解析名单……）登录即可

    def classroom_ok(self, q, cls):
        """一体机的密钥按班校验"""
        key = (q.get("key") or [""])[0] or self.headers.get("X-Key", "")
        want = class_key(str(cls or ""))
        return bool(want) and hmac.compare_digest(key.encode(), want.encode())

    def screen_count(self, path, body):
        """这次请求会往大屏发几条消息（公共模式按老师限频用）"""
        if path == "/api/send":
            raw_cs = body.get("classes") if isinstance(body.get("classes"), list) else []
            return len(list(dict.fromkeys(str(c).strip() for c in raw_cs if str(c or "").strip())))
        if path == "/api/homework/add":
            if not body.get("push"):
                return 0
            raw_cs = body.get("classes") if isinstance(body.get("classes"), list) else ([body.get("class")] if body.get("class") else [])
            return len(list(dict.fromkeys(str(c).strip() for c in raw_cs if str(c or "").strip())))
        if path == "/api/points/add":
            return 1 if body.get("praise") else 0
        return 1 if path in ("/api/homework/push", "/api/tools/timer", "/api/tools/pick", "/api/image/push", "/api/collect/start",
                             "/api/collect/remind", "/api/attend/start", "/api/duty/push", "/api/board/show") else 0

    # ---------- 路由 ----------
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if q.get("class") and users["aliases"]:
            q["class"] = [resolve_class(q["class"][0])]
        if u.path in ("/", "/index.html"):
            return self.send_file("index.html")
        if u.path in ("/classroom", "/classroom.html"):
            return self.send_file("classroom.html")
        if u.path in ("/collect", "/collect.html"):
            return self.send_file("collect.html")
        if u.path in ("/attend", "/attend.html"):
            return self.send_file("attend.html")
        if u.path == "/api/attend/today":
            cls = (q.get("class") or [""])[0]
            if not self.classroom_ok(q, cls):
                return self.send_json({"error": "密钥错误"}, 403)
            if cls not in load_students():
                return self.send_json({"error": f"没有「{cls}」这个班，请关掉这个页面，从桌面图标重新打开"}, 404)
            return self.send_json({"class": cls, "students": [s["name"] for s in load_students().get(cls, [])],
                                   "sessions": SESSIONS, "types": ATTEND_TYPES, "current": current_session(cls=cls),
                                   "records": {k: v["marks"] for k, v in attend_today(cls).items()},
                                   "date": now_cn().strftime("%m月%d日")})
        if u.path == "/api/collect/today":
            cls = (q.get("class") or [""])[0]
            if not self.classroom_ok(q, cls):
                return self.send_json({"error": "密钥错误"}, 403)
            if cls not in load_students():
                return self.send_json({"error": f"没有「{cls}」这个班，请关掉这个页面，从桌面图标重新打开"}, 404)
            items = homework_due(cls)  # 今天要交的作业：昨天布置的"明天交"、今天布置的"今天交"……
            return self.send_json({"class": cls, "students": [s["name"] for s in load_students().get(cls, [])],
                                   "homework": [{**h, "due_date": due_date(h), "collect": collect_of(cls, h["id"])} for h in items],
                                   "today": today(), "yesterday": (now_cn() - timedelta(days=1)).strftime("%Y-%m-%d"),
                                   "subjects": subjects_of(cls)})
        if u.path.startswith("/exam-audio/"):  # 一体机后台预下载考试听力音频（多班共用同一份文件，按班校验密钥）
            if PUBLIC:
                return self.send_json({"error": "公共服务器不支持考试听力"}, 403)
            aid = os.path.basename(u.path)
            cls = (q.get("class") or [""])[0]
            if not cls or not self.classroom_ok(q, cls):
                return self.send_json({"error": "not found"}, 404)
            fh = None
            ext = "mp3"
            fsize = 0
            with lock:
                info = exam_audios.get(aid)
                if info:
                    used = (any(cls in e.get("classes", []) and any(x.get("audio_id") == aid for x in e.get("sessions") or []) for e in exams)
                            or any(cls in t.get("classes", []) and t.get("audio_id") == aid for t in listen_tasks))
                    ext = info.get("ext", "mp3")
                    path = os.path.join(EXAM_AUDIO_DIR, f"{aid}.{ext}")
                    if used and os.path.isfile(path):
                        try:
                            fh = open(path, "rb")
                            fsize = os.fstat(fh.fileno()).st_size
                        except OSError:
                            fh = None
            if not fh:
                return self.send_json({"error": "not found"}, 404)
            try:
                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg" if ext == "mp3" else "audio/wav")
                self.send_header("Content-Length", str(fsize))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                shutil.copyfileobj(fh, self.wfile, 1024 * 1024)
            finally:
                fh.close()
            return
        if u.path.startswith("/files/"):  # 一体机下载老师发来的文件（按班校验密钥）
            fid = os.path.basename(u.path)
            f = next((x for x in files if x["id"] == fid), None)
            path = os.path.join(FILE_DIR, fid)
            if not f or not self.classroom_ok(q, f["class"]) or not os.path.isfile(path):
                return self.send_json({"error": "not found"}, 404)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(os.path.getsize(path)))
            self.end_headers()
            with open(path, "rb") as fh:
                shutil.copyfileobj(fh, self.wfile, 1024 * 1024)
            return
        if u.path.startswith("/img/"):
            name = os.path.basename(u.path)
            path = os.path.join(IMG_DIR, name)
            if not re.fullmatch(r"[0-9a-f]{24}\.(png|jpg)", name) or not os.path.isfile(path):
                return self.send_json({"error": "not found"}, 404)
            try:  # 一体机按屏幕大小要缩放好的图（?w=&h=），结果缓存
                fw, fh = int((q.get("w") or [0])[0]), int((q.get("h") or [0])[0])
            except ValueError:
                fw = fh = 0
            gif = (q.get("fmt") or [""])[0] == "gif"  # 老版本 tkinter（8.5）只认 GIF
            if 100 <= fw <= 4000 and 100 <= fh <= 3000:
                fit = os.path.join(IMG_DIR, f"{name[:24]}_{fw}x{fh}.{'gif' if gif else 'png'}")
                if not os.path.exists(fit):
                    from PIL import Image
                    img = Image.open(path)
                    scale = min(fw / img.width, fh / img.height)
                    img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)
                    if gif:
                        img.convert("P", palette=Image.ADAPTIVE, colors=256).save(fit, "GIF")
                    else:
                        img.save(fit, "PNG")
                path = fit
            with open(path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "image/gif" if path.endswith(".gif") else "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=31536000, immutable")
            self.end_headers()
            return self.wfile.write(body)
        if u.path.startswith("/audio/"):
            return self.send_audio(os.path.basename(u.path))
        if u.path.startswith("/fonts/"):
            return self.send_font(u.path)
        if u.path in ("/kylin/install.sh", "/kylin/install.ps1", "/kylin/uninstall.sh", "/kylin/uninstall.ps1", "/kylin/receiver.py"):  # 一体机安装/卸载文件（麒麟 / Windows）
            path = os.path.join(BASE, "kylin", os.path.basename(u.path))
            with open(path, "rb") as f:
                body = f.read()
            if u.path.endswith((".sh", ".ps1")):  # 填上本服务器地址，别的学校自己搭的服务器也能直接用
                body = body.replace(b"__SERVER__", f"http://{self.host()}".encode())
            if u.path.endswith(".ps1"):  # Windows 安装命令把班级、密钥放在网址里（PowerShell 单引号字符串，' 要写成 ''）
                ps = lambda k: (q.get(k) or [""])[0].replace("'", "''").replace("\r", "").replace("\n", "")
                auto = "1" if (q.get("auto") or [""])[0] == "1" else "0"
                body = (body.replace(b"'__CLASS__'", f"'{ps('c')}'".encode())
                            .replace(b"'__KEY__'", f"'{ps('k')}'".encode())
                            .replace(b"'__AUTO__'", f"'{auto}'".encode()))
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        if u.path.startswith("/dist/"):  # 给其他学校自己搭服务器用的安装包（只有程序，没有任何数据和密码）
            name = os.path.basename(u.path)
            pyexe = os.path.join(BASE, "dist", "python", name)
            if re.fullmatch(r"python-3\.\d+\.\d+(-amd64)?\.exe", name) and os.path.isfile(pyexe):
                # 局域网版：Windows 一体机安装时从这里下载 Python，学校不通外网也能装
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(os.path.getsize(pyexe)))
                self.end_headers()
                with open(pyexe, "rb") as f:
                    return shutil.copyfileobj(f, self.wfile)
            tkdeb = os.path.join(BASE, "dist", "tk", os.path.basename(os.path.dirname(u.path)), name)
            if re.fullmatch(r"/dist/tk/(amd64|arm64)/(python3-tk|libtk8\.6|libtcl8\.6|tk8\.6-blt2\.5)\.deb", u.path) and os.path.isfile(tkdeb):
                # 局域网版：麒麟一体机缺 tkinter、又不能上网时，从这里下载（Ubuntu 20.04 的包，麒麟 V10 能用）
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(os.path.getsize(tkdeb)))
                self.end_headers()
                with open(tkdeb, "rb") as f:
                    return shutil.copyfileobj(f, self.wfile)
            if name == "myip":
                body = self.client_address[0].encode()
            elif name in ("setup.sh", "class-assistant.tar.gz") and os.path.exists(os.path.join(BASE, "dist", name)):
                with open(os.path.join(BASE, "dist", name), "rb") as f:
                    body = f.read()
            else:
                return self.send_json({"error": "not found"}, 404)
            self.send_response(200)
            self.send_header("Content-Type", "application/gzip" if name.endswith(".gz") else "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        if u.path in ("/icon-180.png", "/icon-192.png", "/icon-512.png", "/favicon.png", "/favicon.ico"):  # 存到手机桌面时的图标
            with open(os.path.join(STATIC, "favicon.png" if u.path == "/favicon.ico" else u.path[1:]), "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            return self.wfile.write(body)
        if u.path == "/manifest.json":  # 安卓"添加到桌面"读这个
            return self.send_json({"name": "班级助手", "short_name": "班级助手", "start_url": "/", "display": "standalone",
                                   "background_color": "#f3faf7", "theme_color": "#eafaf3",
                                   "icons": [{"src": f"/icon-{n}.png", "sizes": f"{n}x{n}", "type": "image/png", "purpose": "any maskable"}
                                             for n in (192, 512)]})
        if u.path in ("/setup", "/setup.html"):
            return self.send_file("setup.html")
        if u.path in ("/register", "/register.html"):
            return self.send_file("register.html")
        if u.path == "/api/register/status":
            return self.send_json({"open": reg_enabled(), "public": PUBLIC})
        if u.path == "/api/register/info":
            s = self.sess()
            if not is_owner(s):
                return self.deny("owner")
            return self.send_json({"url": f"http://{self.host()}/register", "code": settings.get("register_code", ""),
                                   "open": bool(PUBLIC or settings.get("register_open")),
                                   "paused": bool(settings.get("register_paused")), "public": PUBLIC, "active": reg_enabled(),
                                   "classes": len(load_students()), "teachers": len(users["users"])})
        if u.path == "/kylin/version":
            with open(os.path.join(BASE, "kylin", "receiver.py"), "rb") as f:
                return self.send_json({"version": hashlib.sha256(f.read()).hexdigest()[:16]})
        if u.path == "/api/poll":
            return self.api_poll(q)
        if u.path == "/api/board":
            return self.api_board(q)
        if u.path in ("/join", "/join.html", "/login"):
            return self.send_file("join.html")
        if u.path == "/app":  # 添加到手机桌面（带个人登录码）
            return self.send_file("app.html")
        if u.path == "/api/join/info":
            inv = users["invites"].get((q.get("c") or [""])[0])
            if not inv or inv["class"] not in load_students():
                return self.send_json({"error": "这个邀请码已失效，请找班主任要一个新的"}, 404)
            return self.send_json({"class": inv["class"], "role": inv["role"]})
        if u.path == "/api/qr":
            import io
            import qrcode
            text = (q.get("t") or [""])[0][:300]
            buf = io.BytesIO()
            qrcode.make(text, box_size=10, border=2).save(buf, "PNG")
            body = buf.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        if u.path == "/api/classes":
            s = self.sess()
            if not s:
                return self.send_json({"error": "请先登录"}, 401)
            students = load_students()
            mine = self.my_classes()
            return self.send_json({"classes": [{"name": c, "students": students[c]} for c in mine],
                                   "roles": {c: self.role(c) for c in mine}, "admin": is_admin(s), "owner": is_owner(s),
                                   "me": s["name"], "uid": s["uid"], "mine": None, "public": PUBLIC, "register_open": reg_enabled(),
                                   "no_schedule": [c for c in mine if c not in settings.get("class_schedule", {})] if PUBLIC else [],
                                   "places": CFG["places"], "voices": VOICES,
                                   "default_voice": default_voice(), "subjects": CFG["subjects"],
                                   "class_subjects": {c: subjects_of(c) for c in mine},
                                   "homework_limit": CFG["homework_limit_minutes"]})
        if not self.teacher():
            return self.send_json({"error": "请先登录"}, 401)
        qcls = (q.get("class") or [""])[0]
        if u.path in ("/api/homework", "/api/quiz", "/api/morning", "/api/notes", "/api/files", "/api/exams", "/api/listen") and not self.allowed(qcls, "teacher"):
            return self.deny()
        if u.path in ("/api/settings", "/api/points", "/api/attend", "/api/screen/shot", "/api/screen/shot.img") and not self.allowed(qcls, "head"):
            return self.deny("head")
        if u.path == "/api/screen/shot":  # 看大屏：截图到了没有
            sh = shots.get(qcls) or {}
            fresh = time.time() - sh.get("at", 0) < 120
            return self.send_json({"at": sh.get("at") if fresh else None, "err": sh.get("err") if fresh else None,
                                   "online": time.time() - last_seen.get(qcls, 0) < 15})
        if u.path == "/api/screen/shot.img":
            sh = shots.get(qcls) or {}
            if not sh.get("img") or time.time() - sh["at"] >= 120:
                return self.send_json({"error": "没有截图"}, 404)
            self.send_response(200)
            self.send_header("Content-Type", sh["type"])
            self.send_header("Content-Length", str(len(sh["img"])))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(sh["img"])
            return
        if u.path.startswith("/api/backup/") and not is_owner(self.sess()):
            return self.deny("owner")
        if u.path == "/api/admins":
            if not is_owner(self.sess()):
                return self.deny("owner")
            return self.send_json(self.admin_info())
        if u.path == "/api/export":
            if not self.allowed(qcls, "teacher"):
                return self.deny()
            rng = (q.get("range") or ["week"])[0]
            body = make_xlsx(export_sheets(qcls, self.allowed(qcls, "head"), rng))
            fname = quote(f"{qcls}-{export_range(rng)[2]}数据-{now_cn().strftime('%Y%m%d')}.xlsx")
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{fname}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        if u.path == "/api/schedule":
            if qcls and not self.allowed(qcls, "teacher"):
                return self.deny()
            own = settings.get("class_schedule", {}).get(qcls)
            adm = is_admin(self.sess())
            show_custom = adm and not PUBLIC
            students = load_students() if show_custom else {}
            custom_cls = [c for c in sorted(settings.get("class_schedule") or {}) if c in students] if show_custom else []
            return self.send_json({**schedule_of(qcls), "own": bool(own), "school": school_schedule(),
                                   "admin": adm, "can_class": bool(qcls) and self.allowed(qcls, "head"),
                                   "custom_classes": custom_cls})
        if u.path == "/api/me":
            s = self.sess()
            u_ = users["users"].get(s["uid"])
            proto = "https" if (self.client_address[0] in ("127.0.0.1", "::1")
                                and (self.headers.get("X-Forwarded-Proto") or "").lower() == "https") else "http"
            base_url = f"{proto}://{self.host()}/"
            link = f"{proto}://{self.host()}/login?u={s['uid']}&k={u_['secret']}" if u_ else None
            return self.send_json({"name": s["name"], "uid": s["uid"], "admin": is_admin(s), "owner": is_owner(s),
                                   "login_url": link, "base_url": base_url})
        if u.path == "/api/invites":
            if not self.allowed(qcls, "head"):
                return self.deny("head")
            host = self.host()
            codes = {inv["role"]: f"http://{host}/join?c={code}" for code, inv in users["invites"].items() if inv["class"] == qcls}
            acl = users["acl"].get(qcls, {})
            who = lambda ids: [{"uid": i, "name": users["users"].get(i, {}).get("name", "?"),  # 同名的老师靠加入日期区分
                                "since": datetime.fromtimestamp(users["users"].get(i, {}).get("created", 0), CN).strftime("%m-%d")} for i in ids]
            return self.send_json({"head": codes.get("head"), "teacher": codes.get("teacher"),
                                   "heads": who(acl.get("heads", [])), "teachers": who(acl.get("teachers", []))})
        if u.path == "/api/notes":
            s = self.sess()
            head = self.allowed(qcls, "head")
            return self.send_json({"items": [{**n, "by": note_by(n), "mine": n.get("uid") == s["uid"], "can_delete": head or n.get("uid") == s["uid"]}
                                             for n in notes_of(qcls)],
                                   "notice": settings.get("notices", {}).get(qcls, "")})
        if u.path == "/api/exams":
            now, d = time.time(), today()
            week_ago = (now_cn() - timedelta(days=7)).strftime("%Y-%m-%d")
            with lock:
                ex_snap = [{**e, "classes": list(e["classes"]), "sessions": list(e["sessions"]),
                            "ended": dict(e.get("ended") or {})} for e in exams]
                aud_snap = {aid: dict(info) for aid, info in exam_audios.items()}
                ready_snap = {aid: {c: dict(v) for c, v in m.items()} for aid, m in exam_audio_ready.items()}
                play_snap = {k: dict(v) for k, v in exam_audio_play.items()}
                cap_snap = {c: dict(v) for c, v in screen_audio_cap.items()}
                rv_snap = {c: dict(v) for c, v in screen_rv.items()}
            live_by_ex = {}
            for sx in exam_sessions(qcls):
                live_by_ex.setdefault(sx["exam_id"], {})[sx["idx"]] = sx
            items = []
            for e in ex_snap:
                if qcls not in e["classes"] or max(x["date"] for x in e["sessions"]) < week_ago:
                    continue  # 只列这个班的、还没考完的（考完一周内的也留着看）
                live = live_by_ex.get(e["id"], {})
                ctrl_cls = [c for c in e["classes"] if self.allowed(c, "teacher")]
                sess_out = []
                for i, x in enumerate(e["sessions"]):
                    st_name = ("ended" if (e.get("ended") or {}).get(f"{qcls}|{i}") else
                               "live" if i in live and live[i]["show_from"] <= now < live[i]["hide_at"] else
                               "done" if x["date"] < d or (i in live and now >= live[i]["hide_at"]) else "todo")
                    row = {**x, "idx": i, "state": st_name}
                    aid = x.get("audio_id") if not PUBLIC else None
                    a = aud_snap.get(aid) if aid else None
                    if a:
                        row["audio"] = {"id": aid, "name": a["name"], "ext": a["ext"], "size": a["size"],
                                        "duration": a["duration"], "sha256": a["sha256"][:12],
                                        "start": x.get("audio_start") or x["start"]}
                        rmap = ready_snap.get(aid) or {}
                        cls_states = []
                        ready_n = 0
                        playing_list = []
                        error_list = []
                        for c in ctrl_cls:
                            on = now - last_seen.get(c, 0) < 15
                            rs = rmap.get(c) or {}
                            if rs.get("status") == "ready":
                                cst, pct, err = "ready", 100, ""
                                ready_n += 1
                            elif rs.get("status") == "downloading" and on and now - rs.get("at", 0) < 90:
                                cst, pct, err = "downloading", int(rs.get("pct") or 0), ""
                            elif rs.get("status") == "error" and now - rs.get("at", 0) < 180:
                                cst, pct, err = "error", 0, rs.get("err") or "下载校验失败"
                            elif not on:
                                cst, pct, err = "offline", 0, ""
                            else:
                                cst, pct, err = "pending", 0, ""
                            cap = cap_snap.get(c)
                            rv_info = rv_snap.get(c)
                            cap_warn = ""
                            if on and rv_info and 0 < rv_info.get("rv", 0) < 6:
                                cap_warn = "等待大屏自动更新（暂不支持可靠控场）"
                            elif cap:
                                if not cap.get("can_wav", True) and not cap.get("can_mp3", True):
                                    cap_warn = "缺少音频播放器"
                                elif a["ext"] == "mp3" and not cap.get("can_mp3", True):
                                    cap_warn = "缺 ffplay（无法播 MP3）"
                                elif not cap.get("can_seek", True):
                                    cap_warn = "缺 ffplay（不支持暂停续播）"
                            ps = play_snap.get(f"{e['id']}|{i}|{c}")
                            if ps and now - ps.get("at", 0) < 25 and ps.get("state") in ("playing", "paused", "testing"):
                                playing_list.append({"class": c, "state": ps["state"], "pos": int(ps.get("pos") or 0),
                                                     "dur": int(ps.get("dur") or a["duration"]),
                                                     "audio_id": ps.get("audio_id") or "",
                                                     "mismatch": bool(ps.get("audio_id") and ps.get("audio_id") != aid)})
                            elif ps and now - ps.get("at", 0) < 120 and ps.get("state") == "error":
                                error_list.append({"class": c, "err": ps.get("err") or "播放失败"})
                            cls_states.append({"class": c, "online": on, "status": cst, "pct": pct, "err": err, "cap_warn": cap_warn})
                        row["audio_status"] = {"total": len(ctrl_cls), "ready": ready_n, "classes": cls_states, "playing": playing_list, "errors": error_list}
                    sess_out.append(row)
                items.append({"id": e["id"], "name": e["name"], "classes": e["classes"], "ctrl_classes": ctrl_cls,
                              "notice": e.get("notice", ""), "creator": e.get("creator", ""),
                              "can_edit": self.can_edit_exam(e), "sessions": sess_out})
            items.sort(key=lambda e: min((x["date"], x["start"]) for x in e["sessions"]))
            return self.send_json({"items": items, "default_notice": EXAM_NOTICE, "audio_enabled": not PUBLIC,
                                   "audio_max_mb": EXAM_AUDIO_MAX // 1024 // 1024, "audio_early_max": EXAM_AUDIO_EARLY_MAX})
        if u.path == "/api/listen":
            if PUBLIC:
                return self.send_json({"items": [], "audio_enabled": False, "audio_max_mb": EXAM_AUDIO_MAX // 1024 // 1024})
            now = time.time()
            s = self.sess()
            week_ago = (now_cn() - timedelta(days=EXAM_AUDIO_KEEP_DAYS)).strftime("%Y-%m-%d")
            with lock:
                lt_snap = [dict(t, classes=list(t.get("classes", []))) for t in reversed(listen_tasks)]
                aud_snap = {aid: dict(info) for aid, info in exam_audios.items()}
                ready_snap = {aid: {c: dict(v) for c, v in m.items()} for aid, m in exam_audio_ready.items()}
                play_snap = {k: dict(v) for k, v in exam_audio_play.items()}
                cap_snap = {c: dict(v) for c, v in screen_audio_cap.items()}
                rv_snap = {c: dict(v) for c, v in screen_rv.items()}
            items = []
            for t in lt_snap:
                if qcls not in t.get("classes", []) or t.get("date", "") < week_ago:
                    continue
                aid = t.get("audio_id") or ""
                a = aud_snap.get(aid)
                if not a:
                    continue
                ctrl_cls = [c for c in t["classes"] if self.allowed(c, "teacher")]
                rmap = ready_snap.get(aid) or {}
                cls_states, ready_n, playing_list, error_list = [], 0, [], []
                for c in ctrl_cls:
                    on = now - last_seen.get(c, 0) < 15
                    rs = rmap.get(c) or {}
                    if rs.get("status") == "ready":
                        cst, pct, err = "ready", 100, ""
                        ready_n += 1
                    elif rs.get("status") == "downloading" and on and now - rs.get("at", 0) < 90:
                        cst, pct, err = "downloading", int(rs.get("pct") or 0), ""
                    elif rs.get("status") == "error" and now - rs.get("at", 0) < 180:
                        cst, pct, err = "error", 0, rs.get("err") or "下载校验失败"
                    elif not on:
                        cst, pct, err = "offline", 0, ""
                    else:
                        cst, pct, err = "pending", 0, ""
                    cap = cap_snap.get(c)
                    rv_info = rv_snap.get(c)
                    cap_warn = ""
                    if on and rv_info and 0 < rv_info.get("rv", 0) < 6:
                        cap_warn = "等待大屏自动更新（暂不支持可靠控场）"
                    elif cap:
                        if not cap.get("can_wav", True) and not cap.get("can_mp3", True):
                            cap_warn = "缺少音频播放器"
                        elif a["ext"] == "mp3" and not cap.get("can_mp3", True):
                            cap_warn = "缺 ffplay（无法播 MP3）"
                        elif not cap.get("can_seek", True):
                            cap_warn = "缺 ffplay（不支持暂停续播）"
                    ps = play_snap.get(f"listen|{t['id']}|{c}")
                    if ps and now - ps.get("at", 0) < 25 and ps.get("state") in ("playing", "paused", "testing"):
                        playing_list.append({"class": c, "state": ps["state"], "pos": int(ps.get("pos") or 0),
                                             "dur": int(ps.get("dur") or a["duration"])})
                    elif ps and now - ps.get("at", 0) < 120 and ps.get("state") == "error":
                        error_list.append({"class": c, "err": ps.get("err") or "播放失败"})
                    cls_states.append({"class": c, "online": on, "status": cst, "pct": pct, "err": err, "cap_warn": cap_warn})
                can_del = all(self.allowed(c, "head") for c in t["classes"]) or bool(
                    s and t.get("uid") == s["uid"] and all(self.allowed(c, "teacher") for c in t["classes"]))
                items.append({
                    "id": t["id"], "title": t["title"], "classes": t["classes"], "ctrl_classes": ctrl_cls,
                    "date": t["date"], "play_at": t.get("play_at") or "", "creator": t.get("creator", ""),
                    "can_delete": bool(can_del),
                    "audio": {"id": aid, "name": a["name"], "ext": a["ext"], "size": a["size"],
                              "duration": a["duration"], "sha256": a["sha256"][:12]},
                    "audio_status": {"total": len(ctrl_cls), "ready": ready_n, "classes": cls_states, "playing": playing_list, "errors": error_list},
                })
            return self.send_json({"items": items, "audio_enabled": True, "audio_max_mb": EXAM_AUDIO_MAX // 1024 // 1024})
        if u.path == "/api/morning":
            d1 = today()
            d2 = next_school_day(qcls, d1)
            start, end = morning_window(qcls)
            return self.send_json({"days": [{"date": d, "items": morning_of(qcls, d)} for d in (d1, d2)],
                                   "start": start, "end": end, "custom": bool((ccfg(qcls).get("morning") or {}).get("start"))})
        if u.path == "/api/homework":
            cls = (q.get("class") or [""])[0]
            day = (q.get("date") or [None])[0] or today()
            edit = self.allowed(cls, "head") or None  # 班主任能改删全部；任课老师只能改删自己布置的
            row = lambda h: {**h, "due_date": due_date(h), "collect": collect_of(cls, h["id"]), "can_edit": edit or h.get("teacher") == self.sess()["name"]}
            items = [row(h) for h in homework_of(cls, day)]
            due = [row(h) for h in homework_due(cls, day) if h["date"] != day]
            return self.send_json({"items": items, "due": due, "today": day, "total": sum(h["minutes"] for h in items),
                                   "class_size": len(load_students().get(cls, []))})
        if u.path == "/api/reminders":
            mine = set(self.my_classes())
            return self.send_json({"items": [r for r in reminders if set(r["classes"]) & mine]})
        if u.path == "/api/phrases":
            return self.send_json({"items": self.my_phrases()})
        if u.path == "/api/settings":  # 只返回这个班的设置（形状和以前一样，前端不用大改）
            c = ccfg(qcls)
            return self.send_json({"countdowns": c["countdowns"], "standby": c["standby"], "screen_font": c["screen_font"],
                                   "board_points": c["board_points"], "eve_plan": c["eve_plan"], "tools_tab": c["tools_tab"], "desk_board": c["desk_board"],
                                   "quotes": {"enabled": c["quotes"]["enabled"], "builtin": c["quotes"]["builtin"],
                                              "custom": {qcls: c["quotes"]["custom"]}},
                                   "notices": {qcls: settings.get("notices", {}).get(qcls, "")},
                                   "duty": {qcls: settings.get("duty", {}).get(qcls, {})},
                                   "timetable": settings.get("timetable", {}).get(qcls, {}), "prep": prep_cfg(qcls),
                                   "day_periods": schedule_of(qcls)["periods"],  # 课程表的行：作息里每一节（含晚自习）
                                   "subjects": subjects_of(qcls), "default_subjects": CFG["subjects"],
                                   "builtin_quotes": len(QUOTES), "quote_now": pick_quote(qcls)})
        if u.path == "/api/backup/list":
            return self.send_json({"items": list_backups()})
        if u.path == "/api/backup/download":
            name = os.path.basename((q.get("name") or [""])[0])
            path = os.path.join(BACKUP_DIR, name)
            if not re.fullmatch(r"data-\d{8}-\d{6}\.tgz", name) or not os.path.isfile(path):
                return self.send_json({"error": "备份文件不存在"}, 404)
            with open(path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/gzip")
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        if u.path == "/api/roster":
            host = self.host()
            cmd = f"wget -qO- {host}/kylin/install.sh | bash -s {{班级}} {{密钥}}"
            win = ("powershell -nop -c \"iex ([Text.Encoding]::UTF8.GetString((New-Object Net.WebClient).DownloadData("  # 不能带 $：在 PowerShell 窗口里粘贴时外层会先把 $变量 替换掉
                   f"'http://{host}/kylin/install.ps1?c={{班级}}&k={{密钥}}')))\"")
            auto_cmd = f"wget -qO- {host}/kylin/install.sh | bash -s auto"
            auto_win = f"powershell -nop -c \"iex ([Text.Encoding]::UTF8.GetString((New-Object Net.WebClient).DownloadData('http://{host}/kylin/install.ps1?auto=1')))\""
            unwin = f"powershell -nop -c \"iex ([Text.Encoding]::UTF8.GetString((New-Object Net.WebClient).DownloadData('http://{host}/kylin/uninstall.ps1')))\""
            admin = is_admin(self.sess())
            mine = {c: v for c, v in load_students().items() if self.allowed(c, "head")}
            devs = device_map_by_class() if not PUBLIC else {}
            now_r = time.time()
            with lock:
                rv_map = {c: (screen_rv.get(c) or {}).get("rv", 0) for c in mine}
            old_screens = sorted([c for c in mine if now_r - last_seen.get(c, 0) < 15 and 0 < rv_map.get(c, 0) < 6], key=class_sort_key)
            return self.send_json({"classes": [{"name": c, "count": len(v), "names": [x["name"] for x in v], "role": self.role(c)}
                                               for c, v in mine.items()], "install": cmd, "install_win": win,
                                   "install_auto": auto_cmd if admin and not PUBLIC else "",
                                   "install_auto_win": auto_win if admin and not PUBLIC else "",
                                   "uninstall": f"wget -qO- {host}/kylin/uninstall.sh | bash", "uninstall_win": unwin,
                                   "keys": {c: class_key(c) or "" for c in mine},
                                   "devices": {c: devs[c] for c in mine if c in devs},
                                   "device_list": device_list_info() if admin and not PUBLIC else [],
                                   "device_bind_open": bool(settings.get("device_bind_open")) if admin and not PUBLIC else False,
                                   "pending_uninstall": sorted(pending_uninstalls().keys(), key=class_sort_key) if admin and not PUBLIC else [],
                                   "old_screens": old_screens if admin and not PUBLIC else [],
                                   "screen_rv": rv_map if admin and not PUBLIC else {},
                                   "admin": admin, "public": PUBLIC,
                                   "online": {c: now_r - last_seen.get(c, 0) < 15 for c in mine}})
        if u.path == "/api/files":
            uid, head = self.sess()["uid"], self.allowed(qcls, "head")
            with lock:
                purge_files()
                items = [{"id": f["id"], "name": f["name"], "size": f["size"], "by": f["by"],
                          "time": datetime.fromtimestamp(f["at"], CN).strftime("%m-%d %H:%M"),
                          "can_delete": head or f.get("uid") == uid} for f in files if f["class"] == qcls][::-1]
            return self.send_json({"items": items, "max_mb": FILE_MAX // 1024 // 1024, "keep_days": FILE_KEEP_DAYS,
                                   "exts": sorted(FILE_EXTS)})
        if u.path == "/api/quiz":
            cls = (q.get("class") or [""])[0]
            return self.send_json({"items": [x for x in quizzes if x["class"] == cls][::-1][:30],
                                   "images": [i for i in settings.get("recent_images", []) if i.get("class") == cls][:12]})
        if u.path == "/api/attend":
            cls = (q.get("class") or [""])[0]
            month = (q.get("month") or [today()[:7]])[0]
            recs = sorted((a for a in attends if a["class"] == cls and a["date"].startswith(month)),
                          key=lambda a: (a["date"], SESSIONS.index(a["session"]) if a["session"] in SESSIONS else 9))
            return self.send_json({"records": recs, "today": today(), "sessions": SESSIONS, "types": ATTEND_TYPES,
                                   "students": [s["name"] for s in load_students().get(cls, [])]})
        if u.path == "/api/points":
            cls = (q.get("class") or [""])[0]
            month = now_cn().replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
            events = [p for p in points if p["class"] == cls][-60:][::-1]
            return self.send_json({"all": point_totals(cls), "week": point_totals(cls, week_start_ts()),
                                   "month": point_totals(cls, month), "events": events})
        if u.path == "/api/recent":
            if not self.teacher():
                return self.send_json({"error": "请先登录"}, 401)
            return self.api_recent(q)
        self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/api/files/upload":  # 请求体是文件本身，不是 JSON
            return self.api_file_upload(q)
        if u.path == "/api/exams/audio/upload":  # 考试听力原始音频文件流（MP3 / WAV，上限 80MB）
            return self.api_exam_audio_upload(q)
        if u.path == "/api/screen/shot/upload":  # 一体机传上来的屏幕截图（请求体是图片本身）
            return self.api_shot_upload(q)
        body = self.read_body()
        if users["aliases"] and u.path != "/api/roster/save":  # 改过名的班：旧名字换成新名字（roster/save 的 class 是新名字，不能换）
            if q.get("class"):
                q["class"] = [resolve_class(q["class"][0])]
            if isinstance(body, dict) and isinstance(body.get("class"), str):
                body["class"] = resolve_class(body["class"])
            if isinstance(body, dict) and isinstance(body.get("classes"), list):
                body["classes"] = [resolve_class(c) if isinstance(c, str) else c for c in body["classes"]]
        if u.path == "/api/login":
            return self.api_login(body)
        if u.path == "/api/join":
            return self.api_join(body)
        if u.path == "/api/login/link":
            return self.api_login_link(body)
        if u.path == "/api/ack":  # 一体机只传消息 id：按这条消息所在的班校验密钥
            mid = body.get("id")
            with lock:
                m = next((x for x in messages if type(mid) is int and x["id"] == mid), None)
                mcls = m["class"] if m else ""
            if not m or not self.classroom_ok(q, mcls):
                return self.send_json({"error": "密钥错误"}, 403)
            return self.api_ack(body)
        if u.path == "/api/screen/tool":  # 一体机右边的「工具」：老师在大屏上直接点计时、抽号
            cls = str(body.get("class") or "")
            if not self.classroom_ok(q, cls):
                return self.send_json({"error": "密钥错误"}, 403)
            if not ccfg(cls)["tools_tab"]:
                return self.send_json({"error": "班主任已关闭大屏工具"}, 403)
            if body.get("tool") == "timer":
                return self.api_timer({"class": cls, "seconds": body.get("seconds")}, "大屏")
            return self.api_pick({"class": cls, "voice": True}, "大屏")
        if u.path == "/api/collect/save":
            if not self.classroom_ok(q, body.get("class")):
                return self.send_json({"error": "密钥错误"}, 403)
            return self.api_collect_save(body)
        if u.path == "/api/attend/save":
            if not self.classroom_ok(q, body.get("class")):
                return self.send_json({"error": "密钥错误"}, 403)
            return self.api_attend_save(body)
        if u.path == "/api/exams/audio/status":  # 一体机上报听力缓存就绪进度与当前播控状态
            if PUBLIC:
                return self.deny("public_forbid")
            cls = str(body.get("class") or "")
            if not cls or cls not in load_students() or not self.classroom_ok(q, cls):
                return self.send_json({"error": "密钥错误"}, 403)
            return self.api_exam_audio_status(body)
        if u.path == "/api/device/check":
            return self.api_device_check(body)
        if u.path == "/api/device/bind":
            return self.api_device_bind(body)
        if u.path == "/api/register":
            return self.api_register(body)
        name = self.teacher()
        if not name:
            return self.send_json({"error": "请先登录"}, 401)
        level = self.post_check(u.path, body)
        if level:
            return self.deny(level)
        uid = self.sess()["uid"]
        n = self.screen_count(u.path, body) if PUBLIC and not is_admin(self.sess()) else 0
        if n:  # 公共模式：每位老师每小时最多往大屏发 60 条
            now = time.time()
            with lock:
                log = send_log[uid] = [t for t in send_log.get(uid, []) if now - t < 3600]
                if len(log) + n > SEND_LIMIT:
                    return self.send_json({"error": "发送太频繁，请稍后再试"}, 429)
                log.extend([now] * n)
        if u.path == "/api/send":
            return self.api_send(body, name)
        if u.path == "/api/recall":
            return self.api_recall(body)
        if u.path == "/api/preview":
            audio = make_speech(str(body.get("text") or "请高二3班张伟同学到物理办公室")[:200], body.get("voice_name"), 1)
            if not audio:
                return self.send_json({"error": "自然人声暂不可用（腾讯云语音没配好，或没装 edge-tts）" if not TC else "腾讯云语音合成失败，请看服务器日志"}, 503)
            return self.send_json({"audio": "/audio/" + audio})
        handler = {
            "/api/me/name": self.api_me_name, "/api/screen/shot": self.api_shot_request,
            "/api/homework/add": self.api_homework_add, "/api/homework/edit": self.api_homework_edit, "/api/homework/delete": self.api_homework_delete,
            "/api/homework/push": self.api_homework_push, "/api/reminders/save": self.api_reminder_save,
            "/api/reminders/delete": self.api_reminder_delete, "/api/phrases/add": self.api_phrase_add,
            "/api/phrases/delete": self.api_phrase_delete, "/api/tools/timer": self.api_timer,
            "/api/tools/pick": self.api_pick, "/api/settings": self.api_settings,
            "/api/points/add": self.api_points_add, "/api/points/delete": self.api_points_delete,
            "/api/duty/push": self.api_duty_push, "/api/collect/start": self.api_collect_start,
            "/api/collect/remind": self.api_collect_remind, "/api/attend/start": self.api_attend_start,
            "/api/attend/delete": self.api_attend_delete, "/api/upload": self.api_upload,
            "/api/image/push": self.api_image_push, "/api/quiz/save": self.api_quiz_save,
            "/api/quiz/delete": self.api_quiz_delete, "/api/quiz/import": self.api_quiz_import,
            "/api/roster/save": self.api_roster_save, "/api/roster/delete": self.api_roster_delete,
            "/api/roster/parse": self.api_roster_parse, "/api/invites/new": self.api_invite_new,
            "/api/members/remove": self.api_member_remove, "/api/members/role": self.api_member_role, "/api/schedule": self.api_schedule,
            "/api/board/show": self.api_board_show, "/api/register/code": self.api_register_code,
            "/api/classroom/uninstall": self.api_classroom_uninstall,
            "/api/classroom/uninstall/cancel": self.api_classroom_uninstall_cancel,
            "/api/morning/add": self.api_morning_add, "/api/morning/delete": self.api_morning_delete,
            "/api/exams/save": self.api_exam_save, "/api/exams/delete": self.api_exam_delete, "/api/exams/end": self.api_exam_end,
            "/api/exams/audio/cmd": self.api_exam_audio_cmd,
            "/api/listen/save": self.api_listen_save, "/api/listen/delete": self.api_listen_delete, "/api/listen/cmd": self.api_listen_cmd,
            "/api/notes/add": self.api_note_add, "/api/notes/edit": self.api_note_edit, "/api/notes/delete": self.api_note_delete,
            "/api/files/prepare": self.api_file_prepare, "/api/files/delete": self.api_file_delete,
            "/api/admin/password": self.api_admin_password, "/api/admins/set": self.api_admins_set,
            "/api/device/toggle": self.api_device_toggle, "/api/device/unbind": self.api_device_unbind,
            "/api/backup/now": lambda body, sender: self.send_json({"ok": True, "name": make_backup(), "items": list_backups()}),
        }.get(u.path)
        if handler:
            return handler(body, name)
        if u.path == "/api/logout":
            token = (self.headers.get("Authorization") or "").replace("Bearer ", "")
            with lock:
                sessions.pop(token, None)
                write_json(SESSION_PATH, sessions)
            return self.send_json({"ok": True})
        self.send_json({"error": "not found"}, 404)

    # ---------- 接口实现 ----------
    def api_login(self, body):
        ip = self.client_ip()
        fails = login_fails.get(ip)
        if fails and fails[0] >= 5 and time.time() - fails[1] < 600:
            return self.send_json({"error": "密码错误次数过多，请 10 分钟后再试"}, 429)
        name = str(body.get("name") or "").strip()[:20]
        if not check_password(str(body.get("password") or "")):
            if not fails or time.time() - fails[1] >= 600:
                login_fails[ip] = [1, time.time()]
            else:
                fails[0] += 1
            return self.send_json({"error": "密码错误"}, 403)
        if not name:
            return self.send_json({"error": "请填写您的称呼"}, 400)
        login_fails.pop(ip, None)
        token = secrets.token_urlsafe(24)
        with lock:  # 用管理员密码登录的就是管理员
            sessions[token] = {"name": name, "uid": "admin", "exp": time.time() + 30 * 86400}
            write_json(SESSION_PATH, sessions)
        self.send_json({"token": token, "name": name})

    def new_session(self, uid, name):
        token = secrets.token_urlsafe(24)
        sessions[token] = {"name": name, "uid": uid, "exp": time.time() + 365 * 86400}
        write_json(SESSION_PATH, sessions)
        return token

    def api_join(self, body):
        """扫班主任/管理员发的二维码加入班级"""
        inv = users["invites"].get(str(body.get("code") or ""))
        if not inv or inv["class"] not in load_students():
            return self.send_json({"error": "这个邀请码已失效，请找班主任要一个新的"}, 404)
        cls, role = inv["class"], inv["role"]
        with lock:
            s = self.sess()
            if s and s["uid"] == "admin":  # 管理员本来就能看到所有班
                return self.send_json({"ok": True, "token": None, "name": s["name"], "class": cls})
            if s:  # 这台设备已经登录过：给同一个老师加一个班
                uid, name = s["uid"], s["name"]
                token = (self.headers.get("Authorization") or "").replace("Bearer ", "")
            else:
                name = re.sub(r"\s+", "", str(body.get("name") or ""))[:20]
                if not name:
                    return self.send_json({"error": "请填写您的称呼"}, 400)
                uid = secrets.token_hex(6)
                users["users"][uid] = {"name": name, "secret": secrets.token_urlsafe(12), "created": time.time()}
                token = self.new_session(uid, name)
            acl = users["acl"].setdefault(cls, {"heads": [], "teachers": []})
            if role == "head":
                acl["teachers"] = [x for x in acl.get("teachers", []) if x != uid]
                if uid not in acl["heads"]:
                    acl["heads"].append(uid)
            elif uid not in acl.get("heads", []) and uid not in acl.setdefault("teachers", []):
                acl["teachers"].append(uid)
            save_users()
        self.send_json({"ok": True, "token": token, "name": name, "class": cls, "role": role})

    def api_register(self, body):
        """老师用注册码自己建班，注册的人就是这个班的班主任（公共模式与自建服务器开关模式共用）"""
        code = str(body.get("code") or "").strip()
        cls = re.sub(r"\s+", "", str(body.get("class") or ""))
        want = settings.get("register_code") or ""
        if not reg_enabled():
            return self.send_json({"error": "暂未开放注册"}, 403)
        ip, d = self.client_ip(), today()
        with lock:
            fails = reg_fails.get(ip)
            if fails and fails[0] >= 10 and time.time() - fails[1] < 600:
                return self.send_json({"error": "注册码错误次数过多，请 10 分钟后再试"}, 429)
            if not hmac.compare_digest(code.encode(), want.encode()):
                if not fails or time.time() - fails[1] >= 600:
                    reg_fails[ip] = [1, time.time()]
                else:
                    fails[0] += 1
                msg = "注册码不对，请向发给你链接的老师确认" if PUBLIC else "注册码不对，请向学校管理员确认"
                return self.send_json({"error": msg}, 403)
            reg_fails.pop(ip, None)
        if not cls:
            return self.send_json({"error": "请填写班级名称"}, 400)
        if len(cls) > 16:
            return self.send_json({"error": "班级名称最长 16 个字"}, 400)
        if cls.startswith("_") or not re.fullmatch(r"[\w·（）\-]+", cls):
            return self.send_json({"error": "班级名称只能用汉字、字母、数字"}, 400)
        s = self.sess()
        if is_admin(s):
            return self.send_json({"error": "管理员请在工作台「班级管理」里建班"}, 400)
        cap = 5 if PUBLIC else 100  # 本校全校共用校园网出口 IP 时避免误伤
        with lock:
            rc = reg_counts.get(ip)
            if rc and rc[0] == d and rc[1] >= cap:
                return self.send_json({"error": "今天注册次数太多了，请明天再试"}, 429)
            if cls in load_students():
                msg = "这个班级名已被使用，请在前面加上学校名" if PUBLIC else "这个班级名已被使用，请换一个名称"
                return self.send_json({"error": msg}, 400)
            if s:  # 已登录的老师：新班加到自己名下
                uid, name = s["uid"], s["name"]
                token = (self.headers.get("Authorization") or "").replace("Bearer ", "")
                if sum(uid in a.get("heads", []) for a in users["acl"].values()) >= HEAD_LIMIT:
                    return self.send_json({"error": f"每位老师最多建 {HEAD_LIMIT} 个班"}, 400)
            else:
                name = re.sub(r"\s+", "", str(body.get("name") or ""))[:20]
                if not name:
                    return self.send_json({"error": "请填写你的称呼"}, 400)
                uid = secrets.token_hex(6)
                users["users"][uid] = {"name": name, "secret": secrets.token_urlsafe(12), "created": time.time()}
                token = self.new_session(uid, name)
            roster = read_json(ROSTER_PATH, {})
            roster[cls] = []
            roster["_deleted"] = [c for c in roster.get("_deleted", []) if c != cls]
            write_json(ROSTER_PATH, roster)
            users["acl"][cls] = {"heads": [uid], "teachers": []}
            users["class_keys"][cls] = secrets.token_urlsafe(12)
            save_users()
            reg_counts[ip] = [d, (rc[1] if rc and rc[0] == d else 0) + 1]
        self.send_json({"ok": True, "token": token, "name": name, "class": cls})

    def api_register_code(self, body, sender):
        if "open" in body:  # 自建服务器：管理员在工作台一键开启/关闭老师自助注册（不改已有教室密钥）
            on = bool(body["open"])
            with lock:
                settings["register_open"] = on
                if on:
                    settings["register_paused"] = False
                    if not settings.get("register_code"):
                        alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
                        settings["register_code"] = "".join(secrets.choice(alphabet) for _ in range(6))
                write_json(SETTINGS_PATH, settings)
            return self.send_json({"ok": True, "open": bool(PUBLIC or settings.get("register_open")),
                                   "code": settings.get("register_code", ""), "active": reg_enabled()})
        if "paused" in body:  # 公共模式：管理员暂停/恢复注册，注册码不变
            with lock:
                settings["register_paused"] = bool(body["paused"])
                write_json(SETTINGS_PATH, settings)
            return self.send_json({"ok": True, "paused": settings["register_paused"], "active": reg_enabled()})
        code = str(body.get("code") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9]{4,20}", code):
            return self.send_json({"error": "注册码要 4–20 位字母或数字"}, 400)
        with lock:
            settings["register_code"] = code
            write_json(SETTINGS_PATH, settings)
        self.send_json({"ok": True, "code": code, "active": reg_enabled()})

    def api_login_link(self, body):
        """用"我的登录码"在另一台设备上登录"""
        u_ = users["users"].get(str(body.get("u") or ""))
        if not u_ or not hmac.compare_digest(str(body.get("k") or ""), u_["secret"]):
            return self.send_json({"error": "登录码无效"}, 403)
        with lock:
            token = self.new_session(str(body["u"]), u_["name"])
        self.send_json({"ok": True, "token": token, "name": u_["name"]})

    def api_invite_new(self, body, sender):
        cls, role = str(body.get("class") or ""), "head" if body.get("role") == "head" else "teacher"
        if cls not in load_students():
            return self.send_json({"error": "没有这个班级"}, 400)
        with lock:
            for code in [k for k, v in users["invites"].items() if v["class"] == cls and v["role"] == role]:
                users["invites"].pop(code)  # 重新生成后，旧二维码作废
            code = secrets.token_urlsafe(8)
            users["invites"][code] = {"class": cls, "role": role, "by": sender, "created": time.time()}
            save_users()
        self.send_json({"ok": True, "url": f"http://{self.host()}/join?c={code}"})

    def api_schedule(self, body, sender):
        cls = str(body.get("class") or "")
        raw_scope = body.get("scope")
        if raw_scope is None:
            scope = "school"
        elif raw_scope in ("class", "school"):
            scope = raw_scope
        else:
            return self.send_json({"error": "scope 参数不对"}, 400)
        if PUBLIC and scope != "class":
            return self.deny("public_forbid")
        apply_all_raw = body.get("apply_all", False)
        if type(apply_all_raw) is not bool:
            return self.send_json({"error": "apply_all 参数必须是布尔值"}, 400)
        apply_all = apply_all_raw
        if PUBLIC and apply_all:
            return self.deny("public_forbid")
        if scope == "class" and apply_all:
            return self.send_json({"error": "本班作息不支持覆盖全校"}, 400)
        if scope == "class" and (not cls or cls not in load_students()):
            return self.send_json({"error": "请选择有效班级"}, 400)
        show_custom = is_admin(self.sess()) and not PUBLIC
        if scope == "class" and body.get("reset"):  # 恢复使用全校作息
            with lock:
                settings.setdefault("class_schedule", {}).pop(cls, None)
                write_json(SETTINGS_PATH, settings)
                students = load_students() if show_custom else {}
                custom_cls = [c for c in sorted(settings.get("class_schedule") or {}) if c in students] if show_custom else []
            return self.send_json({"ok": True, **schedule_of(cls), "own": False, "custom_classes": custom_cls})
        raw_periods = body.get("periods")
        if not isinstance(raw_periods, list) or not (1 <= len(raw_periods) <= 30):
            return self.send_json({"error": "请填写 1 至 30 节课的作息时间"}, 400)
        periods = []
        for idx_, p_ in enumerate(raw_periods, 1):
            if not isinstance(p_, (list, tuple)) or len(p_) != 2 or not all(isinstance(x, str) for x in p_):
                return self.send_json({"error": f"第 {idx_} 节时间格式不对"}, 400)
            s1, s2 = p_[0].strip(), p_[1].strip()
            if not (re.fullmatch(r"\d{1,2}:\d{2}", s1) and re.fullmatch(r"\d{1,2}:\d{2}", s2)):
                return self.send_json({"error": f"第 {idx_} 节时间格式不对：{s1}–{s2}"}, 400)
            try:
                a_ = datetime.strptime(s1, "%H:%M").strftime("%H:%M")
                b_ = datetime.strptime(s2, "%H:%M").strftime("%H:%M")
            except ValueError:
                return self.send_json({"error": f"第 {idx_} 节时间无效：{s1}–{s2}"}, 400)
            if a_ >= b_:
                return self.send_json({"error": f"第 {idx_} 节结束时间要晚于开始时间：{a_}–{b_}"}, 400)
            periods.append([a_, b_])
        raw_days = body.get("weekdays")
        if not isinstance(raw_days, list) or not raw_days:
            return self.send_json({"error": "请至少选择一个上课日"}, 400)
        days_set = set()
        for d in raw_days:
            if type(d) is not int or not (1 <= d <= 7):
                return self.send_json({"error": "上课日参数不对"}, 400)
            days_set.add(d)
        days = sorted(days_set)
        sch = {"periods": sorted(periods), "weekdays": days}
        cleared = 0
        with lock:
            if scope == "class":
                settings.setdefault("class_schedule", {})[cls] = sch
            else:
                settings["schedule"] = sch
                if apply_all:
                    old_cs = settings.get("class_schedule") or {}
                    students_all = load_students()
                    cleared = sum(1 for c in old_cs if c in students_all)
                    settings["class_schedule"] = {}
            write_json(SETTINGS_PATH, settings)
            students = load_students() if show_custom else {}
            custom_cls = [c for c in sorted(settings.get("class_schedule") or {}) if c in students] if show_custom else []
        self.send_json({"ok": True, **sch, "own": scope == "class",
                        "cleared": cleared, "custom_classes": custom_cls})

    def api_board_show(self, body, sender):
        """让大屏马上显示看板（不管现在是不是晚自习），碰一下屏幕就收起"""
        cls = str(body.get("class") or "")
        self.create_msg([cls], "显示班级看板", sender, kind="standby", urgent=True, expire_at=time.time() + 120)
        self.send_json({"ok": True})

    def api_morning_add(self, body, sender):
        cls = str(body.get("class") or "")
        subject = str(body.get("subject") or "").strip()[:10]
        content = str(body.get("content") or "").strip()[:200]
        date = str(body.get("date") or "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) or date < today():
            return self.send_json({"error": "日期不对"}, 400)
        if not subject or not content:
            return self.send_json({"error": "请选科目、填写早读内容"}, 400)
        with lock:
            item = {"id": next_id(mornings), "class": cls, "date": date, "subject": subject, "content": content,
                    "teacher": sender, "created": time.time()}
            mornings.append(item)
            cutoff = (now_cn() - timedelta(days=60)).strftime("%Y-%m-%d")
            mornings[:] = [m for m in mornings if m["date"] >= cutoff]
            write_json(MORNING_PATH, mornings)
        self.send_json({"ok": True, "item": item})

    def api_note_add(self, body, sender):
        raw_cs = body.get("classes") if isinstance(body.get("classes"), list) else [body.get("class")]
        students = load_students()
        classes = list(dict.fromkeys(str(c).strip() for c in raw_cs if str(c or "").strip() in students))
        text = re.sub(r"\s+", " ", str(body.get("text") or "")).strip()[:120]
        subject = str(body.get("subject") or "").strip()[:6]
        if not classes:
            return self.send_json({"error": "请选择班级"}, 400)
        err = self.multi_class_denied(classes)
        if err:
            return self.send_json({"error": err}, 403)
        if not text:
            return self.send_json({"error": "请填写留言内容"}, 400)
        d = now_cn().date()
        until = {"today": d, "week": d + timedelta(days=6 - d.weekday())}.get(body.get("keep"))  # 今天 / 到本周日 / 一直显示
        until_s = until.strftime("%Y-%m-%d") if until else ""
        now, uid = time.time(), self.sess()["uid"]
        added, skipped = [], []
        with lock:
            if len(classes) == 1 and len(notes_of(classes[0])) >= 8:
                return self.send_json({"error": "这个班的留言已经有 8 条了，先删掉几条旧的"}, 400)
            nid = next_id(notes)
            for cls in classes:
                if len(notes_of(cls)) >= 8:
                    skipped.append(cls)
                    continue
                item = {"id": nid, "class": cls, "text": text, "subject": subject, "teacher": sender,
                        "uid": uid, "created": now, "until": until_s}
                notes.append(item)
                added.append(item)
                nid += 1
            if not added:
                return self.send_json({"error": "所选班级的留言都已经有 8 条了，请先删掉一些旧的"}, 400)
            cutoff = (now_cn() - timedelta(days=30)).strftime("%Y-%m-%d")
            notes[:] = [n for n in notes if not n.get("until") or n["until"] >= cutoff]
            write_json(NOTES_PATH, notes)
        self.send_json({"ok": True, "item": added[0], "items": added, "count": len(added), "skipped": skipped})

    def api_note_edit(self, body, sender):
        text = re.sub(r"\s+", " ", str(body.get("text") or "")).strip()[:120]
        if not text:
            return self.send_json({"error": "请填写留言内容"}, 400)
        d = now_cn().date()
        until = {"today": d, "week": d + timedelta(days=6 - d.weekday())}.get(body.get("keep"))
        with lock:
            n = next((x for x in notes if x["id"] == body.get("id")), None)
            if not n:
                return self.send_json({"error": "这条留言已经不在了"}, 404)
            n.update({"text": text, "subject": str(body.get("subject") or "").strip()[:6],
                      "until": until.strftime("%Y-%m-%d") if until else ""})
            write_json(NOTES_PATH, notes)
        self.send_json({"ok": True, "item": n})

    def api_note_delete(self, body, sender):
        with lock:
            notes[:] = [n for n in notes if n["id"] != body.get("id")]
            write_json(NOTES_PATH, notes)
        self.send_json({"ok": True})

    def api_morning_delete(self, body, sender):
        with lock:
            mornings[:] = [m for m in mornings if m["id"] != body.get("id")]
            write_json(MORNING_PATH, mornings)
        self.send_json({"ok": True})

    def api_classroom_uninstall(self, body, sender):
        """班主任单班或管理员批量远程卸载一体机接收端：一体机收到后自己删掉开机自启、桌面图标和程序"""
        students = load_students()
        bulk = bool(body.get("all")) or isinstance(body.get("classes"), list)
        if bulk:
            if PUBLIC:
                return self.deny("public_forbid")
            raw = list(students.keys()) if body.get("all") else (body.get("classes") or [])
            targets = list(dict.fromkeys(str(c).strip() for c in raw if str(c or "").strip() in students))
            if not targets:
                return self.send_json({"error": "请选择至少一个有效班级"}, 400)
            now = time.time()
            # 避免重复堆叠 pending 卸载消息：先把这些班旧的未执行卸载指令标记撤回
            # 注意：若勾选 unbind_mac，不可在此立刻删 MAC 绑定（否则桌面云大屏 /api/poll 会被 rebind 拦截而收不到卸载指令）；
            # 改为把 unbind_mac=True 写入卸载指令 data，等大屏在 api_poll 实际领取卸载指令时同步解绑，且中途撤销卸载不会误丢绑定
            unbind_mac = bool(body.get("unbind_mac"))
            s_cur, cur_sid = self.sess(), self.sid()
            suid = s_cur["uid"] if s_cur else ""
            ids = []
            with lock:
                for m in messages:
                    if m["class"] in targets and is_active_uninstall(m, now):
                        m["recalled"] = True
                nid = next_id(messages)
                for c in targets:
                    messages.append({
                        "id": nid, "class": c, "text": "远程卸载一体机", "sender": sender,
                        "sender_uid": suid, "sender_sid": cur_sid, "kind": "uninstall",
                        "data": {"unbind_mac": True} if unbind_mac else None, "voice": False, "urgent": True,
                        "audio": None, "created": now, "deliver_at": now, "expire_at": now + 7 * 86400,
                        "delivered_at": None, "acked_at": None, "who": None, "loud": False, "send_at": None, "speak": None,
                    })
                    ids.append(nid)
                    nid += 1
                save_messages()
            on_cnt = sum(1 for c in targets if now - last_seen.get(c, 0) < 15)
            with lock:
                old_screens = sorted([c for c in targets if now - last_seen.get(c, 0) < 15 and 0 < (screen_rv.get(c) or {}).get("rv", 0) < 6], key=class_sort_key)
            return self.send_json({
                "ok": True, "count": len(targets), "online_count": on_cnt, "offline_count": len(targets) - on_cnt,
                "online": on_cnt > 0, "classes": targets, "ids": ids,
                "old_screens": old_screens,
                "pending_uninstall": sorted(pending_uninstalls().keys(), key=class_sort_key),
            })
        cls = str(body.get("class") or "")
        if cls not in students:
            return self.send_json({"error": "没有这个班"}, 404)
        now = time.time()
        s_cur, cur_sid = self.sess(), self.sid()
        suid = s_cur["uid"] if s_cur else ""
        with lock:
            has_unbind = any(m["class"] == cls and is_active_uninstall(m, now) and (m.get("data") or {}).get("unbind_mac") for m in messages)
            if not has_unbind:
                for m in messages:
                    if m["class"] == cls and is_active_uninstall(m, now):
                        m["recalled"] = True
                nid = next_id(messages)
                messages.append({
                    "id": nid, "class": cls, "text": "远程卸载一体机", "sender": sender,
                    "sender_uid": suid, "sender_sid": cur_sid, "kind": "uninstall",
                    "data": None, "voice": False, "urgent": True,
                    "audio": None, "created": now, "deliver_at": now, "expire_at": now + 7 * 86400,
                    "delivered_at": None, "acked_at": None, "who": None, "loud": False, "send_at": None, "speak": None,
                })
                save_messages()
        self.send_json({"ok": True, "online": now - last_seen.get(cls, 0) < 15})

    def api_classroom_uninstall_cancel(self, body, sender):
        """管理员撤销尚未可靠确认（含离线待发与已发未回执）的远程卸载指令"""
        if PUBLIC:
            return self.deny("public_forbid")
        target_set = set(str(c).strip() for c in body.get("classes") or [] if str(c or "").strip()) if isinstance(body.get("classes"), list) else None
        now = time.time()
        cancelled = 0
        with lock:
            for m in messages:
                if is_active_uninstall(m, now):
                    if target_set is None or m["class"] in target_set:
                        m["recalled"] = True
                        cancelled += 1
            if cancelled:
                save_messages()
        self.send_json({"ok": True, "cancelled": cancelled,
                        "pending_uninstall": sorted(pending_uninstalls().keys(), key=class_sort_key)})

    def admin_info(self):
        """主管理员看的：其他管理员、可以设为管理员的老师、用管理员密码登录着的设备"""
        name_of = lambda uid: users["users"].get(uid, {}).get("name", "（已删除）")
        cls_of = lambda uid: [c for c, a in users["acl"].items() if uid in a.get("heads", []) + a.get("teachers", [])]
        teachers = [{"uid": uid, "name": u["name"], "classes": cls_of(uid), "admin": uid in users["admins"],
                     "created": datetime.fromtimestamp(u.get("created", 0), CN).strftime("%m-%d")}
                    for uid, u in users["users"].items() if cls_of(uid) or uid in users["admins"]]
        me = (self.headers.get("Authorization") or "").replace("Bearer ", "")
        now = time.time()
        devices = sorted(({"name": v["name"], "me": k == me,
                           "time": datetime.fromtimestamp(v["exp"] - 30 * 86400, CN).strftime("%m-%d %H:%M")}
                          for k, v in sessions.items() if v.get("uid") == "admin" and v["exp"] > now), key=lambda d: d["time"])
        return {"teachers": sorted(teachers, key=lambda t: (not t["admin"], t["name"])), "devices": devices,
                "changed": bool(settings.get("admin_pw"))}

    def api_admin_password(self, body, sender):
        """主管理员改管理员密码：改完其他用密码登录的设备全部下线（自己这台不受影响）"""
        old, new = str(body.get("old") or ""), str(body.get("new") or "")
        if not check_password(old):
            return self.send_json({"error": "原密码不对"}, 403)
        if len(new) < 8 or new.isdigit() or new.isalpha():
            return self.send_json({"error": "新密码至少 8 位，要同时有字母和数字"}, 400)
        if new == old:
            return self.send_json({"error": "新密码和原密码一样"}, 400)
        me = (self.headers.get("Authorization") or "").replace("Bearer ", "")
        with lock:
            salt = secrets.token_hex(16)
            settings["admin_pw"] = {"salt": salt, "hash": hash_pw(new, salt), "at": time.time()}
            write_json(SETTINGS_PATH, settings)
            kicked = [k for k, v in sessions.items() if v.get("uid") == "admin" and k != me]
            for k in kicked:
                sessions.pop(k)
            write_json(SESSION_PATH, sessions)
        self.send_json({"ok": True, "kicked": len(kicked), **self.admin_info()})

    def api_admins_set(self, body, sender):
        uid, on = str(body.get("uid") or ""), bool(body.get("on"))
        if uid not in users["users"]:
            return self.send_json({"error": "没有这位老师"}, 404)
        with lock:
            users["admins"] = [x for x in users["admins"] if x != uid] + ([uid] if on else [])
            save_users()
        self.send_json({"ok": True, **self.admin_info()})

    def api_member_role(self, body, sender):
        """把本班的一位老师改成班主任（head）或任课老师（teacher）；账号和登录码不变，对方刷新工作台就生效"""
        cls, uid, role = str(body.get("class") or ""), str(body.get("uid") or ""), body.get("role")
        if role not in ("head", "teacher"):
            return self.send_json({"error": "参数不对"}, 400)
        with lock:
            acl = users["acl"].get(cls)
            if not acl or uid not in acl.get("heads", []) + acl.get("teachers", []):
                return self.send_json({"error": "这位老师不在这个班"}, 404)
            for k in ("heads", "teachers"):
                acl[k] = [x for x in acl.get(k, []) if x != uid]
            acl.setdefault(role + "s", []).append(uid)
            save_users()
        self.send_json({"ok": True})

    def api_member_remove(self, body, sender):
        cls, uid = str(body.get("class") or ""), str(body.get("uid") or "")
        with lock:
            acl = users["acl"].get(cls, {})
            if uid in acl.get("heads", []) and self.role(cls) != "admin":
                return self.deny("admin")
            for k in ("heads", "teachers"):
                acl[k] = [x for x in acl.get(k, []) if x != uid]
            save_users()
        self.send_json({"ok": True})

    def api_send(self, body, sender):
        students = load_students()
        classes = list(dict.fromkeys(str(c).strip() for c in body.get("classes") or [] if str(c or "").strip() in students))
        text = str(body.get("text") or "").strip()
        if not classes or not text:
            return self.send_json({"error": "请选择班级并填写内容"}, 400)
        err = self.multi_class_denied(classes)
        if err:
            return self.send_json({"error": err}, 403)
        if PUBLIC and len(text) > TEXT_LIMIT:
            return self.send_json({"error": f"消息最长 {TEXT_LIMIT} 字"}, 400)
        text = text[:300]
        send_at = None
        if body.get("at"):  # 定时发送：只能定今天，"HH:MM"
            try:
                send_at = datetime.combine(now_cn().date(), datetime.strptime(str(body["at"]), "%H:%M").time(), CN).timestamp()
            except ValueError:
                return self.send_json({"error": "定时的时间不对"}, 400)
            if send_at <= time.time() + 30:
                return self.send_json({"error": "这个时间已经过了，请选今天稍后的时间"}, 400)
        ids, scheduled = self.create_msg(classes, text, sender, voice=body.get("voice"), urgent=body.get("urgent"),
                                         defer=body.get("defer"), voice_name=body.get("voice_name"), who=str(body.get("who") or "")[:40] or None,
                                         loud=body.get("loud"), send_at=send_at)
        self.send_json({"ok": True, "ids": ids, "scheduled": scheduled,
                        "exam": not body.get("urgent") and any(exam_now(c, send_at) for c in classes)})

    # ---------- 考试 ----------
    def api_exam_audio_upload(self, q):
        """上传考试听力音频（仅非公共模式）：校验 MP3 / WAV、上限 80MB、总容量与孤儿上限、二进制头合法与时长，多班共享只存一份"""
        def fail(msg, code=400):
            self.close_connection = True
            return self.send_json({"error": msg}, code)
        s = self.sess()
        if not s:
            return fail("请先登录", 401)
        if PUBLIC:
            return fail("公共服务器不支持上传考试听力", 403)
        if not self.my_classes() and not is_admin(s):
            return fail("你还没有加入任何班级", 403)
        raw_name = os.path.basename((q.get("name") or [""])[0].replace("\\", "/")).strip()
        raw_name = re.sub(r'[\x00-\x1f<>:"|?*]', "_", raw_name)
        stem, dot, ext = raw_name.rpartition(".")
        ext = ext.lower() if dot else ""
        if ext not in EXAM_AUDIO_EXTS:
            return fail("考试听力只支持 MP3 或 WAV 格式")
        name = f"{(stem or '听力音频')[:75]}.{ext}"
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return fail("音频文件是空的")
        if length > EXAM_AUDIO_MAX:
            return fail(f"音频文件太大了，上限 {EXAM_AUDIO_MAX // 1024 // 1024} MB", 413)
        rep_aid = str((q.get("replace") or [""])[0] or "").strip()
        err_msg, err_code = None, 400
        with lock:
            purge_exam_audio()
            used_ids = ({x.get("audio_id") for e in exams for x in (e.get("sessions") or []) if x.get("audio_id")}
                        | {t.get("audio_id") for t in listen_tasks if t.get("audio_id")})
            # 替换旧临时音频时先不删旧文件（等新文件上传、校验、登记全成功后再删，避免坏文件把旧音频也弄丢）
            can_rep = bool(rep_aid and rep_aid in exam_audios and rep_aid not in used_ids and exam_audios[rep_aid].get("uid") == s["uid"])
            rep_sz = int(exam_audios[rep_aid].get("size") or 0) if can_rep else 0
            total_sz = sum(int(x.get("size") or 0) for x in exam_audios.values()) - rep_sz
            my_orphans = sum(1 for aid_, info_ in exam_audios.items() if info_.get("uid") == s["uid"] and aid_ not in used_ids) - (1 if can_rep else 0)
            if total_sz + length > EXAM_AUDIO_TOTAL_MAX:
                err_msg, err_code = "服务器听力缓存空间已满，请先删掉不用的旧听力后再传", 507
            elif my_orphans >= EXAM_AUDIO_ORPHAN_MAX:
                err_msg, err_code = "您有多个已上传但未保存的听力文件，请先完成保存或稍后再试", 429
        if err_msg:
            return fail(err_msg, err_code)
        aid = secrets.token_hex(12)
        os.makedirs(EXAM_AUDIO_DIR, exist_ok=True)
        tmp = os.path.join(EXAM_AUDIO_DIR, f"{aid}.{ext}.part")
        dst = os.path.join(EXAM_AUDIO_DIR, f"{aid}.{ext}")
        h = hashlib.sha256()
        moved = False
        try:
            left = length
            with open(tmp, "wb") as fh:
                while left > 0:
                    chunk = self.rfile.read(min(left, 1024 * 1024))
                    if not chunk:
                        raise OSError("上传中断")
                    fh.write(chunk)
                    h.update(chunk)
                    left -= len(chunk)
            info = parse_audio_info(tmp, ext)
            if not info:
                return fail("无法识别该音频文件，请确认是有效的 MP3 或 WAV 音频")
            os.replace(tmp, dst)
            moved = True
        except OSError:
            return fail("上传中断了，请重试")
        except Exception:
            return fail("音频文件格式解析失败，请确认是有效的 MP3 或 WAV 音频")
        finally:
            if not moved:
                try:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                except OSError:
                    pass
        rec = {"id": aid, "name": name, "ext": ext, "size": length, "sha256": h.hexdigest(),
               "duration": info["duration"], "by": s["name"], "uid": s["uid"], "at": time.time(), "bound": False}
        err_msg, err_code, old_path_del = None, 400, ""
        with lock:
            purge_exam_audio()
            used_ids = ({x.get("audio_id") for e in exams for x in (e.get("sessions") or []) if x.get("audio_id")}
                        | {t.get("audio_id") for t in listen_tasks if t.get("audio_id")})
            can_rep = bool(rep_aid and rep_aid in exam_audios and rep_aid not in used_ids and exam_audios[rep_aid].get("uid") == s["uid"])
            rep_sz = int(exam_audios[rep_aid].get("size") or 0) if can_rep else 0
            total_sz = sum(int(x.get("size") or 0) for x in exam_audios.values()) - rep_sz
            my_orphans = sum(1 for aid_, info_ in exam_audios.items() if info_.get("uid") == s["uid"] and aid_ not in used_ids) - (1 if can_rep else 0)
            if total_sz + length > EXAM_AUDIO_TOTAL_MAX:
                err_msg, err_code = "服务器听力缓存空间已满，请先删掉不用的旧听力后再传", 507
            elif my_orphans >= EXAM_AUDIO_ORPHAN_MAX:
                err_msg, err_code = "您有多个已上传但未保存的听力文件，请先完成保存或稍后再试", 429
            else:
                exam_audios[aid] = rec
                if can_rep:
                    old_info = exam_audios.pop(rep_aid, None)
                    exam_audio_ready.pop(rep_aid, None)
                    if old_info:
                        old_path_del = os.path.join(EXAM_AUDIO_DIR, f"{rep_aid}.{old_info.get('ext', 'mp3')}")
                write_json(EXAM_AUDIO_PATH, exam_audios)
        if err_msg:
            try:
                if os.path.exists(dst):
                    os.remove(dst)
            except OSError:
                pass
            return fail(err_msg, err_code)
        if old_path_del:
            try:
                if os.path.exists(old_path_del):
                    os.remove(old_path_del)
            except OSError:
                pass
        self.send_json({"ok": True, "audio": {"id": aid, "name": name, "ext": ext, "size": length,
                                              "duration": info["duration"], "sha256": rec["sha256"][:12]}})

    def api_exam_audio_status(self, body):
        """一体机上报：预下载缓存就绪状态（audio_id + status/pct）和/或当前听力播放进度（exam_id/listen_id + play_state）"""
        if PUBLIC:
            return self.deny("public_forbid")
        cls = str(body.get("class") or "")
        if not cls or cls not in load_students():
            return self.send_json({"error": "没有这个班"}, 404)
        now = time.time()
        last_seen[cls] = now
        aid = str(body.get("audio_id") or "")[:40]
        def safe_num(v, hi=86400.0):
            try:
                x = float(v if v is not None else 0)
            except (TypeError, ValueError):
                return 0.0
            return min(hi, max(0.0, x)) if math.isfinite(x) else 0.0
        with lock:
            acap = body.get("acap")
            if acap in (0, 1, 2):
                screen_audio_cap[cls] = {"can_mp3": acap >= 2, "can_seek": acap >= 2, "can_wav": acap >= 1, "at": now}
            if aid and aid in exam_audios and body.get("cache_status") in ("ready", "downloading", "error"):
                used = (any(cls in e.get("classes", []) and any(x.get("audio_id") == aid for x in (e.get("sessions") or [])) for e in exams)
                        or any(cls in t.get("classes", []) and t.get("audio_id") == aid for t in listen_tasks))
                if used:
                    exam_audio_ready.setdefault(aid, {})[cls] = {
                        "status": body["cache_status"],
                        "pct": int(safe_num(body.get("pct"), 100.0)),
                        "at": now,
                        "err": str(body.get("err") or "")[:120],
                    }
            if body.get("play_state") in ("idle", "playing", "paused", "stopped", "done", "testing", "error"):
                lid = body.get("listen_id")
                eid = body.get("exam_id")
                idx = body.get("idx")
                key = ""
                if type(lid) is int:
                    if any(t["id"] == lid and cls in t.get("classes", []) for t in listen_tasks):
                        key = f"listen|{lid}|{cls}"
                elif type(eid) is int and type(idx) is int:
                    if any(e["id"] == eid and cls in e.get("classes", []) and 0 <= idx < len(e.get("sessions") or []) for e in exams):
                        key = f"{eid}|{idx}|{cls}"
                if key:
                    if body["play_state"] in ("idle", "stopped", "done"):
                        exam_audio_play.pop(key, None)
                    else:
                        exam_audio_play[key] = {
                            "state": body["play_state"],
                            "pos": safe_num(body.get("pos")),
                            "dur": safe_num(body.get("dur")),
                            "audio_id": aid,
                            "err": str(body.get("play_err") or body.get("err") or "")[:80],
                            "at": now,
                        }
        self.send_json({"ok": True})

    def api_listen_save(self, body, sender):
        """课堂 -> 播放听力：保存平时听力任务（传好立即触发一体机后台预下载）"""
        if PUBLIC:
            return self.deny("public_forbid")
        s = self.sess()
        students = load_students()
        raw_cs = body.get("classes") if isinstance(body.get("classes"), list) else ([body.get("class")] if body.get("class") else [])
        classes = list(dict.fromkeys(str(c).strip() for c in raw_cs if str(c or "").strip() in students))
        if not classes:
            return self.send_json({"error": "请选择班级"}, 400)
        aid = str(body.get("audio_id") or "").strip()
        if not aid:
            return self.send_json({"error": "请先上传听力音频（MP3 或 WAV）"}, 400)
        play_at = str(body.get("play_at") or "").strip()
        if play_at:
            if not re.fullmatch(r"\d{2}:\d{2}", play_at) or not re_hm(play_at):
                return self.send_json({"error": "定时播放时间格式不对"}, 400)
            play_ts = datetime.combine(now_cn().date(), datetime.strptime(play_at, "%H:%M").time(), CN).timestamp()
            if play_ts <= time.time() + 30:
                return self.send_json({"error": "这个时间已经过了，请选今天稍后的时间"}, 400)
        err_kind, err_msg, item = None, None, None
        with lock:
            a = exam_audios.get(aid)
            if not a:
                err_kind, err_msg = 400, "音频文件不存在或已过期，请重新上传"
            elif not (is_admin(s) or a.get("uid") == s["uid"]):
                err_kind = "own"
            else:
                a["bound"] = True
                default_title = os.path.splitext(a["name"])[0]
                title = re.sub(r"\s+", " ", str(body.get("title") or default_title)).strip()[:36] or "英语听力"
                item = {"id": next_id(listen_tasks), "title": title, "classes": classes, "audio_id": aid,
                        "date": today(), "play_at": play_at, "creator": sender, "uid": s["uid"], "created": time.time()}
                listen_tasks.append(item)
                purge_exam_audio()
                write_json(EXAM_AUDIO_PATH, exam_audios)
                write_json(LISTEN_PATH, listen_tasks)
                exam_rev[0] = time.time()  # 通知一体机 2 秒内拉新看板开始后台静默缓存
        if err_kind == "own":
            return self.deny("own")
        if err_kind:
            return self.send_json({"error": err_msg}, err_kind)
        self.send_json({"ok": True, "item": item})

    def api_listen_delete(self, body, sender):
        if PUBLIC:
            return self.deny("public_forbid")
        tid = body.get("id")
        with lock:
            listen_tasks[:] = [t for t in listen_tasks if t["id"] != tid]
            for k in [k for k in exam_audio_play if k.startswith(f"listen|{tid}|")]:
                exam_audio_play.pop(k, None)
            purge_exam_audio()
            write_json(LISTEN_PATH, listen_tasks)
            exam_rev[0] = time.time()
        self.send_json({"ok": True})

    def api_listen_cmd(self, body, sender):
        """课堂 -> 播放听力控制指令：test（10 秒试音）、play（立即同步播放，大屏浮条不挡课件）、stop（停止）"""
        if PUBLIC:
            return self.deny("public_forbid")
        action = str(body.get("action") or "")
        if action not in ("test", "play", "stop"):
            return self.send_json({"error": "不支持的操作"}, 400)
        raw_cs = body.get("classes") if isinstance(body.get("classes"), list) else ([body.get("class")] if body.get("class") else [])
        tid = body.get("id")
        if type(tid) is not int:
            return self.send_json({"error": "找不到这条听力任务"}, 404)
        err_code, err_msg, targets, payload, on_cnt, start_at = None, None, [], None, 0, 0
        with lock:
            t = next((x for x in listen_tasks if x["id"] == tid), None)
            if not t:
                err_code, err_msg = 404, "找不到这条听力任务"
            else:
                aid = t.get("audio_id") or ""
                a = exam_audios.get(aid)
                if action == "play" and not a:
                    err_code, err_msg = 400, "音频文件不存在或已过期"
                else:
                    targets = list(dict.fromkeys(str(c).strip() for c in raw_cs if str(c or "").strip() in t["classes"]))
                    if not targets:
                        err_code, err_msg = 400, "请至少选择一个目标班级"
                    else:
                        now = time.time()
                        start_at = now + 2.5 if action == "play" else now
                        payload = {"cmd_id": secrets.token_hex(6), "mode": "listen", "action": action, "listen_id": t["id"],
                                   "title": t["title"], "audio_id": aid, "duration": a["duration"] if a else 0,
                                   "sha256": a["sha256"] if a else "", "ext": a["ext"] if a else "mp3",
                                   "url": f"/exam-audio/{aid}" if aid else "", "start_at": start_at}
                        on_cnt = sum(1 for c in targets if now - last_seen.get(c, 0) < 15)
        if err_code:
            return self.send_json({"error": err_msg}, err_code)
        self.create_msg(targets, f"课堂听力控制：{action}", sender, kind="exam_audio", urgent=True,
                        data=payload, expire_at=time.time() + 90)
        self.send_json({"ok": True, "count": len(targets), "online_count": on_cnt, "start_at": start_at})

    def api_exam_audio_cmd(self, body, sender):
        """工作台对选中班级发送听力控制指令：test（10 秒考前试音）、play（立即同步开播）、stop（停止播放）"""
        if PUBLIC:
            return self.deny("public_forbid")
        action = str(body.get("action") or "")
        if action not in ("test", "play", "stop"):
            return self.send_json({"error": "不支持的操作"}, 400)
        raw_cs = body.get("classes") if isinstance(body.get("classes"), list) else ([body.get("class")] if body.get("class") else [])
        eid, idx = body.get("exam_id"), body.get("idx")
        if type(eid) is not int or type(idx) is not int:
            return self.send_json({"error": "找不到这场考试"}, 404)
        err_code, err_msg, targets, payload, on_cnt, start_at = None, None, [], None, 0, 0
        with lock:
            e = next((x for x in exams if x["id"] == eid), None)
            if not e or not 0 <= idx < len(e["sessions"]):
                err_code, err_msg = 404, "找不到这场考试"
            else:
                sess = e["sessions"][idx]
                aid = sess.get("audio_id") or ""
                a = exam_audios.get(aid) if aid else None
                if action == "play" and not a:
                    err_code, err_msg = 400, "这一场考试还没有上传听力音频"
                else:
                    targets = list(dict.fromkeys(str(c).strip() for c in raw_cs if str(c or "").strip() in e["classes"]))
                    if not targets:
                        err_code, err_msg = 400, "请至少选择一个参加本场考试的班级"
                    else:
                        now = time.time()
                        # 统一锚定 2.5 秒后的服务器时间戳，让多班一体机按服务器对时误差 < 1 秒同步起播
                        start_at = now + 2.5 if action == "play" else now
                        cmd_id = secrets.token_hex(6)
                        payload = {"cmd_id": cmd_id, "action": action, "exam_id": e["id"], "idx": idx,
                                   "exam_key": f"{e['id']}-{idx}", "title": f"{sess['subject']}听力",
                                   "audio_id": aid, "ext": a["ext"] if a else "mp3",
                                   "sha256": a["sha256"] if a else "", "duration": a["duration"] if a else 0,
                                   "url": f"/exam-audio/{aid}" if a else "", "start_at": start_at}
                        on_cnt = sum(1 for c in targets if now - last_seen.get(c, 0) < 15)
        if err_code:
            return self.send_json({"error": err_msg}, err_code)
        self.create_msg(targets, f"考试听力控制：{action}", sender, kind="exam_audio", urgent=True,
                        data=payload, expire_at=time.time() + 90)
        self.send_json({"ok": True, "count": len(targets), "online_count": on_cnt, "start_at": start_at})

    def api_exam_save(self, body, sender):
        name = re.sub(r"\s+", " ", str(body.get("name") or "")).strip()[:30]
        classes = list(dict.fromkeys(str(c) for c in (body.get("classes") if isinstance(body.get("classes"), list) else []) if str(c) in load_students()))
        notice = "\n".join(l.strip()[:60] for l in str(body.get("notice") or "").split("\n") if l.strip())[:600]
        raw_sessions = body.get("sessions") if isinstance(body.get("sessions"), list) else []
        sessions = []
        hm_min = lambda t: int(t[:2]) * 60 + int(t[3:5])
        for x in raw_sessions[:30]:
            if not isinstance(x, dict):
                return self.send_json({"error": "场次参数格式不对"}, 400)
            subj, date, a, b = (str(x.get(k) or "").strip() for k in ("subject", "date", "start", "end"))
            if not subj and not a and not b:
                continue
            if not subj or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) or not re_hm(a) or not re_hm(b):
                return self.send_json({"error": "每一场都要填科目、日期、开始和结束时间"}, 400)
            try:
                datetime.strptime(date, "%Y-%m-%d")
            except ValueError:
                return self.send_json({"error": f"{subj}：日期格式不对"}, 400)
            if a >= b:
                return self.send_json({"error": f"{subj}：结束时间要晚于开始时间"}, 400)
            row = {"subject": subj[:10], "date": date, "start": a, "end": b}
            aid = str(x.get("audio_id") or "").strip()
            if aid:
                if PUBLIC:
                    return self.deny("public_forbid")
                astart = str(x.get("audio_start") or "").strip() or a
                if not re.fullmatch(r"\d{2}:\d{2}", astart) or not re_hm(astart):
                    return self.send_json({"error": f"{subj}：听力起播时间格式不对"}, 400)
                if hm_min(astart) < hm_min(a) - EXAM_AUDIO_EARLY_MAX:
                    return self.send_json({"error": f"{subj}：听力起播时间最多只能早于开考 {EXAM_AUDIO_EARLY_MAX} 分钟"}, 400)
                if hm_min(astart) >= hm_min(b):
                    return self.send_json({"error": f"{subj}：听力起播时间要早于考试结束时间"}, 400)
                row["audio_id"] = aid
                row["audio_start"] = astart
            sessions.append(row)
        if not name:
            return self.send_json({"error": "请填写考试名称，如：10 月月考"}, 400)
        if not classes:
            return self.send_json({"error": "请选择参加考试的班级"}, 400)
        err = self.multi_class_denied(classes)
        if err:
            return self.send_json({"error": err}, 403)
        if not sessions:
            return self.send_json({"error": "至少填一场考试"}, 400)
        keyed = sorted(sessions, key=lambda x: (x["date"], x["start"], x["end"]))
        for p_, n_ in zip(keyed, keyed[1:]):
            if p_["date"] == n_["date"]:
                if n_["start"] < p_["end"]:
                    return self.send_json({"error": f"{p_['subject']} 和 {n_['subject']} 的时间重叠了"}, 400)
                if n_.get("audio_id") and n_.get("audio_start") and n_["audio_start"] < p_["end"]:
                    return self.send_json({"error": f"{n_['subject']}听力起播时间（{n_['audio_start']}）早于前一场{p_['subject']}结束时间（{p_['end']}），会冲突"}, 400)
        s_cur = self.sess()
        err_kind, err_msg, item = None, None, None
        with lock:
            old_idx = next((idx_ for idx_, e in enumerate(exams) if e["id"] == body.get("id")), None)
            old = exams[old_idx] if old_idx is not None else None
            old_aids = {x.get("audio_id") for x in (old.get("sessions") or []) if x.get("audio_id")} if old else set()
            bound_recs = []
            for x in sessions:
                aid = x.get("audio_id")
                if aid:
                    a_rec = exam_audios.get(aid)
                    if not a_rec:
                        err_kind, err_msg = 400, f"{x['subject']}：听力音频不存在或已过期，请重新上传"
                        break
                    if not (is_admin(s_cur) or a_rec.get("uid") == s_cur["uid"] or aid in old_aids):
                        err_kind = "own"
                        break
                    bound_recs.append(a_rec)
            if not err_kind:
                for e in exams:  # 同一个班不能同时有两场考试
                    if e is old:
                        continue
                    for c in set(classes) & set(e["classes"]):
                        for x in e["sessions"]:
                            for y in sessions:
                                if x["date"] != y["date"]:
                                    continue
                                if x["start"] < y["end"] and y["start"] < x["end"]:
                                    err_kind, err_msg = 400, f"{c}在{e['name']}里已经有 {x['date'][5:]} {x['start']}–{x['end']} 的{x['subject']}，时间重叠了"
                                    break
                                xs = min(x["start"], x["audio_start"]) if (x.get("audio_id") and x.get("audio_start")) else x["start"]
                                ys = min(y["start"], y["audio_start"]) if (y.get("audio_id") and y.get("audio_start")) else y["start"]
                                if xs < y["end"] and ys < x["end"]:
                                    err_kind, err_msg = 400, f"{c}在{e['name']}里 {x['date'][5:]} {xs}–{x['end']} 的{x['subject']}与{y['subject']}（{ys}–{y['end']}）听力/考试时段冲突"
                                    break
                            if err_kind:
                                break
                        if err_kind:
                            break
                    if err_kind:
                        break
            if not err_kind:
                for a_rec in bound_recs:
                    a_rec["bound"] = True
                if old is not None:
                    changed = {i for i, x in enumerate(old["sessions"]) if i >= len(sessions) or x != sessions[i]}
                    new_ended = {k: v for k, v in (old.get("ended") or {}).items() if int(k.rsplit("|", 1)[1]) not in changed}
                    # 构造新 dict 替换，避免并发读 exam_sessions() 读到半更新的 sessions 列表
                    item = {**old, "name": name, "classes": classes, "sessions": sessions, "notice": notice, "ended": new_ended}
                    exams[old_idx] = item
                else:
                    item = {"id": next_id(exams), "name": name, "classes": classes, "sessions": sessions, "notice": notice,
                            "creator": sender, "uid": s_cur["uid"], "created": time.time(), "ended": {}}
                    exams.append(item)
                cutoff = (now_cn() - timedelta(days=60)).strftime("%Y-%m-%d")
                exams[:] = [e for e in exams if max(x["date"] for x in e["sessions"]) >= cutoff]
                if bound_recs:
                    write_json(EXAM_AUDIO_PATH, exam_audios)
                purge_exam_audio()
                save_exams()
        if err_kind == "own":
            return self.deny("own")
        if err_kind:
            return self.send_json({"error": err_msg}, err_kind)
        self.send_json({"ok": True, "item": item})

    def api_exam_delete(self, body, sender):
        eid = body.get("id")
        with lock:
            exams[:] = [e for e in exams if e["id"] != eid]
            for k in [k for k in exam_audio_play if k.startswith(f"{eid}|")]:
                exam_audio_play.pop(k, None)
            purge_exam_audio()
            save_exams()
        self.send_json({"ok": True})

    def api_exam_end(self, body, sender):
        """提前结束这个班正在显示的这一场（大屏马上收起）"""
        cls = str(body.get("class") or "")
        i = body.get("idx")
        not_found = False
        with lock:
            e = next((e for e in exams if e["id"] == body.get("id") and cls in e["classes"]), None)
            if not e or type(i) is not int or not 0 <= i < len(e["sessions"]):
                not_found = True
            else:
                e["ended"] = {**(e.get("ended") or {}), f"{cls}|{i}": time.time()}
                exam_audio_play.pop(f"{e['id']}|{i}|{cls}", None)
                save_exams()
        if not_found:
            return self.send_json({"error": "找不到这场考试"}, 404)
        self.send_json({"ok": True})

    # ---------- 作业 ----------
    def api_homework_add(self, body, sender):
        raw_cs = body.get("classes") if isinstance(body.get("classes"), list) else ([body.get("class")] if body.get("class") else [])
        students = load_students()
        classes = list(dict.fromkeys(str(c).strip() for c in raw_cs if str(c or "").strip() in students))
        subject = str(body.get("subject") or "").strip()[:10]
        content = str(body.get("content") or "").strip()[:TEXT_LIMIT if PUBLIC else 300]
        if not classes:
            return self.send_json({"error": "请选择班级"}, 400)
        err = self.multi_class_denied(classes)
        if err:
            return self.send_json({"error": err}, 403)
        if not subject or not content:
            return self.send_json({"error": "请填写科目和作业内容"}, 400)
        try:
            minutes = max(0, min(300, int(body.get("minutes") or 0)))
        except (TypeError, ValueError):
            minutes = 0
        due = str(body.get("due") or "").strip()[:20]
        d_today, now = today(), time.time()
        added = []
        with lock:
            nid = next_id(homework)
            for cls in classes:
                item = {"id": nid, "class": cls, "subject": subject, "content": content,
                        "minutes": minutes, "due": due, "date": d_today, "teacher": sender, "created": now}
                item["due_date"] = due_date(item)
                homework.append(item)
                added.append(item)
                nid += 1
            cutoff = (now_cn() - timedelta(days=200)).strftime("%Y-%m-%d")  # 保留一学期多，方便期末导出
            homework[:] = [h for h in homework if h["date"] >= cutoff]
            write_json(HOMEWORK_PATH, homework)
        msg_ids = []
        if body.get("push"):
            text = f"【{subject}作业】{content}" + (f"\n预计 {minutes} 分钟" if minutes else "") + (f" · {due}" if due else "")
            msg_ids, _ = self.create_msg(classes, text, sender, voice=body.get("voice"), urgent=False, defer=True, voice_name=body.get("voice_name"),
                                         kind="homework", say=f"{subject}作业：{content}", repeat=1)
        self.send_json({"ok": True, "item": added[0], "items": added, "count": len(added), "msg_ids": msg_ids})

    def api_homework_edit(self, body, sender):
        subject = str(body.get("subject") or "").strip()[:10]
        content = str(body.get("content") or "").strip()[:TEXT_LIMIT if PUBLIC else 300]
        if not subject or not content:
            return self.send_json({"error": "请填写科目和作业内容"}, 400)
        with lock:
            h = next((x for x in homework if x["id"] == body.get("id")), None)
            if not h:
                return self.send_json({"error": "这条作业已经不在了"}, 404)
            h.update({"subject": subject, "content": content, "minutes": max(0, min(300, int(body.get("minutes") or 0))),
                      "due": str(body.get("due") or "").strip()[:20]})
            h.pop("due_date", None)  # 交作业日期按新的"什么时候交"重算
            h["due_date"] = due_date(h)
            write_json(HOMEWORK_PATH, homework)
        self.send_json({"ok": True, "item": h})

    def api_homework_delete(self, body, sender):
        with lock:
            homework[:] = [h for h in homework if h["id"] != body.get("id")]
            write_json(HOMEWORK_PATH, homework)
        self.send_json({"ok": True})

    def api_homework_push(self, body, sender):
        cls = str(body.get("class") or "")
        text = homework_summary(cls)
        if not text:
            return self.send_json({"error": "今天还没有布置作业"}, 400)
        self.create_msg([cls], text, sender, voice=body.get("voice"), urgent=body.get("urgent"), defer=body.get("defer"),
                        voice_name=body.get("voice_name"), kind="homework", say="今日作业已发布，请同学们抄写", repeat=1)
        self.send_json({"ok": True})

    # ---------- 定时提醒 ----------
    def api_reminder_save(self, body, sender):
        text = str(body.get("text") or "").strip()[:200]
        hm = str(body.get("time") or "")
        days = sorted({int(d) for d in body.get("days") or [] if 1 <= int(d) <= 7})
        students = load_students()
        classes = list(dict.fromkeys(str(c).strip() for c in body.get("classes") or [] if str(c or "").strip() in students))
        if not text or not re_hm(hm) or not days or not classes:
            return self.send_json({"error": "请填写时间、重复日期和提醒内容"}, 400)
        err = self.multi_class_denied(classes)
        if err:
            return self.send_json({"error": err}, 403)
        with lock:
            r = next((x for x in reminders if x["id"] == body.get("id")), None)
            full = [c for c in classes if sum(c in x["classes"] for x in reminders if x is not r) >= REMINDER_LIMIT]
            if PUBLIC and full:
                return self.send_json({"error": f"{full[0]}的定时提醒已经有 {REMINDER_LIMIT} 条了，请先删掉一些"}, 400)
            if not r:
                r = {"id": next_id(reminders), "creator": sender, "uid": self.sess()["uid"]}
                reminders.append(r)
            elif not r.get("uid") and r.get("creator") == sender:
                r["uid"] = self.sess()["uid"]
            r.update({"text": text, "time": hm, "days": days, "classes": classes,
                      "voice": bool(body.get("voice")), "urgent": bool(body.get("urgent")),
                      "voice_name": body.get("voice_name"), "enabled": body.get("enabled", True) is not False})
            if r["time"] <= now_cn().strftime("%H:%M"):
                r["last_fired"] = today()  # 今天已过的时间点不补发
            else:
                r.pop("last_fired", None)
            reminders.sort(key=lambda x: x["time"])
            write_json(REMINDER_PATH, reminders)
        self.send_json({"ok": True, "item": r})

    def api_reminder_delete(self, body, sender):
        with lock:
            reminders[:] = [r for r in reminders if r["id"] != body.get("id")]
            write_json(REMINDER_PATH, reminders)
        self.send_json({"ok": True})

    # ---------- 常用语 ----------
    def api_shot_request(self, body, sender):
        """班主任/管理员点"看一眼大屏"：一体机下次轮询时截一张图传上来"""
        cls = str(body.get("class") or "")
        shot_req[cls] = time.time()
        self.send_json({"ok": True, "req": shot_req[cls], "online": time.time() - last_seen.get(cls, 0) < 15})

    def api_shot_upload(self, q):
        cls = resolve_class((q.get("class") or [""])[0])
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if not self.classroom_ok(q, cls) or length > 4_000_000:
            self.close_connection = True
            return self.send_json({"error": "密钥错误或图片太大"}, 403)
        data = self.rfile.read(length) if length else b""
        ctype = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg" if data[:2] == b"\xff\xd8" else ""
        err = (q.get("err") or [""])[0][:200] or ("" if ctype else "截图失败")
        shots[cls] = {"img": data if ctype else None, "type": ctype, "at": time.time(), "err": err}
        self.send_json({"ok": True})

    def api_me_name(self, body, sender):
        """老师改自己的称呼：账号、登录着的设备、自己布置的作业/提醒/错题/留言跟着改（以前发过的消息不改）"""
        s = self.sess()
        name = re.sub(r"\s+", "", str(body.get("name") or ""))[:20]
        if not name:
            return self.send_json({"error": "请填写称呼"}, 400)
        old, uid = s["name"], s["uid"]
        mine = set(self.my_classes())
        with lock:
            if uid == "admin":  # 用管理员密码登录的：只改这台设备
                s["name"] = name
            else:
                if uid in users["users"]:
                    users["users"][uid]["name"] = name
                    save_users()
                for t in sessions.values():
                    if t.get("uid") == uid:
                        t["name"] = name
            write_json(SESSION_PATH, sessions)
            for items, key, path in ((homework, "teacher", HOMEWORK_PATH), (quizzes, "teacher", QUIZ_PATH), (reminders, "creator", REMINDER_PATH)):
                for x in items:
                    if x.get(key) == old and (set(x["classes"]) & mine if "classes" in x else x.get("class") in mine):
                        x[key] = name
                write_json(path, items)
            for n in notes:
                if n.get("uid") == uid:
                    n["teacher"] = name
            write_json(NOTES_PATH, notes)
            mc = settings.get("my_classes", {})
            if old in mc and name not in mc:
                mc[name] = mc.pop(old)
                write_json(SETTINGS_PATH, settings)
        self.send_json({"ok": True, "name": name})

    def my_phrases(self):
        """公共模式：默认几条大家共用 + 自己加的；本校服务器全部共用"""
        if not PUBLIC:
            return phrases
        uid = self.sess()["uid"]
        return [dict(p, mine=p.get("owner") == uid or (not p.get("owner") and uid == "admin"))
                for p in phrases if not p.get("owner") or p["owner"] == uid]

    def api_phrase_add(self, body, sender):
        text = str(body.get("text") or "").strip()[:200]
        if not text:
            return self.send_json({"error": "内容不能为空"}, 400)
        with lock:
            if not any(p["text"] == text for p in self.my_phrases()):
                phrases.append({"id": next_id(phrases), "text": text, **({"owner": self.sess()["uid"]} if PUBLIC else {})})
                write_json(PHRASE_PATH, phrases)
        self.send_json({"ok": True, "items": self.my_phrases()})

    def api_phrase_delete(self, body, sender):
        uid = self.sess()["uid"]
        p = next((x for x in phrases if x["id"] == body.get("id")), None)
        if PUBLIC and p and not (p.get("owner") == uid or (not p.get("owner") and uid == "admin")):
            return self.send_json({"error": "只能删除自己加的常用语"}, 403)
        with lock:
            phrases[:] = [p for p in phrases if p["id"] != body.get("id")]
            write_json(PHRASE_PATH, phrases)
        self.send_json({"ok": True, "items": self.my_phrases()})

    # ---------- 课堂工具 ----------
    def api_timer(self, body, sender):
        cls = str(body.get("class") or "")
        seconds = max(10, min(3 * 3600, int(body.get("seconds") or 0)))
        end_at = time.time() + seconds
        label = str(body.get("label") or "").strip()[:30]
        mins, secs = divmod(seconds, 60)
        text = f"{label or '计时'} {mins} 分钟" + (f" {secs} 秒" if secs else "")
        ids, _ = self.create_msg([cls], text, sender, kind="timer", urgent=True,
                                 data={"seconds": seconds, "end_at": end_at, "label": label},
                                 expire_at=end_at + 30)
        self.send_json({"ok": True, "id": ids[0], "end_at": end_at})

    def api_pick(self, body, sender):
        cls = str(body.get("class") or "")
        names = [s["name"] for s in load_students().get(cls, [])]
        if not names:
            return self.send_json({"error": "这个班还没有学生名单"}, 400)
        recent = pick_history.setdefault(cls, [])
        pool = [n for n in names if n not in recent] or names
        name = random.choice(pool)
        recent.append(name)
        del recent[:-max(1, len(names) // 2)]
        text = f"请 {name} 同学回答"
        ids, _ = self.create_msg([cls], text, sender, voice=body.get("voice", True), urgent=True,
                                 voice_name=body.get("voice_name"), kind="pick",
                                 data={"name": name, "names": names}, expire_at=time.time() + 600,
                                 say=f"请{name}同学回答", repeat=1)
        self.send_json({"ok": True, "id": ids[0], "name": name})

    # ---------- 作业收交 ----------
    def api_collect_save(self, body):
        cls = str(body.get("class") or "")
        hw = next((h for h in homework if h["id"] == body.get("hw_id") and h["class"] == cls), None)
        if not hw:
            return self.send_json({"error": "找不到这项作业"}, 400)
        roster = {s["name"] for s in load_students().get(cls, [])}
        missing = [n for n in body.get("missing") or [] if n in roster]
        with lock:
            rec = collect_of(cls, hw["id"])
            if not rec:
                rec = {"id": next_id(collects), "class": cls, "hw_id": hw["id"]}
                collects.append(rec)
            rec.update({"date": hw["date"], "subject": hw["subject"], "content": hw["content"], "missing": missing,
                        "ts": time.time()})
            cutoff = (now_cn() - timedelta(days=200)).strftime("%Y-%m-%d")
            collects[:] = [c for c in collects if c.get("date", "") >= cutoff]
            write_json(COLLECT_PATH, collects)
        self.send_json({"ok": True, "missing": missing})

    def api_collect_start(self, body, sender):
        cls = str(body.get("class") or "")
        hw = next((h for h in homework if h["id"] == body.get("hw_id") and h["class"] == cls), None)
        if not hw:
            return self.send_json({"error": "找不到这项作业"}, 400)
        self.create_msg([cls], f"【{hw['subject']}作业】收交登记\n请{hw['subject']}课代表点击下方按钮开始登记", sender,
                        voice=False, urgent=False, defer=body.get("defer", True), kind="collect",
                        data={"hw_id": hw["id"], "subject": hw["subject"]}, expire_at=time.time() + 3 * 3600)
        self.send_json({"ok": True})

    def api_collect_remind(self, body, sender):
        cls = str(body.get("class") or "")
        rec = collect_of(cls, body.get("hw_id"))
        if not rec or not rec["missing"]:
            return self.send_json({"error": "没有未交的同学"}, 400)
        names = "、".join(rec["missing"])
        text = f"请以下同学尽快补交{rec['subject']}作业：\n{names}"
        self.create_msg([cls], text, sender, voice=body.get("voice"), urgent=False, defer=body.get("defer", True), voice_name=body.get("voice_name"),
                        kind="call", say=f"请{names}同学尽快补交{rec['subject']}作业", repeat=1)
        self.send_json({"ok": True})

    # ---------- 学情：题图上传、投屏、错题记录 ----------
    def api_upload(self, body, sender):
        import base64
        data = str(body.get("data") or "")
        if "," in data:
            data = data.split(",", 1)[1]
        try:
            raw = base64.b64decode(data, validate=False)
        except ValueError:
            return self.send_json({"error": "图片格式不对"}, 400)
        if len(raw) < 100:
            return self.send_json({"error": "图片是空的"}, 400)
        name = hashlib.sha256(raw).hexdigest()[:24]
        try:  # 转成 PNG（一体机的 tkinter 只认 PNG），并限制大小
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(raw))
            img = img.convert("RGB")
            img.thumbnail((1800, 1200))
            path = os.path.join(IMG_DIR, name + ".png")
            img.save(path, "PNG", optimize=True)
            w, h = img.size
        except Exception as e:
            print("图片处理失败：", e)
            return self.send_json({"error": "无法读取这张图片，请换一张试试"}, 400)
        self.send_json({"ok": True, "img": f"/img/{name}.png", "w": w, "h": h})

    def api_file_prepare(self, body, sender):
        """群发文件前先校验目标班级、权限和空间，返回临时 batch 编号，避免把几十个中文班名塞进 URL"""
        raw_cs = body.get("classes") if isinstance(body.get("classes"), list) else [body.get("class")]
        students = load_students()
        classes = list(dict.fromkeys(str(c).strip() for c in raw_cs if str(c or "").strip() in students))
        if not classes:
            return self.send_json({"error": "请选择班级"}, 400)
        err = self.multi_class_denied(classes)
        if err:
            return self.send_json({"error": err}, 403)
        name = os.path.basename(str(body.get("name") or "").replace("\\", "/")).strip()
        name = re.sub(r'[\x00-\x1f<>:"|?*]', "_", name)[:80]
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in FILE_EXTS:
            return self.send_json({"error": "只能发 Word、PDF、PPT、Excel、网页、图片、mp4 视频这类文件"}, 400)
        try:
            length = int(body.get("size") or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            return self.send_json({"error": "文件是空的"}, 400)
        if length > FILE_MAX:
            return self.send_json({"error": f"文件太大了，最大 {FILE_MAX // 1024 // 1024}MB"}, 413)
        now = time.time()
        with lock:
            purge_files()
            if files_disk_bytes() + length > FILE_TOTAL_MAX:
                return self.send_json({"error": "服务器空间不够了，请先删掉一些旧文件"}, 507)
            ok_cls, skipped = [], []
            for c in classes:
                if PUBLIC and sum(f["size"] for f in files if f["class"] == c) + length > FILE_CLASS_MAX:
                    skipped.append(c)
                else:
                    ok_cls.append(c)
            if not ok_cls:
                return self.send_json({"error": f"所选班级的文件空间都已满 {FILE_CLASS_MAX // 1024 // 1024}MB，请先删掉一些旧文件"}, 507)
            for k in [k for k, v in file_batches.items() if v["exp"] < now]:
                file_batches.pop(k, None)
            bid = secrets.token_hex(12)
            file_batches[bid] = {"uid": self.sess()["uid"], "classes": ok_cls, "skipped": skipped, "name": name, "exp": now + 300}
        self.send_json({"ok": True, "batch": bid, "count": len(ok_cls), "skipped": skipped})

    def api_file_upload(self, q):
        """老师把文档发到大屏：?class=班级&name=文件名（支持多个 class 或 ?batch=编号），请求体是文件本身（边收边写盘，不占内存）"""
        def fail(msg, code=400):
            self.close_connection = True  # 请求体可能没读完，这条连接不能再用
            return self.send_json({"error": msg}, code)
        s = self.sess()
        if not s:
            return fail("请先登录", 401)
        students = load_students()
        bid = (q.get("batch") or [""])[0]
        pre_skipped = []
        if bid:
            with lock:
                b = file_batches.pop(bid, None)
            if not b or b["exp"] < time.time() or b["uid"] != s["uid"]:
                return fail("群发任务已过期，请重新选择文件上传", 400)
            classes = list(dict.fromkeys(resolve_class(c) for c in b["classes"] if resolve_class(c) in students))
            pre_skipped = list(b.get("skipped") or [])
        else:
            raw_cs = q.get("class") or []
            classes = list(dict.fromkeys(resolve_class(c) for c in raw_cs if c and resolve_class(c) in students))
        if not classes or not all(self.allowed(c, "teacher") for c in classes):
            return fail("你不是这个班的老师", 403)
        err = self.multi_class_denied(classes)
        if err:
            return fail(err, 403)
        name = os.path.basename((q.get("name") or [""])[0].replace("\\", "/")).strip()
        name = re.sub(r'[\x00-\x1f<>:"|?*]', "_", name)[:80]
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in FILE_EXTS:
            return fail("只能发 Word、PDF、PPT、Excel、网页、图片、mp4 视频这类文件")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return fail("文件是空的")
        if length > FILE_MAX:
            return fail(f"文件太大了，最大 {FILE_MAX // 1024 // 1024}MB", 413)
        with lock:
            purge_files()
            if files_disk_bytes() + length > FILE_TOTAL_MAX:
                return fail("服务器空间不够了，请先删掉一些旧文件", 507)
            ok_cls, skipped = [], list(pre_skipped)
            for c in classes:
                if PUBLIC and sum(f["size"] for f in files if f["class"] == c) + length > FILE_CLASS_MAX:
                    if len(classes) == 1 and not pre_skipped:
                        return fail(f"本班文件已经满 {FILE_CLASS_MAX // 1024 // 1024}MB 了，请先删掉一些旧文件（7 天后也会自动删）", 507)
                    skipped.append(c)
                else:
                    ok_cls.append(c)
            if not ok_cls:
                return fail(f"所选班级的文件空间都已满 {FILE_CLASS_MAX // 1024 // 1024}MB，请先删掉一些旧文件", 507)
            if PUBLIC and not is_admin(s):
                now_rl = time.time()
                log = send_log[s["uid"]] = [t for t in send_log.get(s["uid"], []) if now_rl - t < 3600]
                if len(log) + len(ok_cls) > SEND_LIMIT:
                    return fail("发送太频繁，请稍后再试", 429)
                log.extend([now_rl] * len(ok_cls))
        first_fid = secrets.token_hex(12)
        tmp = os.path.join(FILE_DIR, first_fid + ".part")
        created_paths = []
        try:
            left = length
            with open(tmp, "wb") as fh:
                while left > 0:
                    chunk = self.rfile.read(min(left, 1024 * 1024))
                    if not chunk:
                        raise OSError("上传中断")
                    fh.write(chunk)
                    left -= len(chunk)
            first_path = os.path.join(FILE_DIR, first_fid)
            os.replace(tmp, first_path)
            created_paths.append(first_path)
            blob_id = first_fid if len(ok_cls) > 1 else None
            entries = [(ok_cls[0], first_fid, blob_id)]
            for c in ok_cls[1:]:
                fid = secrets.token_hex(12)
                dst = os.path.join(FILE_DIR, fid)
                try:
                    os.link(first_path, dst)
                    entries.append((c, fid, blob_id))
                except OSError:
                    shutil.copy2(first_path, dst)
                    entries.append((c, fid, None))
                created_paths.append(dst)
        except OSError:
            for p in [tmp] + created_paths:
                try:
                    os.remove(p)
                except OSError:
                    pass
            return fail("上传中断了，请重试")
        now = time.time()
        with lock:
            for c, fid, bid_tag in entries:
                rec = {"id": fid, "class": c, "name": name, "ext": ext, "size": length,
                       "by": s["name"], "uid": s["uid"], "at": now}
                if bid_tag:
                    rec["blob"] = bid_tag
                files.append(rec)
            write_json(FILE_PATH, files)
        size = f"{length / 1024 / 1024:.1f}MB" if length >= 1024 * 1024 else f"{max(1, length // 1024)}KB"
        ids, _ = self.create_msg([c for c, _, _ in entries], f"发来文件：{name}", s["name"], kind="file",
                                 data_by_class={c: {"file": fid, "name": name, "size": size} for c, fid, _ in entries})
        fids = [fid for _, fid, _ in entries]
        self.send_json({"ok": True, "id": fids[0], "ids": fids, "msg_id": ids[0], "msg_ids": ids,
                        "count": len(entries), "skipped": skipped})

    def api_file_delete(self, body, sender):
        fid = str(body.get("id") or "")
        with lock:
            for f in files:
                if f["id"] == fid:
                    f["at"] = 0  # 标成过期，交给 purge_files 删掉
            purge_files()
        self.send_json({"ok": True})

    def api_image_push(self, body, sender):
        cls = str(body.get("class") or "")
        img = str(body.get("img") or "")
        if not cls or not re.fullmatch(r"/img/[0-9a-f]{24}\.png", img):
            return self.send_json({"error": "请先选择图片"}, 400)
        caption = str(body.get("caption") or "").strip()[:60]
        seconds = max(0, min(3600, int(body.get("seconds") or 0)))
        now = time.time()
        ids, _ = self.create_msg([cls], caption or "请看大屏上的题目", sender, kind="image", urgent=True,
                                 data={"img": img, "caption": caption, "seconds": seconds,
                                       "end_at": now + seconds if seconds else None},
                                 expire_at=now + max(seconds + 60, 7200))
        with lock:
            recent = [i for i in settings.get("recent_images", []) if i.get("img") != img]
            settings["recent_images"] = ([{"class": cls, "img": img, "caption": caption, "ts": now}] + recent)[:40]
            write_json(SETTINGS_PATH, settings)
        self.send_json({"ok": True, "id": ids[0]})

    def api_quiz_save(self, body, sender):
        cls = str(body.get("class") or "")
        title = str(body.get("title") or "").strip()[:30]
        questions = [str(x).strip()[:8] for x in body.get("questions") or [] if str(x).strip()][:80]
        if not cls or not title or not questions:
            return self.send_json({"error": "请填写测验名称和题号"}, 400)
        roster = {s["name"] for s in load_students().get(cls, [])}
        wrong = {qn: [n for n in (body.get("wrong") or {}).get(qn, []) if n in roster] for qn in questions}
        with lock:
            item = next((x for x in quizzes if x["id"] == body.get("id") and x["class"] == cls), None)
            if not item:
                item = {"id": next_id(quizzes), "class": cls, "date": today(), "source": body.get("source") or "手动"}
                quizzes.append(item)
            item.update({"title": title, "questions": questions, "wrong": wrong, "teacher": sender})
            if body.get("rule") in ("lost", "half", "zero"):
                item["rule"] = body["rule"]
            del quizzes[:-200]
            write_json(QUIZ_PATH, quizzes)
        self.send_json({"ok": True, "item": item})

    def api_quiz_import(self, body, sender):
        """导入智学网"成绩明细.xlsx"（可同时带"试卷分析.xlsx"提供每题满分）"""
        import base64
        cls = str(body.get("class") or "")
        roster = [s["name"] for s in load_students().get(cls, [])]
        detail, fulls = None, {}
        for f in body.get("files") or []:
            try:
                data = str(f.get("data") or "")
                book = read_xlsx(base64.b64decode(data.split(",", 1)[-1]))
            except Exception:
                return self.send_json({"error": f"无法读取文件：{f.get('name')}（请选 .xlsx 格式）"}, 400)
            for rows in book.values():
                if not rows:
                    continue
                head = rows[0]
                if "姓名" in head and detail is None:
                    detail = rows
                elif "题目" in head and "满分" in head:  # 试卷分析：只取第一张（全年级）表
                    ti, fi = head.index("题目"), head.index("满分")
                    for r in rows[1:]:
                        if len(r) > fi and r[ti]:
                            try:
                                fulls.setdefault(qlabel(r[ti]), float(r[fi]))
                            except ValueError:
                                pass
        if not detail:
            return self.send_json({"error": "没有找到成绩明细表（需要有“姓名”列的那个文件）"}, 400)
        head = detail[0]
        ni = head.index("姓名")
        skip = {"姓名", "考号", "学号", "学校", "班级", "得分", "总分", "客观分", "主观分", "校名", "班名", "校次", "班次", "联考名"}
        qcols = [(i, qlabel(h)) for i, h in enumerate(head) if h and h not in skip and re.match(r"\d", h)]
        if not qcols:
            return self.send_json({"error": "成绩明细里没有找到题号列"}, 400)
        scores, seen = {}, set()
        for r in detail[1:]:
            name = r[ni] if len(r) > ni else ""
            if name in roster and name not in seen:
                seen.add(name)
                row = {}
                for i, qn in qcols:
                    try:
                        row[qn] = float(r[i]) if len(r) > i and r[i] != "" else None
                    except ValueError:
                        row[qn] = None
                scores[name] = row
        if not scores:
            return self.send_json({"error": f"表里的名字和{cls}名单一个都对不上"}, 400)
        questions = [qn for _, qn in qcols]
        full = {}
        for qn in questions:  # 有试卷分析就用它的满分，否则用全表最高分
            if qn in fulls:
                full[qn] = fulls[qn]
            else:
                vals = [float(r[i]) for r in detail[1:] for i, q2 in qcols if q2 == qn and len(r) > i and re.fullmatch(r"[\d.]+", r[i] or "")]
                full[qn] = max(vals) if vals else 0
        wrong = {qn: [n for n in roster if n in scores and scores[n].get(qn) is not None and scores[n][qn] < full[qn]]
                 for qn in questions}
        title = str(body.get("title") or "").strip()[:30] or f"智学网导入 {now_cn().strftime('%m-%d')}"
        with lock:
            item = {"id": next_id(quizzes), "class": cls, "date": today(), "source": "智学网", "title": title,
                    "questions": questions, "wrong": wrong, "scores": scores, "full": full, "rule": "lost",
                    "teacher": sender}
            quizzes.append(item)
            del quizzes[:-200]
            write_json(QUIZ_PATH, quizzes)
        self.send_json({"ok": True, "item": item, "matched": len(scores), "missing": [n for n in roster if n not in scores],
                        "full_source": "试卷分析" if fulls else "最高分"})

    # ---------- 班级管理 ----------
    def api_roster_save(self, body, sender):
        cls = re.sub(r"\s+", "", str(body.get("class") or ""))[:16]
        old = re.sub(r"\s+", "", str(body.get("old") or ""))[:16]
        names = []
        for n in body.get("names") or []:
            n = re.sub(r"\s+", "", str(n))[:10]
            if n and n not in names:
                names.append(n)
        if not cls:
            return self.send_json({"error": "请填写班级名称"}, 400)
        with lock:
            roster = read_json(ROSTER_PATH, {})
            # 新建班级可以先不填名单（让班主任扫码加入后自己导入）；已有的班不能清空名单
            if not names and (cls in load_students() or old):
                return self.send_json({"error": "请填写学生名单"}, 400)
            if cls.startswith("_"):
                return self.send_json({"error": "班级名称不能以 _ 开头"}, 400)
            if old and old != cls and cls in load_students():
                return self.send_json({"error": "已经有这个班级名了"}, 400)
            if old and old != cls and old not in load_students():
                return self.send_json({"error": "没有这个班级"}, 400)
            new = cls not in load_students() and not old
            if old and old != cls:  # 改班级名：旧名字标记删除
                roster.pop(old, None)
                roster.setdefault("_deleted", []).append(old)
            roster[cls] = [{"no": f"{i + 1:02d}", "name": n} for i, n in enumerate(names)]
            roster["_deleted"] = [c for c in roster.get("_deleted", []) if c != cls]
            write_json(ROSTER_PATH, roster)
            if new:  # 新班：生成本班的教室密钥
                users["class_keys"][cls] = secrets.token_urlsafe(12)
                users["aliases"].pop(cls, None)
                save_users()
            if old and old != cls:  # 改了班级名：所有东西跟着改；一体机还用旧名也能连上
                rename_class(old, cls)
        self.send_json({"ok": True, "class": cls, "count": len(names)})

    def api_roster_delete(self, body, sender):
        cls = str(body.get("class") or "")
        if cls not in load_students():
            return self.send_json({"error": "没有这个班级"}, 400)
        with lock:
            drop_class(cls)
        self.send_json({"ok": True})

    def api_roster_parse(self, body, sender):
        """从 Excel 里找出学生姓名（有"姓名"列就用它，否则取第一列中文名）"""
        import base64
        try:
            book = read_xlsx(base64.b64decode(str(body.get("data") or "").split(",", 1)[-1]))
        except Exception:
            return self.send_json({"error": "无法读取这个文件（请选 .xlsx 格式）"}, 400)
        names = []
        for rows in book.values():
            if not rows:
                continue
            head = rows[0]
            ci = head.index("姓名") if "姓名" in head else None
            for r in (rows[1:] if ci is not None else rows):
                cells = [r[ci]] if ci is not None and len(r) > ci else r
                for c in cells:
                    c = re.sub(r"\s+", "", c or "")
                    if re.fullmatch(r"[\u4e00-\u9fa5·]{2,6}", c) and c not in ("姓名", "学号", "班级", "性别"):
                        names.append(c)
                        break
            if names:
                break
        if not names:
            return self.send_json({"error": "没有在表格里找到学生姓名"}, 400)
        self.send_json({"ok": True, "names": list(dict.fromkeys(names))})

    # ---------- 桌面云 / 全校统一安装：按大屏网卡 MAC 自动认班 ----------
    def api_device_check(self, body):
        """一体机（通用安装模式）开机或解绑后上报本机网卡 MAC：已绑定直接返回班级和密钥；未绑定且管理员已开放绑定，返回可选班级列表"""
        if PUBLIC:
            return self.send_json({"error": "公共服务器不支持大屏 MAC 自动绑定"}, 404)
        mac = norm_mac(body.get("mac"))
        raw_macs = body.get("macs") if isinstance(body.get("macs"), list) else []
        macs = []
        for x in ([mac] if mac else []) + [norm_mac(m) for m in raw_macs[:8]]:
            if x and x not in macs:
                macs.append(x)
        if not macs:
            return self.send_json({"error": "未获取到有效的网卡 MAC 地址"}, 400)
        primary = macs[0]
        hostname = re.sub(r"[\x00-\x1f]", "", str(body.get("hostname") or "")).strip()[:40]
        with lock:
            students = load_students()
            for m in macs:
                b = devices["bindings"].get(m)
                if not b:
                    continue
                cls = resolve_class(b.get("class") or "")
                key = class_key(cls) if cls in students else None
                if cls in students and key:
                    if b.get("class") != cls or (hostname and b.get("hostname") != hostname):
                        b["class"] = cls
                        if hostname:
                            b["hostname"] = hostname
                        save_devices()
                    return self.send_json({"bound": True, "mac": m, "class": cls, "key": key})
                devices["bindings"].pop(m, None)  # 对应的班已不存在：清理失效绑定
                save_devices()
            is_open = bool(settings.get("device_bind_open"))
            all_classes = sorted(students.keys(), key=class_sort_key)
            if not is_open:
                return self.send_json({"bound": False, "open": False, "mac": primary, "classes": [],
                                       "message": "管理员暂未开放大屏选班绑定，请在工作台「班级管理」里开启后重试"})
            bound_classes = {resolve_class(b.get("class") or "") for b in devices["bindings"].values()}
            avail = [c for c in all_classes if c not in bound_classes]
            return self.send_json({"bound": False, "open": True, "mac": primary, "classes": avail,
                                   "total_classes": len(all_classes), "bound_count": len(all_classes) - len(avail)})

    def api_device_bind(self, body):
        """教室大屏选择班级后提交绑定（MAC <-> 班级 一对一）"""
        if PUBLIC:
            return self.send_json({"error": "公共服务器不支持大屏 MAC 自动绑定"}, 404)
        mac = norm_mac(body.get("mac"))
        cls = resolve_class(str(body.get("class") or "").strip())
        hostname = re.sub(r"[\x00-\x1f]", "", str(body.get("hostname") or "")).strip()[:40]
        if not mac:
            return self.send_json({"error": "网卡 MAC 地址无效"}, 400)
        if not cls:
            return self.send_json({"error": "请选择班级"}, 400)
        with lock:
            if not settings.get("device_bind_open"):
                return self.send_json({"error": "管理员已关闭大屏选班绑定，请先在工作台「班级管理」开启"}, 403)
            students = load_students()
            if cls not in students:
                return self.send_json({"error": f"找不到「{cls}」，请点刷新列表重试"}, 404)
            cur = devices["bindings"].get(mac)
            if cur:
                cur_cls = resolve_class(cur.get("class") or "")
                if cur_cls == cls:
                    return self.send_json({"ok": True, "bound": True, "mac": mac, "class": cls, "key": class_key(cls)})
                if cur_cls in students:
                    return self.send_json({"error": f"这台大屏已绑定「{cur_cls}」，请管理员先在工作台解绑"}, 409)
            other = next((m for m, b in devices["bindings"].items() if m != mac and resolve_class(b.get("class") or "") == cls), None)
            if other:
                return self.send_json({"error": f"「{cls}」已绑定其他大屏（{other}），请管理员先在工作台解绑"}, 409)
            devices["bindings"][mac] = {"class": cls, "hostname": hostname, "bound_at": time.time()}
            save_devices()
            key = class_key(cls)
        self.send_json({"ok": True, "bound": True, "mac": mac, "class": cls, "key": key})

    def api_device_toggle(self, body, sender):
        """管理员开启/关闭大屏自主选班绑定（已绑定的大屏不受影响）"""
        if PUBLIC:
            return self.send_json({"error": "公共服务器不支持此功能"}, 404)
        with lock:
            settings["device_bind_open"] = bool(body.get("open"))
            write_json(SETTINGS_PATH, settings)
        self.send_json({"ok": True, "open": settings["device_bind_open"]})

    def api_device_unbind(self, body, sender):
        """管理员解绑大屏（按 MAC、按班级、或全部解绑）；已运行的大屏会在 2 秒内自动退回待绑定状态"""
        if PUBLIC:
            return self.send_json({"error": "公共服务器不支持此功能"}, 404)
        with lock:
            removed = 0
            if body.get("all"):
                removed = len(devices["bindings"])
                devices["bindings"].clear()
            elif body.get("mac"):
                mac = norm_mac(body.get("mac"))
                if mac in devices["bindings"]:
                    devices["bindings"].pop(mac)
                    removed = 1
            elif body.get("class"):
                cls = resolve_class(str(body.get("class") or ""))
                drop = [m for m, b in devices["bindings"].items() if resolve_class(b.get("class") or "") == cls]
                for m in drop:
                    devices["bindings"].pop(m, None)
                    removed += 1
            if removed:
                save_devices()
        self.send_json({"ok": True, "removed": removed, "open": bool(settings.get("device_bind_open"))})

    def api_quiz_delete(self, body, sender):
        with lock:
            quizzes[:] = [x for x in quizzes if x["id"] != body.get("id")]
            write_json(QUIZ_PATH, quizzes)
        self.send_json({"ok": True})

    # ---------- 考勤 ----------
    def api_attend_save(self, body):
        cls = str(body.get("class") or "")
        session = str(body.get("session") or "")
        if session not in SESSIONS or not cls:
            return self.send_json({"error": "请选择时段"}, 400)
        if cls not in load_students():
            return self.send_json({"error": f"没有「{cls}」这个班"}, 404)
        roster = {s["name"] for s in load_students().get(cls, [])}
        marks = {n: t for n, t in (body.get("marks") or {}).items() if n in roster and t in ATTEND_TYPES}
        with lock:
            d = today()
            rec = next((a for a in attends if a["class"] == cls and a["date"] == d and a["session"] == session), None)
            if not rec:
                rec = {"id": next_id(attends), "class": cls, "date": d, "session": session}
                attends.append(rec)
            rec.update({"marks": marks, "ts": time.time()})
            cutoff = (now_cn() - timedelta(days=400)).strftime("%Y-%m-%d")
            attends[:] = [a for a in attends if a["date"] >= cutoff]
            write_json(ATTEND_PATH, attends)
        self.send_json({"ok": True, "marks": marks})

    def api_attend_start(self, body, sender):
        cls = str(body.get("class") or "")
        self.create_msg([cls], "考勤登记\n请班长点击下方按钮，登记" + current_session(cls=cls) + "的考勤", sender,
                        voice=False, urgent=False, defer=body.get("defer", True), kind="attend", data={"open": "attend"},
                        expire_at=time.time() + 3 * 3600)
        self.send_json({"ok": True})

    def api_attend_delete(self, body, sender):
        with lock:
            attends[:] = [a for a in attends if a["id"] != body.get("id")]
            write_json(ATTEND_PATH, attends)
        self.send_json({"ok": True})

    # ---------- 积分 / 值日 ----------
    def api_points_add(self, body, sender):
        cls = str(body.get("class") or "")
        names = [str(n)[:20] for n in body.get("names") or [] if str(n).strip()][:60]
        try:
            delta = max(-20, min(20, int(body.get("delta") or 0)))
        except (TypeError, ValueError):
            delta = 0
        reason = str(body.get("reason") or "").strip()[:30]
        if not cls or not names or not delta:
            return self.send_json({"error": "请选择学生和分数"}, 400)
        now = time.time()
        with lock:
            nid = next_id(points)
            for i, n in enumerate(names):
                points.append({"id": nid + i, "class": cls, "name": n, "delta": delta, "reason": reason,
                               "teacher": sender, "ts": now})
            write_json(POINTS_PATH, points)
        if body.get("praise") and delta > 0:  # 只有加分才会上大屏表扬
            who = "、".join(names) if len(names) <= 6 else "、".join(names[:6]) + f" 等 {len(names)} 位同学"
            text = f"表扬 {who}\n{reason or '表现优秀'} +{delta}"
            self.create_msg([cls], text, sender, voice=body.get("voice"), urgent=False, defer=True, voice_name=body.get("voice_name"),
                            kind="praise", say=f"表扬{'、'.join(names[:6])}同学，{reason or '表现优秀'}", repeat=1)
        self.send_json({"ok": True})

    def api_points_delete(self, body, sender):
        with lock:
            points[:] = [p for p in points if p["id"] != body.get("id")]
            write_json(POINTS_PATH, points)
        self.send_json({"ok": True})

    def api_duty_push(self, body, sender):
        cls = str(body.get("class") or "")
        if not duty_today(cls):
            return self.send_json({"error": "今天没有安排值日"}, 400)
        self.create_msg([cls], "今日值日：" + duty_text(cls), sender, voice=body.get("voice"), urgent=False, defer=body.get("defer", True),
                        voice_name=body.get("voice_name"), kind="reminder", say="今日值日：" + duty_text(cls), repeat=1)
        self.send_json({"ok": True})

    # ---------- 大屏设置 ----------
    def api_settings(self, body, sender):
        cls = str(body.get("class") or "")
        with lock:
            own = settings.setdefault("cls_cfg", {}).setdefault(cls, {})
            if "countdowns" in body:
                own["countdowns"] = [{"name": str(c.get("name", ""))[:12], "date": str(c.get("date", ""))[:10]}
                                     for c in body["countdowns"] if c.get("name") and c.get("date")][:4]
            if isinstance(body.get("standby"), dict):
                sb = body["standby"]
                own["standby"] = {"enabled": bool(sb.get("enabled")), "idle_minutes": max(1, min(60, int(sb.get("idle_minutes") or 5))),
                                  "mode": sb.get("mode") if sb.get("mode") in ("evening", "breaks", "both") else "evening"}
            if isinstance(body.get("duty"), dict):
                clean = {}
                for day, items in body["duty"].items():
                    if str(day) in "1234567" and len(str(day)) == 1 and isinstance(items, list):
                        clean[str(day)] = [{"name": str(i.get("name", ""))[:20], "task": str(i.get("task", ""))[:10]}
                                           for i in items if isinstance(i, dict) and i.get("name")][:30]
                settings.setdefault("duty", {})[cls] = clean
            if isinstance(body.get("quotes"), dict):
                qd = body["quotes"]
                own["quotes"] = {"enabled": bool(qd.get("enabled", True)), "builtin": bool(qd.get("builtin", True)),
                                 "custom": [str(x).strip()[:80] for x in qd.get("custom") or [] if str(x).strip()][:100]}
            if "board_points" in body:
                own["board_points"] = bool(body["board_points"])
            if "tools_tab" in body:
                own["tools_tab"] = bool(body["tools_tab"])
            if "desk_board" in body:  # 桌面版看板：一直贴在桌面右侧
                own["desk_board"] = bool(body["desk_board"])
            if isinstance(body.get("eve_plan"), list):  # 晚自习时间安排：每天都用这一份
                plan = []
                for it in body["eve_plan"]:
                    a, e, t = (str(it.get(k, "")).strip() for k in ("start", "end", "text")) if isinstance(it, dict) else ("", "", "")
                    if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", a) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", e) and a < e and t:
                        plan.append({"start": a, "end": e, "text": t[:12]})
                own["eve_plan"] = sorted(plan, key=lambda x: x["start"])[:6]
            if isinstance(body.get("prep"), dict):
                pp = body["prep"]
                own["prep"] = {"enabled": bool(pp.get("enabled", True)), "voice": bool(pp.get("voice")),
                               "after": max(1, min(10, int(pp.get("after") or 3))),
                               "default": str(pp.get("default") or "课本、学案").strip()[:40],
                               "items": {str(k).strip()[:6]: str(v).strip()[:40] for k, v in (pp.get("items") or {}).items() if str(k).strip() and str(v).strip()}}
            if isinstance(body.get("morning"), dict):
                mo = body["morning"]
                ok = lambda t: bool(re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(t or "")))
                own["morning"] = {"start": mo["start"], "end": mo["end"]} if ok(mo.get("start")) and ok(mo.get("end")) and mo["start"] < mo["end"] else {}
            if isinstance(body.get("timetable"), dict):
                tt = {}
                for day, rows in body["timetable"].items():
                    if str(day) in "1234567" and len(str(day)) == 1 and isinstance(rows, list):
                        tt[str(day)] = [{"subject": str(r.get("subject", "") if isinstance(r, dict) else r).strip()[:6]}
                                        for r in rows[:20]]  # 只记科目，不存老师姓名
                settings.setdefault("timetable", {})[cls] = tt
            if isinstance(body.get("subjects"), list):
                subs = []
                for x in body["subjects"]:
                    x = str(x).strip()[:6]
                    if x and x not in subs:
                        subs.append(x)
                if subs:
                    own["subjects"] = subs[:16]
                else:
                    own.pop("subjects", None)  # 一个都不勾 = 恢复全校科目
            if body.get("screen_font") in ("misans", "wenkai", "system"):
                own["screen_font"] = body["screen_font"]
            if "notice" in body:
                settings.setdefault("notices", {})[cls] = str(body["notice"])[:300]
            write_json(SETTINGS_PATH, settings)
        self.send_json({"ok": True})

    def api_recall(self, body):
        raw = body.get("ids") if isinstance(body.get("ids"), list) else [body.get("id")]
        ids = {i for i in raw if type(i) is int}
        with lock:
            for m in messages:
                if m["id"] in ids:
                    m["recalled"] = True
            save_messages()
        self.send_json({"ok": True})

    def api_poll(self, q):
        cls = (q.get("class") or [""])[0]
        if not PUBLIC and q.get("mac"):  # 桌面云按 MAC 自动绑定的大屏：管理员解绑或改绑后马上通知重绑
            mac = norm_mac(q["mac"][0])
            b = devices["bindings"].get(mac) if mac else None
            if not b or resolve_class(b.get("class") or "") != cls:
                return self.send_json({"rebind": True})
        if not self.classroom_ok(q, cls):
            return self.send_json({"error": "密钥错误"}, 403)
        now = time.time()
        last_seen[cls] = now
        st = period_status(cls=cls)
        out, changed = [], False
        rv = (q.get("rv") or ["0"])[0]
        rv = int(rv) if (rv.isascii() and rv.isdigit()) else 0  # 接收端版本：3 远程卸载，4 文件，5 听力，6 可靠 ack 控制指令
        acap_s = (q.get("acap") or [""])[0]
        in_exam = exam_now(cls, now)
        with lock:
            screen_rv[cls] = {"rv": rv, "at": now}
            if acap_s in ("0", "1", "2"):
                av = int(acap_s)
                screen_audio_cap[cls] = {"can_mp3": av >= 2, "can_seek": av >= 2, "can_wav": av >= 1, "at": now}
            for m in messages:
                if m["class"] != cls or msg_status(m, now) not in ("pending", "delivered"):
                    continue
                if in_exam and not m["urgent"] and m.get("kind") not in ("uninstall", "standby", "exam_audio"):
                    continue  # 考试期间先不显示（考完还没过期的会再弹出来）
                if m.get("kind") == "file" and rv < 4:  # 旧版接收端不会下载文件：等它 10 分钟内自动更新后再发
                    continue
                if m.get("kind") == "exam_audio":
                    if PUBLIC or rv < 6:  # 公共模式彻底拦截；旧版接收端（rv<6）不支持可靠回执，等其自动升级后再发，绝不提前确认
                        continue
                if m.get("kind") == "uninstall":
                    if rv < 6:  # 旧版接收端（rv<6）不支持卸载可靠回执，绝不提前确认或提前解绑 MAC；保留任务等其自动升级后再下发
                        continue
                if not m["delivered_at"]:
                    m["delivered_at"] = now
                    changed = True
                popup = m["urgent"] or not st["in_class"]
                say = m["voice"] and (popup or m.get("loud"))  # loud：老师上课时在工作台特意打开了"播报"，角落小提示也读出来
                out.append({"id": m["id"], "text": m["text"], "sender": m["sender"],
                            "kind": m.get("kind", "call"), "data": m.get("data"),
                            "time": datetime.fromtimestamp(m["created"], CN).strftime("%H:%M"),
                            "popup": popup, "voice": bool(say),
                            "audio": "/audio/" + m["audio"] if m.get("audio") else None,
                            "speak": m.get("speak") if say else None})  # 没有自然人声时，Windows 接收端用本机语音读这段
            if changed:
                save_messages()
        req = shot_req.get(cls, 0)
        shot = now - req < 20 and req > (shots.get(cls) or {}).get("at", 0)  # 有老师要看大屏，还没传上来
        self.send_json({"messages": out, "in_class": st["in_class"], "server_time": now, "exam_rev": exam_rev[0], "shot": shot})

    def api_board(self, q):
        """一体机待机页的数据"""
        cls = (q.get("class") or [""])[0]
        if not self.classroom_ok(q, cls):
            return self.send_json({"error": "密钥错误"}, 403)
        dt = now_cn()
        items = homework_of(cls)
        days = []
        cc = ccfg(cls)
        for c in cc["countdowns"]:
            try:
                left = (datetime.strptime(c["date"], "%Y-%m-%d").date() - dt.date()).days
                if left >= 0:
                    days.append({"name": c["name"], "days": left})
            except ValueError:
                pass
        st = period_status(dt, cls)
        nxt = next((p for p in schedule_of(cls)["periods"] if p[0] > dt.strftime("%H:%M")), None)
        in_eve, session = evening_info(dt, cls)
        mode = (cc["standby"] or {}).get("mode", "evening")
        window = in_eve if mode == "evening" else (not st["in_class"] and not in_eve) if mode == "breaks" else (in_eve or not st["in_class"])
        self.send_json({"class": cls, "date": dt.strftime("%m月%d日"), "weekday": "一二三四五六日"[dt.weekday()],
                        "period": st, "next_period": nxt, "homework": items,
                        "total_minutes": sum(h["minutes"] for h in items), "countdowns": days,
                        "notice": settings.get("notices", {}).get(cls, ""), "server_time": time.time(),
                        "notes": [{"by": note_by(n), "text": n["text"]} for n in notes_of(cls)],  # 各科老师的大屏留言
                        "standby": {**{"mode": "evening"}, **(cc["standby"] or {})}, "screen_font": cc["screen_font"] if q.get("fv") else "system",  # 旧版接收端认不出中文字体名会反复重启，只给新版发字体
                        "standby_window": window, "evening": in_eve, "session": session, "eve_plan": cc["eve_plan"], "tools_tab": cc["tools_tab"], "desk_board": cc["desk_board"],
                        "classes_today": [c for c in today_classes(cls, dt)[0] if not c["evening"]],  # 白天看板只显示白天的课，晚自习的课在夜空看板上
                        "class_now": today_classes(cls, dt)[1], "class_next": today_classes(cls, dt)[2],
                        "duty": duty_today(cls, dt), "quote": pick_quote(cls),
                        "morning": {"start": morning_window(cls)[0], "end": morning_window(cls)[1], "date": dt.strftime("%Y-%m-%d"),
                                    "items": [{"subject": m["subject"], "content": m["content"]} for m in morning_of(cls)]},
                        "exams": [{**x, "voice": exam_voice()} for x in exam_sessions(cls) if x["hide_at"] > time.time()],
                        "listen_tasks": listen_tasks_for_board(cls),
                        "exam_audio_prefetch": exam_audio_prefetch_for(cls),
                        "stars": sorted(([n, v] for n, v in point_totals(cls, week_start_ts(dt)).items() if v > 0),
                                        key=lambda x: -x[1])[:5] if cc["board_points"] else []})

    def api_ack(self, body):
        mid = body.get("id")
        now = time.time()
        resp = {"ok": False, "status": "not_found", "cancelled": True}
        with lock:
            m = next((x for x in messages if type(mid) is int and x["id"] == mid), None)
            if m:
                if m.get("recalled"):
                    # 管理员已先一步撤销：绝不标记确认、绝不解绑 MAC，并明确告知大屏放弃卸载
                    resp = {"ok": True, "status": "cancelled", "cancelled": True}
                elif m.get("acked_at"):
                    # 幂等回执：前一次回执已落库但响应丢包时，重发回执返回同样成功结果，不重复解绑
                    resp = {"ok": True, "status": "acked", "cancelled": False, "already_acked": True}
                elif m.get("kind") == "uninstall" and m.get("expire_at") and now > m["expire_at"]:
                    resp = {"ok": True, "status": "expired", "cancelled": True}
                else:
                    m["acked_at"] = now
                    # 可靠卸载回执：只有在任务仍有效且未撤销时，才在此解绑 VOI MAC 并结案
                    if m.get("kind") == "uninstall" and (m.get("data") or {}).get("unbind_mac"):
                        cls = m["class"]
                        drop = [mac_ for mac_, b_ in devices["bindings"].items() if resolve_class(b_.get("class") or "") == cls]
                        for mac_ in drop:
                            devices["bindings"].pop(mac_, None)
                        if drop:
                            save_devices()
                    save_messages()
                    resp = {"ok": True, "status": "acked", "cancelled": False}
        self.send_json(resp)

    def api_recent(self, q=None):
        s = self.sess()
        cur_sid = self.sid()
        admin = is_admin(s)
        mine = set(self.my_classes())
        qcls = ((q or {}).get("class") or [""])[0]
        now = time.time()
        with lock:
            all_mine = [m for m in messages if m["class"] in mine and m.get("kind") != "exam_audio"]
            top_all = list(reversed(all_mine[-40:]))
            if qcls and qcls in mine:
                top_cls = list(reversed([m for m in all_mine if m["class"] == qcls][-40:]))
                seen = {m["id"] for m in top_cls}
                active = sorted(top_cls + [m for m in top_all if m["id"] not in seen], key=lambda m: m["created"], reverse=True)
            else:
                active = top_all
            recent = []
            for m in active:
                is_mine = self.is_msg_owner(m, s)
                # 送达回执弹窗与提示音：严格只对“本账号在本登录设备主动发出”的消息触发，彻底杜绝串人
                notify_mine = bool(s and m.get("sender_uid") == s["uid"] and m.get("sender_sid") and m.get("sender_sid") == cur_sid)
                recent.append({
                    "id": m["id"], "class": m["class"], "text": m["text"], "sender": m["sender"],
                    "mine": is_mine, "notify_mine": notify_mine, "can_recall": bool(admin or is_mine),
                    "time": datetime.fromtimestamp(m["created"], CN).strftime("%m-%d %H:%M"),
                    "deliver": datetime.fromtimestamp(m["deliver_at"], CN).strftime("%H:%M"),
                    "status": msg_status(m, now), "kind": m.get("kind", "call"), "who": m.get("who"),
                    "ack_secs": int(m["acked_at"] - (m.get("delivered_at") or m["deliver_at"])) if m.get("acked_at") else None,
                    "delivered_ago": int(now - m["delivered_at"]) if m.get("delivered_at") else None,
                })
        online = {c: now - last_seen.get(c, 0) < 15 for c in mine}
        safe_cls = qcls if (qcls and qcls in mine) else ""
        st = period_status(cls=safe_cls or None)
        self.send_json({"messages": recent, "online": online, "period": st,
                        "now": now_cn().strftime("%H:%M"),
                        "exam": (lambda x: {"subject": x["subject"], "start": x["start"], "end": x["end"], "id": x["exam_id"], "idx": x["idx"]} if x else None)(
                            exam_now(safe_cls) if safe_cls else None)})


def re_hm(v):
    try:
        datetime.strptime(v, "%H:%M")
        return len(v) == 5
    except ValueError:
        return False


def lan_ips():
    """这台电脑在局域网里的地址（第一个是默认网卡的；学校没有外网也能找到）"""
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # UDP 不会真的发包，只是让系统选出默认网卡
        ips.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    ips = [ip for ip in ips if not ip.startswith(("127.", "169.254.", "0."))]

    def private(ip):  # 学校内网地址排前面（代理软件的虚拟网卡如 198.18.x.x 排后面）
        a = [int(x) for x in ip.split(".")]
        return a[0] == 10 or (a[0] == 192 and a[1] == 168) or (a[0] == 172 and 16 <= a[1] <= 31)
    ips.sort(key=lambda ip: not private(ip))
    return ips or ["127.0.0.1"]


if __name__ == "__main__":
    port = int(os.environ.get("PORT") or CFG["port"])
    migrate_notices()
    try:
        httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    except OSError as e:
        print(f"\n× 端口 {port} 被占用，启动失败（{e}）")
        print("  可能是班级助手已经开着了（看看任务栏里有没有它的窗口），")
        print(f"  或者别的程序占了这个端口：用记事本打开 config.json，把 \"port\": {port} 改成别的数字（如 8090）再启动。")
        raise SystemExit(3)  # 3：启动脚本看到它就不自动重启
    threading.Thread(target=reminder_loop, daemon=True).start()
    threading.Thread(target=backup_loop, daemon=True).start()
    if os.name == "nt":  # 局域网版跑在学校 Windows 电脑上
        import ctypes
        try:
            k32 = ctypes.windll.kernel32
            k32.SetThreadExecutionState(0x80000000 | 0x00000001)  # 开着班级助手时电脑不自动睡眠（屏幕照样可以关）
            h, mode = k32.GetStdHandle(-10), ctypes.c_uint32()
            if k32.GetConsoleMode(h, ctypes.byref(mode)):  # 关掉"快速编辑"：误点黑窗口会让程序卡住
                k32.SetConsoleMode(h, (mode.value & ~0x0040) | 0x0080)
        except Exception:
            pass
    suffix = "" if port == 80 else f":{port}"
    print("=" * 56)
    print(" 班级助手 已启动（这个窗口不要关，关了就停了）")
    print("=" * 56)
    if TC:
        print(f" 语音：腾讯云语音合成（每天最多 {int(TC.get('daily_chars') or 20000)} 字）")
    elif EDGE:
        print(f" 语音：自然人声（{CFG['tts_voice']}，需要联网）")
    elif LOCAL_VOICES:
        print(f" 语音：{LOCAL_VOICES[0]['name']}，不用联网")
    else:
        print(" 语音：这台电脑没有中文语音，大屏只响提示音（Windows 一体机会用自己的语音朗读）")
    ips = lan_ips()
    print(" 工作台地址（老师手机连学校 Wi-Fi 后打开）：")
    for ip in ips:
        print(f"     http://{ip}{suffix}/")
    if ips == ["127.0.0.1"]:
        print("     （没找到局域网地址：请检查这台电脑有没有连上网线或 Wi-Fi）")
    print(" 管理员密码：     已在工作台修改（config.json 里的不再有效）" if settings.get("admin_pw") else f" 管理员密码：     {CFG['teacher_password']}")
    print(" 提示：这台电脑要一直开着；最好请网管把它的 IP 地址固定下来，")
    print("       不然地址变了，一体机和老师手机就连不上了。")
    print(" 按 Ctrl+C 停止")
    print("=" * 56)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
