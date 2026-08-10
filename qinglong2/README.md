# 小米运动 (Zepp Life) 刷步数 - 青龙面板重构版

本目录下的 `mimotion_ql.py` 是针对青龙面板专门重构并合并的**单文件版本**。

## 特点

1. **单文件部署**：所有登录、加密解密、Token缓存、步数提交及推送逻辑均整合在 `mimotion_ql.py` 中，无需依赖额外的本地工具类文件。
2. **完美兼容 GA 版登录逻辑**：完整保留了原 GitHub Actions 版本中 `app_token` 缓存验证 -> `login_token` 刷新 -> `access_token` 重新获取的逐级降级刷新逻辑。
3. **青龙通知原生适配**：优先调用青龙面板内置的 `notify.py` 发送推送；若独立运行或未导入 `notify`，自动回退到 PushPlus、企业微信 Webhook 或 Telegram Bot 等内置推送。
4. **自定义固定步数**：支持通过 `STEP` 环境变量设置固定步数，设置后优先使用固定步数，未设置时仍按时间段计算随机步数。

---

## 环境变量配置

在青龙面板的【环境变量】中添加以下变量：

### 1. `MIMOTION_CONFIG` (必填)

格式为合法 JSON 字符串。支持多账号，账号与密码用 `#` 分隔（必须数量一致）。

```json
{
  "USER": "13800138000#abc@qq.com",
  "PWD": "password1#password2",
  "MIN_STEP": "18000",
  "MAX_STEP": "25000",
  "SLEEP_GAP": "5",
  "USE_CONCURRENT": "False"
}
```

- `USER`: Zepp Life 绑定的手机号或邮箱（手机号自动补充 `+86`）。
- `PWD`: Zepp Life 登录密码。
- `MIN_STEP`: 最小步数（默认 18000）。
- `MAX_STEP`: 最大步数（默认 25000）。
- `SLEEP_GAP`: 多账号时单线程执行间隔秒数（默认 5 秒）。
- `USE_CONCURRENT`: 是否开启多线程并发执行（`True` 或 `False`）。

---

### 2. `MIMOTION_AES_KEY` (可选)

- **说明**：用于在本地加密保存 Token（加密文件 `encrypted_tokens.data` 生成在脚本所在目录）。
- **要求**：必须为 **16 个字符** 的字符串（如：`1234567890abcdef`）。
- **作用**：配置后可实现 Token 缓存复用，避免每次运行都重新进行账号密码登录，降低被风控或 429 的风险。

---

### 3. `STEP` (可选)

- **说明**：自定义每次运行提交的步数。
- **示例**：`25000`
- **优先级**：当设置了 `STEP` 环境变量时，直接使用该数值作为步数，不再按 `MIN_STEP`/`MAX_STEP` 随机生成。

---

## 依赖安装 (青龙面板)

在青龙面板【依赖管理】 -> 【Python3】 中添加以下依赖：
- `requests`
- `pytz`
- `pycryptodome` (必须安装，用于 AES 加密及 Token 保存)

---

## 任务添加

在青龙面板【定时任务】中新建任务：
- **名称**：小米运动刷步数
- **命令**：`task qinglong2/mimotion_ql.py`（或者 `python3 qinglong2/mimotion_ql.py`）
- **定时规则**：例如 `0 9,12,15,18,21 * * *`（每天 9/12/15/18/21 点执行）
