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
        out = [n.strip() for n in names.splitlines() if n.strip()]
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
        with open(CONFIG_PATH, encoding="utf-8") as f:
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
exams = read_json(EXAM_PATH, [])        # 考试：{id, name, classes, sessions: [{subject, date, start, end}], notice, creator, uid, created, ended: {"班级|场次": 时间戳}}
exam_rev = [time.time()]                 # 考试安排改了就变：一体机轮询时看到变化马上重新拉看板
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
login_fails = {}                         # ip -> [失败次数, 首次失败时间]
reg_counts = {}                          # ip -> [日期, 今天注册次数]
send_log = {}                            # 老师 uid -> 最近一小时上大屏的时间戳（公共模式限频）
SEND_LIMIT, HEAD_LIMIT, REMINDER_LIMIT, TEXT_LIMIT = 60, 3, 30, 200  # 公共模式的限制


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
        ps = os.path.join(AUDIO_DIR, "sapi.ps1")
        if not os.path.exists(ps):
            with open(ps, "w", encoding="utf-8-sig") as f:
                f.write(SAPI_PS)
        txt = path[:-4] + ".txt"
        with open(txt, "w", encoding="utf-8") as f:
            f.write(say)
        run_quiet(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps, voice[6:], tmp, txt], 60)
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


make_chime()


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


def save_messages():
    """每个班各留最近 200 条"""
    seen, keep = {}, []
    for m in reversed(messages):
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
    if (m.get("expire_at") and now > m["expire_at"]) or now - m["deliver_at"] > CFG["expire_minutes"] * 60:
        return "expired"
    return "delivered" if m.get("delivered_at") else "pending"


def next_id(items):
    return max((x["id"] for x in items), default=0) + 1


def today():
    return now_cn().strftime("%Y-%m-%d")


def create_messages(classes, text, sender, voice=False, urgent=False, defer=False, voice_name=None,
                    kind="call", data=None, expire_at=None, say=None, repeat=None, who=None, loud=False, send_at=None):
    """所有推送到教室大屏的内容（喊话、作业、提醒、课堂工具）都走这里；send_at：老师定时发送的时间"""
    now = time.time()
    def deliver_time(cls):  # 上课时间的普通消息，等到这个班下课再送（晚自习不等：值班老师要马上看到）
        ex = exam_now(cls, send_at or now) if not urgent and kind not in ("uninstall", "standby") else None
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
            messages.append({"id": nid, "class": cls, "text": text, "sender": sender, "kind": kind,
                             "data": data, "voice": bool(voice), "urgent": bool(urgent), "audio": audio,
                             "created": now, "deliver_at": at, "expire_at": expire_at,
                             "delivered_at": None, "acked_at": None, "who": who, "loud": bool(loud and voice), "send_at": send_at,
                             "speak": "。".join([say or text] * max(1, int(repeat or CFG["tts_repeat"]))) if voice else None})
            if at > now and not urgent and exam_now(cls, send_at or now):
                messages[-1]["exam_hold"] = True  # 因为考试压住的：考试提前结束、删除时要放出来
            ids.append(nid)
            nid += 1
        save_messages()
    return ids, deliver_at > now


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


def exam_sessions(cls, date=None):
    """这个班某天（默认今天）的各场考试，按开考时间排好；每场带上大屏要用的时间戳"""
    date = date or today()
    out = []
    for e in exams:
        if cls not in e["classes"]:
            continue
        order = sorted(range(len(e["sessions"])), key=lambda i: (e["sessions"][i]["date"], e["sessions"][i]["start"]))
        for n, i in enumerate(order):
            x = e["sessions"][i]
            if x["date"] != date:
                continue
            ts = lambda hm: datetime.combine(datetime.strptime(date, "%Y-%m-%d").date(), datetime.strptime(hm, "%H:%M").time(), CN).timestamp()
            start, end = ts(x["start"]), ts(x["end"])
            ended = (e.get("ended") or {}).get(f"{cls}|{i}")
            nx = next((e["sessions"][j] for j in order[n + 1:]), None)
            out.append({"key": f"{e['id']}-{i}", "exam_id": e["id"], "idx": i, "name": e["name"], "no": n + 1, "total": len(order),
                        "subject": x["subject"], "date": date, "start": x["start"], "end": x["end"], "start_ts": start, "end_ts": end,
                        "show_from": start - EXAM_BEFORE, "hide_at": min(end + EXAM_AFTER, ended) if ended else end + EXAM_AFTER,
                        "ended": bool(ended), "notice": [l for l in (e.get("notice") or "").split("\n") if l.strip()],
                        "next": {"subject": nx["subject"], "date": nx["date"], "start": nx["start"], "end": nx["end"]} if nx else None})
    return sorted(out, key=lambda x: x["start_ts"])


def exam_now(cls, now=None):
    """这个班现在是不是在考试（开考前 15 分钟到结束后 5 分钟），是就返回那一场"""
    now = now or time.time()
    return next((x for x in exam_sessions(cls) if x["show_from"] <= now < x["hide_at"]), None)


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


def class_key(cls):
    """这个班的教室密钥：有独立密钥用它；没有的话本校服务器用全局密钥，公共服务器没有"""
    return users["class_keys"].get(cls) or (None if PUBLIC else CFG["classroom_key"])


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
    purge_files(cls)


def ccfg(cls):
    """某个班的大屏设置：默认值 <- 旧的全局设置 <- 这个班自己的设置"""
    q = settings.get("quotes", {})
    base = {"countdowns": settings.get("countdowns", [{"name": "高考", "date": "2027-06-07"}]),
            "standby": settings.get("standby", {"enabled": True, "idle_minutes": 5}),
            "screen_font": settings.get("screen_font", "wenkai"), "board_points": settings.get("board_points", True), "eve_plan": [], "tools_tab": True,
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
        if rel.startswith(("data/audio", "data/files/")) or re.search(r"data/images/.+_\d+x\d+\.(png|gif)$", rel) or rel.endswith(".tmp"):
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
    """每天北京时间 3 点以后，如果今天还没备份就备份一次"""
    while True:
        try:
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
                for cls in r["classes"]:
                    create_messages([cls], r["text"].replace("{值日}", duty_text(cls)),
                                    r.get("creator", "定时提醒"), voice=r.get("voice"),
                                    urgent=r.get("urgent"), voice_name=r.get("voice_name"), kind="reminder")
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
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            limit = 12_000_000 if self.path.startswith(("/api/upload", "/api/quiz/import", "/api/roster/parse")) else 100_000
            return json.loads(self.rfile.read(min(length, limit)).decode("utf-8"))
        except ValueError:
            return {}

    def client_ip(self):
        ip = self.client_address[0]  # 服务器直接对外，只有本机反向代理转发的才认 X-Forwarded-For（否则谁都能伪造，限频就失效了）
        return (self.headers.get("X-Forwarded-For") or ip).split(",")[0].strip() if ip in ("127.0.0.1", "::1") else ip

    def sess(self):
        token = (self.headers.get("Authorization") or "").replace("Bearer ", "")
        s = sessions.get(token)
        return s if s and s.get("uid") and s["exp"] > time.time() else None

    def teacher(self):
        s = self.sess()
        return s["name"] if s else None

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

    def my_classes(self):
        return [c for c in load_students() if self.role(c)]

    def deny(self, level="teacher"):
        return self.send_json({"error": "只能删除自己发的内容" if level == "own" else "这个操作只有主管理员（用管理员密码登录）可以做" if level == "owner" else "这个操作只有班主任可以做" if level == "head" else
                               "管理员才能做这个操作" if level == "admin" else "你不是这个班的老师"}, 403)

    def post_check(self, path, body):
        """POST 接口的权限检查：返回 None 表示通过，否则返回 (级别) 表示拒绝"""
        s = self.sess()
        admin, owner = is_admin(s), is_owner(s)
        cls_of = lambda items, key="id": next((x["class"] for x in items if x["id"] == body.get(key)), None)
        teacher_level = {"/api/board/show", "/api/homework/add", "/api/morning/add", "/api/notes/add", "/api/homework/push", "/api/collect/start", "/api/collect/remind",
                         "/api/tools/timer", "/api/tools/pick", "/api/image/push", "/api/quiz/import", "/api/quiz/save"}
        head_level = {"/api/screen/shot", "/api/points/add", "/api/duty/push", "/api/attend/start", "/api/settings", "/api/classroom/uninstall"}
        if path in teacher_level:
            return None if self.allowed(str(body.get("class") or ""), "teacher") else "teacher"
        if path in head_level:
            return None if self.allowed(str(body.get("class") or ""), "head") else "head"
        if path == "/api/send":
            return None if body.get("classes") and all(self.allowed(str(c), "teacher") for c in body["classes"]) else "teacher"
        if path == "/api/recall":
            c = cls_of(messages)
            return None if c and self.allowed(c, "teacher") else "teacher"
        if path == "/api/notes/delete":  # 自己的留言自己删；班主任能删本班所有留言
            n = next((x for x in notes if x["id"] == body.get("id")), None)
            return None if n and (self.allowed(n["class"], "head") or (s and n.get("uid") == s["uid"])) else "own"
        if path == "/api/files/delete":  # 自己发的自己删；班主任能删本班全部
            f = next((x for x in files if x["id"] == body.get("id")), None)
            return None if f and (self.allowed(f["class"], "head") or (s and f.get("uid") == s["uid"])) else "own"
        if path in ("/api/exams/save", "/api/exams/delete"):  # 管理员可以一次发多个班；班主任只能管自己的班
            old = next((e for e in exams if e["id"] == body.get("id")), None)
            want = [str(c) for c in (body.get("classes") or [])] if path.endswith("save") else []
            return None if all(self.allowed(c, "head") for c in want + (old["classes"] if old else [])) else "head"
        if path == "/api/exams/end":  # 本班老师（如监考老师）都能提前结束
            return None if self.allowed(str(body.get("class") or ""), "teacher") else "teacher"
        if path == "/api/morning/delete":
            c = cls_of(mornings)
            return None if c and self.allowed(c, "teacher") else "teacher"
        if path in ("/api/homework/delete", "/api/quiz/delete"):
            items = homework if "homework" in path else quizzes
            it = next((x for x in items if x["id"] == body.get("id")), None)
            if not it:
                return None
            own = it.get("teacher") == s["name"]
            return None if self.allowed(it["class"], "head") or (own and self.allowed(it["class"], "teacher")) else "head"
        if path in ("/api/reminders/save", "/api/reminders/delete"):
            r = next((x for x in reminders if x["id"] == body.get("id")), None)
            if r:  # 改/删已有提醒：自己建的，或者是这个班的班主任
                own = r.get("creator") == s["name"]
                if not all(self.allowed(c, "head") or (own and self.allowed(c, "teacher")) for c in r["classes"]):
                    return "head"
            if path.endswith("save"):
                return None if body.get("classes") and all(self.allowed(str(c), "teacher") for c in body["classes"]) else "teacher"
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
        if path == "/api/schedule":  # 全校作息只有管理员能改；本班作息班主任也能改
            if body.get("scope") == "class":
                return None if self.allowed(str(body.get("class") or ""), "head") else "head"
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
            return len(body.get("classes") or [])
        if path in ("/api/homework/add", "/api/points/add"):
            return 1 if body.get("push") or body.get("praise") else 0
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
                body = body.replace(b"'__CLASS__'", f"'{ps('c')}'".encode()).replace(b"'__KEY__'", f"'{ps('k')}'".encode())
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
            return self.send_file("register.html") if PUBLIC else self.send_json({"error": "not found"}, 404)
        if u.path == "/api/register/info":
            if not PUBLIC:
                return self.send_json({"error": "not found"}, 404)
            s = self.sess()
            if not is_owner(s):
                return self.deny("owner")
            return self.send_json({"url": f"http://{self.host()}/register", "code": settings.get("register_code", ""), "paused": bool(settings.get("register_paused")),
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
                                   "me": s["name"], "mine": None, "public": PUBLIC,
                                   "no_schedule": [c for c in mine if c not in settings.get("class_schedule", {})] if PUBLIC else [],
                                   "places": CFG["places"], "voices": VOICES,
                                   "default_voice": default_voice(), "subjects": CFG["subjects"],
                                   "class_subjects": {c: subjects_of(c) for c in mine},
                                   "homework_limit": CFG["homework_limit_minutes"]})
        if not self.teacher():
            return self.send_json({"error": "请先登录"}, 401)
        qcls = (q.get("class") or [""])[0]
        if u.path in ("/api/homework", "/api/quiz", "/api/morning", "/api/notes", "/api/files", "/api/exams") and not self.allowed(qcls, "teacher"):
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
            return self.send_json({**schedule_of(qcls), "own": bool(own), "school": school_schedule(),
                                   "admin": is_admin(self.sess()), "can_class": bool(qcls) and self.allowed(qcls, "head")})
        if u.path == "/api/me":
            s = self.sess()
            u_ = users["users"].get(s["uid"])
            link = f"http://{self.host()}/login?u={s['uid']}&k={u_['secret']}" if u_ else None
            return self.send_json({"name": s["name"], "admin": is_admin(s), "owner": is_owner(s), "login_url": link})
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
            items = []
            for e in exams:
                if qcls not in e["classes"] or max(x["date"] for x in e["sessions"]) < week_ago:
                    continue  # 只列这个班的、还没考完的（考完一周内的也留着看）
                live = {x["idx"]: x for x in exam_sessions(qcls) if x["exam_id"] == e["id"]}
                items.append({"id": e["id"], "name": e["name"], "classes": e["classes"], "notice": e.get("notice", ""), "creator": e.get("creator", ""),
                              "can_edit": all(self.allowed(c, "head") for c in e["classes"]),
                              "sessions": [{**x, "idx": i, "state": ("ended" if (e.get("ended") or {}).get(f"{qcls}|{i}") else
                                                                     "live" if i in live and live[i]["show_from"] <= now < live[i]["hide_at"] else
                                                                     "done" if x["date"] < d or (i in live and now >= live[i]["hide_at"]) else "todo")}
                                           for i, x in enumerate(e["sessions"])]})
            items.sort(key=lambda e: min((x["date"], x["start"]) for x in e["sessions"]))
            return self.send_json({"items": items, "default_notice": EXAM_NOTICE})
        if u.path == "/api/morning":
            d1 = today()
            d2 = next_school_day(qcls, d1)
            start, end = morning_window(qcls)
            return self.send_json({"days": [{"date": d, "items": morning_of(qcls, d)} for d in (d1, d2)],
                                   "start": start, "end": end, "custom": bool((ccfg(qcls).get("morning") or {}).get("start"))})
        if u.path == "/api/homework":
            cls = (q.get("class") or [""])[0]
            day = (q.get("date") or [None])[0] or today()
            items = [{**h, "due_date": due_date(h), "collect": collect_of(cls, h["id"])} for h in homework_of(cls, day)]
            due = [{**h, "due_date": due_date(h), "collect": collect_of(cls, h["id"])} for h in homework_due(cls, day) if h["date"] != day]
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
                                   "board_points": c["board_points"], "eve_plan": c["eve_plan"], "tools_tab": c["tools_tab"],
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
            cmd = f"wget -qO- {self.host()}/kylin/install.sh | bash -s {{班级}} {{密钥}}"
            win = ("powershell -nop -c \"iex ([Text.Encoding]::UTF8.GetString((New-Object Net.WebClient).DownloadData("  # 不能带 $：在 PowerShell 窗口里粘贴时外层会先把 $变量 替换掉
                   f"'http://{self.host()}/kylin/install.ps1?c={{班级}}&k={{密钥}}')))\"")
            host = self.host()
            unwin = f"powershell -nop -c \"iex ([Text.Encoding]::UTF8.GetString((New-Object Net.WebClient).DownloadData('http://{host}/kylin/uninstall.ps1')))\""
            mine = {c: v for c, v in load_students().items() if self.allowed(c, "head")}
            return self.send_json({"classes": [{"name": c, "count": len(v), "names": [x["name"] for x in v], "role": self.role(c)}
                                               for c, v in mine.items()], "install": cmd, "install_win": win,
                                   "uninstall": f"wget -qO- {host}/kylin/uninstall.sh | bash", "uninstall_win": unwin,
                                   "keys": {c: class_key(c) or "" for c in mine},
                                   "admin": is_admin(self.sess()), "public": PUBLIC,
                                   "online": {c: time.time() - last_seen.get(c, 0) < 15 for c in mine}})
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
            m = next((x for x in messages if x["id"] == body.get("id")), None)
            if not m or not self.classroom_ok(q, m["class"]):
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
        if u.path == "/api/register":
            return self.api_register(body) if PUBLIC else self.send_json({"error": "not found"}, 404)
        if u.path == "/api/register/code" and not PUBLIC:
            return self.send_json({"error": "not found"}, 404)
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
            "/api/homework/add": self.api_homework_add, "/api/homework/delete": self.api_homework_delete,
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
            "/api/morning/add": self.api_morning_add, "/api/morning/delete": self.api_morning_delete,
            "/api/exams/save": self.api_exam_save, "/api/exams/delete": self.api_exam_delete, "/api/exams/end": self.api_exam_end,
            "/api/notes/add": self.api_note_add, "/api/notes/delete": self.api_note_delete,
            "/api/files/delete": self.api_file_delete,
            "/api/admin/password": self.api_admin_password, "/api/admins/set": self.api_admins_set,
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
        """公共服务器：网友用注册码自己建班，注册的人就是这个班的班主任"""
        code = str(body.get("code") or "").strip()
        cls = re.sub(r"\s+", "", str(body.get("class") or ""))
        want = settings.get("register_code") or ""
        if not want or settings.get("register_paused"):
            return self.send_json({"error": "暂未开放注册"}, 403)
        if not hmac.compare_digest(code.encode(), want.encode()):
            return self.send_json({"error": "注册码不对，请看公众号自动回复里的最新注册码"}, 403)
        if not cls:
            return self.send_json({"error": "请填写班级名称"}, 400)
        if len(cls) > 16:
            return self.send_json({"error": "班级名称最长 16 个字"}, 400)
        if cls.startswith("_") or not re.fullmatch(r"[\w·（）\-]+", cls):
            return self.send_json({"error": "班级名称只能用汉字、字母、数字"}, 400)
        ip, d = self.client_ip(), today()
        s = self.sess()
        if is_admin(s):
            return self.send_json({"error": "管理员请在工作台「班级管理」里建班"}, 400)
        with lock:
            rc = reg_counts.get(ip)
            if rc and rc[0] == d and rc[1] >= 5:
                return self.send_json({"error": "今天注册次数太多了，请明天再试"}, 429)
            if cls in load_students():
                return self.send_json({"error": "这个班级名已被使用，请在前面加上学校名"}, 400)
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
        if "paused" in body:  # 管理员暂停/恢复注册，注册码不变
            with lock:
                settings["register_paused"] = bool(body["paused"])
                write_json(SETTINGS_PATH, settings)
            return self.send_json({"ok": True, "paused": settings["register_paused"]})
        code = str(body.get("code") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9]{4,20}", code):
            return self.send_json({"error": "注册码要 4–20 位字母或数字"}, 400)
        with lock:
            settings["register_code"] = code
            write_json(SETTINGS_PATH, settings)
        self.send_json({"ok": True, "code": code})

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
        if body.get("scope") == "class" and body.get("reset"):  # 恢复使用全校作息
            with lock:
                settings.setdefault("class_schedule", {}).pop(cls, None)
                write_json(SETTINGS_PATH, settings)
            return self.send_json({"ok": True, **schedule_of(cls), "own": False})
        periods = []
        for p_ in body.get("periods") or []:
            try:
                a_, b_ = [datetime.strptime(x.strip(), "%H:%M").strftime("%H:%M") for x in p_]
            except (ValueError, TypeError):
                return self.send_json({"error": f"时间格式不对：{p_}"}, 400)
            if a_ >= b_:
                return self.send_json({"error": f"结束时间要晚于开始时间：{a_}–{b_}"}, 400)
            periods.append([a_, b_])
        days = sorted({int(d) for d in body.get("weekdays") or [] if 1 <= int(d) <= 7})
        if not periods or not days:
            return self.send_json({"error": "请至少填一节课和一个上课日"}, 400)
        sch = {"periods": sorted(periods), "weekdays": days}
        with lock:
            if body.get("scope") == "class":
                settings.setdefault("class_schedule", {})[cls] = sch
            else:
                settings["schedule"] = sch
            write_json(SETTINGS_PATH, settings)
        self.send_json({"ok": True, **sch, "own": body.get("scope") == "class"})

    def api_board_show(self, body, sender):
        """让大屏马上显示看板（不管现在是不是晚自习），碰一下屏幕就收起"""
        cls = str(body.get("class") or "")
        create_messages([cls], "显示班级看板", sender, kind="standby", urgent=True, expire_at=time.time() + 120)
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
        cls = str(body.get("class") or "")
        text = re.sub(r"\s+", " ", str(body.get("text") or "")).strip()[:120]
        subject = str(body.get("subject") or "").strip()[:6]
        if not text:
            return self.send_json({"error": "请填写留言内容"}, 400)
        d = now_cn().date()
        until = {"today": d, "week": d + timedelta(days=6 - d.weekday())}.get(body.get("keep"))  # 今天 / 到本周日 / 一直显示
        with lock:
            if len(notes_of(cls)) >= 8:
                return self.send_json({"error": "这个班的留言已经有 8 条了，先删掉几条旧的"}, 400)
            item = {"id": next_id(notes), "class": cls, "text": text, "subject": subject, "teacher": sender,
                    "uid": self.sess()["uid"], "created": time.time(), "until": until.strftime("%Y-%m-%d") if until else ""}
            notes.append(item)
            cutoff = (now_cn() - timedelta(days=30)).strftime("%Y-%m-%d")
            notes[:] = [n for n in notes if not n.get("until") or n["until"] >= cutoff]
            write_json(NOTES_PATH, notes)
        self.send_json({"ok": True, "item": item})

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
        """班主任/管理员远程卸载这个班的一体机接收端：一体机收到后自己删掉开机自启、桌面图标和程序"""
        cls = str(body.get("class") or "")
        if cls not in load_students():
            return self.send_json({"error": "没有这个班"}, 404)
        create_messages([cls], "远程卸载一体机", sender, kind="uninstall", urgent=True, expire_at=time.time() + 7 * 86400)
        self.send_json({"ok": True, "online": time.time() - last_seen.get(cls, 0) < 15})

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
        classes = [str(c) for c in body.get("classes") or [] if str(c).strip()]
        text = str(body.get("text") or "").strip()
        if not classes or not text:
            return self.send_json({"error": "请选择班级并填写内容"}, 400)
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
        ids, scheduled = create_messages(classes, text, sender, body.get("voice"), body.get("urgent"),
                                         body.get("defer"), body.get("voice_name"), who=str(body.get("who") or "")[:40] or None,
                                         loud=body.get("loud"), send_at=send_at)
        self.send_json({"ok": True, "ids": ids, "scheduled": scheduled,
                        "exam": not body.get("urgent") and any(exam_now(c, send_at) for c in classes)})

    # ---------- 考试 ----------
    def api_exam_save(self, body, sender):
        name = re.sub(r"\s+", " ", str(body.get("name") or "")).strip()[:30]
        classes = list(dict.fromkeys(str(c) for c in body.get("classes") or [] if str(c) in load_students()))
        notice = "\n".join(l.strip()[:60] for l in str(body.get("notice") or "").split("\n") if l.strip())[:600]
        sessions = []
        for x in (body.get("sessions") or [])[:30]:
            subj, date, a, b = (str(x.get(k) or "").strip() for k in ("subject", "date", "start", "end"))
            if not subj and not a and not b:
                continue
            if not subj or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) or not re.fullmatch(r"\d{2}:\d{2}", a) or not re.fullmatch(r"\d{2}:\d{2}", b):
                return self.send_json({"error": "每一场都要填科目、日期、开始和结束时间"}, 400)
            if a >= b:
                return self.send_json({"error": f"{subj}：结束时间要晚于开始时间"}, 400)
            sessions.append({"subject": subj[:10], "date": date, "start": a, "end": b})
        if not name:
            return self.send_json({"error": "请填写考试名称，如：10 月月考"}, 400)
        if not classes:
            return self.send_json({"error": "请选择参加考试的班级"}, 400)
        if not sessions:
            return self.send_json({"error": "至少填一场考试"}, 400)
        keyed = sorted((x["date"], x["start"], x["end"], x["subject"]) for x in sessions)
        for p_, n_ in zip(keyed, keyed[1:]):
            if p_[0] == n_[0] and n_[1] < p_[2]:
                return self.send_json({"error": f"{p_[3]} 和 {n_[3]} 的时间重叠了"}, 400)
        with lock:
            old = next((e for e in exams if e["id"] == body.get("id")), None)
            for e in exams:  # 同一个班不能同时有两场考试
                if e is old:
                    continue
                for c in set(classes) & set(e["classes"]):
                    for x in e["sessions"]:
                        for y in sessions:
                            if x["date"] == y["date"] and x["start"] < y["end"] and y["start"] < x["end"]:
                                return self.send_json({"error": f"{c}在{e['name']}里已经有 {x['date'][5:]} {x['start']}–{x['end']} 的{x['subject']}，时间重叠了"}, 400)
            if old:
                changed = {i for i, x in enumerate(old["sessions"]) if i >= len(sessions) or x != sessions[i]}
                old.update(name=name, classes=classes, sessions=sessions, notice=notice,
                           ended={k: v for k, v in (old.get("ended") or {}).items() if int(k.split("|")[1]) not in changed})
                item = old
            else:
                item = {"id": next_id(exams), "name": name, "classes": classes, "sessions": sessions, "notice": notice,
                        "creator": sender, "uid": self.sess()["uid"], "created": time.time(), "ended": {}}
                exams.append(item)
            cutoff = (now_cn() - timedelta(days=60)).strftime("%Y-%m-%d")
            exams[:] = [e for e in exams if max(x["date"] for x in e["sessions"]) >= cutoff]
            save_exams()
        self.send_json({"ok": True, "item": item})

    def api_exam_delete(self, body, sender):
        with lock:
            exams[:] = [e for e in exams if e["id"] != body.get("id")]
            save_exams()
        self.send_json({"ok": True})

    def api_exam_end(self, body, sender):
        """提前结束这个班正在显示的这一场（大屏马上收起）"""
        cls = str(body.get("class") or "")
        with lock:
            e = next((e for e in exams if e["id"] == body.get("id") and cls in e["classes"]), None)
            i = body.get("idx")
            if not e or not isinstance(i, int) or not 0 <= i < len(e["sessions"]):
                return self.send_json({"error": "找不到这场考试"}, 404)
            e.setdefault("ended", {})[f"{cls}|{i}"] = time.time()
            save_exams()
        self.send_json({"ok": True})

    # ---------- 作业 ----------
    def api_homework_add(self, body, sender):
        cls = str(body.get("class") or "")
        subject = str(body.get("subject") or "").strip()[:10]
        content = str(body.get("content") or "").strip()[:TEXT_LIMIT if PUBLIC else 300]
        if not cls or not subject or not content:
            return self.send_json({"error": "请填写科目和作业内容"}, 400)
        minutes = max(0, min(300, int(body.get("minutes") or 0)))
        due = str(body.get("due") or "").strip()[:20]
        with lock:
            item = {"id": next_id(homework), "class": cls, "subject": subject, "content": content,
                    "minutes": minutes, "due": due, "date": today(), "teacher": sender, "created": time.time()}
            item["due_date"] = due_date(item)
            homework.append(item)
            cutoff = (now_cn() - timedelta(days=200)).strftime("%Y-%m-%d")  # 保留一学期多，方便期末导出
            homework[:] = [h for h in homework if h["date"] >= cutoff]
            write_json(HOMEWORK_PATH, homework)
        if body.get("push"):
            text = f"【{subject}作业】{content}" + (f"\n预计 {minutes} 分钟" if minutes else "") + (f" · {due}" if due else "")
            create_messages([cls], text, sender, body.get("voice"), False, True, body.get("voice_name"),
                            kind="homework", say=f"{subject}作业：{content}", repeat=1)
        self.send_json({"ok": True, "item": item})

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
        create_messages([cls], text, sender, body.get("voice"), body.get("urgent"), body.get("defer"),
                        body.get("voice_name"), kind="homework", say="今日作业已发布，请同学们抄写", repeat=1)
        self.send_json({"ok": True})

    # ---------- 定时提醒 ----------
    def api_reminder_save(self, body, sender):
        text = str(body.get("text") or "").strip()[:200]
        hm = str(body.get("time") or "")
        days = sorted({int(d) for d in body.get("days") or [] if 1 <= int(d) <= 7})
        classes = [str(c) for c in body.get("classes") or [] if str(c).strip()]
        if not text or not re_hm(hm) or not days or not classes:
            return self.send_json({"error": "请填写时间、重复日期和提醒内容"}, 400)
        with lock:
            r = next((x for x in reminders if x["id"] == body.get("id")), None)
            full = [c for c in classes if sum(c in x["classes"] for x in reminders if x is not r) >= REMINDER_LIMIT]
            if PUBLIC and full:
                return self.send_json({"error": f"{full[0]}的定时提醒已经有 {REMINDER_LIMIT} 条了，请先删掉一些"}, 400)
            if not r:
                r = {"id": next_id(reminders), "creator": sender}
                reminders.append(r)
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
        ids, _ = create_messages([cls], text, sender, kind="timer", urgent=True,
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
        ids, _ = create_messages([cls], text, sender, voice=body.get("voice", True), urgent=True,
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
        create_messages([cls], f"【{hw['subject']}作业】收交登记\n请{hw['subject']}课代表点击下方按钮开始登记", sender,
                        False, False, body.get("defer", True), kind="collect",
                        data={"hw_id": hw["id"], "subject": hw["subject"]}, expire_at=time.time() + 3 * 3600)
        self.send_json({"ok": True})

    def api_collect_remind(self, body, sender):
        cls = str(body.get("class") or "")
        rec = collect_of(cls, body.get("hw_id"))
        if not rec or not rec["missing"]:
            return self.send_json({"error": "没有未交的同学"}, 400)
        names = "、".join(rec["missing"])
        text = f"请以下同学尽快补交{rec['subject']}作业：\n{names}"
        create_messages([cls], text, sender, body.get("voice"), False, body.get("defer", True), body.get("voice_name"),
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

    def api_file_upload(self, q):
        """老师把文档发到大屏：?class=班级&name=文件名，请求体是文件本身（边收边写盘，不占内存）"""
        def fail(msg, code=400):
            self.close_connection = True  # 请求体可能没读完，这条连接不能再用
            return self.send_json({"error": msg}, code)
        s = self.sess()
        if not s:
            return fail("请先登录", 401)
        cls = (q.get("class") or [""])[0]
        if not self.allowed(cls, "teacher"):
            return fail("你不是这个班的老师", 403)
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
            if sum(f["size"] for f in files) + length > FILE_TOTAL_MAX:
                return fail("服务器空间不够了，请先删掉一些旧文件", 507)
            if PUBLIC and sum(f["size"] for f in files if f["class"] == cls) + length > FILE_CLASS_MAX:
                return fail(f"本班文件已经满 {FILE_CLASS_MAX // 1024 // 1024}MB 了，请先删掉一些旧文件（7 天后也会自动删）", 507)
        fid = secrets.token_hex(12)
        tmp = os.path.join(FILE_DIR, fid + ".part")
        try:
            left = length
            with open(tmp, "wb") as fh:
                while left > 0:
                    chunk = self.rfile.read(min(left, 1024 * 1024))
                    if not chunk:
                        raise OSError("上传中断")
                    fh.write(chunk)
                    left -= len(chunk)
            os.replace(tmp, os.path.join(FILE_DIR, fid))
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass
            return fail("上传中断了，请重试")
        with lock:
            files.append({"id": fid, "class": cls, "name": name, "ext": ext, "size": length,
                          "by": s["name"], "uid": s["uid"], "at": time.time()})
            write_json(FILE_PATH, files)
        size = f"{length / 1024 / 1024:.1f}MB" if length >= 1024 * 1024 else f"{max(1, length // 1024)}KB"
        ids, _ = create_messages([cls], f"发来文件：{name}", s["name"], kind="file",
                                 data={"file": fid, "name": name, "size": size})
        self.send_json({"ok": True, "id": fid, "msg_id": ids[0]})

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
        ids, _ = create_messages([cls], caption or "请看大屏上的题目", sender, kind="image", urgent=True,
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
        create_messages([cls], "考勤登记\n请班长点击下方按钮，登记" + current_session(cls=cls) + "的考勤", sender,
                        False, False, body.get("defer", True), kind="attend", data={"open": "attend"},
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
            create_messages([cls], text, sender, body.get("voice"), False, True, body.get("voice_name"),
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
        create_messages([cls], "今日值日：" + duty_text(cls), sender, body.get("voice"), False, body.get("defer", True),
                        body.get("voice_name"), kind="reminder", say="今日值日：" + duty_text(cls), repeat=1)
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
        with lock:
            for m in messages:
                if m["id"] == body.get("id"):
                    m["recalled"] = True
            save_messages()
        self.send_json({"ok": True})

    def api_poll(self, q):
        cls = (q.get("class") or [""])[0]
        if not self.classroom_ok(q, cls):
            return self.send_json({"error": "密钥错误"}, 403)
        now = time.time()
        last_seen[cls] = now
        st = period_status(cls=cls)
        out, changed = [], False
        rv = (q.get("rv") or ["0"])[0]
        rv = int(rv) if rv.isdigit() else 0  # 接收端版本：3 起认得远程卸载
        in_exam = exam_now(cls, now)
        with lock:
            for m in messages:
                if m["class"] != cls or msg_status(m, now) not in ("pending", "delivered"):
                    continue
                if in_exam and not m["urgent"] and m.get("kind") not in ("uninstall", "standby"):
                    continue  # 考试期间先不显示（考完还没过期的会再弹出来）
                if m.get("kind") == "file" and rv < 4:  # 旧版接收端不会下载文件：等它 10 分钟内自动更新后再发
                    continue
                if m.get("kind") == "uninstall":
                    if rv < 3:  # 旧版接收端不认识卸载指令（会当成普通消息弹出来）：等它 10 分钟内自动更新后再发
                        continue
                    m["acked_at"] = now  # 只发一次：卸载后重新安装的接收端不会再收到
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
                        "standby_window": window, "evening": in_eve, "session": session, "eve_plan": cc["eve_plan"], "tools_tab": cc["tools_tab"],
                        "classes_today": [c for c in today_classes(cls, dt)[0] if not c["evening"]],  # 白天看板只显示白天的课，晚自习的课在夜空看板上
                        "class_now": today_classes(cls, dt)[1], "class_next": today_classes(cls, dt)[2],
                        "duty": duty_today(cls, dt), "quote": pick_quote(cls),
                        "morning": {"start": morning_window(cls)[0], "end": morning_window(cls)[1], "date": dt.strftime("%Y-%m-%d"),
                                    "items": [{"subject": m["subject"], "content": m["content"]} for m in morning_of(cls)]},
                        "exams": [{**x, "voice": exam_voice()} for x in exam_sessions(cls) if x["hide_at"] > time.time()],
                        "stars": sorted(([n, v] for n, v in point_totals(cls, week_start_ts(dt)).items() if v > 0),
                                        key=lambda x: -x[1])[:5] if cc["board_points"] else []})

    def api_ack(self, body):
        with lock:
            for m in messages:
                if m["id"] == body.get("id") and not m.get("acked_at"):
                    m["acked_at"] = time.time()
            save_messages()
        self.send_json({"ok": True})

    def api_recent(self, q=None):
        mine = set(self.my_classes())
        now = time.time()
        with lock:
            recent = [{"id": m["id"], "class": m["class"], "text": m["text"], "sender": m["sender"],
                       "time": datetime.fromtimestamp(m["created"], CN).strftime("%m-%d %H:%M"),
                       "deliver": datetime.fromtimestamp(m["deliver_at"], CN).strftime("%H:%M"),
                       "status": msg_status(m, now), "kind": m.get("kind", "call"), "who": m.get("who"),
                       "ack_secs": int(m["acked_at"] - (m.get("delivered_at") or m["deliver_at"])) if m.get("acked_at") else None,
                       "delivered_ago": int(now - m["delivered_at"]) if m.get("delivered_at") else None}
                      for m in reversed([m for m in messages if m["class"] in mine][-40:])]
        online = {c: now - last_seen.get(c, 0) < 15 for c in mine}
        st = period_status(cls=((q or {}).get("class") or [""])[0] or None)
        self.send_json({"messages": recent, "online": online, "period": st,
                        "now": now_cn().strftime("%H:%M"),
                        "exam": (lambda x: {"subject": x["subject"], "start": x["start"], "end": x["end"], "id": x["exam_id"], "idx": x["idx"]} if x else None)(
                            exam_now(((q or {}).get("class") or [""])[0]))})


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
