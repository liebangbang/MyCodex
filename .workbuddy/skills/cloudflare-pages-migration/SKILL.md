---
name: cloudflare-pages-migration
description: 将指向中国大陆服务器（阿里云 ECS 等）的静态官网迁移到 Cloudflare Pages 境外节点，消除因域名无法 ICP 备案（如 .me/.io 等国别/新顶级域）导致的浏览器“危险”/拦截标记。当用户提到“官网被标危险”“迁移到 Cloudflare”“国内服务器备案”“网站打不开”“把官网搬到境外”时触发。
agent_created: true
---

# 国内静态站 → Cloudflare Pages 迁移

## 适用场景
- 静态官网（纯 HTML/CSS/JS，无服务端逻辑）当前托管在**中国大陆 ECS**，但域名**无法 ICP 备案**（`.me`、`.io`、`.xyz` 等国别/新顶级域，工信部备案系统不支持）
- 浏览器 / 运营商给站点打“危险”“不安全”“已拦截”标记
- 目标：把源站搬到**全球版 Cloudflare（cloudflare.com）境外节点**，免费、免备案、自动 HTTPS，消除标记

## 核心认知（先讲清，避免选错平台）
1. **cloudflare-cn.com ≠ cloudflare.com**
   - `cloudflare-cn.com`（中国网络，京东云合作）：**企业版 + 强制 ICP 备案**，无法备案的域名照样卡死 → 不能用
   - 全球版 `cloudflare.com`：境外节点，**免费套餐、无需备案** → 正确选择
   - 注册 / 登录：`https://dash.cloudflare.com/`；Pages 文档：`https://developers.cloudflare.com/pages/`
2. **危险标记的成因**：未备案域名 + 国内服务器，是合规拦截，不是网站有病毒
3. **改 NS，不是改 A 记录**：把域名交给 Cloudflare 托管（改 nameserver），而非在云解析里改 IP

## 迁移工作流

### 阶段 0：准备部署包（本地）
- 确认站点是纯静态（`index.html` + `style.css` + 资源图），可原样迁移
- **关键：下载包同步**。若 `index.html` 下载按钮指向 `/download/xxx.zip` 且 zip 只在原 ECS 上，先把 zip 同步到本地 `site/download/`：
  `scp <host>:/var/www/<site>/download/xxx.zip site/download/`（避免迁移后 404）
- 用 Git 方式部署则把 `site/` 提交 push（含 download/zip）；上传方式则稍后选文件夹

### 阶段 1：域名 NS 改到 Cloudflare（用户操作 + 你指导）
1. `dash.cloudflare.com` → Add a Site → 输入域名（如 `mycodex.me`）→ 选 Free
2. Cloudflare 给出 **2 个 nameserver**（`xxx.ns.cloudflare.com`）
3. 去**域名注册商**（阿里云 / HiChina）改 NS：
   - 入口在**域名注册控制台 → 域名列表 → 管理 → 修改 DNS 服务器**（不在“云解析 DNS”里，那里只能改 A/CNAME）
   - 删掉原 `dns*.hichina.com`，粘贴 Cloudflare 的 2 个 NS，保存
4. **旧 A 记录不用手删**，改 NS 后自动失效
5. 用 `dig NS <domain> +short` 或 `scripts/verify_migration.sh <domain>` 的 NS 段验证：WHOIS 先变，DNS 缓存几分钟到十几分钟

### 阶段 2：部署 site/ 到 Pages（二选一）
- **连 Git（推荐）**：Workers & Pages → Pages → 创建项目 → 导入 Git → 授权仓库 → Framework preset **None**、Build command 空、Build output directory **`site`** → 保存并部署
- **上传资源**：创建应用程序 → Pages → 上传资源 → 选本地 `site/` 文件夹 → 项目名 `mycodex` → 部署
- 得到 `xxx.pages.dev` 预览地址

### 阶段 3：绑定自定义域名
1. Pages 项目 → 自定义域 → 设置自定义域 → 输入 `mycodex.me`
2. Cloudflare 自动加 DNS 记录 + 签发 HTTPS 证书（几分钟变绿）
3. **删除残留 A 记录**：去 `mycodex.me → DNS → DNS 记录`，删掉 `@/www → 39.106.211.107` 旧 A 记录，只留 Pages 自动加的记录，防冲突

### 阶段 4：验证
运行 `bash scripts/verify_migration.sh mycodex.me`，确认：
- NS 已是 cloudflare
- `https://mycodex.me/` 返回 200
- 下载链接 `https://mycodex.me/download/xxx.zip` 返回 200

## 双域名分工建议
- `.me`/国别域 → 境外 Cloudflare Pages（品牌 / 海外站，免备案）
- `.com.cn`/`.cn`/`.com`（可备案）→ 走 ICP 备案做国内主站（备案期间域名不能对外提供内容）
- 猎帮帮等业务后端：照常在 ECS 上靠 IP 直接访问，不受迁移影响

## 常见坑（详见 references/troubleshooting.md）
- 误选 cloudflare-cn.com（中国网络）→ 仍要备案，白做
- 在“云解析 DNS”里改 NS（那里改不了，要去域名注册控制台）
- 把 Cloudflare 扫描导入的 A 记录误当成 NS 去改
- 绑定自定义域后残留旧 A 记录冲突
- 迁移后下载按钮 404（zip 没同步）

## 资源
- `scripts/verify_migration.sh`：一键检查 NS 传播 + HTTPS + 下载链接可用性
- `references/troubleshooting.md`：分步排查与平台混淆澄清
