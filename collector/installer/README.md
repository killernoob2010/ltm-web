# WH6 成交采集器 Windows 安装包

这里保留安装包构建约定，不放入二进制、账户令牌或任何 Supabase 密钥。最终交付物为 Windows x64 自包含的 `WH6成交采集器-Setup.exe`；本机 Mac 环境没有 Windows 构建工具，因此必须在 Windows 11 虚拟机或 Windows CI 上构建并验收。

## 构建顺序

1. 在干净 Windows x64 构建机安装 Python 3.11，并执行 `python -m pip install -r collector/requirements-windows.txt`。
2. 在 `collector` 目录执行 `pyinstaller WH6成交采集器.spec`，得到不依赖系统 Python 的 `dist/WH6成交采集器/WH6成交采集器.exe`。
3. 使用 Inno Setup 6（免费）或 NSIS（免费）将该目录封装为单文件 `WH6成交采集器-Setup.exe`。安装器只写程序目录、开始菜单/开机启动项和 `%LOCALAPPDATA%\\WH6成交采集器`，不写 WH6 `Record` 目录。
4. 构建时只允许使用 `https://ltm-web-staging.onrender.com` 或本地测试地址；安装包中不得出现 Production URL、数据库密码、`service_role`、完整账户号或静态设备令牌。

## 安装与迁移验收

- 双击 Setup 后不要求用户安装 Python；登录 Windows 后自动启动托盘程序。
- 首次设置优先自动发现 WH6，失败时允许选择 WH6 根目录或 `Record` 目录；程序只读取 `*match.dat`，不读取/改写 `*order.dat`。
- 通过 Web 管理页生成 15 分钟一次性连接码，在托盘程序输入后才绑定设备；设备令牌只保存在本机应用数据目录，并可从 Web 管理页撤销。
- 新电脑不复制旧电脑的本地 SQLite 或令牌；重新安装、选择路径、输入新连接码即可。服务端按账户和成交身份去重。
- 卸载程序不得删除 Staging 已上传事实；重新安装前的本地队列是否保留由用户明确选择。

真实 Windows 11 虚拟机完成安装、路径选择、账户确认、历史回补、账户切换暂停、断网排队和一条自然产生的新铁矿石期权成交在 10 秒内上传后，才可称为 Windows 安装包验收通过。
