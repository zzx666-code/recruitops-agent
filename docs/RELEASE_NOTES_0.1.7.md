Windows 10/11 x64 便携版。下载 `RecruitOps-Desktop-0.1.7-Windows-x64.zip`，完整解压到可写入的短路径（例如 `D:\RecruitOps`），双击 `RecruitOps-Desktop-Preview.exe`。首次启动会在程序旁创建独立的 `.data`，初始化内置 PostgreSQL。无需另装 Python、Node.js、PostgreSQL、Docker 或 Visual C++ 运行库。

此版本包含当前仓库的桌面工作台、内置浏览器、岗位抓取与匹配、投递记录、邮件和日程功能。模型连接与邮箱授权码由用户在本机配置；发布包不含个人资料或服务密钥。退出软件前请等待正在运行的任务结束，升级时完整备份旧版程序旁的 `.data`。

此包未做 Windows 代码签名，首次启动可能触发 SmartScreen 提示。ZIP 旁提供 SHA-256 校验文件。浏览器抓取和模型调用仍取决于网站及所配置的外部服务。
