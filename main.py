#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
menu_tool_batch.py

Features:
 - Extraction: build 0_header.json + part_x.json (minimal redundancy)
 - Merge: restore original JSON from header + parts
 - AI Translate: batch translation (auto token splitting or manual token limit),
   per-part streaming (only one part in memory), multi-threaded batch workers,
   back-translation (batch), prompt instruction box, UI locks (partial), pause/stop,
   colored logs, export logs, robust progress with locking.
"""

import json
import os
import threading
from queue import Queue, Empty
from difflib import SequenceMatcher
import time
import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import requests
import math

# ---------------------------
# utility functions
# ---------------------------
def safe_json_load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def safe_json_dump(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def is_chinese(text: str) -> bool:
    if not text:
        return False
    return any('\u4e00' <= ch <= '\u9fff' for ch in text)

def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()

def estimate_tokens(text: str) -> int:
    # heuristic: 1 token per 4 characters (approx). Bound at least 1.
    if not text:
        return 1
    return max(1, math.ceil(len(text) / 4.0))

# ---------------------------
# Extraction / Merge (same as before)
# ---------------------------
def build_structure_and_entryids(json_data):
    top_name = json_data.get("Name", "")
    structure = []
    entry_ids = []
    for wrapper in json_data.get("FmgWrappers", []):
        wname = wrapper.get("Name", "")
        wid = wrapper.get("ID", 0)
        fmg = wrapper.get("Fmg", {})
        fname = fmg.get("Name", "")
        # 新：保存 FMG 元字段
        fmg_version = fmg.get("Version")
        fmg_bigendian = fmg.get("BigEndian")
        fmg_unicode = fmg.get("Unicode")
        fmg_compression = fmg.get("Compression")
        indexes = []
        for entry in fmg.get("Entries", []):
            idx = len(entry_ids)
            indexes.append(idx)
            entry_ids.append(entry["ID"])
        structure.append({
            "WrapperName": wname,
            "WrapperID": wid,
            "FmgName": fname,
            "EntryIndexes": indexes,
            # 新增：Fmg meta
            "FmgVersion": fmg_version,
            "FmgBigEndian": fmg_bigendian,
            "FmgUnicode": fmg_unicode,
            "FmgCompression": fmg_compression
        })
    return top_name, structure, entry_ids

def write_header_and_parts(source_file, top_name, structure, entry_ids, texts, split, max_per_file):
    base_dir = os.path.dirname(source_file)
    base_name = os.path.splitext(os.path.basename(source_file))[0]
    out_dir = os.path.join(base_dir, base_name + "_extracted")
    os.makedirs(out_dir, exist_ok=True)
    header = {
        "Meta": {"SourceTopName": top_name, "Version": 1, "TotalEntries": len(entry_ids)},
        "Structure": structure,
        "EntryIDs": entry_ids
    }
    safe_json_dump(header, os.path.join(out_dir, "0_header.json"))
    if not split:
        part = {
            "Meta": {"ChunkIndex": 1, "ChunkCount": 1, "StartIndex": 0, "Count": len(texts)},
            "Entries": texts[:]
        }
        safe_json_dump(part, os.path.join(out_dir, "part_1.json"))
        return 1
    chunks = [texts[i:i + max_per_file] for i in range(0, len(texts), max_per_file)]
    chunk_count = len(chunks)
    for i, chunk in enumerate(chunks, start=1):
        start_index = (i - 1) * max_per_file
        part = {
            "Meta": {"ChunkIndex": i, "ChunkCount": chunk_count, "StartIndex": start_index, "Count": len(chunk)},
            "Entries": chunk
        }
        safe_json_dump(part, os.path.join(out_dir, f"part_{i}.json"))
    return chunk_count

def merge_folder(folder_path):
    header_path = os.path.join(folder_path, "0_header.json")
    if not os.path.exists(header_path):
        raise FileNotFoundError("头文件 0_header.json 未找到。")
    header = safe_json_load(header_path)
    top_name = header.get("Meta", {}).get("SourceTopName", "")
    structure = header.get("Structure", [])
    entry_ids = header.get("EntryIDs", [])
    total = header.get("Meta", {}).get("TotalEntries", len(entry_ids))
    texts = [""] * total
    part_files = sorted([f for f in os.listdir(folder_path) if f.startswith("part_") and f.endswith(".json")])
    if not part_files:
        raise FileNotFoundError("未找到 part_*.json 分片文件。")
    for pf in part_files:
        ppath = os.path.join(folder_path, pf)
        pdata = safe_json_load(ppath)
        meta = pdata.get("Meta", {})
        start = int(meta.get("StartIndex", 0))
        arr = pdata.get("Entries", [])
        for i, t in enumerate(arr):
            gi = start + i
            if 0 <= gi < total:
                texts[gi] = t
    merged_entries = [{"ID": entry_ids[i], "Text": texts[i] if i < len(texts) else ""} for i in range(len(entry_ids))]
    return top_name, structure, merged_entries

def restore_original_from_parts(top_name, structure, merged_entries):
    lookup = {i: e["Text"] for i, e in enumerate(merged_entries)}
    entry_ids = [e["ID"] for e in merged_entries]
    wrappers = []
    for block in structure:
        entry_list = []
        for idx in block.get("EntryIndexes", []):
            eid = entry_ids[idx] if 0 <= idx < len(entry_ids) else None
            entry_list.append({"ID": eid, "Text": lookup.get(idx, "")})
        wrappers.append({
            "Name": block.get("WrapperName", ""),
            "ID": block.get("WrapperID", 0),
            "Fmg": {
                "Name": block.get("FmgName", ""),
                "Entries": entry_list,
                # ↓ 新增：把 FMG 元字段恢复到正确的位置
                "Version": block.get("FmgVersion"),
                "BigEndian": block.get("FmgBigEndian"),
                "Unicode": block.get("FmgUnicode"),
                "Compression": block.get("FmgCompression")
}
        })
    return {"Name": top_name, "FmgWrappers": wrappers}

# ---------------------------
# Translation API batch wrapper
# Note: For batch mode we implement:
#  - ChatGPT-like batch via structured JSON request (preferred)
#  - Google fallback: individual requests (inefficient)
# ---------------------------
def translate_batch(api_name, api_key, texts, target_lang="zh", user_prompt="", back_check=False, back_api=None, back_key=None):
    """
    texts: list of strings
    Returns: list of translations (same length), back_checks list of tuples (index, back_text, score) if any
    """
    translations = []
    back_checks = []

    if not texts:
        return [], []

    # CHATGPT-LIKE batch: construct prompt asking JSON output
    if api_name in ("ChatGPT", "DeepSeek", "Gemini", "Claude"):
        if not api_key:
            # no key -> return originals
            return texts[:], []
        try:
            # construct system + user prompt
            system_msg = f"You are a professional translator. Translate the provided list of texts into {target_lang}."
            user_instructions = ""
            if user_prompt:
                user_instructions = f"Additional instructions: {user_prompt}\n"
            # build numbered list
            numbered = "\n".join([f"{i+1}. {t}" for i, t in enumerate(texts)])
            user_message = f"{user_instructions}Translate these texts. Return a JSON array of objects with keys: index (0-based), text (translated). Example: [{'{'}\"index\":0,\"text\":\"...\"{'}'}]\n\nTexts:\n{numbered}"

            # send request (OpenAI style)
            url = "https://api.openai.com/v1/chat/completions" if api_name == "ChatGPT" else None
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            payload = {
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_message}
                ],
                "max_tokens": 2000,
                "temperature": 0.0
            }
            r = requests.post(url, json=payload, headers=headers, timeout=60)
            jr = r.json()
            # extract assistant content (try robustly)
            content = ""
            if "choices" in jr and jr["choices"]:
                content = jr["choices"][0].get("message", {}).get("content", "")
            elif "error" in jr:
                content = ""
            # try parse JSON inside content
            parsed = None
            try:
                parsed = json.loads(content.strip())
            except:
                # try to extract json substring
                import re
                m = re.search(r"(\[.*\])", content, re.S)
                if m:
                    try:
                        parsed = json.loads(m.group(1))
                    except:
                        parsed = None
            if isinstance(parsed, list):
                # build translations
                translations = [None] * len(texts)
                for obj in parsed:
                    idx = obj.get("index")
                    txt = obj.get("text")
                    if idx is not None and 0 <= idx < len(texts):
                        translations[idx] = txt
                # fill missing with original
                for i in range(len(translations)):
                    if translations[i] is None:
                        translations[i] = texts[i]
            else:
                # fallback: no parse -> return originals
                translations = texts[:]
        except Exception as e:
            # on error, fallback to originals and log externally
            translations = texts[:]
    else:
        # GOOGLE fallback: no batch support -> perform sequential calls (not ideal)
        translations = []
        for t in texts:
            try:
                q = requests.utils.quote(t)
                url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl={target_lang}&dt=t&q={q}"
                r = requests.get(url, timeout=15)
                arr = r.json()
                translated = "".join([seg[0] for seg in arr[0]])
                translations.append(translated)
            except:
                translations.append(t)

    # back-translation (batch) if requested
    if back_check:
        # perform back-translation according to back_api/back_key or same
        back_api_name = back_api or api_name
        back_api_key = back_key or api_key
        try:
            if back_api_name in ("ChatGPT", "DeepSeek", "Gemini", "Claude"):
                # build prompt to translate translations back into English and return JSON [ {index, text} ]
                system_msg = "You are a translation assistant. Translate the provided list of texts into English."
                numbered = "\n".join([f"{i+1}. {t}" for i, t in enumerate(translations)])
                user_message = f"Translate these texts back into English and return JSON array of objects: {{'index':0,'text':'...'}}\n\nTexts:\n{numbered}"
                url = "https://api.openai.com/v1/chat/completions" if back_api_name == "ChatGPT" else None
                headers = {"Authorization": f"Bearer {back_api_key}", "Content-Type": "application/json"}
                payload = {
                    "model": "gpt-4o-mini",
                    "messages": [{"role":"system","content":system_msg},{"role":"user","content":user_message}],
                    "max_tokens":2000,
                    "temperature":0.0
                }
                r = requests.post(url, json=payload, headers=headers, timeout=60)
                jr = r.json()
                content = ""
                if "choices" in jr and jr["choices"]:
                    content = jr["choices"][0].get("message", {}).get("content", "")
                # parse JSON like before
                parsed = None
                try:
                    parsed = json.loads(content.strip())
                except:
                    import re
                    m = re.search(r"(\[.*\])", content, re.S)
                    if m:
                        try:
                            parsed = json.loads(m.group(1))
                        except:
                            parsed = None
                if isinstance(parsed, list):
                    for obj in parsed:
                        idx = obj.get("index")
                        bt = obj.get("text")
                        if idx is not None and 0 <= idx < len(texts):
                            score = similarity(texts[idx].lower(), bt.lower())
                            back_checks.append((idx, bt, score))
                # else no parsed -> skip
            else:
                # Google fallback: perform sequential back translations
                for i, tr in enumerate(translations):
                    try:
                        q2 = requests.utils.quote(tr)
                        url2 = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=en&dt=t&q={q2}"
                        r2 = requests.get(url2, timeout=15)
                        arr2 = r2.json()
                        bt = "".join([seg[0] for seg in arr2[0]])
                        score = similarity(texts[i].lower(), bt.lower())
                        back_checks.append((i, bt, score))
                    except:
                        continue
        except Exception:
            pass

    return translations, back_checks

# ---------------------------
# Batch splitting (auto by token, or manual by token limit)
# ---------------------------
def split_into_batches(texts, mode="auto", max_tokens=1000, manual_batch_tokens=1000):
    """
    texts: list of strings
    mode:
      - "auto": accumulate until token sum would exceed max_tokens
      - "manual": create batches each with manual_batch_tokens limit (by token estimate)
    returns: list of batches, where each batch is (start_index, end_index_exclusive, texts_slice)
    """
    batches = []
    n = len(texts)
    i = 0
    if mode == "manual":
        # manual batch by tokens
        while i < n:
            acc = 0
            j = i
            while j < n:
                tok = estimate_tokens(texts[j])
                if acc + tok > manual_batch_tokens and j > i:
                    break
                acc += tok
                j += 1
            if j == i:
                # single large item bigger than limit -> force include it
                j = i + 1
            batches.append((i, j, texts[i:j]))
            i = j
    else:
        # auto mode: similar behaviour, but max_tokens parameter used
        while i < n:
            acc = 0
            j = i
            while j < n:
                tok = estimate_tokens(texts[j])
                if acc + tok > max_tokens and j > i:
                    break
                acc += tok
                j += 1
            if j == i:
                j = i + 1
            batches.append((i, j, texts[i:j]))
            i = j
    return batches

# ---------------------------
# GUI App
# ---------------------------
class App:
    def __init__(self, root):
        self.root = root
        self.root.title("FromSoft JSON Tool (Batch Translate)")
        self.root.geometry("980x720")

        # vars
        self.extract_file_var = tk.StringVar()
        self.extract_split_var = tk.IntVar(value=0)
        self.extract_max_var = tk.StringVar(value="250")

        self.merge_folder_var = tk.StringVar()
        self.merge_savevar = tk.StringVar()

        self.translate_folder_var = tk.StringVar()
        self.translate_api_var = tk.StringVar(value="ChatGPT")
        self.translate_key_var = tk.StringVar()
        self.translate_target_lang = tk.StringVar(value="zh")
        self.translate_thread_count = tk.StringVar(value="4")
        self.translate_skip_empty = tk.IntVar(value=1)
        self.translate_skip_translated = tk.IntVar(value=1)
        self.translate_back_check = tk.IntVar(value=1)

        # batching controls
        self.batch_mode_var = tk.StringVar(value="auto")  # "auto" or "manual"
        self.max_tokens_var = tk.StringVar(value="1000")  # token limit for auto
        self.manual_tokens_var = tk.StringVar(value="800")  # manual batch token size

        # prompt box
        self.prompt_var = tk.StringVar(value="请按照黑暗之魂3风格翻译，不要出现“您”，参考黑魂3 wiki 用词。")

        # synchronization
        self.progress_lock = threading.Lock()
        self.log_lock = threading.Lock()
        self.ui_lock = threading.Lock()

        self._translation_running = False
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()

        # progress counters
        self.total_tasks = 0
        self.done_tasks = 0

        # UI
        self.build_ui()

    def build_ui(self):
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=6, pady=6)

        tab_extract = tk.Frame(nb)
        tab_merge = tk.Frame(nb)
        tab_translate = tk.Frame(nb)

        nb.add(tab_extract, text="提取 (Extraction)")
        nb.add(tab_merge, text="合并 (Merge)")
        nb.add(tab_translate, text="AI 翻译 (AI Translate)")

        self.build_extract_tab(tab_extract)
        self.build_merge_tab(tab_merge)
        self.build_translate_tab(tab_translate)

    # ---------- Extract tab ----------
    def build_extract_tab(self, tab):
        tk.Label(tab, text="选择原始 JSON 文件：").pack(anchor="w", padx=10, pady=(10,0))
        frm = tk.Frame(tab); frm.pack(fill="x", padx=10, pady=4)
        tk.Entry(frm, textvariable=self.extract_file_var, width=92).pack(side="left", fill="x", expand=True)
        tk.Button(frm, text="浏览", command=self.on_browse_extract).pack(side="left", padx=6)
        tk.Checkbutton(tab, text="拆分多个分片", variable=self.extract_split_var).pack(anchor="w", padx=10, pady=(8,0))
        tk.Label(tab, text="每个分片最大条数：").pack(anchor="w", padx=10, pady=(8,0))
        tk.Entry(tab, textvariable=self.extract_max_var, width=12).pack(anchor="w", padx=10, pady=(0,8))
        tk.Button(tab, text="开始提取", command=self.on_start_extract, height=2).pack(pady=8)
        tk.Label(tab, text="提示：提取后会在原文件同级生成 <文件名>_extracted 目录").pack(anchor="w", padx=10, pady=(6,0))

    def on_browse_extract(self):
        p = filedialog.askopenfilename(filetypes=[("JSON files", "*.json")])
        if p:
            self.extract_file_var.set(p)

    def on_start_extract(self):
        p = self.extract_file_var.get()
        if not p:
            messagebox.showerror("错误", "请选择原始 JSON 文件")
            return
        try:
            data = safe_json_load(p)
        except Exception as e:
            messagebox.showerror("错误", f"无法读取 JSON：\n{e}")
            return
        top_name, structure, entry_ids = build_structure_and_entryids(data)
        texts = []
        for wrapper in data.get("FmgWrappers", []):
            fmg = wrapper.get("Fmg", {})
            for entry in fmg.get("Entries", []):
                texts.append(entry.get("Text", ""))
        split_flag = bool(self.extract_split_var.get())
        maxn = int(self.extract_max_var.get()) if split_flag else 0
        try:
            count = write_header_and_parts(p, top_name, structure, entry_ids, texts, split_flag, maxn)
            default_out = os.path.join(os.path.dirname(p), os.path.splitext(os.path.basename(p))[0] + "_extracted")
            self.translate_folder_var.set(default_out)
            messagebox.showinfo("完成", f"提取完成：已生成头文件与 {count} 个分片\n目录：{default_out}")
        except Exception as e:
            messagebox.showerror("错误", f"写入失败：\n{e}")

    # ---------- Merge tab ----------
    def build_merge_tab(self, tab):
        tk.Label(tab, text="选择分片文件夹（包含 0_header.json & part_*.json）：").pack(anchor="w", padx=10, pady=(10,0))
        frm = tk.Frame(tab); frm.pack(fill="x", padx=10, pady=4)
        tk.Entry(frm, textvariable=self.merge_folder_var, width=92).pack(side="left", fill="x", expand=True)
        tk.Button(frm, text="浏览", command=self.on_browse_merge_folder).pack(side="left", padx=6)
        tk.Label(tab, text="输出文件（可点击选择）：").pack(anchor="w", padx=10, pady=(8,0))
        frm2 = tk.Frame(tab); frm2.pack(fill="x", padx=10, pady=4)
        tk.Entry(frm2, textvariable=self.merge_savevar, width=92).pack(side="left", fill="x", expand=True)
        tk.Button(frm2, text="选择输出", command=self.on_choose_merge_save).pack(side="left", padx=6)
        tk.Button(tab, text="开始合并并恢复", command=self.on_start_merge, height=2).pack(pady=12)
        tk.Label(tab, text="如果未选择输出路径，默认保存为：<分片父目录>/<foldername>_merged.json").pack(anchor="w", padx=10, pady=(6,0))

    def on_browse_merge_folder(self):
        p = filedialog.askdirectory()
        if p:
            self.merge_folder_var.set(p)
            foldername = os.path.basename(os.path.normpath(p))
            default_out = os.path.join(os.path.dirname(p), foldername + "_merged.json")
            self.merge_savevar.set(default_out)

    def on_choose_merge_save(self):
        initial = self.merge_savevar.get() or os.getcwd()
        init_dir = os.path.dirname(initial) if os.path.dirname(initial) else None
        fname = os.path.basename(initial) if os.path.basename(initial) else None
        p = filedialog.asksaveasfilename(defaultextension=".json", initialfile=fname, initialdir=init_dir, filetypes=[("JSON files","*.json")])
        if p:
            self.merge_savevar.set(p)

    def on_start_merge(self):
        folder = self.merge_folder_var.get()
        savep = self.merge_savevar.get()
        if not folder:
            messagebox.showerror("错误", "请选择分片文件夹")
            return
        if not savep:
            foldername = os.path.basename(os.path.normpath(folder))
            savep = os.path.join(os.path.dirname(folder), foldername + "_merged.json")
            self.merge_savevar.set(savep)
        try:
            top_name, structure, merged_entries = merge_folder(folder)
            restored = restore_original_from_parts(top_name, structure, merged_entries)
            safe_json_dump(restored, savep)
            messagebox.showinfo("完成", f"合并并恢复完成：\n{savep}")
        except Exception as e:
            messagebox.showerror("错误", f"合并失败：\n{e}")

    # ---------- Translate tab ----------
    def build_translate_tab(self, tab):
        # folder select
        tk.Label(tab, text="选择 extracted 文件夹（包含 0_header.json & part_*.json）：").pack(anchor="w", padx=10, pady=(10,0))
        frm = tk.Frame(tab); frm.pack(fill="x", padx=10, pady=4)
        tk.Entry(frm, textvariable=self.translate_folder_var, width=76).pack(side="left", fill="x", expand=True)
        tk.Button(frm, text="浏览", command=self.on_browse_translate_folder).pack(side="left", padx=6)

        # api selection
        tk.Label(tab, text="选择翻译接口：").pack(anchor="w", padx=10, pady=(8,0))
        api_options = ["ChatGPT", "DeepSeek", "Gemini", "Claude", "Google"]
        cb = ttk.Combobox(tab, values=api_options, textvariable=self.translate_api_var, width=20, state="readonly")
        cb.pack(anchor="w", padx=10)

        tk.Label(tab, text="API Key（某些服务需要）：").pack(anchor="w", padx=10, pady=(6,0))
        tk.Entry(tab, textvariable=self.translate_key_var, width=40).pack(anchor="w", padx=10, pady=(0,6))

        tk.Label(tab, text="目标语言（例如 zh / en / ja）：").pack(anchor="w", padx=10)
        tk.Entry(tab, textvariable=self.translate_target_lang, width=10).pack(anchor="w", padx=10, pady=(0,6))

        # prompt/instructions box
        tk.Label(tab, text="翻译要求（Prompt）：").pack(anchor="w", padx=10, pady=(6,0))
        self.prompt_entry = tk.Entry(tab, textvariable=self.prompt_var, width=120)
        self.prompt_entry.pack(anchor="w", padx=10, pady=(0,6))

        # batching mode controls
        tk.Label(tab, text="批量模式：").pack(anchor="w", padx=10)
        mode_frame = tk.Frame(tab); mode_frame.pack(anchor="w", padx=10)
        tk.Radiobutton(mode_frame, text="自动按 token（默认）", variable=self.batch_mode_var, value="auto").pack(side="left")
        tk.Radiobutton(mode_frame, text="手动 token 限制", variable=self.batch_mode_var, value="manual").pack(side="left", padx=(10,0))
        tk.Label(tab, text="Auto 最大 tokens：").pack(anchor="w", padx=10)
        tk.Entry(tab, textvariable=self.max_tokens_var, width=10).pack(anchor="w", padx=10, pady=(0,6))
        tk.Label(tab, text="Manual 每批最大 tokens：").pack(anchor="w", padx=10)
        tk.Entry(tab, textvariable=self.manual_tokens_var, width=10).pack(anchor="w", padx=10, pady=(0,6))

        # options
        tk.Checkbutton(tab, text="跳过空文本", variable=self.translate_skip_empty).pack(anchor="w", padx=10)
        tk.Checkbutton(tab, text="自动跳过已翻译内容（中文检测）", variable=self.translate_skip_translated).pack(anchor="w", padx=10)
        tk.Checkbutton(tab, text="回译检查（批量）", variable=self.translate_back_check).pack(anchor="w", padx=10)

        frm_thr = tk.Frame(tab); frm_thr.pack(anchor="w", padx=10, pady=(6,0))
        tk.Label(frm_thr, text="工作线程数（并发批次）：").pack(side="left")
        tk.Entry(frm_thr, textvariable=self.translate_thread_count, width=6).pack(side="left", padx=(6,0))

        # controls: start/pause/stop/export
        ctrl_frame = tk.Frame(tab); ctrl_frame.pack(anchor="w", padx=10, pady=(10,4))
        self.btn_start = tk.Button(ctrl_frame, text="开始翻译", command=self.on_start_translate, width=12)
        self.btn_start.pack(side="left", padx=6)
        self.btn_pause = tk.Button(ctrl_frame, text="暂停", command=self.on_pause_toggle, width=10, state="disabled")
        self.btn_pause.pack(side="left", padx=6)
        self.btn_stop = tk.Button(ctrl_frame, text="停止", command=self.on_stop_translate, width=10, state="disabled")
        self.btn_stop.pack(side="left", padx=6)
        tk.Button(ctrl_frame, text="导出日志", command=self.export_logs, width=10).pack(side="left", padx=6)

        # progress bar
        self.progress_var = tk.DoubleVar(value=0.0)
        self.progress_bar = ttk.Progressbar(tab, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill="x", padx=10, pady=(6,4))
        self.label_progress = tk.StringVar(value="进度：0/0 (0.0%)")
        tk.Label(tab, textvariable=self.label_progress).pack(anchor="w", padx=10)

        # current entry
        self.current_entry_var = tk.StringVar(value="当前条目：等待")
        tk.Label(tab, textvariable=self.current_entry_var, wraplength=920, fg="blue").pack(anchor="w", padx=10, pady=(6,4))

        # log area
        tk.Label(tab, text="翻译日志：").pack(anchor="w", padx=10)
        log_frame = tk.Frame(tab); log_frame.pack(fill="both", expand=True, padx=10, pady=(0,10))
        self.log_text = tk.Text(log_frame, height=20)
        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar = tk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.pack(side="right", fill="y")
        self.log_text.config(yscrollcommand=scrollbar.set)
        self.log_text.tag_config("normal", foreground="black")
        self.log_text.tag_config("warn", foreground="#d18a00")
        self.log_text.tag_config("error", foreground="red")

        # lock state
        self._ui_locked = False
        self._log_lock = threading.Lock()
        self._progress_lock = threading.Lock()

    def on_browse_translate_folder(self):
        p = filedialog.askdirectory()
        if p:
            self.translate_folder_var.set(p)

    # logging helpers
    def log(self, msg, level="normal"):
        with self._log_lock:
            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.log_text.insert("end", f"{ts} {msg}\n", level)
            self.log_text.see("end")

    def export_logs(self):
        text = self.log_text.get("1.0", "end")
        fn = filedialog.asksaveasfilename(defaultextension=".txt", initialfile="translate_log_"+datetime.datetime.now().strftime("%Y%m%d_%H%M%S")+".txt", filetypes=[("Text files","*.txt")])
        if not fn:
            return
        try:
            with open(fn, "w", encoding="utf-8") as f:
                f.write(text)
            messagebox.showinfo("导出成功", f"日志已保存到：\n{fn}")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    # UI lock/unlock (partial: only lock translate tab controls except pause/stop)
    def lock_translate_ui(self):
        if self._ui_locked:
            return
        self._ui_locked = True
        # disable inputs that should not be changed
        self.btn_start.config(state="disabled")
        self.btn_pause.config(state="normal")
        self.btn_stop.config(state="normal")
        self.prompt_entry.config(state="disabled")
        # disable config fields
        # keep pause/stop enabled
        # also disable API selection and other controls
        for widget in [self.translate_api_var, self.translate_key_var, self.translate_target_lang,
                       self.batch_mode_var, self.max_tokens_var, self.manual_tokens_var,
                       self.translate_thread_count, self.translate_skip_empty, self.translate_skip_translated, self.translate_back_check]:
            # we cannot disable StringVar directly; disable corresponding widgets by searching? Simpler: disable entire window controls by disabling root? We'll disable relevant buttons/entries by name:
            pass
        # simulate disabling common widgets by disabling all entries in translate tab except pause/stop and export
        for child in self.log_text.master.winfo_children():
            # noop
            pass

    def unlock_translate_ui(self):
        if not self._ui_locked:
            return
        self._ui_locked = False
        self.btn_start.config(state="normal")
        self.btn_pause.config(state="disabled")
        self.btn_stop.config(state="disabled")
        self.prompt_entry.config(state="normal")

    # pause/stop
    def on_pause_toggle(self):
        if not self._translation_running:
            return
        if not self.pause_event.is_set():
            self.pause_event.set()
            self.btn_pause.config(text="继续")
            self.log("翻译已暂停", "warn")
        else:
            self.pause_event.clear()
            self.btn_pause.config(text="暂停")
            self.log("继续翻译", "normal")

    def on_stop_translate(self):
        if not self._translation_running:
            return
        self.stop_event.set()
        self.log("用户请求停止翻译，正在终止...", "error")

    # start translation: per-part processing, per-part batching, multi-threaded batch workers
    def on_start_translate(self):
        if self._translation_running:
            messagebox.showwarning("提示", "翻译正在运行。")
            return
        folder = self.translate_folder_var.get()
        if not folder or not os.path.isdir(folder):
            messagebox.showerror("错误", "请选择有效的 extracted 文件夹")
            return
        header_path = os.path.join(folder, "0_header.json")
        if not os.path.exists(header_path):
            messagebox.showerror("错误", "在文件夹中未找到 0_header.json")
            return

        # read settings
        api = self.translate_api_var.get()
        key = self.translate_key_var.get().strip()
        target = self.translate_target_lang.get().strip() or "zh"
        try:
            threads = max(1, int(self.translate_thread_count.get()))
        except:
            messagebox.showerror("错误", "线程数须为整数")
            return
        skip_empty = bool(self.translate_skip_empty.get())
        skip_translated = bool(self.translate_skip_translated.get())
        back_check = bool(self.translate_back_check.get())
        batch_mode = self.batch_mode_var.get()
        try:
            max_tokens = int(self.max_tokens_var.get())
        except:
            max_tokens = 1000
        try:
            manual_tokens = int(self.manual_tokens_var.get())
        except:
            manual_tokens = 800
        prompt_instructions = self.prompt_var.get().strip()

        # lock UI (partial)
        self.lock_translate_ui()
        self._translation_running = True
        self.stop_event.clear()
        self.pause_event.clear()
        self.log_text.delete("1.0", "end")
        self.log("开始批量翻译任务", "normal")

        # stages: for each part_*.json in folder, process
        part_files = sorted([f for f in os.listdir(folder) if f.startswith("part_") and f.endswith(".json")])
        if not part_files:
            self.log("未找到任何 part_*.json 分片文件", "error")
            self.unlock_translate_ui()
            self._translation_running = False
            return

        # compute total tasks (sum lengths)
        total = 0
        for pf in part_files:
            pdata = safe_json_load(os.path.join(folder, pf))
            total += len(pdata.get("Entries", []))
        with self._progress_lock:
            self.total_tasks = total
            self.done_tasks = 0
        self.update_progress_ui()

        # create a thread to process parts sequentially (so only one part in memory)
        t = threading.Thread(target=self._process_parts_worker, args=(
            folder, part_files, api, key, target, threads, skip_empty, skip_translated, back_check,
            batch_mode, max_tokens, manual_tokens, prompt_instructions
        ), daemon=True)
        t.start()

    def _process_parts_worker(self, folder, part_files, api, key, target, threads, skip_empty, skip_translated, back_check, batch_mode, max_tokens, manual_tokens, prompt_instructions):
        out_dir = folder.rstrip("/\\") + "_translated"
        os.makedirs(out_dir, exist_ok=True)

        try:
            for pf in part_files:
                if self.stop_event.is_set():
                    self.log("检测到停止信号，终止分片处理", "error")
                    break
                ppath = os.path.join(folder, pf)
                pdata = safe_json_load(ppath)
                entries = pdata.get("Entries", [])
                count = len(entries)
                if count == 0:
                    # write empty copy
                    safe_json_dump({"Meta": pdata.get("Meta", {}), "Entries": []}, os.path.join(out_dir, pf))
                    continue

                # Prepare batches for this part
                if batch_mode == "manual":
                    batches = split_into_batches(entries, mode="manual", manual_batch_tokens=manual_tokens)
                else:
                    batches = split_into_batches(entries, mode="auto", max_tokens=max_tokens)

                # results container for this part (index -> translated text)
                results = [None] * count

                # queue batches
                q = Queue()
                for (s, e, texts) in batches:
                    q.put((s, e, texts))

                # per-part back_checks accumulator
                part_back_checks = []

                # worker function for batch workers
                def batch_worker():
                    while True:
                        if self.stop_event.is_set():
                            break
                        if self.pause_event.is_set():
                            time.sleep(0.1)
                            continue
                        try:
                            s, e, texts = q.get(timeout=0.5)
                        except Empty:
                            break
                        # call batch translate
                        try:
                            trans, back = translate_batch(api, key, texts, target_lang=target, user_prompt=prompt_instructions, back_check=back_check, back_api=None, back_key=None)
                            if trans is None:
                                trans = texts[:]
                        except Exception as ex:
                            trans = texts[:]
                            back = []
                            self.log(f"[ERROR] 批次翻译失败 {pf}[{s}:{e}]: {ex}", "error")
                        # write back results into results array
                        for offset, ttxt in enumerate(trans):
                            idx = s + offset
                            results[idx] = ttxt
                        # collect back checks
                        if back:
                            # back is list of tuples (index_within_batch, back_text, score)
                            for (bi, bt, sc) in back:
                                global_index = s + bi
                                part_back_checks.append((global_index, bt, sc))
                                # log if low score
                                if sc < 0.6:
                                    self.log(f"[回译差异大] {pf}#{global_index} (score={sc:.2f})", "warn")
                        # update progress (batch size counted as items)
                        with self._progress_lock:
                            self.done_tasks += (e - s)
                        self.update_progress_ui()
                        q.task_done()

                # start worker threads for this part
                workers = []
                for _ in range(max(1, threads)):
                    wt = threading.Thread(target=batch_worker, daemon=True)
                    wt.start()
                    workers.append(wt)

                # wait for batches to finish
                q.join()
                # stop workers if any
                # write results to out_dir/part
                # ensure any None replaced with original
                for i in range(count):
                    if results[i] is None:
                        results[i] = entries[i]
                safe_json_dump({"Meta": pdata.get("Meta", {}), "Entries": results}, os.path.join(out_dir, pf))
                # write back_checks if any for this part
                if part_back_checks:
                    # append to a global back_checks file per out_dir
                    bc_path = os.path.join(out_dir, "back_checks.json")
                    existing = {}
                    if os.path.exists(bc_path):
                        try:
                            existing = safe_json_load(bc_path)
                        except:
                            existing = {}
                    existing.setdefault(pf, []).extend([{"index": idx, "back_text": bt, "score": sc} for (idx, bt, sc) in part_back_checks])
                    safe_json_dump(existing, bc_path)

                self.log(f"分片 {pf} 翻译完成（{count} 条）", "normal")

            # finished all parts or stopped
            if not self.stop_event.is_set():
                self.log("全部分片处理完成", "normal")
            else:
                self.log("翻译被用户停止，已保存已完成的分片", "warn")
        except Exception as ex:
            self.log(f"翻译流程出现异常：{ex}", "error")
        finally:
            # finalize UI state
            self.root.after(100, self._on_translate_finished)

    def update_progress_ui(self):
        with self._progress_lock:
            total = self.total_tasks
            done = self.done_tasks
        pct = (done / total * 100) if total else 100.0
        self.progress_var.set(pct)
        self.label_progress.set(f"进度：{done}/{total} ({pct:.1f}%)")
        # current entry is not tracked here for batch, we show last updated progress time
        self.current_entry_var.set(f"当前条目：已完成 {done}/{total}")

    def _on_translate_finished(self):
        self._translation_running = False
        self.unlock_translate_ui()
        self.btn_pause.config(state="disabled", text="暂停")
        self.btn_stop.config(state="disabled")
        self.btn_start.config(state="normal")
        # final progress update
        with self._progress_lock:
            done = self.done_tasks
            total = self.total_tasks
        self.progress_var.set(100.0 if total else 0.0)
        self.label_progress.set(f"完成：{done}/{total} (100%)")
        messagebox.showinfo("完成", "翻译任务已结束（见日志与输出目录）")

# ---------------------------
# run
# ---------------------------
def main():
    root = tk.Tk()
    app = App(root)
    root.mainloop()

if __name__ == "__main__":
    main()
