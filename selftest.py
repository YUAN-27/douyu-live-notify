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
