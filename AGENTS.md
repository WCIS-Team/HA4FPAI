# HA4FPAI 执行约定

## GitHub Actions 只手动运行

- 本仓所有工作流只允许 `workflow_dispatch`；需要复用时可保留 `workflow_call`，调用链的入口必须是用户手动发起的工作流。
- 不添加 `push`、`pull_request`、`schedule`、tag 推送或其他自动事件触发。新增工作流也遵守这一约定，除非用户明确修改要求。
- 提交、推送和打 tag 不代表授权启动 Actions。只有用户明确要求运行时，才执行 `gh workflow run` 或等价的启动操作。
- 保留 GitHub Actions 功能供手动运行；通过工作流触发配置控制执行，不把仓库 Actions 整体禁用。
