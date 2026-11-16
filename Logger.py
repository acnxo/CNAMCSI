#!/usr/bin/env python3
"""统一日志模块：北京时间时间戳 + 主题前缀 + 级别，控制台与文件双写。

输出格式（示例）::

    [2026-11-15 15:05:12][CNAMCI][INFO ] 开始计算 2026-11-13
    [2026-11-15 15:05:12][CNAMCI][STEP ] [1/7] 数据就绪检查：开始
    [2026-11-15 15:05:20][CNAMCI/DATA][WARN ] 今日数据尚未更新，60 秒后重试

设计要点：
- 时间戳固定用北京时间（UTC+8），不依赖 runner 的本地时区；
- 主题（topic）用于区分模块，例如 ``CNAMCI/DATA``、``CNAMCI/INDEX``；
- ``Step`` 提供「开始 → 过程 → 完成/失败」的上下文管理器，自动附加耗时。
"""

import datetime
import os
import time

_TOPIC = "CNAMCI"          # 默认主题前缀
_LOG_FILE = None           # 可选：同时写入的日志文件
_BEIJING = datetime.timezone(datetime.timedelta(hours=8))


def beijingNow():
    """返回北京时间（aware datetime）。"""
    return datetime.datetime.now(_BEIJING)


def timestamp():
    """日志时间戳字符串，格式 ``YYYY-MM-DD HH:MM:SS``。"""
    return beijingNow().strftime("%Y-%m-%d %H:%M:%S")


def setTopic(topic):
    """设置默认主题前缀（例如 ``CNAMCI``）。"""
    global _TOPIC
    _TOPIC = topic


def setLogFile(path):
    """开启文件双写；传 ``None`` 关闭。目录不存在时自动创建。"""
    global _LOG_FILE
    if not path:
        _LOG_FILE = None
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8"):   # 每次运行覆盖，只保留本次日志
        pass
    _LOG_FILE = path


def _emit(message, level, topic):
    """格式化并输出一行日志；文件写入失败不影响主流程。"""
    line = f"[{timestamp()}][{topic or _TOPIC}][{level:<5}] {message}"
    print(line, flush=True)
    if _LOG_FILE:
        try:
            with open(_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass
    return line


def info(message, topic=None):
    """一般信息。"""
    return _emit(message, "INFO", topic)


def ok(message, topic=None):
    """成功/完成。"""
    return _emit(message, "OK", topic)


def warn(message, topic=None):
    """警告（降级、重试、跳过）。"""
    return _emit(message, "WARN", topic)


def error(message, topic=None):
    """错误（不可恢复）。"""
    return _emit(message, "ERROR", topic)


def section(title, topic=None):
    """章节分隔（前后各留一个空行，便于阅读）。"""
    print("", flush=True)
    return _emit(f"===== {title} =====", "SECTION", topic)


class Step:
    """可追踪步骤：开始 → （可选）过程提示 → 完成/失败，自动带序号与耗时。

    用法::

        with Step(2, 7, "拉取全市场快照", topic="CNAMCI/DATA") as step:
            step.note("数据源 tencent，约 4 批")
            step.result("3493 条行情，8 次请求 / 3.13 MB")

    若块内抛异常，``__exit__`` 会输出失败日志（含异常信息与耗时）并继续抛出。
    """

    def __init__(self, index, total, title, topic=None, begin=True):
        self.index = index          # 当前步骤序号（从 1 开始）
        self.total = total          # 总步骤数
        self.title = title          # 步骤标题（中文）
        self.topic = topic          # 日志主题
        self.startedAt = time.time()
        self.finished = False
        if begin:
            self.begin()

    def _prefix(self):
        """统一的步骤前缀：``[2/7] 拉取全市场快照``。"""
        return f"[{self.index}/{self.total}] {self.title}"

    def begin(self, detail=""):
        """步骤开始；``detail`` 用于说明本步的参数/输入。"""
        suffix = f"（{detail}）" if detail else ""
        _emit(f"{self._prefix()}：开始{suffix}", "STEP", self.topic)
        return self

    def note(self, message):
        """步骤内的过程反馈（查到什么、重试几次、中间指标）。"""
        _emit(f"{self._prefix()}：{message}", "INFO", self.topic)
        return self

    def warn(self, message):
        """步骤内的降级提示。"""
        _emit(f"{self._prefix()}：{message}", "WARN", self.topic)
        return self

    def result(self, detail=""):
        """步骤完成；``detail`` 是本步的结果反馈（数量、金额、请求数等）。"""
        self.finished = True
        suffix = f" · {detail}" if detail else ""
        _emit(f"{self._prefix()}：完成{suffix} · 耗时 {time.time() - self.startedAt:.2f}s",
              "OK", self.topic)
        return self

    def fail(self, message=""):
        """步骤失败；``message`` 是失败原因。"""
        self.finished = True
        suffix = f" · {message}" if message else ""
        _emit(f"{self._prefix()}：失败{suffix} · 耗时 {time.time() - self.startedAt:.2f}s",
              "ERROR", self.topic)
        return self

    def skip(self, reason=""):
        """步骤跳过（非交易日、dry-run、mode=index 等）。"""
        self.finished = True
        suffix = f" · {reason}" if reason else ""
        _emit(f"{self._prefix()}：跳过{suffix} · 耗时 {time.time() - self.startedAt:.2f}s",
              "WARN", self.topic)
        return self

    def __enter__(self):
        return self

    def __exit__(self, excType, exc, traceback):
        # 块内异常：记录失败日志后继续向上抛；正常结束但未调用 result() 时补一条完成日志
        if excType is not None:
            self.fail(str(exc))
            return False
        if not self.finished:
            self.result()
        return False
