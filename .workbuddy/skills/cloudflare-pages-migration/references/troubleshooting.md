# Cloudflare Pages 迁移排查参考

## 一、平台混淆澄清（最高频错误）

| 对比项 | cloudflare-cn.com（中国网络） | 全球版 cloudflare.com |
|--------|------------------------------|----------------------|
| 运营主体 | 京东云合作（境内节点） | Cloudflare 全球网络（境外节点） |
| 是否需要 ICP 备案 | **必须**（且要求企业版） | **不需要** |
| 套餐费用 | 企业版，付费门槛高 | 免费套餐即可 |
| `.me` 等无法备案域名能用吗 | ❌ 不能 | ✅ 能 |
| 能否消除“危险”标记 | ❌ 不能 | ✅ 能（源站出境外） |
| 国内访问速度 | 快（境内节点） | 略慢（跨境），但能打开 |

注册 / 登录入口：**https://dash.cloudflare.com/**（不是 cloudflare-cn.com）
Pages 文档：**https://developers.cloudflare.com/pages/**

## 二、在阿里云哪里改 NS（用户常找错地方）

阿里云控制台里有两处容易混：

| 位置 | 能不能改 NS | 说明 |
|------|------------|------|
| 云解析 DNS → 解析设置 | ❌ 不能 | 只能改 A / CNAME 记录 |
| **域名注册控制台 → 域名列表 → 管理 → 修改 DNS 服务器** | ✅ 能 | 这里改 NS 为 Cloudflare 给的 2 个 NS |

快速入口：`https://dc.console.aliyun.com`（域名 → 域名列表 → 管理）。

## 三、Cloudflare 控制台分步

1. **Add a domain**：输入裸域名（如 `mycodex.me`），选 Free
2. 扫描原 DNS 记录后点 **继续前往激活**，复制给出的 2 个 `xxx.ns.cloudflare.com`
3. 去注册商改 NS，回到 Cloudflare 等状态变绿（几分钟到十几分钟）
4. **部署 Pages**：左侧 计算 → Workers 和 Pages → Pages → 创建项目
   - 不要点蓝色的“创建 Worker”（那是云函数，不是静态站）
   - 选“导入现有 Git 存储库”或“上传资源”
   - Git 方式：Framework preset `None`、Build command 留空、Build output directory `site`
5. **绑定自定义域**：Pages 项目 → 自定义域 → 输入裸域名 → 自动加 DNS + 证书

## 四、迁移后仍“危险” / 打不开的排查顺序

1. `dig NS mycodex.me +short` 是否已是 cloudflare —— 没切就等或检查注册商 NS 是否保存
2. `curl -sI https://mycodex.me/` 是否 200 —— 否，则自定义域证书可能还在签发（等几分钟）或 DNS 记录冲突
3. 检查 Cloudflare **DNS 记录**里是否残留 `@/www → 39.106.211.107` 旧 A 记录，有则删（Pages 会自动加正确记录）
4. 下载按钮 404：确认 `site/download/xxx.zip` 已随站点一起部署（Git 仓库含该文件，或上传时选了整个 site/）

## 五、NS 传播验证

```bash
dig NS mycodex.me +short          # 看是否出现 cloudflare
whois mycodex.me | grep -i "Name Server"   # 注册局层面，最先变
curl -sI https://mycodex.me/      # 生效后应 200
```

传播规律：WHOIS 层先变（几分钟），递归 DNS 缓存按 TTL 刷新（通常几分钟到 1 小时）。Cloudflare 控制台显示“激活完成”即代表已全绿。
