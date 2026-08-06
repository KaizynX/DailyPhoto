# DailyPhoto

每天第一次登录或解锁 Windows 后，自动用摄像头记录一张照片。

## 日常行为

1. 登录或解锁后，Windows 自动启动 `app\DailyPhoto.exe`。
2. 如果当天已经有保存的照片，程序静默退出。
3. 否则显示摄像头预览并倒计时 10 秒。
4. 拍摄后可以选择“保存”或“重拍”。
5. 15 秒内没有操作会自动保存。

照片按年月存放在 `photos\YYYY\MM\` 中。程序只在本机处理照片，不会上传。

## 管理

- 重新注册自动任务：右键 `install.ps1`，选择“使用 PowerShell 运行”。
- 取消自动任务：运行 `uninstall.ps1`。这不会删除照片。
- 调整倒计时、确认超时或摄像头编号：编辑 `config.json`。
- 手动测试：运行 `app\DailyPhoto.exe --force`。

完整设计和调研记录见 [PLAN.md](PLAN.md)。
