# OR 账号信息回填 Web

本地 FastAPI Web 工具，用于批量回填 OpenRouter 账号信息：

- 保存并校验 AdsPower 连接地址与 API Key；
- 上传 CSV/XLSX，校验 `account_id`、`AK`、`MK`、`bank_card_tail` 的实际值，以及 `register_time`、`register_ip`、`charge_ip`、`remark` 表头；
- 通过 MK 查询 OpenRouter API Key `created_at`，按北京时间写入 `register_time`；
- 通过 AdsPower `username` 匹配账号并优先回填 `proxy_host`；
- 规范化银行卡后四位；
- 以 MK 优先、AK 兜底查询余额；任一余额低于 1 或任一回填失败时任务失败；
- 成功后提供 CSV 下载。

## 本地启动

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
./start.sh
```

浏览器访问：<http://127.0.0.1:8765>

## 数据安全

- AdsPower API Key 仅在当前浏览器请求中用于校验和处理，不写入项目文件。
- 上传文件和生成结果默认被 `.gitignore` 排除，不会提交到 GitHub。
- 服务仅绑定 `127.0.0.1`。
