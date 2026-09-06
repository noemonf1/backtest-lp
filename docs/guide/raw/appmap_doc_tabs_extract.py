"""Re-extract the Documentation tab text from AppTest using DIRECT children
only (the first harness walked recursively, which duplicated sub-tab text
into the parent).  Output: docs/guide/raw/appmap_doc_tabs.txt"""
from streamlit.testing.v1 import AppTest
at = AppTest.from_file("/home/user/backtest-lp/app.py", default_timeout=600)
at.run()
assert not at.exception
out = []
def direct(block):
    for ch in block.children.values():
        t = getattr(ch, "type", "")
        if t in ("markdown", "caption", "subheader"):
            out.append(f"[{t}] {ch.value}")
        elif t == "help_info":
            p = ch.proto
            out.append(f"[st.help] {p.name} ({p.type}): {p.value}")
            out.append(f"[st.help doc_string] {p.doc_string}")
        elif t == "divider":
            out.append("[divider]")
        elif t == "tab_container":
            pass  # sub-tabs are handled explicitly below
doc = [t for t in at.tabs if t.label == "Documentation"][0]
out.append("=== Documentation tab: top-level text (app.py:2010-2020) ===")
direct(doc)
for ch in doc.children.values():
    if getattr(ch, "type", "") == "tab_container":
        for sub in ch.children.values():
            out.append(f"\n=== Sub-tab: {sub.label} ===")
            direct(sub)
text = "\n".join(out) + "\n"
open("/home/user/backtest-lp/docs/guide/raw/appmap_doc_tabs.txt", "w").write(text)
print(text)
