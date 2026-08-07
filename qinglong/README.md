# 小米运动刷步数 - 青龙面板版

基于 GitHub Actions 版本改造，适配青龙面板运行。

## 部署方式

### 方式一：上传文件（推荐）

1. 将整个 `qinglong` 文件夹上传到青龙面板的脚本目录：
   ```
   /ql/data/scripts/mimotion/
   ```
   
   目录结构应为：
   ```
   /ql/data/scripts/mimotion/
   ├── mimotion.py
   └── util/
       ├── __init__.py
       ├── aes_help.py
       ├── zepp_helper.py
       └── push_util.py
   ```

2. 安装依赖（在青龙面板「依赖管理」中添加）：
   ```
   requests pytz pycryptodome
   ```
   或者通过终端执行：
   ```bash
   pip3 install requests pytz pycryptodome
   ```

### 方式二：订阅仓库

如果你有维护的 Git 仓库，可以在青龙面板「订阅管理」中添加订阅：

1. **链接**：填写仓库 Git 地址，如 `https://github.com/5kywa1ker/mimotion.git`
2. **白名单**：填写正则表达式（每行一条，按仓库内文件相对路径匹配），只拉取 `qinglong/` 文件夹：
   ```
   ^qinglong/.*\.py$
   ```
   如果还想把 README 也拉下来，可以放宽为：
   ```
   ^qinglong/
   ```
3. **定时类型**：按需选择，建议 `interval` 或 `cron`（例如每天凌晨同步一次：`0 3 * * *`）

拉取后的文件会保留仓库内的目录结构，保存在：
```
/ql/data/scripts/mimotion/qinglong/
```

对应的定时任务命令应改为：
```
task /ql/data/scripts/mimotion/qinglong/mimotion.py
```

## 环境变量配置

在青龙面板「环境变量」中添加以下变量：

### 必填变量

| 变量名 | 说明 | 示例 |
|--------|------|------|
| `MIMOTION_CONFIG` | JSON格式用户配置 | 见下方配置说明 |

### 可选变量

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `MIMOTION_AES_KEY` | 16位字符密钥，加密保存登录token | 不设置则每次重新登录 |
| `MIMOTION_DATA_PATH` | token持久化文件路径 | 脚本同目录 |

> **兼容说明**：脚本同时支持 `CONFIG` 和 `AES_KEY` 变量名，优先使用 `MIMOTION_CONFIG` / `MIMOTION_AES_KEY`。

## CONFIG 配置说明

`MIMOTION_CONFIG` 的值为 JSON 格式字符串：

```json
{
  "USER": "abcxxx@xx.com",
  "PWD": "password",
  "MIN_STEP": "18000",
  "MAX_STEP": "25000",
  "PUSH_PLUS_TOKEN": "",
  "PUSH_PLUS_HOUR": "",
  "PUSH_PLUS_MAX": "30",
  "PUSH_WECHAT_WEBHOOK_KEY": "",
  "TELEGRAM_BOT_TOKEN": "",
  "TELEGRAM_CHAT_ID": "",
  "SLEEP_GAP": "5",
  "USE_CONCURRENT": "False"
}
```

### 字段说明

| 字段名 | 必填 | 说明 |
|--------|------|------|
| `USER` | 是 | 小米运动登录账号（手机号或邮箱），多账号用 `#` 分隔 |
| `PWD` | 是 | 小米运动登录密码，多账号用 `#` 分隔 |
| `MIN_STEP` | 否 | 最小步数，默认 18000 |
| `MAX_STEP` | 否 | 最大步数，默认 25000 |
| `PUSH_PLUS_TOKEN` | 否 | PushPlus 推送 token |
| `PUSH_PLUS_HOUR` | 否 | 只在指定整点推送（如设置 21，只在北京时间 21 点推送） |
| `PUSH_PLUS_MAX` | 否 | 推送最大账号详情数，默认 30 |
| `PUSH_WECHAT_WEBHOOK_KEY` | 否 | 企业微信机器人 Webhook key |
| `TELEGRAM_BOT_TOKEN` | 否 | Telegram 机器人 token |
| `TELEGRAM_CHAT_ID` | 否 | Telegram chat ID |
| `SLEEP_GAP` | 否 | 多账号执行间隔（秒），默认 5 |
| `USE_CONCURRENT` | 否 | 是否多线程执行，设为 `True` 启用 |

### 多账号示例

```json
{
  "USER": "13800138000#13800138001",
  "PWD": "abc123qwe#abcqwe2",
  "MIN_STEP": "18000",
  "MAX_STEP": "25000"
}
```

> **注意**：`#` 分隔的账号和密码数量必须一致，否则跳过执行。

## 添加定时任务

在青龙面板「定时任务」中添加：

| 字段 | 值 |
|------|-----|
| 名称 | 小米运动刷步数 |
| 命令 | `task /ql/data/scripts/mimotion/mimotion.py` |
| 定时规则 | `0 8,10,12,14,16,22 * * *` |

> 定时规则为标准 cron 表达式，上述示例表示每天 8、10、12、14、16、22 点各执行一次。  
> 步数会随时间线性增长，北京时间 22 点达到配置的最大值。

## 步数计算规则

步数范围随时间线性增长：
```
实际步数范围 = (当前分钟数 / 22点总分钟数) × 配置的步数范围
```

例如 10 点执行时：`10/22 × 18000 ~ 10/22 × 25000` = `8181 ~ 11363`

22 点之后执行则直接使用配置的完整范围 `18000 ~ 25000`。

## 注意事项

1. **账号类型**：必须使用小米运动（Zepp Life）独立账号，不支持小米主账号
2. **AES_KEY**：设置为 16 位字符（不支持中文），用于加密保存登录状态，避免每次重新登录
3. **限频风险**：同一 IP 登录过多账号可能触发 429 限频，多账号建议设置合理的 `SLEEP_GAP`
4. **同步失败**：如支付宝未同步步数，建议在小米运动 APP 中清空数据后重新登录绑定
5. **持久化路径**：如使用 Docker 部署青龙面板，确保 `MIMOTION_DATA_PATH` 指向持久化挂载目录，否则容器重建后 token 会丢失

## 故障排查

| 问题 | 可能原因 | 解决方案 |
|------|----------|----------|
| 登录失败 | 账号密码错误 | 检查 CONFIG 中 USER/PWD |
| 429 错误 | IP 限频 | 增加账号间隔或减少并发 |
| token 丢失 | 容器重建 | 使用持久化路径存储 |
| 推送失败 | token/key 配置错误 | 检查推送相关配置 |
| 依赖缺失 | 未安装 Python 依赖 | 在依赖管理中添加 |
