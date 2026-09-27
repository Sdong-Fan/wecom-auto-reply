# WeComBot PyInstaller 打包技能

## 概述

将企业微信智能客服系统打包为免安装的 Windows 可执行程序。
输出：`dist/WeComBot/`（~3.2GB），用户解压即用，无需 Python 环境。

## 前置条件

- Python 3.10 + 所有依赖已安装
- `bge_model/` 目录存在（包含 `model.safetensors`）
- `.env` 文件存在（至少有 `DEEPSEEK_API_KEY`）

## 一键打包

```bash
cd D:\\your-project
python scripts/build.py
```

输出：`dist/WeComBot/`，~3.2GB。

## 架构

```
WeComBot.spec          # PyInstaller 配置（datas/binaries/hiddenimports）
scripts/build.py       # 构建脚本（clean → pyinstaller → copy extras → verify）
启动.bat               # 用户启动脚本（设环境变量 → 启动 exe）
```

### 打包模式

**One-folder + COLLECT**（当前方案）：
- `启动.exe`（~1.2GB）：包含 bootloader + PKG 压缩包
- `_internal/`：解压后的 Python 运行时 + 依赖
- 用户双击 `启动.bat` → 设环境变量 → 启动 `启动.exe`

### sys._MEIPASS 路径

PyInstaller frozen 模式下：
- `sys._MEIPASS` → `dist/WeComBot/_internal/`（PKG 解压目录）
- `sys.executable` → `dist/WeComBot/启动.exe`
- CWD → `dist/WeComBot/`（用户双击 bat 时的目录）

**datas 中指定的文件最终在 `_internal/` 中**：
- `("bge_model", "bge_model")` → `_internal/bge_model/`
- `("config.json", ".")` → `_internal/config.json`

**binaries 中指定的 DLL 最终在 `_internal/` 中**：
- `("mklml.dll", ".")` → `_internal/mklml.dll`

## 已知陷阱（完整清单）

### 1. PaddlePaddle DLL 缺失

**症状**：`RuntimeError: The third-party dynamic library (mklml.dll) that Paddle depends on is not configured correctly. (error code is 126)`

**根因**：PaddlePaddle 依赖 `paddle/libs/*.dll`（mklml, mkldnn, libblas 等），PyInstaller 不自动打包。

**修复**：spec 中添加 binaries：
```python
import paddle
_PADDLE_LIBS = Path(paddle.__file__).parent / "libs"
paddle_dlls = [(str(dll), ".") for dll in _PADDLE_LIBS.glob("*.dll")]
# 在 Analysis 中：binaries=paddle_dlls
```

**同时需要**：`启动.bat` 中设置 PATH：
```bat
set PATH=%~dp0_internal;%~dp0;%PATH%
```

### 2. imageio dist-info 缺失

**症状**：`importlib.metadata.PackageNotFoundError: No package metadata was found for imageio`

**根因**：`imageio.__init__` 导入时调用 `importlib.metadata.version("imageio")`，需要 `.dist-info/METADATA`。PyInstaller 不自动打包。

**导入链**：`main.py → paddleocr → imgaug → imageio → importlib.metadata`

**修复**：spec 中添加 datas：
```python
import importlib.metadata
_IMAGEIO_DIST_INFO = Path(importlib.metadata.distribution("imageio")._path)
datas += [(str(_IMAGEIO_DIST_INFO), _IMAGEIO_DIST_INFO.name)]
```

### 3. Cython Utility 缺失

**症状**：`FileNotFoundError: Cython\Utility\CppSupport.cpp`

**根因**：PaddleOCR → paddle → Cython.Compiler.Code 需要 `*.cpp` 模板文件。

**修复**：spec 中添加 datas：
```python
import Cython
_CYTHON_UTILITY = Path(Cython.__file__).parent / "Utility"
datas += [(str(_CYTHON_UTILITY), "Cython/Utility")]
```

### 4. PaddleOCR 动态导入路径错误

**症状**：`FileNotFoundError: tools/__init__.py`（在 `_MEI*` 根目录）

**根因**：PaddleOCR 用 `spec_from_file_location()` 动态导入 `tools/`、`ppocr/`、`ppstructure/`。`paddleocr.paddleocr` 包/模块同名导致 `__file__` 解析到 `_MEI*/paddleocr.py`（根），而非 `_MEI*/paddleocr/paddleocr.py`（嵌套）。`os.path.dirname(__file__)` → temp 根目录。

**修复**：spec 中同时放 root 级和 nested 级：
```python
_PADDLEOCR_DIR = Path(paddleocr.__file__).parent
datas += [
    (str(_PADDLEOCR_DIR / "tools"), "tools"),
    (str(_PADDLEOCR_DIR / "tools"), "paddleocr/tools"),
    (str(_PADDLEOCR_DIR / "ppocr"), "ppocr"),
    (str(_PADDLEOCR_DIR / "ppocr"), "paddleocr/ppocr"),
    (str(_PADDLEOCR_DIR / "ppstructure"), "ppstructure"),
    (str(_PADDLEOCR_DIR / "ppstructure"), "paddleocr/ppstructure"),
]
```

### 5. BGE 模型加载失败

**症状**：启动后 embedding 报错，或尝试从 HuggingFace 下载（阻塞）。

**根因**：PyInstaller 打包后 `Path(__file__).parent.parent / "bge_model"` 不存在。

**修复**：
- spec 中打包：`("bge_model", "bge_model")`
- `embed_query.py` 中检查 frozen 模式：
```python
if getattr(sys, "frozen", False):
    bundled = Path(sys._MEIPASS) / "bge_model"
    if (bundled / "model.safetensors").exists():
        return str(bundled)
```

### 6. tkinter 找不到 Tcl/Tk

**症状**：`_tkinter.TclError: Can't find a usable init.tcl`

**根因**：COLLECT 模式下 tkinter 找不到 `_tcl_data/init.tcl`。

**修复**：`启动.bat` 中设置环境变量：
```bat
set TCL_LIBRARY=%~dp0_internal\_tcl_data
set TK_LIBRARY=%~dp0_internal\_tk_data
```

### 7. BAT 文件乱码

**症状**：CMD 中中文显示为乱码。

**根因**：BAT 文件是 LF 行尾。Windows CMD 需要 CRLF。

**修复**：确保 BAT 文件是 CRLF 行尾。Git 中：
```bash
git add --renormalize 启动.bat
```

### 8. console=False 无错误输出

**症状**：exe 崩溃但无任何输出。

**根因**：spec 中 `console=False`（GUI 模式），不显示控制台。

**排查方法**：查看 `logs/monitor.log`。或临时改为 `console=True` 重打包调试。

## 验证清单

打包后逐项检查：

```bash
# 1. paddle DLLs
ls dist/WeComBot/_internal/mklml.dll
ls dist/WeComBot/_internal/mkldnn.dll

# 2. bge_model
ls dist/WeComBot/_internal/bge_model/model.safetensors

# 3. imageio dist-info
ls -d dist/WeComBot/_internal/imageio*

# 4. Cython Utility
ls dist/WeComBot/_internal/Cython/Utility/CppSupport.cpp

# 5. PaddleOCR 数据
ls dist/WeComBot/_internal/tools/__init__.py
ls dist/WeComBot/_internal/ppocr/__init__.py

# 6. 启动.bat PATH 设置
grep "set PATH" dist/WeComBot/启动.bat

# 7. .env.example
ls dist/WeComBot/.env.example

# 8. config.json
ls dist/WeComBot/config.json

# 9. 快速启动测试（8秒无崩溃）
cd dist/WeComBot && timeout 8 ./启动.exe; echo $?
```

## 配置文件

### WeComBot.spec 关键配置

| 配置项 | 说明 |
|--------|------|
| `hiddenimports` | PyInstaller 无法自动检测的模块（torch, paddle 等） |
| `datas` | 非 Python 数据文件（bge_model, Cython, PaddleOCR 数据） |
| `binaries` | DLL 文件（paddle/libs/*.dll） |
| `console=False` | GUI 模式，不显示控制台 |
| `upx=True` | 启用 UPX 压缩 |

### 启动.bat 环境变量

| 变量 | 说明 |
|------|------|
| `TCL_LIBRARY` | tkinter Tcl 数据路径 |
| `TK_LIBRARY` | tkinter Tk 数据路径 |
| `PATH` | 添加 `_internal/` 让 exe 找到 paddle DLLs |
| `HF_ENDPOINT` | HuggingFace 镜像（中国加速） |
| `KMP_DUPLICATE_LIB_OK` | PyTorch DLL 兼容 |

## 新增依赖时的打包检查

添加新依赖后，检查：
1. 是否有 C/C++ DLL → 添加到 `binaries`
2. 是否有非 Python 数据文件 → 添加到 `datas`
3. 是否在导入时调用 `importlib.metadata` → 添加 dist-info 到 `datas`
4. 是否用动态导入（`importlib.util.spec_from_file_location`）→ 添加到 `datas` 和 `hiddenimports`
5. 运行验证清单确认无缺失
