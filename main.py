import json
import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox


# -------------------------------
# 提取部分：从原始 JSON 构建 header + 分片
# -------------------------------
def build_structure_and_entryids(json_data):
    """
    返回: top_name, structure_blocks, entry_ids_list
    structure_blocks: 每个 block 包含 WrapperName, WrapperID, FmgName, EntryIndexes(全局索引列表)
    entry_ids_list: 全局索引 -> 原始 ID
    """
    top_name = json_data.get("Name", "")
    structure = []
    entry_ids = []

    # 全局索引从0开始，按遍历顺序分配
    for wrapper in json_data.get("FmgWrappers", []):
        wname = wrapper.get("Name", "")
        wid = wrapper.get("ID", 0)
        fmg = wrapper.get("Fmg", {})
        fname = fmg.get("Name", "")

        indexes = []
        for entry in fmg.get("Entries", []):
            idx = len(entry_ids)
            indexes.append(idx)
            entry_ids.append(entry["ID"])

        structure.append({
            "WrapperName": wname,
            "WrapperID": wid,
            "FmgName": fname,
            "EntryIndexes": indexes
        })

    return top_name, structure, entry_ids


def write_header_and_parts(source_file, top_name, structure, entry_ids, texts, split, max_per_file):
    """
    texts: 全局索引顺序对应的 Text 列表（长度 == len(entry_ids)）
    生成 0_header.json; 然后生成 part_x.json，其中每个 part 包含 Meta.StartIndex 与 Entries(仅Text)
    返回分片数量
    """
    base_dir = os.path.dirname(source_file)
    base_name = os.path.splitext(os.path.basename(source_file))[0]
    out_dir = os.path.join(base_dir, base_name + "_extracted")
    os.makedirs(out_dir, exist_ok=True)

    # 写 header
    header = {
        "Meta": {"SourceTopName": top_name, "Version": 1, "TotalEntries": len(entry_ids)},
        "Structure": structure,
        "EntryIDs": entry_ids
    }
    with open(os.path.join(out_dir, "0_header.json"), "w", encoding="utf-8") as fh:
        json.dump(header, fh, ensure_ascii=False, indent=2)

    # 写分片
    if not split:
        part = {
            "Meta": {"ChunkIndex": 1, "ChunkCount": 1, "StartIndex": 0, "Count": len(texts)},
            "Entries": texts[:]  # 每个元素是 Text 字符串
        }
        with open(os.path.join(out_dir, "part_1.json"), "w", encoding="utf-8") as f:
            json.dump(part, f, ensure_ascii=False, indent=2)
        return 1

    chunks = [texts[i:i + max_per_file] for i in range(0, len(texts), max_per_file)]
    chunk_count = len(chunks)
    for i, chunk in enumerate(chunks, start=1):
        start_index = (i - 1) * max_per_file
        part = {
            "Meta": {"ChunkIndex": i, "ChunkCount": chunk_count, "StartIndex": start_index, "Count": len(chunk)},
            "Entries": chunk
        }
        with open(os.path.join(out_dir, f"part_{i}.json"), "w", encoding="utf-8") as f:
            json.dump(part, f, ensure_ascii=False, indent=2)

    return chunk_count


def extract_gui_build_and_write(file_path, split_flag, max_per_file):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        messagebox.showerror("错误", f"读取原始 JSON 失败：\n{e}")
        return

    top_name, structure, entry_ids = build_structure_and_entryids(data)

    # build texts list from original so translator sees original Text order
    texts = []
    # 根据 entry_ids 长度把对应 Text 填入（遍历 original structure）
    # We iterate same order used when building entry_ids above
    for wrapper in data.get("FmgWrappers", []):
        fmg = wrapper.get("Fmg", {})
        for entry in fmg.get("Entries", []):
            texts.append(entry.get("Text", ""))

    # write files
    out_count = write_header_and_parts(file_path, top_name, structure, entry_ids, texts, split_flag, max_per_file)
    messagebox.showinfo("完成", f"提取完成：已生成头文件 + {out_count} 个分片 (目录: {os.path.dirname(file_path)}/{os.path.splitext(os.path.basename(file_path))[0]}_extracted )")


# -------------------------------
# 合并部分：读取 0_header.json + 所有 part_x.json，然后恢复原始结构
# -------------------------------
def merge_folder_and_restore(folder, save_path):
    header_path = os.path.join(folder, "0_header.json")
    if not os.path.exists(header_path):
        messagebox.showerror("错误", "未找到头文件 0_header.json，请确保头文件存在。")
        return

    try:
        header = json.load(open(header_path, "r", encoding="utf-8"))
    except Exception as e:
        messagebox.showerror("错误", f"读取头文件失败：\n{e}")
        return

    top_name = header.get("Meta", {}).get("SourceTopName", "")
    structure = header.get("Structure", [])
    entry_ids = header.get("EntryIDs", [])
    total = header.get("Meta", {}).get("TotalEntries", len(entry_ids))

    # 初始化文本数组
    texts = [""] * total

    # 遍历 part_*.json，按 StartIndex 写入 texts
    part_files = sorted([f for f in os.listdir(folder) if f.startswith("part_") and f.endswith(".json")])
    if not part_files:
        messagebox.showerror("错误", "未找到任何 part_*.json 分片文件")
        return

    for pf in part_files:
        ppath = os.path.join(folder, pf)
        try:
            pdata = json.load(open(ppath, "r", encoding="utf-8"))
        except Exception as e:
            messagebox.showerror("错误", f"读取分片 {pf} 失败：\n{e}")
            return

        meta = pdata.get("Meta", {})
        start = int(meta.get("StartIndex", 0))
        entries_texts = pdata.get("Entries", [])
        for i, txt in enumerate(entries_texts):
            gi = start + i
            if 0 <= gi < total:
                texts[gi] = txt

    # texts 已经填好（任何未翻译项可能仍是空字符串）
    # 根据 structure 与 entry_ids 恢复原始 JSON 结构
    wrappers = []
    for block in structure:
        entry_list = []
        for idx in block.get("EntryIndexes", []):
            eid = entry_ids[idx] if 0 <= idx < len(entry_ids) else None
            entry_list.append({
                "ID": eid,
                "Text": texts[idx] if 0 <= idx < len(texts) else ""
            })
        wrappers.append({
            "Name": block.get("WrapperName", ""),
            "ID": block.get("WrapperID", 0),
            "Fmg": {
                "Name": block.get("FmgName", ""),
                "Entries": entry_list
            }
        })

    restored = {
        "Name": top_name,
        "FmgWrappers": wrappers
    }

    # 写入用户指定路径
    try:
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(restored, f, ensure_ascii=False, indent=2)
    except Exception as e:
        messagebox.showerror("错误", f"写入合并文件失败：\n{e}")
        return

    messagebox.showinfo("完成", f"合并并恢复完成，输出文件：\n{save_path}")


# -------------------------------
# Tkinter GUI
# -------------------------------
root = tk.Tk()
root.title("JSON 提取（头文件+分片）与合并工具")
root.geometry("760x420")

notebook = ttk.Notebook(root)
notebook.pack(fill="both", expand=True, padx=6, pady=6)

# ---- 提取 tab ----
tab_extract = tk.Frame(notebook)
notebook.add(tab_extract, text="提取（生成头文件 + 分片）")

extract_file_var = tk.StringVar()
extract_split_var = tk.IntVar(value=0)
extract_max_var = tk.StringVar(value="500")

tk.Label(tab_extract, text="选择原始 JSON 文件：").pack(anchor="w", padx=10, pady=(10, 0))
frm_e = tk.Frame(tab_extract)
frm_e.pack(fill="x", padx=10, pady=4)
tk.Entry(frm_e, textvariable=extract_file_var, width=68).pack(side="left", fill="x", expand=True)
def on_browse_extract():
    p = filedialog.askopenfilename(filetypes=[("JSON files", "*.json")])
    if p:
        extract_file_var.set(p)
tk.Button(frm_e, text="浏览", command=on_browse_extract).pack(side="left", padx=6)

tk.Checkbutton(tab_extract, text="拆分为多个分片", variable=extract_split_var).pack(anchor="w", padx=10, pady=(8,0))
tk.Label(tab_extract, text="每个分片最大条数：").pack(anchor="w", padx=10, pady=(8,0))
tk.Entry(tab_extract, textvariable=extract_max_var, width=12).pack(anchor="w", padx=10, pady=(0,8))

def on_start_extract():
    p = extract_file_var.get()
    if not p:
        messagebox.showerror("错误", "请先选择原始 JSON 文件")
        return
    split_flag = extract_split_var.get() == 1
    try:
        maxn = int(extract_max_var.get()) if split_flag else 0
    except:
        messagebox.showerror("错误", "请填写有效的每分片最大条数")
        return

    # 读取原始 JSON 并构建 structure + entry ids + texts
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        messagebox.showerror("错误", f"读取原始文件失败：\n{e}")
        return

    top_name, structure, entry_ids = build_structure_and_entryids(data)
    # build texts in same order
    texts = []
    for wrapper in data.get("FmgWrappers", []):
        fmg = wrapper.get("Fmg", {})
        for entry in fmg.get("Entries", []):
            texts.append(entry.get("Text", ""))

    count = write_header_and_parts(p, top_name, structure, entry_ids, texts, split_flag, maxn)
    messagebox.showinfo("完成", f"提取成功：已写入头文件 0_header.json + {count} 个分片（目录见同级 {os.path.splitext(os.path.basename(p))[0]}_extracted）")

tk.Button(tab_extract, text="开始提取", command=on_start_extract, height=2).pack(pady=12)


# ---- 合并 tab ----
tab_merge = tk.Frame(notebook)
notebook.add(tab_merge, text="合并并恢复")

merge_folder_var = tk.StringVar()
merge_savevar = tk.StringVar()

tk.Label(tab_merge, text="选择分片文件夹（包含 0_header.json & part_*.json）：").pack(anchor="w", padx=10, pady=(10,0))
frm_m1 = tk.Frame(tab_merge)
frm_m1.pack(fill="x", padx=10, pady=4)
tk.Entry(frm_m1, textvariable=merge_folder_var, width=68).pack(side="left", fill="x", expand=True)
def on_browse_folder():
    p = filedialog.askdirectory()
    if p:
        merge_folder_var.set(p)
        # 同步默认输出名为 foldername_merged.json
        foldername = os.path.basename(os.path.normpath(p))
        default_out = os.path.join(os.path.dirname(p), foldername + "_merged.json")
        merge_savevar.set(default_out)
tk.Button(frm_m1, text="浏览", command=on_browse_folder).pack(side="left", padx=6)

tk.Label(tab_merge, text="选择输出合并文件（可修改）：").pack(anchor="w", padx=10, pady=(10,0))
frm_m2 = tk.Frame(tab_merge)
frm_m2.pack(fill="x", padx=10, pady=4)
tk.Entry(frm_m2, textvariable=merge_savevar, width=68).pack(side="left", fill="x", expand=True)
def on_choose_save():
    init = merge_savevar.get() or os.getcwd()
    p = filedialog.asksaveasfilename(defaultextension=".json", initialfile=os.path.basename(init), initialdir=os.path.dirname(init) if os.path.dirname(init) else None, filetypes=[("JSON files","*.json")])
    if p:
        merge_savevar.set(p)
tk.Button(frm_m2, text="选择输出", command=on_choose_save).pack(side="left", padx=6)

def on_start_merge():
    folder = merge_folder_var.get()
    savep = merge_savevar.get()
    if not folder:
        messagebox.showerror("错误", "请选择分片文件夹")
        return
    if not savep:
        messagebox.showerror("错误", "请选择输出文件路径")
        return

    merge_folder_and_restore(folder, savep)

tk.Button(tab_merge, text="开始合并并恢复", command=on_start_merge, height=2).pack(pady=12)


root.mainloop()
