# TraitTutor Main Page

TraitTutor 官方主页（Landing Page）——人格支持与学习证据驱动的 AI 学习教练。

> **TraitTutor：人格决定怎么教，证据决定教什么。**
> Big Five 人格映射你需要的支持方式，BKT 学科证据追踪你真正掌握到哪里。

线上地址：<https://traittutor.com> · 在线体验：<https://threethreeliu.top/traittutor-all-web/>

English summary is at the [bottom of this file](#english-summary).

## 页面特性

- **单文件、零依赖**：全部 HTML / CSS / JS 内联于 `index.html`，无构建步骤、无外部请求，任意静态托管即可部署。
- **中英双语**：页面右上角一键切换（状态保存在 `localStorage`），`<html lang>` 与 `document.title` 随语言同步更新。
- **SEO / GEO 就绪**：完整的 Open Graph、Twitter Card、canonical、robots、sitemap，以及 `WebSite` / `Organization` / `SoftwareApplication` / `FAQPage` 四类 Schema.org 结构化数据。
- **交互动效**：Big Five 人格拖拽演示、组件编排实时预览、滚动渐显，全部原生 JS 实现。
- **无障碍**：skip link、语义化地标（`nav` / `main` / `footer`）、FAQ 使用原生 `<details>` 折叠，无 JS 也能阅读全部内容。

## 目录结构

```
.
├── index.html          # 整个页面（结构 + 样式 + 交互 + JSON-LD）
├── robots.txt          # 搜索引擎抓取规则 + sitemap 地址
├── sitemap.xml         # 站点地图（traittutor.com）
└── assets/
    ├── brand/
    │   └── traittutor-mark-snow.png   # 品牌图标（512×512，OG / Schema logo）
    └── icons/
        ├── apple-touch-icon.png
        ├── favicon.png
        ├── favicon-32x32.png
        └── favicon.svg
```

## 本地预览

无需安装依赖，任选其一：

```bash
# 方式一：Python
python3 -m http.server 8080

# 方式二：Node
npx serve .
```

打开 <http://localhost:8080> 即可。

## 部署

纯静态站点，适配任意静态托管：

- **GitHub Pages**：仓库 Settings → Pages，选择 `main` 分支根目录即可；`CNAME` 指向 `traittutor.com`。
- **Vercel / Netlify / Cloudflare Pages**：直接导入本仓库，无构建命令，输出目录为根目录。

注意：`index.html`、`robots.txt`、`sitemap.xml` 与 JSON-LD 中的域名均写作 `https://traittutor.com/`，更换域名时需同步替换。

## 与 monorepo 的关系

本仓库内容由 [traittutor/traittutor](https://github.com/traittutor/traittutor) monorepo 中的 `main/traittutor-landing/` 目录经 `git subtree split` 推送而来，**请勿直接在本仓库提交修改**。同步方式（在 monorepo 中执行）：

```bash
cd traittutor            # monorepo 根目录
git subtree split --prefix=main/traittutor-landing -b landing-page-split
git push https://github.com/traittutor/traittutor_main_page.git landing-page-split:main
git branch -D landing-page-split
```

## 修改指南

- **文案**：所有内容均为中英成对出现（`<span class="t-zh">` / `<span class="t-en">`），修改时务必两种语言同步更新。
- **主题色**：样式变量集中在 `index.html` 顶部 `:root`（`--frost` 为品牌蓝 `#2563EB`），与主应用 `web/app/globals.css` 的 `.theme-snow` 保持一致。
- **SEO**：改动 `<title>`、meta description 或结构化数据后，同步检查 `sitemap.xml` 的 `<lastmod>`。

## 许可

Apache License 2.0 — 与主仓库一致。

---

## English Summary

This repository hosts the official TraitTutor landing page — an AI learning coach where **personality shapes how we teach, and evidence decides what to teach**.

- Single-file, dependency-free static page (`index.html`), deployable on any static host (GitHub Pages, Vercel, Netlify, …).
- Built-in Chinese / English toggle; full SEO setup including Open Graph, canonical, sitemap, and `WebSite` / `Organization` / `SoftwareApplication` / `FAQPage` structured data.
- Content flows from the [`traittutor/traittutor`](https://github.com/traittutor/traittutor) monorepo (`main/traittutor-landing/`) via `git subtree split` — make changes there, not here.

Licensed under Apache License 2.0.
