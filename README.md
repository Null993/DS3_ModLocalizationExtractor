
# 🎮 FromSoft JSON 工具（Extraction / Merge）

**用于提取、分片、合并 用 SmithBox 导出的 FromSoftware 游戏文本**

本工具用于处理用 SmithBox 导出的“黑暗之魂 / 只狼 / 艾尔登法环”等 FromSoftware 系游戏文本 JSON 文件，适合作为大批量翻译前的预处理步骤。
可将大型 FMG JSON 拆分为可编辑的多文件格式，并在修改后恢复为原始结构。

---

## 📦 功能概览

### ✔ 1. 提取（Extraction）

用于将 SmithBox 导出的原始JSON（通常为解包DCX文件得到的 FMG 文件）拆分为便于编辑和翻译的结构。

#### 支持内容：

* 读取原始 JSON（包含多个 `FmgWrapper`）
* 识别以下结构，并安全保留：

  * `Name`
  * `ID`
  * `Fmg → Name`
  * `Fmg → Entries[]`
  * `Fmg → Version / BigEndian / Unicode / Compression`
* 自动生成：

  * 全局头文件 `0_header.json`
  * 若启用拆分：`part_1.json`、`part_2.json`...
* 可自定义：

  * 是否拆分多个文件
  * 每个分片最大条目数

#### 输出结构（示例）：

```
<source>_extracted/
    ├── 0_header.json
    ├── part_1.json
    ├── part_2.json
    ├── ...
```

`0_header.json` 保存所有必要元数据，`part_x.json` 包含可用于翻译的提取集中化的实际 `Text` 内容。

---

### ✔ 2. 合并（Merge）

将提取目录（`_extracted/`）恢复为完整的可以直接通过SmithBox导入到DCX文件中的JSON 结构。

#### 支持内容：

* 读取所有 `part_*.json` 和 `0_header.json`
* 按原始顺序恢复全部文本
* 自动构建原始 FMG JSON 格式：

```json
{
  "Name": "...",
  "ID": ...,
  "Fmg": {
    "Name": "...",
    "Entries": [...],
    "Version": 2,
    "BigEndian": false,
    "Unicode": true,
    "Compression": 1
  }
}
```

* 自动输出：

  * 默认文件名：`<folder>_merged.json`
  * 或者自定义输出路径

#### 保证内容：

* ID 不变
* Text 内容不变
* FMG 元字段完整恢复（Version / BigEndian / Unicode / Compression）

---

## 📁 文件格式说明

### 输入（原始 JSON）

典型 FromSoftware DCX文件SmithBox Export File结构：

```json
{
  "Name": "Messages - DLC2.fmg",
  "ID": 279,
  "Fmg": {
    "Name": "Messages - DLC2.fmg",
    "Entries": [
      { "ID": 5010, "Text": "Take the plunge. You won't die." },
      { "ID": 5011, "Text": "Take the plunge." }
    ],
    "Version": 2,
    "BigEndian": false,
    "Unicode": true,
    "Compression": 1
  }
}
```

### 输出（分片目录）

```
menu_en_cinder_extracted/
    ├── 0_header.json       # 全局结构
    ├── part_1.json         # Entries 分片
    ├── part_2.json
```

`part_x.json` 示例：

```json
{
  "Meta": {
    "ChunkIndex": 1,
    "ChunkCount": 3,
    "StartIndex": 0,
    "Count": 500
  },
  "Entries": [
    "Take the plunge.",
    "Try jumping.",
    ...
  ]
}
```

---

## 🖥 操作说明（提取 / 合并）

### ➤ 提取步骤（Extraction）

*. 使用SmithBox的TextEditor->Data->Export->File...导出文本JSON文件
1. 打开程序后切换到“提取”页签
2. 选择原始 xxx.JSON 文件
3. 可选：启用“拆分多个分片”
4. 可选：设置每分片最大条数
5. 点击“开始提取”
6. 程序将创建：

   * `xxx_extracted/0_header.json`
   * `xxx_extracted/part_x.json`

提取后的目录可用于手动编辑或翻译。

---

### ➤ 合并步骤（Merge）

1. 切换到“合并”页签
2. 选择 `xxx_extracted/` 目录
3. 指定输出文件（可选）
4. 点击“开始合并并恢复”
5. 程序生成完整 FromSoftware JSON 文件

合并后的格式与原始结构完全一致，可使用SmithBox导入到DCX文件。

---

## ⚠ AI 翻译功能说明（未完成）

尚未完成

---

## 🚀 启动程序

```bash
python menu_tool_batch.py
```

或双击运行（Windows）。

---

## 📜 许可证

自由使用、修改、分发。适用于非商业用途翻译 / Mod 开发。

---

## 📝 提示

本工具专为 FromSoftware 游戏文本本地化处理设计，适用于：

* 文本翻译
* 分段 QA
* 文本替换/校对
* Mod 制作

