import json
import os
import re
import threading
from pathlib import Path

import streamlit as st

DATA_FILE = Path(__file__).parent / "items.json"

NOTICE = (
    "ℹ️ 이 목록은 모든 방문자가 함께 쓰며, 서버가 재시작되면 초기화될 수 있어요."
)


# ---------- 데이터 로드/저장 ----------
# 파일이 유일한 원본이다. 세션은 목록을 캐시하지 않고, 변경은 항상
# "락 → 파일 읽기 → 수정 → 원자적 쓰기"로 처리해서 여러 탭/방문자가
# 서로의 변경을 덮어쓰지 않게 한다.
@st.cache_resource
def _lock():
    return threading.Lock()


def _parse_done(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value) if isinstance(value, int) else False


def load_data():
    """{"next_id": int, "items": [...]}를 돌려준다.

    파일이 없거나 깨졌어도, 값이 이상해도 예외 없이 가능한 만큼만 복구한다.
    (구버전 형식인 리스트도 읽는다. id가 중복되면 앞의 것만 남긴다.)
    """
    try:
        raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"next_id": 1, "items": []}

    stored_next = 1
    if isinstance(raw, dict):
        entries = raw.get("items", [])
        if isinstance(raw.get("next_id"), int) and not isinstance(raw.get("next_id"), bool):
            stored_next = raw["next_id"]
    else:
        entries = raw
    if not isinstance(entries, list):
        entries = []

    items, seen = [], set()
    for d in entries:
        if not isinstance(d, dict):
            continue
        item_id, name = d.get("id"), d.get("name")
        if isinstance(item_id, bool) or not isinstance(item_id, int) or item_id in seen:
            continue
        if not isinstance(name, str) or not name.strip():
            continue
        seen.add(item_id)
        items.append(
            {"id": item_id, "name": name.strip(), "done": _parse_done(d.get("done"))}
        )
    # 삭제 후에도, 새로고침 후에도 id가 재사용되지 않도록 카운터를 파일에 둔다.
    next_id = max([stored_next] + [i["id"] + 1 for i in items])
    return {"next_id": next_id, "items": items}


def save_data(data):
    """임시 파일에 쓴 뒤 교체해서, 쓰다가 끊겨도 파일이 깨지지 않게 한다."""
    tmp = DATA_FILE.with_name(DATA_FILE.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, DATA_FILE)
        return True
    except OSError:
        st.toast("목록을 파일에 저장하지 못했어요.", icon="⚠️")
        return False


def mutate(fn):
    """파일을 읽어 fn(data)로 수정하고 저장한다. fn의 반환값을 돌려준다."""
    with _lock():
        data = load_data()
        result = fn(data)
        save_data(data)
    return result


# ---------- 표시용 ----------
def md_escape(text):
    """이름이 마크다운으로 해석되지 않게 ASCII 기호를 모두 이스케이프한다."""
    return re.sub(r"([!-/:-@\[-`{-~])", r"\\\1", text)


def short(text, limit=20):
    return text if len(text) <= limit else text[:limit] + "…"


# ---------- 상태 ----------
def init_state():
    if "editing_id" not in st.session_state:
        st.session_state.editing_id = None


# ---------- 콜백 ----------
def add_item():
    name = st.session_state.get("new_item", "").strip()
    if not name:
        st.toast("아이템 이름을 입력해주세요.", icon="⚠️")
        return

    def op(data):
        data["items"].append({"id": data["next_id"], "name": name, "done": False})
        data["next_id"] += 1

    mutate(op)
    st.session_state.new_item = ""
    st.toast(f"'{md_escape(short(name))}' 추가했어요", icon="✅")


def toggle_item(item_id):
    done = bool(st.session_state.get(f"chk_{item_id}"))

    def op(data):
        for item in data["items"]:
            if item["id"] == item_id:
                item["done"] = done

    mutate(op)


def delete_item(item_id):
    def op(data):
        data["items"] = [i for i in data["items"] if i["id"] != item_id]

    mutate(op)
    if st.session_state.editing_id == item_id:
        st.session_state.editing_id = None
    st.toast("삭제했어요", icon="🗑️")


def start_edit(item_id):
    name = next((i["name"] for i in load_data()["items"] if i["id"] == item_id), None)
    if name is None:
        st.toast("이미 삭제된 항목이에요.", icon="⚠️")
        return
    st.session_state.editing_id = item_id
    st.session_state[f"edit_{item_id}"] = name


def cancel_edit():
    st.session_state.editing_id = None


def save_edit(item_id):
    name = st.session_state.get(f"edit_{item_id}", "").strip()
    if not name:
        st.toast("이름을 비워둘 수 없어요.", icon="⚠️")
        return

    def op(data):
        for item in data["items"]:
            if item["id"] == item_id:
                item["name"] = name
                return True
        return False

    found = mutate(op)
    st.session_state.editing_id = None
    if found:
        st.toast("수정했어요", icon="✏️")
    else:
        st.toast("이미 삭제된 항목이에요.", icon="⚠️")


def clear_done():
    def op(data):
        data["items"] = [i for i in data["items"] if not i["done"]]

    mutate(op)
    st.toast("완료 항목을 삭제했어요", icon="🧹")


# ---------- 화면 ----------
# 고정 CSS만 사용한다. 사용자 입력은 HTML에 삽입하지 않는다.
CSS = """
<style>
/* 좁은 화면에서도 한 줄 유지 (Streamlit은 기본적으로 세로로 쌓는다) */
[class*="st-key-row_"] [data-testid="stHorizontalBlock"],
[data-testid="stForm"] [data-testid="stHorizontalBlock"] {
    flex-wrap: nowrap !important;
    gap: 0.4rem !important;
}
[class*="st-key-row_"] [data-testid="stColumn"],
[data-testid="stForm"] [data-testid="stColumn"] {
    min-width: 0 !important;
}
/* 카드형 행 */
[class*="st-key-row_"] {
    border: 1px solid rgba(128, 128, 128, 0.28);
    border-radius: 12px;
    padding: 0.35rem 0.75rem;
    margin-bottom: 0.4rem;
}
[class*="st-key-row_"] [data-testid="stVerticalBlock"] { gap: 0.25rem; }
/* 완료 항목은 흐리게 */
[class*="st-key-row_done_"] label { opacity: 0.5; }
/* 수정/삭제 아이콘 버튼은 작게 */
[class*="st-key-row_"] button {
    min-height: 2rem;
    padding: 0.1rem 0.5rem;
}
/* 수정 모드의 폼은 카드 안에서 여백 없이 */
[class*="st-key-row_edit_"] [data-testid="stForm"] { padding: 0; }
/* 하단 영역 구분 */
.st-key-footer {
    margin-top: 1rem;
    padding-top: 0.75rem;
    border-top: 1px solid rgba(128, 128, 128, 0.28);
}
.empty-box {
    text-align: center;
    padding: 2.5rem 1rem;
    border: 2px dashed rgba(128, 128, 128, 0.35);
    border-radius: 14px;
    opacity: 0.85;
}
.empty-box .big { font-size: 2.4rem; }
</style>
"""


def inject_css():
    st.markdown(CSS, unsafe_allow_html=True)


def render_header():
    st.title("🛒 쇼핑 리스트")


def render_add_form():
    with st.form("add_form", clear_on_submit=False, border=False):
        col_input, col_btn = st.columns([5, 2], vertical_alignment="bottom")
        col_input.text_input(
            "아이템 추가", key="new_item", placeholder="예: 우유 2팩"
        )
        col_btn.form_submit_button(
            "추가", on_click=add_item, type="primary", use_container_width=True
        )


def render_summary(items):
    done = sum(1 for i in items if i["done"])
    st.progress(done / len(items), text=f"전체 {len(items)}개 중 {done}개 완료")


def render_row(item):
    item_id = item["id"]
    if st.session_state.editing_id == item_id:
        with st.container(key=f"row_edit_{item_id}"):
            # 폼이라서 Enter로도 저장된다. 첫 번째 제출 버튼이 저장이다.
            with st.form(f"edit_form_{item_id}", border=False):
                col_input, col_save, col_cancel = st.columns(
                    [5, 2, 2], vertical_alignment="center"
                )
                col_input.text_input(
                    "이름 수정", key=f"edit_{item_id}", label_visibility="collapsed"
                )
                col_save.form_submit_button(
                    "저장", key=f"save_{item_id}", on_click=save_edit,
                    args=(item_id,), type="primary", use_container_width=True,
                )
                col_cancel.form_submit_button(
                    "취소", key=f"cancel_{item_id}", on_click=cancel_edit,
                    use_container_width=True,
                )
        return

    state = "done" if item["done"] else "todo"
    # 다른 탭/방문자가 바꾼 값이 보이도록 파일 값을 위젯 상태에 반영한다.
    st.session_state[f"chk_{item_id}"] = item["done"]
    with st.container(key=f"row_{state}_{item_id}"):
        col_chk, col_edit, col_del = st.columns([8, 1.2, 1.2], vertical_alignment="center")
        name = md_escape(item["name"])
        col_chk.checkbox(
            f"~~{name}~~" if item["done"] else name,
            key=f"chk_{item_id}",
            on_change=toggle_item,
            args=(item_id,),
        )
        col_edit.button(
            "✏️", key=f"edit_btn_{item_id}", on_click=start_edit, args=(item_id,),
            help="수정", type="tertiary",
        )
        col_del.button(
            "🗑️", key=f"del_{item_id}", on_click=delete_item, args=(item_id,),
            help="삭제", type="tertiary",
        )


def render_empty():
    st.markdown(
        '<div class="empty-box"><div class="big">🧺</div>'
        "<div>아직 등록된 아이템이 없어요</div>"
        "<small>위 입력창에 살 물건을 적어보세요</small></div>",
        unsafe_allow_html=True,
    )


def render_list(items):
    if not items:
        render_empty()
        return
    render_summary(items)
    for item in items:
        render_row(item)
    has_done = any(i["done"] for i in items)
    with st.container(key="footer"):
        st.button(
            "🧹 완료 항목 모두 삭제", on_click=clear_done, disabled=not has_done,
            use_container_width=True,
        )


def main():
    st.set_page_config(page_title="쇼핑 리스트", page_icon="🛒", layout="centered")
    init_state()
    inject_css()
    items = load_data()["items"]
    # 다른 곳에서 지워진 항목을 수정 중이었다면 수정 모드를 해제한다.
    if st.session_state.editing_id not in {i["id"] for i in items}:
        st.session_state.editing_id = None
    render_header()
    render_add_form()
    render_list(items)
    st.caption(NOTICE)


main()
