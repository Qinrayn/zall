# zall — Release Notes (0.6.0)

0.5.2 → 0.6.0 的变更。都是多轮真实使用后的常规迭代，无破坏性变更，
配置文件格式不变。技术细节见 `CHANGELOG.md`。

## 变更

- `/provider` 重做：列表包含自定义 provider；内置 14 家常见网关目录，
  名字 + key 即接入（base 自动补全，如 `/provider zhipu`）；直接贴 URL
  也行；接上后自动探测模型列表；`-p` 落盘。
- 模型干活期间可以打字：输入实时可见，Enter 排队，回合结束自动发出；
  Ctrl-C 随时可打断。
- 提示缓存可观测：状态栏显示上下文剩余与 cache 命中率；Anthropic 默认
  打缓存断点（`ZALL_ANTHROPIC_CACHE=0` 可关）。
- 控制台交互对齐 Argus / Codex：参数位 TAB 补全、`/status` `/keys`
  `/mcp` `/new`、`!` 直接执行 shell、长任务耗时显示为 `1m 05s` 式、
  配色对比度提升、API 错误只报一次。
- `/science`：参数位补全、`runall` / `last`、profiles、favorites。

## 修复

- UTF-8 BOM 导致配置静默解析失败（`[[providers]]` 数组与数字全部失效）。
- 启动恢复提示会吞掉正在打的首条任务。
- 404 "model not found" 被误报为 endpoint 问题。
- 自定义 provider 注册表与 `CONFIG_DIR` 脱节（中文用户名路径下）。

## 升级

```
pip install -U zall
```
