# PikPak 批量邀请注册

基于 Python 的 PikPak 全协议注册工具，集成**腾讯天御 V2 验证码**自动识别，纯 HTTP 请求完成注册流程，无需浏览器。

## 功能特性

- **全协议注册**：纯 HTTP 请求模拟完整注册流程，不依赖浏览器
- **腾讯天御 V2 验证码**：自动识别并提交验证码（YOLOv5 目标检测 + Siamese 孪生网络图标比对 + Node.js V8 沙箱 TDC）
- **批量邀请**：支持多线程批量注册，自动完成邀请奖励任务
- **临时邮箱**：内置多个临时邮箱服务（GuerrillaMail、Mail.tm、Temp-Mail），自动获取验证码
- **代理支持**：支持 HTTP/SOCKS5 代理池轮换，避免 IP 风控
- **GUI 界面**：基于 tkinter 的图形化操作界面，方便配置和监控
- **结果持久化**：注册成功自动保存账号、密码、Token 到本地文件

## 项目结构

```
PikpakV1.0/
├── main.py                 # 核心注册逻辑（主入口）
├── gui.py                  # 图形化界面（GUI 入口）
├── v8_submit.js            # Node.js V8 沙箱 TDC 运行器（验证码提交）
├── image_tools.py          # 图像处理工具（验证码图片切割/合成）
├── setup.bat               # Windows 一键环境安装脚本
├── requirements.txt        # Python 依赖
├── gui_config.json.example # GUI 配置文件模板
├── batch_result_protocol.txt # 注册结果输出文件
├── models/
│   ├── yolov5_detector.py  # YOLOv5 ONNX 推理封装（验证码目标检测）
│   └── siamese_compare.py  # Siamese ONNX 推理封装（图标相似度比对）
├── YOLO5/
│   ├── best.onnx           # YOLOv5 模型权重
│   └── best.onnx.data      # YOLOv5 模型数据
├── Siamese/
│   ├── IconCompare.onnx    # Siamese 模型权重
│   └── IconCompare.onnx.data # Siamese 模型数据
└── lib/
    ├── __init__.py
    ├── http_client.py      # HTTP 客户端 + 代理管理器
    ├── mail.py             # 临时邮箱服务封装
    └── utils.py            # 工具函数（签名、PoW、设备ID生成等）
```

## 环境要求

| 依赖 | 版本要求 |
|------|----------|
| Python | 3.9+ |
| Node.js | 任意版本（用于验证码 V8 沙箱） |
| pip | 最新版 |

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/your-username/your-repo.git
cd your-repo
```

### 2. 安装依赖

**Windows 用户**（推荐）：双击运行 `setup.bat`

**手动安装**：

```bash
pip install -r requirements.txt
```

确保已安装 Node.js（[下载地址](https://nodejs.org/)）。

### 3. 配置

复制配置文件模板并修改：

```bash
copy gui_config.json.example gui_config.json
```

编辑 `gui_config.json`，填入你的邀请链接等参数。

### 4. 运行

**GUI 模式**（推荐）：

```bash
python gui.py
```

**命令行模式**：

```bash
python main.py
```

## 验证码识别原理

本项目针对腾讯天御 V2 验证码实现了完整的自动化识别流程：

1. **预请求**（`cap_union_prehandle`）：获取验证码会话信息
2. **图像采集**：下载验证码背景图和滑块/图标素材
3. **YOLOv5 目标检测**：定位验证码中的目标元素位置
4. **Siamese 孪生网络**：计算图标相似度，匹配正确的目标
5. **V8 沙箱 TDC**：通过 Node.js 执行腾讯 TDC 脚本，生成采集数据
6. **PoW 计算**：完成工作量证明（Proof of Work）挑战
7. **提交验证**（`cap_union_new_verify`）：提交完整验证结果

## 配置文件说明

```json
{
  "delay": 0,              // 每次注册间隔（秒）
  "workers": 10,           // 并发线程数
  "max_rounds": 1,         // 最大注册轮数
  "yolo_path": "YOLO5\\best.onnx",       // YOLOv5 模型路径
  "siamese_path": "Siamese\\IconCompare.onnx", // Siamese 模型路径
  "v8_js": "v8_submit.js",                // V8 提交脚本路径
  "result_file": "batch_result_protocol.txt", // 结果输出文件
  "proxy_gateway": "",     // 代理网关地址
  "proxy_list": "",        // 代理列表（每行一个）
  "proxy_rotate": 1,       // 代理轮换间隔（请求数）
  "domain": "随机",         // 邮箱域名
  "invite_link": "https://mypikpak.com/s/YOUR_SHARE_ID", // 邀请链接
  "invite_share_id": "",   // 邀请分享ID
  "invite_pass_code_token": "",    // 邀请码Token
  "invite_trace_file_ids": ""      // 邀请追踪文件ID
}
```

## 输出格式

注册结果保存在 `batch_result_protocol.txt`，每行格式：

```
邮箱地址 | 密码 | AccessToken | 用户ID | 时间戳 | 状态
```

## 依赖项

- `requests` — HTTP 请求
- `Pillow` — 图像处理
- `numpy` — 数值计算
- `opencv-python` — 图像预处理
- `onnxruntime` — ONNX 模型推理
- Node.js（用于 `v8_submit.js`）

## 免责声明

本项目仅供学习和研究用途，请勿用于任何违反 PikPak 服务条款的行为。使用者需自行承担所有风险和责任。

## License

MIT