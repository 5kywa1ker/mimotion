# mimotion 青龙面板版（qinglong）

小米运动 / Zepp Life 自动刷步数脚本的 **青龙面板单文件版本**。

由 GitHub Actions 版本（仓库根目录的 `main.py` + `util/`）改造而来，**核心逻辑与原版保持一致**，尤其是 `access_token → login_token → app_token` 三级 token 登录缓存逻辑：优先复用已保存的 token，逐级失效逐级重新获取，最大限度减少重复登录、降低被风控的概率。

> 与 GA 版差异：无需 `PAT`、无需 `CONFIG` Secret 里再管随机 cron —— 定时由青龙面板自己负责。

## 文件说明

| 文件 | 说明 |
| --- | --- |
| `mimotion.py` | 青龙面板单文件脚本（已内联 AES 加解密、华米接口调用、推送逻辑） |
| `README.md` | 本文档 |

## 特点

- **单文件**，不需要 `util/` 目录，直接粘贴到青龙脚本管理即可运行。
- **token 登录缓存**：登录成功后把 `access_token / login_token / app_token / user_id / device_id` 等信息用 `AES_KEY`（AES-128-CBC + PKCS7，与原版完全相同）加密保存为同目录下的 `encrypted_tokens.data`，下次运行优先复用；失效时按 `app_token → login_token → access_token` 逐级重新申请；全部失效才重新账号密码登录。
- 支持多账号、随机步数（随时间线性增长）、多线程与多推送渠道（pushplus / 企业微信 / Telegram / Bark）。

## 一、安装依赖

脚本依赖 `requests`、`pytz`、`pycryptodome` 三个 Python 库。

在青龙面板中：**依赖管理 → 新建依赖**，目标选 `Python3`，名称分别填：

```
requests
pytz
pycryptodome
```

（或直接在宿主机执行 `pip3 install requests pytz pycryptodome`）

## 二、配置环境变量

在青龙面板 **环境变量** 页面添加。有两种方式，**二选一**即可：

> **字段归属速记**：`USER、PWD、MIN_STEP、MAX_STEP、SLEEP_GAP、USE_CONCURRENT` 以及所有推送配置（`PUSH_PLUS_*`、`PUSH_WECHAT_WEBHOOK_KEY`、`TELEGRAM_*`、`BARK_KEY`）都是 **CONFIG（JSON）里的字段**；用独立变量方式时，它们各自单独建一个变量。
> **`AES_KEY` 是例外：无论哪种方式，`AES_KEY` 都单独作为一个环境变量，绝不写进 CONFIG 的 JSON 里**（脚本只从环境变量读取它，写进 JSON 不会生效）。

### 方式 A：配置单个 `CONFIG` 变量（与 GA 版 Secret 完全一致）

如果你以前用 GitHub Actions 版，直接把你 Secret 里 `CONFIG` 的值原样填到青龙的 `CONFIG` 环境变量里即可：

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
  "BARK_KEY": "https://api.day.app/你的key",
  "SLEEP_GAP": "5",
  "USE_CONCURRENT": "False"
}
```

### 方式 B：配置独立环境变量（青龙面板常用方式）

分别添加以下变量（多账号用 `#` 或换行分隔，账号密码数量必须一一对应）：

| 环境变量 | 说明 | 默认值 |
| --- | --- | --- |
| `USER` | 登录账号，手机号或邮箱；多账号用 `#` 或换行分隔 | 必填 |
| `PWD` | 登录密码；多账号用 `#` 或换行分隔，需与 `USER` 数量一致 | 必填 |
| `MIN_STEP` | 最小步数 | `18000` |
| `MAX_STEP` | 最大步数 | `25000` |
| `SLEEP_GAP` | 多账号执行间隔（秒） | `5` |
| `USE_CONCURRENT` | 是否多线程（`True` / `False`），启用后忽略 `SLEEP_GAP` | `False` |
| `PUSH_PLUS_TOKEN` | pushplus 个人 token | 空 |
| `PUSH_PLUS_HOUR` | 仅在指定北京时间整点推送（整数，如 `21`） | 空（每次都推） |
| `PUSH_PLUS_MAX` | pushplus 每个账号详情最大条数，超出只推概要 | `30` |
| `PUSH_WECHAT_WEBHOOK_KEY` | 企业微信机器人 key | 空 |
| `TELEGRAM_BOT_TOKEN` | Telegram 机器人 token | 空 |
| `TELEGRAM_CHAT_ID` | Telegram chatId | 空 |
| `BARK_KEY` | Bark 设备 key 或完整地址 | 空 |
| `AES_KEY` | **16 位字符串**，用于加密保存 token（强烈推荐） | 空 |

> 如果同时配置了 `CONFIG` 和独立变量，会以 `CONFIG` 为准。

### AES_KEY 与 token 缓存（重要）

- **`AES_KEY` 是独立环境变量，不要写进 `CONFIG` 的 JSON 里**：脚本只认 `os.environ` 里的 `AES_KEY`，写在 CONFIG 里不会生效。
- 配置 `AES_KEY`（注意**必须恰好 16 个字符**，不要用中文）后，程序会把每个账号的登录 token 加密保存到脚本同目录的 `encrypted_tokens.data`，下次运行直接复用，不必反复登录。
- 第一次配置 `AES_KEY` 时如果提示“密钥不正确或者加密内容损坏 放弃token”，属于正常现象（旧文件用的是别的密钥），忽略即可，运行一次后会自动生成新密钥的文件。
- **从 GA 版迁移**：GA 版仓库里的 `encrypted_tokens.data` 用的是同样的 AES 方式加密，直接把该文件放到青龙脚本同目录即可无缝延续，无需重新登录。
- **请定期备份 `encrypted_tokens.data`**：删除/重建任务、更新脚本、重装面板时脚本目录可能变化，token 缓存会丢失（丢失后重新跑一次即可自动重新登录）。
- 不配置 `AES_KEY` 也能运行，但每次都会完整登录，多账号时容易触发接口风控（429），建议配置。

## 三、创建定时任务

1. 青龙面板 → **脚本管理** → 新建脚本，把 `mimotion.py` 内容粘贴进去保存（建议文件名保持 `mimotion.py`）。
2. 青龙面板 → **定时任务** → 新建任务：
   - 名称：`mimotion 刷步数`
   - 命令：`task mimotion.py`
   - 定时规则（cron）：按你**青龙服务器本地时区**设置，例如北京时间每天 0、2、4、6、8、10、12、14、16、18、20、22 点执行：
     ```
     0 0,2,4,6,8,10,12,14,16,18,20,22 * * *
     ```
   - 也可以每小时一次：`5 * * * *`，或每天 8 / 12 / 20 点：`0 8,12,20 * * *`。
3. 点击 ✅ 启用任务。

> **注**：GA 版支持随机分钟、cron 自动更新等机制，青龙版不需要 —— 定时由青龙面板负责，`PAT`/`CRON_HOURS` 相关配置一律忽略。

## 四、多账号配置

- `CONFIG` 方式：`USER` 与 `PWD` 用 **`#`** 分隔，例如 `13800138000#13800138001`。
- 独立变量方式：同一变量里每行一个账号（或 `#` 分隔）。
- **账号和密码数量必须一致**，否则脚本会打印数量不匹配并跳过执行。
- 账号与密码用**同一套分隔符**，且 `USER` 内不能出现 `#`、`PWD` 内不能出现换行/`#`。

## 五、推送配置

与 GA 版相同，支持：

- **pushplus**：`PUSH_PLUS_TOKEN`（[申请地址](https://www.pushplus.plus/push1.html)），可加 `PUSH_PLUS_HOUR` 限制整点推送、`PUSH_PLUS_MAX` 限制条数。
- **企业微信**：`PUSH_WECHAT_WEBHOOK_KEY`（webhook 机器人 key）。
- **Telegram**：`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`（两个都要配）。
- **Bark（iOS）**：`BARK_KEY`（纯 key 或完整地址，如 `https://api.day.app/你的key`）。

执行完会根据结果汇总推送；不配置则只打印日志。

## 六、运行与查看日志

- 在 **定时任务** 中点击对应任务的“运行”按钮手动跑一次，验证配置是否正确。
- 运行日志直接在任务详情里查看，会打印每个账号的随机步数与接口返回结果。

## 常见问题（FAQ）

1. **提示“密钥不正确或者加密内容损坏 放弃token”**：首次配置 `AES_KEY` 或更换过密钥导致，正常现象，忽略即可。
2. **提示“未配置账号密码”**：没填 `USER/PWD`，也没填 `CONFIG`，检查环境变量名是否正确（区分大小写）。
3. **手动在终端（bash）里测试时，脚本把 shell 自带的 `USER`/`PWD` 当成了账号密码**：终端环境默认有 `USER`（系统用户名）和 `PWD`（当前目录）这两个 shell 变量，会污染账号读取。先在终端执行 `unset USER PWD`（或 `export -n USER PWD`）再运行；**青龙面板定时任务本身不会注入这两个变量**，在面板里正常使用无需处理。
4. **提示账号数与密码数不匹配**：`USER/PWD` 中账号数量不一致，检查分隔方式。
5. **刷步失败 / 429**：同 IP 登录过多账号容易触发接口限制，适当调大 `SLEEP_GAP`，或减少频率与账号数量。如果支付宝不更新，建议注销账号重新登录并重新绑定（详见下文注意事项）。
6. **运行报 `ModuleNotFoundError`**：`requests/pytz/pycryptodome` 未安装，回到“一、安装依赖”安装。
7. **青龙面板日志中文乱码**：脚本文件请以 UTF-8 保存。

## 注意事项

1. 账号是**小米运动 / ZeppLife** 的账号（手机号或邮箱注册），不是小米账号。
2. 步数随机范围随时间线性增长，北京时间 22 点达到 `MIN_STEP~MAX_STEP` 最大值：10 点时约为 `10/22 × MIN_STEP ~ 10/22 × MAX_STEP`。可自行修改 `MIN_STEP` / `MAX_STEP`。
3. 如果支付宝等第三方不同步，到小米运动 App → 设置 → 账号 → 注销账号 → 清空数据，重新登录并重新绑定第三方。
4. token 缓存文件 `encrypted_tokens.data` 请在更新脚本 / 重建任务前做好备份。
5. 本脚本仅供个人学习与自动化测试使用，请遵守平台规则，文明使用。