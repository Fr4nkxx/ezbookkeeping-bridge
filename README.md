# ezBookkeeping Bridge｜截图记账与余额对账

**从截图到入账，让日常记账更轻松。**

ezBookkeeping Bridge 为 [ezBookkeeping](https://github.com/mayswind/ezbookkeeping) 增加截图识别入口。通过苹果快捷指令提交付款记录或账户余额截图，服务会结合现有账户与分类识别内容，将交易或余额调整写入账本，并把截图作为附件保存。它适合希望保留自托管账本，同时减少重复录入的个人和家庭。

## 亮点

| 能力 | 带来的便利 |
| --- | --- |
| **截图即记账** | 识别支出、收入和账户间转账，匹配 ezBookkeeping 中的账户与二级分类；可附带当前位置。 |
| **单图与多图余额对账** | 将截图余额与账面余额比较，按差额生成调整记录。支持一次合计 2～10 张不同平台的截图，再对同一个 ez 账户进行调整；余额一致时不新增记录。 |
| **定时排查相同金额账单** | 每天按设定时间检查近 3 天的交易；发现相同金额时，通过 Bark 提醒核对，帮助发现可能的重复记账。提醒只供人工判断，不会修改交易。 |
| **多人独立使用** | 每位使用者拥有自己的 ezBookkeeping 令牌、Bridge 令牌和通知配置，账本互不混用。新用户可从首位 `default` 用户一次性复制账户结构与分类，减少重复配置。 |
| **配置可控、部署灵活** | 提示词可直接修改；视觉模型可接入兼容服务。ezBookkeeping、Bridge 与模型均可自行部署。若使用云端模型或外部 Bark 服务，相关数据会发送至对应服务。 |

关键交易信息不足、账户或币种无法确认时，服务会提示核对。每次处理结果可从请求响应查看；配置 Bark 后，也可在手机上接收通知。

## 快速开始

### 准备条件

- 已部署的 ezBookkeeping，且已启用 **API 令牌**和**交易图片**功能。
- 可运行 Docker Compose、并能访问 ezBookkeeping 的电脑或 NAS。
- 支持图片识别的 OpenAI 兼容模型服务；纯文本模型无法处理截图。
- 可选：用于接收处理结果与相同金额提醒的 Bark。

### 部署服务

1. 复制配置文件：

   ```bash
   cp .env.example .env
   ```

2. 编辑 `.env`，填写 `EZ_BASE_URL`、`EZ_API_TOKEN`、`BRIDGE_API_TOKEN`，以及模型服务的 `LLM_BASE_URL`、`LLM_MODEL`、`LLM_API_KEY`。`EZ_BASE_URL` 必须是 **Bridge 容器能够访问到**的地址。若账本中的默认现金账户名称不同，还需修改 `DEFAULT_CASH_ACCOUNT_NAME`。

3. 启动并检查服务：

   ```bash
   docker compose up -d --build
   ```

   打开 `http://你的设备地址:10140/health`，返回 `status: ok` 即表示服务已启动。需要查看日志时运行 `docker compose logs -f bridge`。

默认对外端口为 **10140**；修改 `docker-compose.yml` 中的端口后，快捷指令中的地址也需同步更新。

### 接入苹果快捷指令

快捷指令以 `POST` 表单请求 Bridge，地址使用 `http://你的设备地址:10140` 加下表中的路径，并在请求标头加入 `Authorization: Bearer 你的bridge_token`。外网使用前，请自行配置安全的访问方式。

| 场景 | 路径 | 表单内容 |
| --- | --- | --- |
| 截图记账 | `/receipt` | 文件字段 `image`：一张付款、收款或转账截图；可选文本字段 `latitude` 与 `longitude`，两者需同时提供。 |
| 单图余额对账 | `/balance` | 文件字段 `image`：一张显示账户余额的截图。 |
| 多图合计对账 | `/balance/aggregate` | 文件字段 `archive`：包含 2～10 张余额截图的 ZIP。 |

在快捷指令中可用「选择照片」或截屏获取图片，必要时先「转换图像」为 JPEG，再通过「获取 URL 内容」发送表单。多图流程可用「选择多张」和「制作归档」生成临时 ZIP；无需将 ZIP 保存到相簿或「文件」。最后添加「显示结果」即可查看处理状态。**不要手动设置 `Content-Type`**，由快捷指令自动生成表单边界。

多图对账中的截图必须属于**同一个 ez 账户**、来自不同平台且币种一致。服务不会自动换汇，也不会将不同账户的余额合并。

## 进阶配置

### 定时检查可能重复的账单

单用户模式下在 `.env` 设置 `BARK_PUSH_URL`；多用户模式下，为需要提醒的用户设置 `bark_push_url`。服务会按 `DUPLICATE_WATCH_DAILY_TIME` 每天执行一次，默认 **09:00**，时区由 `DEFAULT_TIMEZONE` 指定。只有配置了通知渠道的用户才会进行检查；没有发现相同金额时不会推送提醒。

```dotenv
BARK_PUSH_URL=https://bark.example.com/YOUR_DEVICE_KEY
DUPLICATE_WATCH_DAILY_TIME=09:00
```

检查范围为最近 **3 天**，筛选条件是交易金额相同，因此正常交易也可能被列入提醒。请在 ezBookkeeping 中结合时间、账户和备注人工核对。

### 多用户与账户分类初始化

1. 复制 `config/users.example.json` 为 `config/users.json`。首位用户须为 `id: "default"`；为每位用户配置不同的 `bridge_token` 和各自的 `ez_api_token`，并按需填写 `bark_push_url`、`default_cash_account_name` 或 `ez_base_url`。示例中的 `initialize_from_default` 为 `true`；暂不复制时请先改为 `false`。
2. 在 `.env` 中设置 `BRIDGE_USERS_FILE=/app/config/users.json`，然后运行 `docker compose up -d --build`。启用多用户模式后，以用户文件中的令牌为准；快捷指令只需保存对应用户的 **Bridge 令牌**。
3. 新用户需要沿用 `default` 的设置时，将该用户的 `initialize_from_default` 设为 `true`，先预览变更，再重启 Bridge。服务启动时会复制可见账户结构和分类；成功后自动将标记改回 `false`。

**初始化会先删除目标用户已有的分类。**它不会复制余额、交易记录、图片等个人数据，适合尚未正式记账的新用户。若目标账户结构冲突或来源使用自定义图标，初始化会停止并保留标记。可在启动前运行以下命令预览变更：

```bash
docker compose run --rm bridge python -m scripts.copy_default_catalog --target user-b
```

其中 `user-b` 替换为 `config/users.json` 中的目标用户 `id`。预览命令不会执行复制；确认后运行 `docker compose restart bridge` 完成初始化。

### 让识别结果符合自己的账本

模型会参考 ezBookkeeping 中已有的账户、二级分类和分类说明。建议先整理分类用途，再检查 `prompts/receipt.txt`、`prompts/balance.txt` 和 `prompts/balance_aggregate.txt` 是否符合自己的记账规则。提示词修改后，下次请求即可生效。

默认示例将微信、支付宝和银行卡等日常资金归入一个现金账户，将基金、股票和虚拟币归入相应投资账户。现金余额调整默认匹配名称含「修改余额」的收支二级分类；投资余额调整默认匹配名称含「涨跌」的收支二级分类。这些是示例约定，并非 ezBookkeeping 的统一标准。若分类名称不同，可修改提示词，或在 `.env` 中通过 `BALANCE_ADJUST_*_CATEGORY_PATH`、`BALANCE_INVESTMENT_*_CATEGORY_PATH` 指定完整分类路径。

首次使用时，建议用清晰的截图核对几笔结果。只显示收益、单个持仓或局部余额的页面，不适合作为总余额对账依据。

## 致谢与说明

感谢 [ezBookkeeping](https://github.com/mayswind/ezbookkeeping) 提供账本与开放接口、[Bark](https://github.com/Finb/Bark) 提供可选通知，以及苹果快捷指令提供手机端入口。本项目基于 FastAPI 等开源工具构建；代码、提示词和文档由 AI（Codex）完成。
