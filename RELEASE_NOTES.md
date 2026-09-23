# zall — Release Notes (0.6.3)

0.6.2 → 0.6.3 的变更。三个看点：跨会话记忆（`/memory`）、上下文窗口读数
开始如实（上游 `/models` 的 context_length 为准）、等待时不再出现"一行
空白"。无破坏性变更，配置文件格式不变。技术细节见 `CHANGELOG.md`。

## 跨会话记忆：/memory

zall 现在能记住你。记下的内容存 `~/.zall/memory.jsonl`，每个新会话自动
注入 system prompt（USER MEMORY 段）——身份、偏好、项目背景不用每次
重新交代。

```
/memory                           列出已有记忆
/memory add 我叫云丘，偏好中文    记录身份/偏好（默认类型）
/memory add project_knowledge …   记录项目知识
/memory add decisions …           记录已做的决定
/memory rm <编号>                 删除一条
/memory clear                     清空全部
```

同一会话内记下的内容，下一个新会话起生效。

## 变更

- **上下文水位"说实话"**：模型窗口大小以上游 `/models` 返回的
  `context_length` 为准（实测商汤网关 deepseek 系是 1M，内置表旧值只有
  128k/默认 32k，会提前触发压缩、footer 显示也是错的）。REPL 启动时后台
  探测，footer 与水位从第一回合就用真实窗口；窗口完全未知时不硬凑
  "32K" 或百分比，只显示已用 token 数。指纹小窗口（本地小模型）下 12K
  固定底座"凭空吞掉整窗"导致的永远 0% 也已修正（底座自适应缩到窗口的
  1/4）。
- **`/model` 列表按上游事实裁剪**：同时并行探测所有已配置 provider 的
  `/models`，只列当前网关真的在用的模型；没配置的 provider 折叠成一行
  （"not configured"，`/provider` 引导接入）。占位 key（`sk-secret-xxx`
  、`yourkey`）不再把 provider 算成"已配置"。
- **选择器交互升级**:`/model` 直接打字过滤候选（实时收窄）；过滤无
  匹配时 Enter 把输入原文作为模型名切换（先校验字符，`/exit` 这类命令
  拒绝，不污染模型名）。菜单与无终端降级共用同一契约，非交互场景行为
  一致。

## 修复

- **等待时"一行空白"不再**：spinner 帧从盲文字形换成句点序列——盲文
  U+28xx 在 Consolas/中文终端缺字形，等待行整个空白，看起来像卡死。
  现在等待有明确的动态反馈，且按 ascii/unicode 模式自动切换。
- **记忆在 Windows 上从未真正持久化**（0.6.3 起被修复）：`_save()` 的
  临时文件名套用了 `tempfile` 目录路径（含反斜杠与冒号，Windows 非法
  字符），写入一直静默失败——所以之前的"记忆"其实是一条都没存住。改用
  `mkstemp` 生成合法临时名，原子替换语义不变，`/memory` 的存储从版本
  号起真正可靠。

## 升级

```
pip install -U zall
```

Windows 免 Python 用户：直接下载新的 `zall.exe` 替换旧文件即可，配置与
会话都存放在 `~/.zall`，不受影响。