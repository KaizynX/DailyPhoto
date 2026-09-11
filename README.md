# DailyPhoto

DailyPhoto 是一个常驻 Windows 通知区域的每日拍照工具。所有照片只在本机处理，不会上传。

## 日常使用

1. 启动 `app\DailyPhoto.exe` 后，任务栏通知区域会显示 DailyPhoto 图标。
2. 如果当天还没有照片，程序启动或当天第一次解锁屏幕时会自动显示摄像头预览并开始倒计时；当天已经拍过则静默驻留。
3. 拍摄后可以选择“保存”或“重拍”，无操作时会在设定时间后自动保存。
4. 关闭拍照窗口不会退出程序，之后可以从托盘菜单再次拍照。

右键托盘图标可以：

- 立即拍照
- 生成人脸对齐的 MP4 / GIF
- 修改照片目录、倒计时、摄像头、镜像和图片质量
- 打开照片目录
- 开启或关闭开机自启动
- 退出程序

照片默认按年月存放在 `photos\YYYY\MM\` 中。

## 生成人脸对齐延时影像

最方便的入口是右键托盘图标，选择“生成延时影像…”。对话框中可以选择包含完整头部和环境的 16:9 画面或正方形人脸特写、MP4/GIF、每帧时长，以及是否显示拍摄日期。生成在后台进行，不会卡住托盘。

也可以运行 `create-timelapse.ps1`。默认生成带日期的宽景 MP4；原始照片不会被修改。

```powershell
# 默认生成 MP4
.\create-timelapse.ps1

# 同时生成 MP4 和 GIF
.\create-timelapse.ps1 --format both

# 只生成 GIF
.\create-timelapse.ps1 --format gif

# 生成人脸特写，且不显示日期
.\create-timelapse.ps1 --crop face --timestamp none
```

对齐后的独立帧和处理报告也会保留，便于检查没有检测到人脸的照片。详细参数见 [操作手册](docs/USER_GUIDE.md)。

## 安装与开发

- 生成程序：使用 PowerShell 7 运行 `build.ps1`；会同时生成拍照程序和延时影像工具。
- 注册开机自启动：运行 `install.ps1`，也可以直接在托盘菜单中启用。
- 取消开机自启动：运行 `uninstall.ps1` 或在托盘菜单中关闭。
- 强制打开拍照窗口进行测试：运行 `app\DailyPhoto.exe --force`。

详细操作、设置说明和故障排查见 [操作手册](docs/USER_GUIDE.md)。

完整设计和调研记录见 [PLAN.md](PLAN.md)。
