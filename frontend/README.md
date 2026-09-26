# 前端代码地图

前端是独立的 React/Vite 应用，只通过 `/api` 与后端通信。生产环境由 FastAPI 挂载编译后的 `dist`，只是本地分发方式，不形成源码依赖。

```text
src/
├─ main.tsx                   # React 启动入口
├─ app/                       # 页面装配、导航与跨页面工作区状态
├─ features/                  # 按业务能力组织的页面和组件
│  ├─ home/ inbox/ accounts/ assistant/ drafts/
│  ├─ work-items/ calendar/ activity/
│  └─ imports/ memory/ settings/ records/
├─ shared/
│  ├─ api/client.ts           # 统一 HTTP 客户端
│  ├─ api/generated.ts        # OpenAPI 生成类型
│  └─ mail.ts                 # 跨功能共享的轻量类型和显示映射
└─ styles/                    # 工作台样式
```

页面组件不应自行拼接认证头，也不应导入后端代码。新增功能优先放在对应 `features/<name>`；只有两个以上业务功能共同使用的代码才进入 `shared`。
