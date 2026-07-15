# OR 账号信息回填 Web

本地 FastAPI 工具：校验 AdsPower 连接、回填 OpenRouter 账号注册时间/IP/卡尾、检查余额，并在成功后提供 CSV 下载。

## 启动

```bash
git clone https://github.com/chuangbestco/or.git
cd or
./start.sh
```

首次启动会自动安装 `uv`（如尚未安装），下载兼容的 Python 3.13，并创建 `.venv` 安装依赖；随后浏览器访问 <http://127.0.0.1:8765>。即使系统默认 Python 是 3.14，也无需额外处理。

## 使用说明

1. 首次填写 AdsPower 连接地址和 API Key，点击“保存并校验连接”。信息只保存在**当前电脑**的 `~/.or-account-info-backfill/settings.json`，权限为仅当前用户可读写；后续启动自动读取。需要更换配置时，在页面重新填写并保存即可。
2. 上传 CSV / XLSX：必须包含 `account_id`、`AK`、`MK`、`bank_card_tail` 的实际内容，以及 `register_time`、`register_ip`、`charge_ip`、`remark` 表头。
3. 点击“执行回填”，查看实时进度。全部账号回填成功且余额均不低于 1 后，显示“下载csv”。

## 安全和本地性

- AdsPower API Key 不会提交 GitHub，也不会保存在项目目录内。
- 上传文件和输出文件由 `.gitignore` 排除。
- 服务仅监听 `127.0.0.1`。
- AdsPower `/api/v1/user/list` 采用全局限速及自动重试，避免触发每秒一次的接口限制。
