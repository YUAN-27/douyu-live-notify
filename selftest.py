# -*- coding: utf-8 -*-
"""端到端自检：验证状态机、轮播判定、消息格式、OneBot 报文。

特点：全程用假的探测函数，**不联网、不发送真实消息**，任何机器上结果都一样。
改完 watch.py 先跑这个：

    python selftest.py && tail -3 selftest_result.txt

退出码：全部通过为 0，有失败为 1（方便接 CI）。
"""
import contextlib
import importlib.util
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("w", os.path.join(HERE, "watch.py"))
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)

# 测试用的假房间号。故意不写任何真实主播的号 —— 本文件全程不联网、
# 探测函数全被替换成木头人，填什么号都不影响结果。
TEST_ROOM_ID = "1000001"
TEST_ANCHOR = "示例主播"

report = []
FAILS = []
TOTAL = [0]


def log(*a):
    report.append(" ".join(str(x) for x in a))


def check(name, cond, extra=""):
    TOTAL[0] += 1
    if not cond:
        FAILS.append(name)
    log("[%s] %s%s" % ("PASS" if cond else "FAIL", name, ("   " + extra) if extra else ""))


def fake_state(is_live, loop=None, online=None, title="示例直播间"):
    return {"source": "fake", "is_live": is_live, "room_id": TEST_ROOM_ID,
            "anchor": TEST_ANCHOR, "title": title, "online": online,
            "start_time": "2026-01-01 20:00:00", "loop_flag": loop, "raw": {}}


class FakeNotifier:
    name = "fake"

    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)


STATE = os.path.join(HERE, "state_%s.json" % TEST_ROOM_ID)
backup = None
if os.path.exists(STATE):
    with open(STATE, "r", encoding="utf-8") as fp:
        backup = fp.read()

try:
    if os.path.exists(STATE):
        os.remove(STATE)

    cfg = json.loads(json.dumps(w.DEFAULT_CFG))
    # DEFAULT_CFG 里的 room_id 是空的（要用户自己填），测试自己给一个
    cfg["room_id"] = TEST_ROOM_ID
    cfg["channels"] = ["console"]

    # ---- 断网：探测函数换成可控木头人（read_state 的默认参数在定义时已绑定，
    #      所以必须替换 read_state / probe_legacy 这两个模块级名字）----
    CUR = {"v": fake_state(False)}
    w.read_state = lambda room_id, **kw: CUR["v"]
    w.probe_legacy = lambda room_id: CUR["v"]

    def tick(notifier=None):
        return w.tick(cfg, notifiers=[notifier or FakeNotifier()], verbose=False)

    log("=" * 68)
    log("场景 1：首次 tick —— 只记录状态，不发通知")
    log("=" * 68)
    CUR["v"] = fake_state(False)
    r = tick()
    st = json.load(open(STATE, encoding="utf-8"))
    check("首次运行不触发通知", r["action"] is None, "action=%s" % r["action"])
    check("首次运行把状态落盘", st["is_live"] is False, "is_live=%s" % st["is_live"])

    log("")
    log("=" * 68)
    log("场景 2：真人开播（loop=0）—— 连续 confirm_rounds 次后推送「开播」")
    log("=" * 68)
    fake = FakeNotifier()
    CUR["v"] = fake_state(True, loop=0, online=2830556)
    r1 = tick(fake)
    check("第 1 次读到开播不立即推送（防抖）", r1["action"] is None, "action=%s" % r1["action"])
    r2 = tick(fake)
    check("第 2 次读到开播才推送", r2["action"] == "up", "action=%s" % r2["action"])
    check("共收到 1 条通知", len(fake.sent) == 1, "收到 %d 条" % len(fake.sent))
    if fake.sent:
        log("-" * 68)
        log(fake.sent[0])
        log("-" * 68)

    log("")
    log("=" * 68)
    log("场景 3：接口抖动 —— 状态反复横跳不应推送")
    log("=" * 68)
    fake2 = FakeNotifier()
    CUR["v"] = fake_state(False)
    tick(fake2)                                   # 抖动：读到下播
    CUR["v"] = fake_state(True, loop=0)
    tick(fake2)                                   # 读到开播 -> pending 1
    CUR["v"] = fake_state(False)
    tick(fake2)                                   # 又回到下播 -> pending 清空
    CUR["v"] = fake_state(True, loop=0)
    r = tick(fake2)                               # 再读到开播 -> 又是 pending 1
    check("反复横跳不推送", len(fake2.sent) == 0, "收到 %d 条" % len(fake2.sent))

    log("")
    log("=" * 68)
    log("场景 4：轮播判定（核心）")
    log("=" * 68)
    # 4a: treat_loop_as_live=False（默认值）—— 轮播应判为「未开播」
    loop_state = fake_state(True, loop=1, online=2830305)
    w.read_state = lambda room_id, **kw: loop_state
    w.probe_legacy = lambda room_id: loop_state
    cfg["treat_loop_as_live"] = False
    if os.path.exists(STATE):
        os.remove(STATE)
    tick(); tick(); tick()
    st = json.load(open(STATE, encoding="utf-8"))
    check("treat_loop_as_live=false 时，轮播不判为开播",
          st["is_live"] is False, "is_live=%s" % st["is_live"])

    # 4b: 改回 True —— 轮播应判为「开播」并推送
    cfg["treat_loop_as_live"] = True
    if os.path.exists(STATE):
        os.remove(STATE)
    CUR["v"] = fake_state(False)
    w.read_state = lambda room_id, **kw: CUR["v"]
    tick()
    CUR["v"] = loop_state
    tick()
    fake3 = FakeNotifier()
    r = tick(fake3)
    check("treat_loop_as_live=true 时，轮播会推送开播",
          r["action"] == "up", "action=%s" % r["action"])
    cfg["treat_loop_as_live"] = False

    log("")
    log("=" * 68)
    log("场景 5：消息格式")
    log("=" * 68)
    sample = fake_state(True, loop=1, online=2830556)
    msg = w.format_message(sample, cfg, "up")
    log(msg)
    check("消息含标题", "标题：" in msg)
    check("热度按万换算", "283.1 万" in msg, "热度=%s" % sample["online"])
    check("轮播时附提示行", "轮播" in msg)

    log("")
    log("=" * 68)
    log("场景 6：OneBot 报文构造（不真发）")
    log("=" * 68)
    n = w.OneBotNotifier({"base": "http://127.0.0.1:3000", "target_type": "private",
                          "target_id": "10001", "at_all": True})
    pre = n._at_prefix("private")
    log("private 模式 at 前缀 = %r" % pre)
    check("私聊不带 @ 前缀", pre == "")

    ng = w.OneBotNotifier({"base": "http://127.0.0.1:3000", "target_type": "group",
                           "target_id": "123", "at_all": False, "at_users": ["10001"]})
    pre_g = ng._at_prefix("group")
    log("group  模式 at 前缀 = %r" % pre_g)
    check("群聊 @ 指定人", pre_g == "[CQ:at,qq=10001] ", "前缀=%r" % pre_g)

    log("")
    log("=" * 68)
    log("场景 7：本机地址必须绕过代理（服务器上有全局代理时会踩）")
    log("=" * 68)
    for u, want in (("http://127.0.0.1:3000/send_group_msg", True),
                    ("http://localhost:3000/x", True),
                    ("http://127.0.0.1:6099/webui", True),
                    ("https://www.douyu.com/betard/%s" % TEST_ROOM_ID, False),
                    ("https://open.douyucdn.cn/api/RoomApi/room/%s" % TEST_ROOM_ID, False)):
        got = w._is_local_url(u)
        check("_is_local_url(%s) == %s" % (u, want), got is want, "实际 %s" % got)

    log("")
    log("=" * 68)
    log("场景 8：配置校验（room_id 必填）")
    log("=" * 68)
    check("空 room_id 被拦下", not w.validate_cfg({"room_id": "", "channels": ["console"]}, "x"))
    check("非数字 room_id 被拦下", not w.validate_cfg({"room_id": "abc", "channels": ["console"]}, "x"))
    check("空 channels 被拦下", not w.validate_cfg({"room_id": TEST_ROOM_ID, "channels": []}, "x"))
    check("正常配置放行", w.validate_cfg(
        {"room_id": TEST_ROOM_ID, "channels": ["console"]}, "x"))
    check("onebot 的 target_id 是占位符时被拦下", not w.validate_cfg(
        {"room_id": TEST_ROOM_ID, "channels": ["onebot"],
         "onebot": {"target_id": "填群号", "token": "t"}}, "x"))
    check("onebot 的 target_id 正常时放行", w.validate_cfg(
        {"room_id": TEST_ROOM_ID, "channels": ["onebot"],
         "onebot": {"target_id": "123456", "token": "t"}}, "x"))

    log("")
    log("=" * 68)
    log("场景 9：发送失败要能自动补发（2026-09-23 丢通知的根治项）")
    log("=" * 68)

    class FailNotifier:
        """真实通道，但每次都失败。"""
        name = "onebot"
        counts_as_delivery = True

        def __init__(self):
            self.calls = 0

        def send(self, text):
            self.calls += 1
            raise RuntimeError("OneBot 返回异常：NTEvent sendMsg failed")

    class ConsoleLikeNotifier:
        """和 ConsoleNotifier 一样：永远成功，但不算送达。"""
        name = "console"
        counts_as_delivery = False

        def __init__(self):
            self.sent = []

        def send(self, text):
            self.sent.append(text)

    def tick_with(ns):
        """跑一轮，并把 stdout 掐掉。

        失败路径本来就会打 [error]（那正是本场景要测的东西），
        但让它们出现在自检输出里，会让人误以为是自检自己报错了。
        要看的结论用 check() 断言，不靠肉眼读日志。
        """
        with contextlib.redirect_stdout(io.StringIO()):
            return w.tick(cfg, notifiers=ns, verbose=False)

    def fresh_state(is_live):
        if os.path.exists(STATE):
            os.remove(STATE)
        with open(STATE, "w", encoding="utf-8") as fp:
            json.dump({"is_live": is_live, "pending": None, "pending_n": 0,
                       "last_change_at": None, "notify": None}, fp)

    def load_st():
        with open(STATE, encoding="utf-8") as fp:
            return json.load(fp)

    def age_pending(seconds):
        """把 last_attempt_at 往前挪，用来绕过退避（否则刚失败不会立刻重试）。"""
        st = load_st()
        st["notify"]["last_attempt_at"] = (
            w.datetime.now(w.CST) - w.timedelta(seconds=seconds)).isoformat()
        with open(STATE, "w", encoding="utf-8") as fp:
            json.dump(st, fp)

    cfg["notify_retry_max"] = 30
    cfg["notify_retry_backoff_cap_minutes"] = 10

    # 9a 首次发送失败 —— 不再当作「已送达」，而是落盘记下待补发
    fresh_state(False)
    CUR["v"] = fake_state(True, loop=0, online=2830556)
    bad = FailNotifier()
    tick_with([bad])
    tick_with([bad])
    st = load_st()
    check("发送失败不再被判成「已送达」", st.get("notify") is not None,
          "notify=%s" % (st.get("notify") or {}).get("kind"))
    check("失败记录里存了正文，补发不用重新拼",
          "【斗鱼开播】" in ((st.get("notify") or {}).get("text") or ""))
    check("状态照样落盘（不然永远不再判定）", st["is_live"] is True)

    # 9b 退避期内不该重试
    before = bad.calls
    tick_with([bad])
    st = load_st()
    check("退避期内不重试（别每分钟都去撞）",
          bad.calls == before and int(st["notify"]["attempts"]) == 1,
          "calls=%d attempts=%s" % (bad.calls, st["notify"]["attempts"]))

    # 9c 过了退避期 —— 自动补发，成功后清掉记录
    age_pending(120)
    good = FakeNotifier()
    r = tick_with([good])
    st = load_st()
    check("过了退避期会自动补发", len(good.sent) == 1, "收到 %d 条" % len(good.sent))
    check("补发成功后清掉待补发记录", st.get("notify") is None)
    check("返回值如实说明这是「补发」",
          (r.get("notify") or {}).get("stage") == "retry"
          and (r.get("notify") or {}).get("delivered") is True,
          "notify=%s" % r.get("notify"))

    # 9d console 的成功不能掩盖 onebot 的失败（这是最容易写错的地方）
    fresh_state(False)
    CUR["v"] = fake_state(True, loop=0)
    bad2 = FailNotifier()
    con = ConsoleLikeNotifier()
    tick_with([con, bad2])
    tick_with([con, bad2])
    st = load_st()
    check("console 成功不算送达，不掩盖 onebot 的失败",
          st.get("notify") is not None and len(con.sent) == 1,
          "notify=%s console发了%d条" % ((st.get("notify") or {}).get("kind"), len(con.sent)))

    # 9e 一直失败 —— 重试次数要递增
    age_pending(600)
    tick_with([con, bad2])
    st = load_st()
    check("反复失败时重试次数递增", int(st["notify"]["attempts"]) == 2,
          "attempts=%s" % st["notify"]["attempts"])

    # 9f 状态翻面 —— 旧记录作废（--recover-notify 就是靠这个清干净的）
    st = load_st()
    st["is_live"] = False              # 模拟人工把 is_live 改回 false
    with open(STATE, "w", encoding="utf-8") as fp:
        json.dump(st, fp)
    CUR["v"] = fake_state(False)
    tick_with([con, bad2])
    check("状态翻面后放弃补发旧通知", load_st().get("notify") is None)

    # 9g 到达上限 —— 放弃，别无限撞
    fresh_state(False)
    CUR["v"] = fake_state(True, loop=0)
    cfg["notify_retry_max"] = 1
    bad3 = FailNotifier()
    tick_with([bad3])
    tick_with([bad3])
    tick_with([bad3])
    st = load_st()
    check("重试到顶就放弃，不再无限撞",
          bool((st.get("notify") or {}).get("gave_up_at")),
          "notify=%s" % st.get("notify"))
    cfg["notify_retry_max"] = 30

    # 9h --tick 的那行回执不能撒谎（以前不管发没发出去都写「已推送」）
    fresh_state(False)
    CUR["v"] = fake_state(True, loop=0)
    real_build = w.build_notifiers
    w.build_notifiers = lambda c: [FailNotifier()]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            w.cmd_tick(cfg)
            w.cmd_tick(cfg)
    finally:
        w.build_notifiers = real_build
    out = buf.getvalue()
    check("--tick 发送失败时不谎称「已推送」",
          "已推送" not in out and "未送出" in out,
          "末行=%r" % (out.strip().splitlines() or [""])[-1][:80])

    # 9i 状态文件被手改坏 / 配置写错 —— 绝不能把整轮检查搞崩
    # （状态文件是允许手改的，而 tick 崩了 systemd 每轮都失败，监控会安静地死掉）
    fresh_state(False)
    CUR["v"] = fake_state(True, loop=0)
    tick_with([FailNotifier()])
    tick_with([FailNotifier()])
    st = load_st()
    st["notify"]["last_attempt_at"] = "2020-01-01T00:00:00"   # 少了时区
    st["notify"]["first_failed_at"] = "看不出来是什么时间"      # 彻底乱写
    st["notify"]["attempts"] = "三次"                          # 类型都不对
    with open(STATE, "w", encoding="utf-8") as fp:
        json.dump(st, fp, ensure_ascii=False)
    cfg["notify_retry_backoff_cap_minutes"] = "十分钟"          # 配置也写错

    crashed = None
    try:
        tick_with([FakeNotifier()])
    except Exception as exc:  # noqa: BLE001
        crashed = exc
    check("状态文件乱写 / 配置写错，tick 也不会崩", crashed is None,
          "异常=%r" % crashed)
    check("乱写的 attempts 当 0 处理，仍会去重试（不是躺平）",
          crashed is None and load_st().get("notify") is None,
          "notify=%s" % load_st().get("notify"))
    cfg["notify_retry_backoff_cap_minutes"] = 10

    log("")
    log("=" * 68)
    log("场景 10：下播提醒与直播时长（时长必须靠自己记账，不信接口）")
    log("=" * 68)

    w.read_state = lambda room_id, **kw: CUR["v"]
    w.probe_legacy = lambda room_id: CUR["v"]

    def save_st(patch):
        st = load_st()
        st.update(patch)
        with open(STATE, "w", encoding="utf-8") as fp:
            json.dump(st, fp, ensure_ascii=False)

    def now_ago(**kw):
        return (w.datetime.now(w.CST) - w.timedelta(**kw)).isoformat()

    # 10a 时长文案本身（含各量级与四类脏输入）
    for sec, want in ((0, "不到 1 分钟"), (59, "不到 1 分钟"), (60, "1 分钟"),
                      (1830, "30 分钟"), (3599, "59 分钟"), (3600, "1 小时"),
                      (3720, "1 小时 2 分"), (4980, "1 小时 23 分"),
                      (5400, "1 小时 30 分"), (86400, "24 小时")):
        got = w._fmt_duration(sec)
        check("_fmt_duration(%d) == %s" % (sec, want), got == want, "实际 %r" % got)
    check("时长解析不了就返回空串（那一行整行不写，也不编）", w._fmt_duration("三次") == "")
    check("负时长返回空串（不写「-3 小时」）", w._fmt_duration(-5) == "")
    check("None 返回空串", w._fmt_duration(None) == "")
    check("数字被手改成了字符串也认", w._fmt_duration("3600") == "1 小时")
    check("下限写法：「至少 1 小时 23 分」",
          w._fmt_duration(4980, at_least=True) == "至少 1 小时 23 分",
          "实际 %r" % w._fmt_duration(4980, at_least=True))
    check("下限但不满 1 分钟时不加「至少」（避免病句）",
          w._fmt_duration(30, at_least=True) == "不到 1 分钟",
          "实际 %r" % w._fmt_duration(30, at_least=True))

    # 10b 下播消息的版式
    end_msg = w.format_message(fake_state(False), cfg, "end",
                               duration_seconds=4980,
                               started_at="2026-09-24T19:30:00+08:00")
    log("-" * 68)
    log(end_msg)
    log("-" * 68)
    check("下播消息标题是【斗鱼下播】", end_msg.startswith("【斗鱼下播】"))
    check("下播消息带「直播时长」行", "直播时长：1 小时 23 分" in end_msg)
    check("下播消息带开播时间，且用自己记的那个（口径与时长一致）",
          "开播时间：2026-09-24 19:30:00" in end_msg)
    check("拿不到时长就整行不写", "直播时长" not in w.format_message(
        fake_state(False), cfg, "end", duration_seconds=None, started_at=None))
    check("开播消息不受影响（仍是开播标题、无时长行）",
          w.format_message(fake_state(True, loop=0), cfg, "up").startswith("【斗鱼开播】")
          and "直播时长" not in w.format_message(fake_state(True, loop=0), cfg, "up"))
    check("_to_cst_text 把带时区的 ISO 渲染成北京时间",
          w._to_cst_text("2026-09-24T19:30:00+08:00") == "2026-09-24 19:30:00",
          "实际 %r" % w._to_cst_text("2026-09-24T19:30:00+08:00"))
    for bad in ("看不懂", "", None, 12345):
        check("_to_cst_text(%r) 读不懂就返回空串（通知里不出现垃圾值）" % (bad,),
              w._to_cst_text(bad) == "", "实际 %r" % w._to_cst_text(bad))

    # 10c 端到端：开播记账 → 下播算出时长
    cfg["notify_on_end"] = True
    fresh_state(False)
    CUR["v"] = fake_state(True, loop=0)
    tick_with([FakeNotifier()])
    tick_with([FakeNotifier()])
    st = load_st()
    check("确认开播时把开播时刻记进了状态文件", bool(st.get("live_started_at")),
          "live_started_at=%s" % st.get("live_started_at"))
    check("接口 start_time 不可信时不拿它当基准，退回本轮（并标记为下限）",
          st.get("live_started_approx") is True,
          "live_started_approx=%s" % st.get("live_started_approx"))

    save_st({"live_started_at": now_ago(seconds=4983), "live_started_approx": False})
    CUR["v"] = fake_state(False)
    down = FakeNotifier()
    tick_with([down])
    check("下播也要连续 confirm 次才推（不因加时长而破坏防抖）",
          len(down.sent) == 0, "收到 %d 条" % len(down.sent))
    tick_with([down])
    check("下播会推送一条通知", len(down.sent) == 1, "收到 %d 条" % len(down.sent))
    if down.sent:
        log("-" * 68)
        log(down.sent[0])
        log("-" * 68)
    check("下播通知里带着本次直播总时长",
          "直播时长：1 小时 23 分" in (down.sent[0] if down.sent else ""),
          "实际 %r" % (down.sent[0].splitlines() if down.sent else None))
    check("记录准确的时刻时不写「至少」",
          "至少" not in (down.sent[0] if down.sent else ""))
    check("下播后把开播时刻收掉（下次开播重新记）",
          load_st().get("live_started_at") is None
          and load_st().get("live_started_approx") is False)

    # 10d 只有下限时措辞要诚实（升级/重启兜底的场景）
    fresh_state(False)
    CUR["v"] = fake_state(True, loop=0)
    tick_with([FakeNotifier()])
    tick_with([FakeNotifier()])
    save_st({"live_started_at": now_ago(hours=9, minutes=5), "live_started_approx": True})
    CUR["v"] = fake_state(False)
    d2 = FakeNotifier()
    tick_with([d2])
    tick_with([d2])
    check("只知道下限时写「至少 9 小时 5 分」，不当成准确值报",
          "直播时长：至少 9 小时 5 分" in (d2.sent[0] if d2.sent else ""),
          "实际 %r" % (d2.sent[0].splitlines() if d2.sent else None))

    # 10e notify_on_end=false —— 老行为不能被改坏
    cfg["notify_on_end"] = False
    fresh_state(False)
    CUR["v"] = fake_state(True, loop=0)
    tick_with([FakeNotifier()])
    tick_with([FakeNotifier()])
    off = FakeNotifier()
    CUR["v"] = fake_state(False)
    tick_with([off])
    tick_with([off])
    check("notify_on_end=false 时不推下播", len(off.sent) == 0, "收到 %d 条" % len(off.sent))
    check("notify_on_end=false 时也把开播时刻收掉（免得留脏数据）",
          load_st().get("live_started_at") is None)
    cfg["notify_on_end"] = True

    # 10f 旧状态文件（升级前就在播）：要补记，否则这场的下播没有时长
    fresh_state(True)
    CUR["v"] = fake_state(True, loop=0)
    tick_with([FakeNotifier()])
    st = load_st()
    check("旧状态文件里没有 live_started_at 时会补记一次",
          bool(st.get("live_started_at")), "live_started_at=%s" % st.get("live_started_at"))
    check("补记出来的时刻就是刚才，不是接口那个陈旧值",
          abs(w._age_seconds(st.get("live_started_at"), w.datetime.now(w.CST))) < 120,
          "已过去 %s 秒" % w._age_seconds(st.get("live_started_at"), w.datetime.now(w.CST)))
    check("补记的值同样标成「下限」", st.get("live_started_approx") is True)

    # 10g _pick_live_start 的取舍：接口值像真的就用，不像就退
    ref_now = w.datetime.now(w.CST)
    ok_state = fake_state(True, loop=0)
    ok_state["start_time"] = (ref_now - w.timedelta(minutes=45)).strftime("%Y-%m-%d %H:%M:%S")
    iso, approx = w._pick_live_start(ok_state, False, cfg, ref_now)
    check("接口给的开播时间在合理范围内就采用它", approx is False,
          "下限标记=%s 值=%s" % (approx, iso))
    check("采用的就是接口那个时刻（距今 45 分钟）",
          abs((w._age_seconds(iso, ref_now) or -1) - 2700) < 2,
          "距今 %s 秒" % w._age_seconds(iso, ref_now))
    for label, c, lp in (
            ("落在未来", dict(ok_state, start_time=(
                ref_now + w.timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")), False),
            ("太旧（超过 notify_end_max_hours）",
             dict(ok_state, start_time="2026-01-01 20:00:00"), False),
            ("轮播中（show_time 是轮播场次起点，不能用）", ok_state, True)):
        iso2, approx2 = w._pick_live_start(c, lp, cfg, ref_now)
        check("开播时间%s → 退回本轮并标成下限" % label, approx2 is True,
              "下限标记=%s 值=%s" % (approx2, iso2))

    # 10h 状态文件被手改坏 —— 不能崩，也不能编一个时长出来
    fresh_state(True)
    save_st({"live_started_at": "看不出来是什么时间"})
    CUR["v"] = fake_state(True, loop=0)
    crashed = None
    try:
        tick_with([FakeNotifier()])
    except Exception as exc:  # noqa: BLE001
        crashed = exc
    check("live_started_at 是乱写的字符串时 tick 不崩", crashed is None, "异常=%r" % crashed)
    check("乱写的值被当成「没有」，自动补记一个新的（能救回来）",
          w._parse_iso(load_st().get("live_started_at")) is not None,
          "live_started_at=%r" % load_st().get("live_started_at"))

    fresh_state(True)
    save_st({"live_started_at": {"坏": "结构也不对"}, "live_started_approx": "也许"})
    CUR["v"] = fake_state(True, loop=0)
    tick_with([FakeNotifier()])          # 这一轮会把坏值补记掉
    CUR["v"] = fake_state(False)
    d3 = FakeNotifier()
    crashed = None
    try:
        tick_with([d3])
        tick_with([d3])
    except Exception as exc:  # noqa: BLE001
        crashed = exc
    check("开播时刻结构损坏 + 标记类型也不对，下播流程依然不崩",
          crashed is None, "异常=%r" % crashed)
    check("补记之后下播照样有（下限）时长",
          len(d3.sent) == 1 and "直播时长：不到 1 分钟" in d3.sent[0],
          "实际 %r" % (d3.sent[0].splitlines() if d3.sent else None))

    # 10i 下播那一刻才发现时刻不可用：只报下播，不写时长行。
    # 这里把 confirm_rounds 临时压到 1，好让「下播」在一次 tick 内就成立 ——
    # 否则前一轮的补记逻辑早把坏值救回来了，走不到这个防御分支。
    cfg["confirm_rounds"] = 1
    fresh_state(True)
    save_st({"live_started_at": "2020-13-45 99:99:99", "live_started_approx": False})
    CUR["v"] = fake_state(False)
    d4 = FakeNotifier()
    crashed = None
    try:
        tick_with([d4])
    except Exception as exc:  # noqa: BLE001
        crashed = exc
    check("非法日期的开播时刻不会让下播流程崩", crashed is None, "异常=%r" % crashed)
    check("算不出时长时就只报下播、不编时长行",
          len(d4.sent) == 1 and "直播时长" not in d4.sent[0],
          "实际 %r" % (d4.sent[0].splitlines() if d4.sent else None))
    check("读不懂的日期不会被原样印进通知（宁可退回接口值）",
          len(d4.sent) == 1 and "2020-13-45" not in d4.sent[0],
          "实际 %r" % (d4.sent[0].splitlines() if d4.sent else None))
    check("下播通知本身照发（不能因为时长缺失就不通知）",
          len(d4.sent) == 1 and "【斗鱼下播】" in d4.sent[0])
    # 开播时刻缺失（而不是非法）时同理 —— 一样只报下播
    fresh_state(True)
    save_st({"live_started_at": None})
    d5 = FakeNotifier()
    crashed = None
    try:
        tick_with([d5])
    except Exception as exc:  # noqa: BLE001
        crashed = exc
    check("开播时刻缺失时也只报下播、不写时长行",
          crashed is None and len(d5.sent) == 1 and "直播时长" not in d5.sent[0],
          "异常=%r 实际 %r" % (crashed, d5.sent[0].splitlines() if d5.sent else None))
    cfg["confirm_rounds"] = 2

    # 10j --tick 的回执也要带时长：翻日志就能直接回答「上一场播了多久」
    cfg["notify_on_end"] = True
    fresh_state(False)
    CUR["v"] = fake_state(True, loop=0)
    real_build = w.build_notifiers
    w.build_notifiers = lambda c: [FakeNotifier()]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            w.cmd_tick(cfg)
            w.cmd_tick(cfg)
        save_st({"live_started_at": now_ago(hours=2, minutes=30),
                 "live_started_approx": False})
        CUR["v"] = fake_state(False)
        with contextlib.redirect_stdout(buf):
            w.cmd_tick(cfg)
            w.cmd_tick(cfg)
    finally:
        w.build_notifiers = real_build
    out = buf.getvalue()
    check("--tick 的回执里带上了本次直播时长", "2 小时 30 分" in out,
          "末行=%r" % ((out.strip().splitlines() or [""])[-1][:110]))

finally:
    if backup is not None:
        with open(STATE, "w", encoding="utf-8") as fp:
            fp.write(backup)
        log("")
        log("[cleanup] 已还原原有 state 文件")
    elif os.path.exists(STATE):
        os.remove(STATE)
        log("")
        log("[cleanup] 已删除测试产生的 state 文件")

report.append("")
report.append("=" * 68)
report.append("结果：%d 项通过，%d 项失败（共 %d 项）"
              % (TOTAL[0] - len(FAILS), len(FAILS), TOTAL[0]))
if FAILS:
    report.append("失败项：")
    for f in FAILS:
        report.append("  - " + f)
report.append("=" * 68)

out = os.path.join(HERE, "selftest_result.txt")
with open(out, "w", encoding="utf-8") as fp:
    fp.write("\n".join(report))
print("written:", out)
print("FAILS =", len(FAILS))

# 显式退出码，方便接 CI / 部署脚本做「不过就别继续」的判断
sys.exit(1 if FAILS else 0)
