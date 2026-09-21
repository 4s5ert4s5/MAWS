"""
MAWS v0.2.1 原型 —— 多智能体写作工作室
三个 AI 导师陪你写,不替你写。
(v0.2.1 修复:点击"发送"后清空输入框导致的报错)
"""
import os, json, datetime, re
import streamlit as st
from openai import OpenAI

st.set_page_config(page_title="MAWS 写作工作室 · 原型", page_icon="✍️", layout="wide")

CODE_VERSION = "0.2.1"

# ---------- 配置:云端从 Secrets 读取,本地从环境变量读取 ----------
def cfg(key: str, default: str = "") -> str:
    try:
        v = st.secrets[key]
        if v:
            return v
    except Exception:
        pass
    return os.getenv(key, default)

MODEL = cfg("MAWS_MODEL", "gpt-4o-mini")
PROMPT_VERSION = "v0.2"

@st.cache_resource
def get_client():
    return OpenAI(api_key=cfg("OPENAI_API_KEY"),
                  base_url=cfg("OPENAI_BASE_URL") or None)
client = get_client()

# ---------- 人设 ----------
SHARED_RULES = """\
你是 MAWS 写作工作室中的一位 AI 导师,与另外两位同事共同服务一位学生写作者。
同事包括:引导者(规划与反思)、挑战者(质疑与论证审查)、支持者(语言与概念解释)。你只做自己的角色,不越界。
铁律:学生是唯一的作者。你的任务是支持学生的思考,而不是代替学生写作;未经明确请求,不得改写或生成大段文字。
每次回复不超过 150 字,一次只谈一个重点。
用中文与学生交流;引用学生原文或举例时保留原文语言。
若收到【其他导师最近立场】摘要:不要重复他们已说的观点;同意须补充新证据,不同意则直接引用并说明理由。
引用学生原文必须逐字,不得虚构。若学生的问题明显超出你的角色,先说明,并以「→建议找X」结尾。
"""

AGENTS = {
    "引导者": {
        "icon": "🧭",
        "temp": 0.5,
        "hint": "试着问我:我的论点清楚吗?结构怎么搭?",
        "prompt": """\
你是【引导者 Facilitator】:帮助学生规划写作、反思过程、管理修改节奏。
1. 通过提问推进:一次最多 1–2 个问题,聚焦最关键的决策(论点、结构、读者)。
2. 可以复述学生自己的观点来确认理解,但不要提出新论点或新内容。
3. 学生卡住时,给出思考框架(如:主张—理由—证据—让步),而不是答案。
4. 学生拖延或过度纠结时,建议一个具体的小步骤。""",
    },
    "挑战者": {
        "icon": "⚔️",
        "temp": 0.9,
        "hint": "让我攻击你这段话最弱的论证试试。",
        "prompt": """\
你是【挑战者 Challenger】:一位友善但严格的批判性读者。
1. 你只提问和质疑,永远不提供修改后的文本或答案。
2. 每次回复:指出论证中最薄弱的一个点,并给出一个具体反例或追问。
3. 必要时代入反方立场("如果审稿人认为……你会怎么回应?")。
4. 即使论证总体不错,也要找到值得推敲之处;但不为挑刺而挑刺。
5. 学生理由充分时,明确承认,再转向下一个薄弱点。""",
    },
    "支持者": {
        "icon": "🛠️",
        "temp": 0.6,
        "hint": "可以让我解释概念;勾选'请求示例'后我才会给示例。",
        "prompt": """\
你是【支持者 Supporter】:语言与知识助手。
1. 默认只解释和澄清;仅当学生明确勾选"请求示例"或明确要求时,才提供示例句/段。
2. 给示例时必须解释它为什么有效,并提醒学生改写成自己的表达。
3. 学生要求你直接代写时,温和拒绝,改为一半示范或提问引导。
4. 学生表达不清时,先复述他的意思请他确认。""",
    },
}

STAGES = {
    "构思/大纲": "【写作阶段】学生正在构思论点、搭建大纲。优先关注:论点是否清晰、立场是否明确、结构是否合理。",
    "写作中":   "【写作阶段】学生正在起草正文。优先关注:段落展开、论证推进、读者意识。",
    "修改":     "【写作阶段】学生正在修改草稿。优先关注:论证漏洞、语言准确性、整体连贯。",
}

SAMPLE_PARAGRAPH = (
    "Online learning is better than traditional classroom learning. "
    "First, it is more convenient because students can study anywhere. "
    "Second, it is cheaper. In addition, many students prefer using computers. "
    "Therefore, universities should replace face-to-face classes with online courses."
)

# ---------- 会话状态 ----------
def init_state():
    defaults = [("history", {n: [] for n in AGENTS}),
                ("stances", {n: "" for n in AGENTS}),
                ("log", []),
                ("roundtable", {})]
    for key, val in defaults:
        if key not in st.session_state:
            st.session_state[key] = val

init_state()

def log_interaction(agent, stage, user_msg, reply):
    st.session_state["log"].append({
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "agent": agent, "stage": stage, "user": user_msg, "reply": reply,
    })

def ask(name, draft, stage, history, user_msg, force_example=False, include_digest=False):
    extra = ""
    if name == "支持者" and force_example:
        extra = "\n【注意】学生已明确请求示例,现在可以提供示例句/段,但必须解释有效性并提醒学生改写。"
    if include_digest:
        peers = [f"{AGENTS[k]['icon']} {k}:{v}" for k, v in st.session_state["stances"].items()
                 if k != name and v]
        if peers:
            extra += "\n【其他导师最近立场】\n" + "\n".join(peers)
    system = f"{SHARED_RULES}\n{AGENTS[name]['prompt']}\n{STAGES[stage]}{extra}"
    user_content = f"【学生当前草稿】\n{draft.strip() or '(还没有动笔)'}\n\n【学生说】{user_msg.strip()}"
    messages = [{"role": "system", "content": system}, *history,
                {"role": "user", "content": user_content}]
    try:
        resp = client.chat.completions.create(
            model=MODEL, messages=messages, temperature=AGENTS[name]["temp"])
        reply = resp.choices[0].message.content
    except Exception as e:
        st.error(f"调用失败:{e}\n请检查 Secrets 中的 OPENAI_API_KEY / OPENAI_BASE_URL / MAWS_MODEL")
        return None
    history.append({"role": "user", "content": user_msg.strip()})
    history.append({"role": "assistant", "content": reply})
    log_interaction(name, stage, user_msg, reply)
    st.session_state["stances"][name] = reply.split("\n")[0][:100]
    return reply

HANDOFF_RE = re.compile(r"→\s*建议找(引导者|挑战者|支持者)")

def run_roundtable(draft, stage, focus):
    """三方会诊:挑战者开火 → 支持者回应 → 引导者收束"""
    out = {}
    tail = f"(我特别关心:{focus.strip()})" if focus.strip() else ""
    out["挑战者"] = ask("挑战者", draft, stage, st.session_state["history"]["挑战者"],
        f"通读我的草稿,攻击其中最弱的一个论证,并在结尾点名一个希望支持者解释的问题。{tail}",
        include_digest=True) or "(调用失败)"
    out["支持者"] = ask("支持者", draft, stage, st.session_state["history"]["支持者"],
        f"挑战者刚才的质疑如下:\n“{out['挑战者']}”\n请回应其中你最认同与最不认同的一点,再从语言/概念角度给一条建议。",
        include_digest=True) or "(调用失败)"
    out["引导者"] = ask("引导者", draft, stage, st.session_state["history"]["引导者"],
        f"两位同事刚才的发言:\n【挑战者】{out['挑战者']}\n【支持者】{out['支持者']}\n"
        "请标注:①共识区 ②真实分歧区 ③2–3个留给学生决策的问题。不要下结论,不要改写文本。",
        include_digest=True) or "(调用失败)"
    return out

# ---------- 界面 ----------
st.title("✍️ MAWS 写作工作室(原型)")
st.caption(f"模型:{MODEL} · Prompt {PROMPT_VERSION} · 界面 v{CODE_VERSION} · 三个 AI 导师陪你写,不替你写")

# 回调:清空所有对话(回调在页面重绘前执行,可以安全清空输入框)
def clear_all():
    for n in AGENTS:
        st.session_state["history"][n] = []
        st.session_state["stances"][n] = ""
        st.session_state.pop(f"in_{n}", None)
        st.session_state.pop(f"pending_{n}", None)
    st.session_state["roundtable"] = {}
    st.session_state["log"] = []

# 回调:载入示例段落
def load_sample():
    st.session_state["draft"] = SAMPLE_PARAGRAPH

# 回调:发送(先把输入存入"待发送"中转站,再清空输入框)
def make_send(name):
    def _send():
        msg = (st.session_state.get(f"in_{name}", "") or "").strip()
        if msg:
            st.session_state[f"pending_{name}"] = msg
            st.session_state[f"in_{name}"] = ""
    return _send

with st.sidebar:
    stage = st.radio("写作阶段", list(STAGES.keys()))
    st.divider()
    st.button("🗑️ 清空所有对话", on_click=clear_all)
    st.caption("清空只影响你自己当前的会话。")

st.button("📄 载入示例段落(快速测试用)", on_click=load_sample)
draft = st.text_area("我的草稿", key="draft", height=240,
                     placeholder="在这里写或粘贴你的草稿……")

tabs = st.tabs([f'{AGENTS[n]["icon"]} {n}' for n in AGENTS] + ["🪑 三方会诊"])

for tab, name in zip(tabs, AGENTS):
    with tab:
        h = st.session_state["history"][name]
        if not h:
            st.info(f"{AGENTS[name]['icon']} {name}已就位。{AGENTS[name]['hint']}")
        # 处理"待发送"消息:先调用、更新历史,再渲染
        pending = st.session_state.pop(f"pending_{name}", None)
        if pending:
            force = (name == "支持者") and st.session_state.get("req_example", False)
            with st.spinner(f"{name}思考中……"):
                ask(name, draft, stage, h, pending, force_example=force)
        for m in h:
            with st.chat_message("user" if m["role"] == "user" else "assistant"):
                st.markdown(m["content"])
        if name == "支持者":
            st.checkbox("✅ 我明确请求示例(勾选后支持者才会给示例)", key="req_example")
        st.text_area("对TA说……", key=f"in_{name}", height=70)
        st.button("发送", key=f"go_{name}", on_click=make_send(name))

with tabs[3]:
    focus = st.text_input("这次最想让导师们关注什么?(可选)", key="focus")
    if st.button("🪑 召集三方会诊", type="primary") and draft.strip():
        with st.spinner("三位导师正在阅读你的草稿……"):
            st.session_state["roundtable"] = run_roundtable(draft, stage, focus)
        st.rerun()
    if st.session_state["roundtable"]:
        cols = st.columns(3)
        for col, name in zip(cols, AGENTS):
            with col:
                st.markdown(f'#### {AGENTS[name]["icon"]} {name}')
                reply = st.session_state["roundtable"].get(name) or "_(未调用)_"
                st.markdown(reply)
                m = HANDOFF_RE.search(reply)
                if m:
                    st.info(f"🔀 认为这个问题更适合 {m.group(1)},可切换到对应标签继续深聊")
        st.caption("👀 观察点:三位导师在哪里一致、在哪里冲突?冲突处正是你要自己决策的地方。")

st.divider()
payload = {
    "exported_at": datetime.datetime.now().isoformat(timespec="seconds"),
    "prompt_version": PROMPT_VERSION, "model": MODEL, "stage": stage,
    "draft": draft,
    "conversations": st.session_state["history"],
    "roundtable": st.session_state["roundtable"],
    "event_log": st.session_state["log"],
}
st.download_button("⬇️ 导出研究日志(JSON)",
                   data=json.dumps(payload, ensure_ascii=False, indent=2),
                   file_name=f"maws_log_{datetime.datetime.now():%Y%m%d_%H%M%S}.json")
